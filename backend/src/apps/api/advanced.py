from __future__ import annotations

from typing import cast
from uuid import UUID

from django.core.exceptions import ValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import Capability, has_capability
from apps.api.permissions import authenticated_user
from apps.api.schema import SchemaAPIView
from apps.automation.models import HumanTask
from apps.automation.presentation import review_reason_for_task
from apps.automation.services import close_human_task
from apps.configuration.models import SearchZone
from apps.contacts.queries import attention_queryset
from apps.overture.models import OvertureCoveragePartition, OvertureReleaseCheck
from apps.overture.releases import validate_release_id
from apps.overture.services import get_active_snapshot, get_latest_snapshot
from apps.overture.tasks import sync_snapshot


class HumanTaskResolutionSerializer(serializers.Serializer[dict[str, object]]):
    note = serializers.CharField(max_length=2000)


class AttentionView(SchemaAPIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        actor = authenticated_user(request)
        if not has_capability(actor, Capability.VIEW_CONTACTS):
            raise PermissionDenied
        data = []
        for task in attention_queryset(workspace_id=actor.membership.workspace_id)[:100]:
            reason = review_reason_for_task(task)
            data.append(
                {
                    "id": str(task.pk),
                    "contact_id": str(task.contact_id),
                    "contact_name": task.contact.name or task.contact.organization.name,
                    "kind": task.kind,
                    "reason": task.reason,
                    "status": task.status,
                    "title": reason.title,
                    "summary": reason.summary,
                    "next_step": reason.next_step,
                    "opened_at": task.opened_at,
                }
            )
        return Response({"data": data})


class HumanTaskActionView(SchemaAPIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request: Request, task_id: UUID, action: str) -> Response:
        actor = authenticated_user(request)
        if not has_capability(actor, Capability.MANAGE_AUTOMATION):
            raise PermissionDenied
        if action not in {"resolve", "dismiss"}:
            raise serializers.ValidationError({"action": "La acción no existe."})
        task = HumanTask.objects.filter(
            pk=task_id,
            workspace_id=actor.membership.workspace_id,
        ).first()
        if task is None:
            raise NotFound
        serializer = HumanTaskResolutionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            saved = close_human_task(
                task,
                actor=actor,
                dismiss=action == "dismiss",
                note=cast(str, serializer.validated_data["note"]),
            )
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": {"id": str(saved.pk), "status": saved.status}})


class OvertureStatusView(SchemaAPIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        actor = authenticated_user(request)
        if not has_capability(actor, Capability.MANAGE_INTEGRATIONS):
            raise PermissionDenied
        latest = get_latest_snapshot()
        active = get_active_snapshot()
        return Response(
            {
                "data": {
                    "latest_snapshot_id": str(latest.pk) if latest else None,
                    "active_snapshot_id": str(active.pk) if active else None,
                    "release_checks": [
                        {
                            "status": item.status,
                            "latest_release": item.latest_release,
                            "created_at": item.created_at,
                        }
                        for item in OvertureReleaseCheck.objects.order_by("-created_at")[:10]
                    ],
                    "partitions": [
                        {
                            "id": str(item.pk),
                            "release_id": item.release.release_id,
                            "province_code": item.province_code,
                            "province_name": item.province_name,
                            "status": item.status,
                            "is_active": item.is_active,
                            "place_count": item.place_count,
                            "imported_at": item.imported_at,
                            "error": item.error if item.status == item.Status.FAILED else "",
                        }
                        for item in OvertureCoveragePartition.objects.select_related(
                            "release"
                        ).order_by("province_name", "-created_at")[:100]
                    ],
                }
            }
        )


class OvertureSyncView(SchemaAPIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request: Request) -> Response:
        actor = authenticated_user(request)
        if not has_capability(actor, Capability.MANAGE_INTEGRATIONS):
            raise PermissionDenied
        release_id = cast(str, request.data.get("release_id", ""))
        province_code = cast(str, request.data.get("province_code", ""))
        try:
            release_id = validate_release_id(release_id)
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if not OvertureReleaseCheck.objects.filter(
            status=OvertureReleaseCheck.Status.SUCCEEDED,
            latest_release=release_id,
        ).exists():
            raise serializers.ValidationError("El release no está verificado por maintenance.")
        province = SearchZone.objects.filter(
            workspace_id=actor.membership.workspace_id,
            official_code=province_code,
            level=SearchZone.Level.PROVINCE,
            active=True,
            archived_at__isnull=True,
        ).first()
        if province is None:
            raise NotFound
        task = sync_snapshot.delay(release_id, province_code)
        return Response(
            {
                "data": {
                    "status": "queued",
                    "celery_task_id": task.id,
                    "province_code": province_code,
                }
            },
            status=status.HTTP_202_ACCEPTED,
        )
