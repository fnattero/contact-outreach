from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.api.permissions import ManageConfigurationPermission, authenticated_user
from apps.api.schema import SchemaAPIView
from apps.configuration.message_templates import (
    create_message_template_revision,
    ensure_default_message_templates,
)
from apps.configuration.models import (
    SearchCategory,
    SearchZone,
    WorkspaceMessageTemplateRevision,
)
from apps.configuration.services import (
    runtime_prompt_configuration,
    save_config_item,
    save_prompt_configuration,
)


class SearchCategoryInputSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=160)
    sort_order = serializers.IntegerField(min_value=0, required=False, default=0)


class SearchCategorySerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    sort_order = serializers.IntegerField()
    rules_revision = serializers.IntegerField()
    rules = serializers.ListField(child=serializers.DictField())


class CategoryRuleInputSerializer(serializers.Serializer[dict[str, Any]]):
    taxonomy_code = serializers.CharField(max_length=160, required=False, allow_blank=True)
    name_terms = serializers.ListField(
        child=serializers.CharField(max_length=80), required=False, default=list
    )


class CategoryRulesInputSerializer(serializers.Serializer[dict[str, Any]]):
    rules = serializers.ListField(child=CategoryRuleInputSerializer(), max_length=40)


class SearchZoneSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    official_code = serializers.CharField()
    level = serializers.CharField()
    province_code = serializers.CharField()
    province_name = serializers.CharField()
    parent_id = serializers.UUIDField(allow_null=True)
    selectable = serializers.BooleanField()
    location_text = serializers.CharField()
    boundary_revision = serializers.IntegerField()
    boundary_hash = serializers.CharField()


class MessageTemplateInputSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.ChoiceField(choices=WorkspaceMessageTemplateRevision.Kind.choices)
    subject = serializers.CharField(max_length=255, required=False, allow_blank=True)
    body = serializers.CharField(max_length=12000)


class MessageTemplateSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    subject = serializers.CharField()
    body = serializers.CharField()
    revision = serializers.IntegerField()
    content_hash = serializers.CharField()
    approved_at = serializers.DateTimeField()
    active = serializers.BooleanField()


class PromptInputSerializer(serializers.Serializer[dict[str, Any]]):
    email_drafting_prompt = serializers.CharField(max_length=4000)


class PromptSerializer(serializers.Serializer[dict[str, Any]]):
    email_drafting_prompt = serializers.CharField()
    automatic_reply_prompt = serializers.CharField()
    revision = serializers.IntegerField()


def _category_data(category: SearchCategory) -> dict[str, object]:
    return {
        "id": category.pk,
        "name": category.name,
        "sort_order": category.sort_order,
        "rules_revision": category.rules_revision,
        "rules": [
            {
                "id": rule.pk,
                "taxonomy_code": rule.taxonomy_code,
                "name_terms": rule.name_terms,
                "active": rule.active,
                "sort_order": rule.sort_order,
            }
            for rule in category.rules.filter(active=True).order_by("sort_order", "created_at")
        ],
    }


def _zone_data(zone: SearchZone) -> dict[str, object]:
    return {
        "id": zone.pk,
        "name": zone.name,
        "official_code": zone.official_code,
        "level": zone.level,
        "province_code": zone.province_code,
        "province_name": zone.province_name,
        "parent_id": zone.parent_id,
        "selectable": zone.selectable,
        "location_text": zone.location_text,
        "boundary_revision": zone.boundary_revision,
        "boundary_hash": zone.boundary_hash,
    }


def _template_data(template: WorkspaceMessageTemplateRevision) -> dict[str, object]:
    return {
        "id": template.pk,
        "kind": template.kind,
        "subject": template.subject,
        "body": template.body,
        "revision": template.revision,
        "content_hash": template.content_hash,
        "approved_at": template.approved_at,
        "active": template.active,
    }


class SearchCategoryListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        workspace_id = authenticated_user(request).membership.workspace_id
        categories = SearchCategory.objects.filter(
            workspace_id=workspace_id,
            active=True,
            archived_at__isnull=True,
        ).prefetch_related("rules")
        return Response(
            {"data": [SearchCategorySerializer(_category_data(item)).data for item in categories]}
        )

    def post(self, request: Request) -> Response:
        serializer = SearchCategoryInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            category = SearchCategory.objects.create(
                workspace=authenticated_user(request).membership.workspace,
                name=cast(str, serializer.validated_data["name"]),
                sort_order=cast(int, serializer.validated_data["sort_order"]),
            )
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"name": "Ya existe una categoría con ese nombre."}
            ) from exc
        return Response(
            {"data": SearchCategorySerializer(_category_data(category)).data},
            status=status.HTTP_201_CREATED,
        )


class SearchCategoryRulesView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def _category(self, request: Request, category_id: UUID) -> SearchCategory:
        try:
            return SearchCategory.objects.get(
                pk=category_id,
                workspace_id=authenticated_user(request).membership.workspace_id,
                archived_at__isnull=True,
            )
        except SearchCategory.DoesNotExist as exc:
            raise NotFound from exc

    def get(self, request: Request, category_id: UUID) -> Response:
        category = self._category(request, category_id)
        return Response({"data": SearchCategorySerializer(_category_data(category)).data})

    def post(self, request: Request, category_id: UUID) -> Response:
        category = self._category(request, category_id)
        serializer = CategoryRulesInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            saved = save_config_item(
                item=category,
                actor=authenticated_user(request),
                category_rules=cast(list[dict[str, object]], serializer.validated_data["rules"]),
            )
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": SearchCategorySerializer(_category_data(saved)).data})

    patch = post


class SearchZoneListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        zones = SearchZone.objects.filter(
            workspace_id=authenticated_user(request).membership.workspace_id,
            active=True,
            archived_at__isnull=True,
        ).order_by("province_name", "sort_order", "name")
        level = request.query_params.get("level")
        if level:
            zones = zones.filter(level=level)
        parent_id = request.query_params.get("parent_id")
        if parent_id:
            try:
                parent_uuid = UUID(parent_id)
            except ValueError as exc:
                raise serializers.ValidationError(
                    {"parent_id": "El identificador de la zona no es válido."}
                ) from exc
            zones = zones.filter(parent_id=parent_uuid)
        return Response({"data": [SearchZoneSerializer(_zone_data(zone)).data for zone in zones]})


class SearchZoneGeometryView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request, zone_id: UUID) -> Response:
        try:
            zone = SearchZone.objects.get(
                pk=zone_id,
                workspace_id=authenticated_user(request).membership.workspace_id,
                active=True,
                archived_at__isnull=True,
            )
        except SearchZone.DoesNotExist as exc:
            raise NotFound from exc
        return Response(
            {
                "data": {
                    "id": str(zone.pk),
                    "boundary_revision": zone.boundary_revision,
                    "boundary_hash": zone.boundary_hash,
                    "geojson": zone.boundary_geojson,
                    "bbox": zone.boundary_bbox,
                }
            }
        )


class MessageTemplateRevisionView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        workspace = authenticated_user(request).membership.workspace
        ensure_default_message_templates(workspace)
        templates = WorkspaceMessageTemplateRevision.objects.filter(workspace=workspace).order_by(
            "kind", "-revision"
        )
        return Response(
            {"data": [MessageTemplateSerializer(_template_data(item)).data for item in templates]}
        )

    def post(self, request: Request) -> Response:
        serializer = MessageTemplateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = authenticated_user(request).membership.workspace
        try:
            template = create_message_template_revision(
                workspace=workspace,
                actor=authenticated_user(request),
                kind=cast(str, serializer.validated_data["kind"]),
                subject=cast(str, serializer.validated_data.get("subject", "")),
                body=cast(str, serializer.validated_data["body"]),
            )
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(
            {"data": MessageTemplateSerializer(_template_data(template)).data},
            status=status.HTTP_201_CREATED,
        )


class PromptConfigurationView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        runtime = runtime_prompt_configuration(authenticated_user(request).pk)
        return Response(
            {
                "data": PromptSerializer(
                    {
                        "email_drafting_prompt": runtime.email_drafting_prompt,
                        "automatic_reply_prompt": runtime.automatic_reply_prompt,
                        "revision": runtime.revision,
                    }
                ).data
            }
        )

    def patch(self, request: Request) -> Response:
        serializer = PromptInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            saved = save_prompt_configuration(
                owner=authenticated_user(request),
                email_drafting_prompt=cast(str, serializer.validated_data["email_drafting_prompt"]),
            )
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(
            {
                "data": PromptSerializer(
                    {
                        "email_drafting_prompt": saved.email_drafting_prompt,
                        "automatic_reply_prompt": saved.automatic_reply_prompt,
                        "revision": saved.revision,
                    }
                ).data
            }
        )
