from __future__ import annotations

from typing import cast
from uuid import UUID

from django.core.exceptions import ValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.api.payloads import json_object
from apps.api.permissions import (
    ManageAutomationPermission,
    ManageIntegrationsPermission,
    ViewContactsPermission,
    authenticated_user,
)
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
    permission_classes = (IsAuthenticated, ViewContactsPermission)

    def get(self, request: Request) -> Response:
        actor = authenticated_user(request)
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
    permission_classes = (IsAuthenticated, ManageAutomationPermission)

    def post(self, request: Request, task_id: UUID, action: str) -> Response:
        actor = authenticated_user(request)
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


def _latest_verified_release() -> str | None:
    check = (
        OvertureReleaseCheck.objects.filter(status=OvertureReleaseCheck.Status.SUCCEEDED)
        .exclude(latest_release="")
        .order_by("-created_at")
        .first()
    )
    return check.latest_release if check else None


def _province_states(workspace_id: UUID | str) -> list[dict[str, object]]:
    """Every province the app knows, with whether its business data is ready to search."""

    partitions = list(
        OvertureCoveragePartition.objects.select_related("release").order_by("-created_at")
    )
    rows: list[dict[str, object]] = []
    provinces = SearchZone.objects.filter(
        workspace_id=workspace_id,
        level=SearchZone.Level.PROVINCE,
        active=True,
        archived_at__isnull=True,
    ).order_by("name")
    for province in provinces:
        own = [item for item in partitions if item.province_code == province.official_code]
        ready = next((item for item in own if item.is_active and item.status == "READY"), None)
        importing = next((item for item in own if item.status == "IMPORTING"), None)
        failed = own[0] if own and own[0].status == "FAILED" else None
        state = (
            "READY" if ready else "IMPORTING" if importing else "FAILED" if failed else "MISSING"
        )
        shown = ready or importing or failed
        rows.append(
            {
                "code": province.official_code,
                "name": province.name,
                "state": state,
                "place_count": ready.place_count if ready else 0,
                "release_id": shown.release.release_id if shown else None,
                "updated_at": (shown.imported_at or shown.updated_at) if shown else None,
                "error": failed.error if failed else "",
            }
        )
    return rows


class OvertureStatusView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> Response:
        latest = get_latest_snapshot()
        active = get_active_snapshot()
        return Response(
            {
                "data": {
                    "provinces": _province_states(
                        authenticated_user(request).membership.workspace_id
                    ),
                    "latest_verified_release": _latest_verified_release(),
                    "latest_snapshot_id": str(latest.pk) if latest else None,
                    "active_snapshot_id": str(active.pk) if active else None,
                    # Overture's terms require the attribution and notices to stay visible.
                    "attribution": (
                        {
                            "release_id": active.release_id,
                            "attribution": active.attribution,
                            "licenses": [str(item) for item in active.source_licenses or []],
                            "notices": [str(item) for item in active.notices or []],
                        }
                        if active
                        else None
                    ),
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
    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def post(self, request: Request) -> Response:
        actor = authenticated_user(request)
        body = json_object(request)
        province_code = cast(str, body.get("province_code", ""))
        # Without an explicit version, load the newest one the maintenance check verified.
        release_id = cast(str, body.get("release_id", "")) or (_latest_verified_release() or "")
        if not release_id:
            raise serializers.ValidationError(
                "Todavía no hay una versión de datos verificada. Probá de nuevo en unos minutos."
            )
        try:
            release_id = validate_release_id(release_id)
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if OvertureCoveragePartition.objects.filter(
            status=OvertureCoveragePartition.Status.IMPORTING
        ).exists():
            raise serializers.ValidationError(
                "Ya hay una carga de datos en curso. Esperá a que termine para cargar otra."
            )
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
