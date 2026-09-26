from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from django.core.exceptions import ValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import Capability, workspace_for_user
from apps.api.permissions import ManageKnowledgePermission, authenticated_user
from apps.api.schema import SchemaAPIView
from apps.automation.models import (
    KnowledgeFactRevision,
    WorkspaceKnowledgeContextRevision,
)
from apps.automation.retrieval import retrieve_relevant_fact_revisions
from apps.automation.services import (
    approve_global_knowledge_context_revision,
    approve_knowledge_revision,
    save_global_knowledge_context,
    save_knowledge_revision,
)
from apps.integrations.contracts import ProviderError


class KnowledgeFactInputSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(max_length=240)
    category = serializers.CharField(max_length=120, required=False, allow_blank=True)
    text = serializers.CharField(max_length=4000)
    source_notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class KnowledgeContextInputSerializer(serializers.Serializer[dict[str, Any]]):
    context_text = serializers.CharField(max_length=4000)


class KnowledgeSearchSerializer(serializers.Serializer[dict[str, Any]]):
    query = serializers.CharField(max_length=2000)


def _fact_data(revision: KnowledgeFactRevision) -> dict[str, object]:
    return {
        "id": str(revision.pk),
        "fact_id": str(revision.fact_id),
        "title": revision.fact.title,
        "category": revision.fact.category,
        "version": revision.version,
        "text": revision.text,
        "source_notes": revision.source_notes,
        "content_hash": revision.content_hash,
        "approved": revision.is_approved,
        "approved_at": revision.approved_at.isoformat() if revision.approved_at else None,
    }


def _context_data(revision: WorkspaceKnowledgeContextRevision) -> dict[str, object]:
    return {
        "id": str(revision.pk),
        "version": revision.version,
        "context_text": revision.context_text,
        "source_notes": revision.source_notes,
        "content_hash": revision.content_hash,
        "approved": revision.is_approved,
        "approved_at": revision.approved_at.isoformat() if revision.approved_at else None,
    }


class KnowledgeFactListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageKnowledgePermission)

    def get(self, request: Request) -> Response:
        workspace = workspace_for_user(authenticated_user(request), Capability.MANAGE_KNOWLEDGE)
        revisions = KnowledgeFactRevision.objects.filter(fact__workspace=workspace).select_related(
            "fact"
        )
        return Response({"data": [_fact_data(item) for item in revisions]})

    def post(self, request: Request) -> Response:
        serializer = KnowledgeFactInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor = authenticated_user(request)
        workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
        try:
            revision = save_knowledge_revision(
                workspace=workspace,
                actor=actor,
                title=cast(str, serializer.validated_data["title"]),
                category=cast(str, serializer.validated_data.get("category", "")),
                text=cast(str, serializer.validated_data["text"]),
                source_notes=cast(str, serializer.validated_data.get("source_notes", "")),
            )
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _fact_data(revision)}, status=status.HTTP_201_CREATED)


class KnowledgeFactApproveView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageKnowledgePermission)

    def post(self, request: Request, revision_id: UUID) -> Response:
        actor = authenticated_user(request)
        try:
            revision = KnowledgeFactRevision.objects.select_related("fact").get(pk=revision_id)
        except KnowledgeFactRevision.DoesNotExist as exc:
            raise NotFound from exc
        try:
            saved = approve_knowledge_revision(revision, actor=actor)
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _fact_data(saved)})


class KnowledgeContextRevisionView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageKnowledgePermission)

    def get(self, request: Request) -> Response:
        workspace = workspace_for_user(authenticated_user(request), Capability.MANAGE_KNOWLEDGE)
        revisions = WorkspaceKnowledgeContextRevision.objects.filter(workspace=workspace)
        return Response({"data": [_context_data(item) for item in revisions]})

    def post(self, request: Request) -> Response:
        serializer = KnowledgeContextInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor = authenticated_user(request)
        workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
        try:
            revision = save_global_knowledge_context(
                workspace=workspace,
                actor=actor,
                context_text=cast(str, serializer.validated_data["context_text"]),
            )
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _context_data(revision)}, status=status.HTTP_201_CREATED)


class KnowledgeContextApproveView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageKnowledgePermission)

    def post(self, request: Request, revision_id: UUID) -> Response:
        actor = authenticated_user(request)
        try:
            revision = WorkspaceKnowledgeContextRevision.objects.get(
                pk=revision_id, workspace=actor.membership.workspace
            )
        except WorkspaceKnowledgeContextRevision.DoesNotExist as exc:
            raise NotFound from exc
        try:
            saved = approve_global_knowledge_context_revision(revision, actor=actor)
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": _context_data(saved)})


class KnowledgeSearchPreviewView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageKnowledgePermission)

    def post(self, request: Request) -> Response:
        serializer = KnowledgeSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = workspace_for_user(authenticated_user(request), Capability.MANAGE_KNOWLEDGE)
        try:
            result = retrieve_relevant_fact_revisions(
                workspace_id=workspace.pk,
                query_text=cast(str, serializer.validated_data["query"]),
            )
        except (ProviderError, ValidationError) as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(
            {
                "data": {
                    "status": result.status,
                    "manifest": result.manifest,
                    "matches": [
                        {
                            "revision_id": str(item.revision.pk),
                            "title": item.revision.fact.title,
                            "score": item.score,
                            "selected": item.selected,
                            "may_be_irrelevant": item.may_be_irrelevant,
                        }
                        for item in result.scores
                    ],
                }
            }
        )
