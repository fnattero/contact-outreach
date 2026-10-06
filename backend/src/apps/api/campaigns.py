from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import Capability, has_capability, workspace_for_user
from apps.api.concurrency import add_etag
from apps.api.errors import raise_domain_error
from apps.api.pagination import PageQuerySerializer, page_slice
from apps.api.payloads import json_object
from apps.api.permissions import (
    ExportDataPermission,
    ManageCampaignsPermission,
    ViewCampaignsPermission,
    authenticated_user,
)
from apps.api.schema import SchemaAPIView
from apps.audit.models import ApiIdempotencyRecord
from apps.campaigns.approval import approve_campaign, start_per_message_campaign
from apps.campaigns.models import Campaign, OutboundMessage
from apps.campaigns.services import create_campaign, resume_campaign, transition_campaign
from apps.campaigns.tasks import orchestrate_extraction
from apps.catalogs.models import Catalog
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import SearchCategory, SearchZone
from apps.contacts.models import CampaignEnrollment
from apps.dashboard.csv_export import csv_download
from apps.dashboard.queries import prospect_queryset
from apps.mailbox.tasks import deliver_message_task
from apps.prospects.models import Prospect


class CampaignListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    q = serializers.CharField(required=False, allow_blank=True, max_length=150)
    state = serializers.ChoiceField(required=False, choices=Campaign.State.choices)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)


WEEKDAY_CHOICES = (
    (0, "Lunes"),
    (1, "Martes"),
    (2, "Miércoles"),
    (3, "Jueves"),
    (4, "Viernes"),
    (5, "Sábado"),
    (6, "Domingo"),
)
MAX_CATALOG_BYTES = 15 * 1024 * 1024
MAX_COMBINED_CATALOG_BYTES = 17 * 1024 * 1024


def _selected[ModelT: Any](queryset: QuerySet[ModelT], ids: list[uuid.UUID]) -> list[ModelT] | None:
    """Return the rows for ``ids`` in queryset order, or None when any id is out of scope."""
    wanted = set(ids)
    rows = list(queryset.filter(pk__in=wanted))
    return rows if len(rows) == len(wanted) else None


class CampaignCreateSerializer(serializers.Serializer[dict[str, Any]]):
    """Validate a campaign draft; the caller's workspace arrives in ``context["workspace"]``.

    Every selectable object is resolved inside that workspace only, so an id belonging to another
    workspace is indistinguishable from one that does not exist. Provider fields are deliberately
    absent: they always come from the workspace's integration configuration, never the client.
    """

    name = serializers.CharField(max_length=200)
    delivery_mode = serializers.ChoiceField(choices=Campaign.DeliveryMode.choices)
    approval_mode = serializers.ChoiceField(choices=Campaign.ApprovalMode.choices)
    confirm_live = serializers.BooleanField(default=False)
    reminder_enabled = serializers.BooleanField(default=False)
    reminder_delay_days = serializers.IntegerField(min_value=1, max_value=32767)
    location_text = serializers.CharField(max_length=300)
    objective = serializers.IntegerField(min_value=1)
    max_raw_records = serializers.IntegerField(min_value=1)
    overture_min_confidence = serializers.DecimalField(
        max_digits=4, decimal_places=3, min_value=Decimal(0), max_value=Decimal(1)
    )
    daily_limit = serializers.IntegerField(min_value=1)
    message_interval_minutes = serializers.IntegerField(min_value=1)
    weekdays = serializers.ListField(
        child=serializers.ChoiceField(choices=WEEKDAY_CHOICES), allow_empty=False
    )
    window_start = serializers.TimeField()
    window_end = serializers.TimeField()
    timezone_name = serializers.CharField(max_length=64)
    categories = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    zones = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    provinces = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    catalogs = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        workspace = self.context["workspace"]
        errors: dict[str, list[str]] = {}

        def fail(field: str, message: str) -> None:
            errors.setdefault(field, []).append(message)

        categories = _selected(
            SearchCategory.objects.filter(
                workspace=workspace, active=True, archived_at__isnull=True
            ).order_by("sort_order", "name"),
            attrs["categories"],
        )
        if categories is None:
            fail("categories", "Alguno de los rubros seleccionados no está disponible.")
        zones = _selected(
            SearchZone.objects.filter(
                workspace=workspace,
                active=True,
                selectable=True,
                level__in=(SearchZone.Level.DISTRICT, SearchZone.Level.NEIGHBORHOOD),
                archived_at__isnull=True,
            )
            .select_related("parent")
            .order_by("province_name", "sort_order", "name"),
            attrs["zones"],
        )
        if zones is None:
            fail("zones", "Alguno de los distritos seleccionados no está disponible.")
        provinces = _selected(
            SearchZone.objects.filter(
                workspace=workspace,
                level=SearchZone.Level.PROVINCE,
                active=True,
                archived_at__isnull=True,
            ).order_by("name"),
            attrs["provinces"],
        )
        if provinces is None:
            fail("provinces", "Alguna de las provincias seleccionadas no está disponible.")
        catalogs = _selected(
            Catalog.objects.filter(workspace=workspace, active=True, missing=False).order_by(
                "name", "-version"
            ),
            attrs["catalogs"],
        )
        if catalogs is None:
            fail("catalogs", "Alguno de los PDF seleccionados no está disponible.")
        else:
            if any(item.byte_size > MAX_CATALOG_BYTES for item in catalogs):
                fail("catalogs", "Cada PDF debe pesar como máximo 15 MiB.")
            if sum(item.byte_size for item in catalogs) > MAX_COMBINED_CATALOG_BYTES:
                fail("catalogs", "Los PDFs seleccionados superan el límite combinado de 17 MiB.")

        if zones is not None and provinces is not None:
            if any(zone.parent_id is not None for zone in zones) and not provinces:
                fail("provinces", "Elegí la provincia de los distritos seleccionados.")
            if provinces:
                province_ids = {province.pk for province in provinces}
                if any(
                    zone.parent_id is not None and zone.parent_id not in province_ids
                    for zone in zones
                ):
                    fail(
                        "zones",
                        "Hay distritos elegidos dentro de una provincia que no está seleccionada.",
                    )
                if any(
                    not any(zone.parent_id == province.pk for zone in zones)
                    for province in provinces
                ):
                    fail("zones", "Elegí al menos un distrito en cada provincia seleccionada.")
        if attrs["delivery_mode"] == Campaign.DeliveryMode.LIVE and not attrs["confirm_live"]:
            fail("confirm_live", "Confirmá explícitamente antes de habilitar el modo en vivo.")
        if errors:
            raise serializers.ValidationError(errors)

        assert categories is not None and zones is not None and catalogs is not None
        attrs.update(
            weekdays=[int(day) for day in attrs["weekdays"]],
            categories=categories,
            zones=zones,
            provinces=provinces,
            catalogs=catalogs,
            catalog=catalogs[0],
        )
        return attrs


def _page(
    queryset: QuerySet[Campaign], *, page: int, page_size: int
) -> tuple[list[Campaign], dict[str, int]]:
    total = queryset.count()
    start = (page - 1) * page_size
    return list(queryset[start : start + page_size]), {
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _campaign_data(campaign: Campaign, *, include_admin: bool) -> dict[str, object]:
    first_messages = campaign.messages.filter(
        kind__in=(OutboundMessage.Kind.FIRST_CONTACT, OutboundMessage.Kind.INITIAL)
    )
    data: dict[str, object] = {
        "id": str(campaign.pk),
        "name": campaign.name,
        "state": campaign.state,
        "state_label": campaign.get_state_display(),
        "discovery_state": campaign.discovery_state,
        "discovery_state_label": campaign.get_discovery_state_display(),
        "delivery_mode": campaign.delivery_mode,
        "approval_mode": campaign.approval_mode,
        "created_at": campaign.created_at.isoformat(),
        "updated_at": campaign.updated_at.isoformat(),
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
    }
    if not include_admin:
        return data

    data.update(
        {
            "location_text": campaign.location_text,
            "objective": campaign.objective,
            "max_raw_records": campaign.max_raw_records,
            "daily_limit": campaign.daily_limit,
            "message_interval_minutes": campaign.message_interval_minutes,
            "weekdays": campaign.weekdays,
            "window_start": campaign.window_start.strftime("%H:%M"),
            "window_end": campaign.window_end.strftime("%H:%M"),
            "timezone_name": campaign.timezone_name,
            "reminder_enabled": campaign.reminder_enabled,
            "reminder_delay_days": campaign.reminder_delay_days,
            "status_reason": campaign.status_reason,
            "catalog": {
                "id": str(campaign.catalog_id),
                "name": campaign.catalog.name,
                "version": campaign.catalog.version,
            },
            "categories": [
                {
                    "id": str(selection.category_id),
                    "name": selection.name_snapshot,
                    "sort_order": selection.sort_order,
                }
                for selection in campaign.category_selections.order_by("sort_order", "created_at")
            ],
            "zones": [
                {
                    "id": str(selection.zone_id),
                    "name": selection.name_snapshot,
                    "sort_order": selection.sort_order,
                }
                for selection in campaign.zone_selections.order_by("sort_order", "created_at")
            ],
            "attachments": [
                {
                    "catalog_id": str(attachment.catalog_id),
                    "name": attachment.catalog.name,
                    "version": attachment.catalog.version,
                    "position": attachment.position,
                }
                for attachment in campaign.attachments.select_related("catalog").order_by(
                    "position", "created_at"
                )
            ],
            "metrics": {
                "enrollments": campaign.enrollments.count(),
                "prospects": campaign.prospects.count(),
                "initial_messages": first_messages.count(),
                "sent": first_messages.filter(state=OutboundMessage.State.SENT).count(),
                "review_ready": first_messages.filter(
                    state=OutboundMessage.State.REVIEW_READY
                ).count(),
                "queued": first_messages.filter(
                    state__in=(
                        OutboundMessage.State.PREPARED,
                        OutboundMessage.State.QUEUED,
                        OutboundMessage.State.SENDING,
                        OutboundMessage.State.RECONCILING,
                    )
                ).count(),
                "errors": first_messages.filter(state=OutboundMessage.State.SEND_FAILED).count(),
            },
            "audience_hash": campaign.audience_hash or None,
            "content_hash": campaign.content_hash or None,
            "attachment_hash": campaign.attachment_hash or None,
            "schedule_hash": campaign.schedule_hash or None,
        }
    )
    return data


def _campaign_queryset(
    workspace_id: uuid.UUID | str, *, include_drafts: bool
) -> QuerySet[Campaign]:
    queryset = Campaign.objects.select_related("catalog").filter(workspace_id=workspace_id)
    return queryset if include_drafts else queryset.exclude(state=Campaign.State.DRAFT)


class CampaignListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ViewCampaignsPermission)

    def get(self, request: Request) -> Response:
        query = CampaignListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_CAMPAIGNS)
        is_admin = has_capability(user, Capability.MANAGE_CAMPAIGNS)
        campaigns = _campaign_queryset(workspace.pk, include_drafts=is_admin)
        if values.get("q"):
            search = values["q"]
            campaigns = campaigns.filter(
                Q(name__icontains=search) | Q(location_text__icontains=search)
            )
        if values.get("state"):
            campaigns = campaigns.filter(state=values["state"])
        rows, meta = _page(campaigns, page=values["page"], page_size=values["page_size"])
        return Response(
            {
                "data": [_campaign_data(campaign, include_admin=is_admin) for campaign in rows],
                "meta": meta,
            }
        )

    def post(self, request: Request) -> Response:
        permission = ManageCampaignsPermission()
        if not permission.has_permission(request, self):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.MANAGE_CAMPAIGNS)
        serializer = CampaignCreateSerializer(data=request.data, context={"workspace": workspace})
        serializer.is_valid(raise_exception=True)
        cleaned = serializer.validated_data
        runtime = runtime_integration_configuration(user.pk)
        values = {
            field: cleaned[field]
            for field in (
                "name",
                "delivery_mode",
                "approval_mode",
                "reminder_enabled",
                "reminder_delay_days",
                "location_text",
                "objective",
                "max_raw_records",
                "overture_min_confidence",
                "daily_limit",
                "message_interval_minutes",
                "weekdays",
                "window_start",
                "window_end",
                "timezone_name",
                "catalog",
            )
        }
        values.update(
            extractor_provider=runtime.extractor_provider,
            website_fetcher=runtime.website_fetcher,
            llm_provider=runtime.llm_provider,
            llm_base_url=runtime.llm_base_url(),
            llm_model=runtime.llm_model,
        )
        try:
            campaign = create_campaign(
                actor=user,
                values=values,
                category_ids=[item.pk for item in cleaned["categories"]],
                zone_ids=[item.pk for item in cleaned["zones"]],
                catalog_ids=[item.pk for item in cleaned["catalogs"]],
            )
        except ValidationError as exc:
            raise_domain_error(exc)
        return Response(
            {"data": _campaign_data(campaign, include_admin=True)},
            status=status.HTTP_201_CREATED,
        )


class CampaignDetailView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ViewCampaignsPermission)

    @extend_schema(operation_id="campaign_detail")
    def get(self, request: Request, campaign_id: uuid.UUID) -> Response:
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_CAMPAIGNS)
        queryset = _campaign_queryset(
            workspace.pk,
            include_drafts=has_capability(user, Capability.MANAGE_CAMPAIGNS),
        ).prefetch_related("category_selections", "zone_selections", "attachments__catalog")
        try:
            campaign = queryset.get(pk=campaign_id)
        except Campaign.DoesNotExist as exc:
            raise serializers.ValidationError({"campaign_id": "La campaña no existe."}) from exc
        return add_etag(
            Response(
                {
                    "data": _campaign_data(
                        campaign,
                        include_admin=has_capability(user, Capability.MANAGE_CAMPAIGNS),
                    )
                }
            ),
            campaign,
        )


def _prepare_review_messages(message_ids: list[str]) -> None:
    for message_id in message_ids:
        deliver_message_task.delay(message_id)


class CampaignActionView(SchemaAPIView):
    # Approval is additionally gated by APPROVE_CAMPAIGNS inside post(); both capabilities are
    # held by ADMIN only today.
    permission_classes = (IsAuthenticated, ManageCampaignsPermission)

    allowed_actions = frozenset(
        {"start-discovery", "approve", "start-approved", "pause", "resume", "cancel"}
    )

    def post(self, request: Request, campaign_id: uuid.UUID, action: str) -> Response:
        if action not in self.allowed_actions:
            raise serializers.ValidationError({"action": "La acción no existe."})
        key = request.headers.get("Idempotency-Key") or ""
        try:
            uuid.UUID(key)
        except ValueError as exc:
            raise serializers.ValidationError(
                {"Idempotency-Key": "Enviá una clave UUID para esta acción."}
            ) from exc
        user = authenticated_user(request)
        method = request.method or ""
        fingerprint = sha256(
            b"|".join((method.encode(), request.path.encode(), request.body))
        ).hexdigest()
        capability = (
            Capability.APPROVE_CAMPAIGNS
            if action in {"approve", "start-approved"}
            else Capability.MANAGE_CAMPAIGNS
        )
        workspace = workspace_for_user(user, capability)
        if not Campaign.objects.filter(pk=campaign_id, workspace=workspace).exists():
            raise serializers.ValidationError({"campaign_id": "La campaña no existe."})
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=user.pk)
            existing = (
                ApiIdempotencyRecord.objects.select_for_update()
                .filter(user=locked_user, key=uuid.UUID(key), expires_at__gt=timezone.now())
                .first()
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise serializers.ValidationError(
                        {"Idempotency-Key": "La clave ya fue usada para otra solicitud."}
                    )
                return Response(existing.response_body, status=existing.response_status)
            try:
                if action == "approve":
                    campaign = approve_campaign(campaign_id, actor=locked_user)
                    if campaign.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY:
                        # Review-only messages are prepared (never sent) by the delivery task, so
                        # without this they would sit in QUEUED and never become reviewable.
                        queued_ids = [
                            str(pk)
                            for pk in campaign.messages.filter(
                                kind=OutboundMessage.Kind.INITIAL,
                                state=OutboundMessage.State.QUEUED,
                            ).values_list("pk", flat=True)
                        ]
                        transaction.on_commit(lambda: _prepare_review_messages(queued_ids))
                elif action == "start-approved":
                    campaign = start_per_message_campaign(campaign_id, actor=locked_user)
                elif action == "resume":
                    campaign = resume_campaign(
                        campaign_id=campaign_id,
                        actor=locked_user,
                        reason=str(json_object(request).get("reason", "")),
                    )
                    if campaign.state == Campaign.State.DISCOVERING:
                        transaction.on_commit(
                            lambda: orchestrate_extraction.delay(str(campaign.pk))
                        )
                else:
                    targets = {
                        "start-discovery": Campaign.State.DISCOVERING,
                        "pause": Campaign.State.PAUSED,
                        "cancel": Campaign.State.CANCELLED,
                    }
                    campaign = transition_campaign(
                        campaign_id=campaign_id,
                        target_state=targets[action],
                        actor=locked_user,
                        reason=str(json_object(request).get("reason", "")),
                    )
                    if action == "start-discovery":
                        transaction.on_commit(
                            lambda: orchestrate_extraction.delay(str(campaign.pk))
                        )
            except Campaign.DoesNotExist as exc:
                raise serializers.ValidationError({"campaign_id": "La campaña no existe."}) from exc
            except ValidationError as exc:
                raise_domain_error(exc)
            body = {"data": _campaign_data(campaign, include_admin=True)}
            ApiIdempotencyRecord.objects.create(
                user=locked_user,
                key=uuid.UUID(key),
                request_fingerprint=fingerprint,
                response_status=status.HTTP_200_OK,
                response_body=body,
                expires_at=timezone.now() + timedelta(hours=24),
            )
        return Response(body, status=status.HTTP_200_OK)


class CampaignCoverageView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ViewCampaignsPermission)

    def get(self, request: Request, campaign_id: uuid.UUID) -> Response:
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_CAMPAIGNS)
        campaign = Campaign.objects.filter(pk=campaign_id, workspace=workspace).first()
        if campaign is None or (
            campaign.state == Campaign.State.DRAFT
            and not has_capability(user, Capability.MANAGE_CAMPAIGNS)
        ):
            raise serializers.ValidationError({"campaign_id": "La campaña no existe."})
        return Response(
            {
                "data": {
                    "campaign_id": str(campaign.pk),
                    "categories": [
                        {
                            "id": str(item.category_id),
                            "name": item.name_snapshot,
                            "sort_order": item.sort_order,
                        }
                        for item in campaign.category_selections.order_by("sort_order")
                    ],
                    "zones": [
                        {
                            "id": str(item.zone_id),
                            "name": item.name_snapshot,
                            "sort_order": item.sort_order,
                        }
                        for item in campaign.zone_selections.order_by("sort_order")
                    ],
                    "search_runs": [
                        {
                            "id": str(run.pk),
                            "state": run.state,
                            "raw_count": run.raw_count,
                            "email_count": run.email_count,
                            "error": run.error if run.state == run.State.FAILED_PERMANENT else "",
                        }
                        for run in campaign.search_runs.order_by("created_at")
                    ],
                }
            }
        )


class CampaignEnrollmentListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ViewCampaignsPermission)

    def get(self, request: Request, campaign_id: uuid.UUID) -> Response:
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_CAMPAIGNS)
        campaign = Campaign.objects.filter(pk=campaign_id, workspace=workspace).first()
        if campaign is None or (
            campaign.state == Campaign.State.DRAFT
            and not has_capability(user, Capability.MANAGE_CAMPAIGNS)
        ):
            raise serializers.ValidationError({"campaign_id": "La campaña no existe."})
        enrollments = CampaignEnrollment.objects.filter(campaign=campaign).select_related(
            "organization", "selected_email"
        )
        return Response(
            {
                "data": [
                    {
                        "id": str(item.pk),
                        "organization_name": item.organization.name,
                        "selected_email": (
                            item.selected_email.original_email if item.selected_email else None
                        ),
                        "state": item.state,
                        "state_label": item.get_state_display(),
                        "exclusion_reason": (
                            item.exclusion_reason
                            if has_capability(user, Capability.MANAGE_CAMPAIGNS)
                            else ""
                        ),
                    }
                    for item in enrollments
                ]
            }
        )


class CampaignMessageListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ViewCampaignsPermission)

    def get(self, request: Request, campaign_id: uuid.UUID) -> Response:
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_CAMPAIGNS)
        campaign = Campaign.objects.filter(pk=campaign_id, workspace=workspace).first()
        if campaign is None or (
            campaign.state == Campaign.State.DRAFT
            and not has_capability(user, Capability.MANAGE_CAMPAIGNS)
        ):
            raise serializers.ValidationError({"campaign_id": "La campaña no existe."})
        messages = campaign.messages.order_by("created_at")
        if not has_capability(user, Capability.MANAGE_CAMPAIGNS):
            messages = messages.filter(state=OutboundMessage.State.SENT)
        from apps.api.mailbox import _outbound_data

        return Response(
            {
                "data": [
                    _outbound_data(
                        item,
                        include_admin=has_capability(user, Capability.MANAGE_CAMPAIGNS),
                    )
                    for item in messages
                ]
            }
        )


class CampaignProspectListView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ManageCampaignsPermission)

    def get(self, request: Request, campaign_id: uuid.UUID) -> Response:
        workspace = workspace_for_user(authenticated_user(request), Capability.MANAGE_CAMPAIGNS)
        if not Campaign.objects.filter(pk=campaign_id, workspace=workspace).exists():
            raise serializers.ValidationError({"campaign_id": "La campaña no existe."})
        prospects = Prospect.objects.filter(campaign_id=campaign_id).prefetch_related("emails")
        return Response(
            {
                "data": [
                    {
                        "id": str(item.pk),
                        "name": item.name,
                        "address": item.address,
                        "neighborhood": item.neighborhood,
                        "category": item.category,
                        "website": item.website,
                        "pipeline_state": item.pipeline_state,
                        "emails": [email.normalized_email for email in item.emails.all()],
                    }
                    for item in prospects
                ]
            }
        )


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _provenance_data(prospect: Prospect) -> dict[str, object] | None:
    """Where this record came from, plus the attribution and licences its data carries.

    Returns None for records that carry no Overture provenance (fake or older campaigns).
    """
    data = _mapping(prospect.provider_data)
    campaign_snapshot = prospect.campaign.overture_snapshot
    dataset = _mapping(data.get("snapshot"))
    zone = _mapping(data.get("zone"))
    provenance = _mapping(data.get("provenance"))
    if campaign_snapshot is None and not dataset and not data.get("overture_id"):
        return None
    licenses = provenance.get("source_licenses") or dataset.get("source_licenses") or []
    attribution = list(
        dict.fromkeys(
            str(value) for value in (dataset.get("attribution"), zone.get("attribution")) if value
        )
    )
    rule = _mapping(data.get("matched_rule"))
    email = next((item for item in prospect.emails.all() if item.is_primary), None)
    return {
        "release_id": (
            campaign_snapshot.release_id if campaign_snapshot else dataset.get("release_id")
        ),
        "overture_id": data.get("overture_id") or None,
        "confidence": data.get("confidence") or None,
        "matched_rule": (
            {
                "taxonomy_code": str(rule.get("taxonomy_code") or ""),
                "name_terms": [str(term) for term in rule.get("name_terms") or []],
            }
            if rule
            else None
        ),
        "contact_source": (
            {
                "email": email.normalized_email,
                "source": email.source,
                "source_url": email.source_url or None,
            }
            if email
            else None
        ),
        "attribution": attribution,
        "licenses": [str(item) for item in licenses] if isinstance(licenses, list) else [],
    }


class ProspectListQuerySerializer(PageQuerySerializer):
    campaign = serializers.UUIDField(required=False)
    state = serializers.ChoiceField(required=False, choices=Prospect.PipelineState.choices)
    category = serializers.CharField(required=False, allow_blank=True, max_length=150)
    neighborhood = serializers.CharField(required=False, allow_blank=True, max_length=150)


class ProspectListView(SchemaAPIView):
    """Prospects across every campaign, with the same filters the export honours."""

    permission_classes = (IsAuthenticated, ManageCampaignsPermission)

    def get(self, request: Request) -> Response:
        query = ProspectListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        workspace = workspace_for_user(authenticated_user(request), Capability.MANAGE_CAMPAIGNS)
        prospects = prospect_queryset(request.query_params).filter(campaign__workspace=workspace)
        rows, meta = page_slice(prospects, page=values["page"], page_size=values["page_size"])
        return Response(
            {
                "data": [
                    {
                        "id": str(item.pk),
                        "name": item.name,
                        "address": item.address,
                        "neighborhood": item.neighborhood,
                        "category": item.category,
                        "website": item.website,
                        "pipeline_state": item.pipeline_state,
                        "pipeline_state_label": item.get_pipeline_state_display(),
                        "primary_email": getattr(item, "primary_email", None),
                        # Only campaigns that predate fixed-message outreach carry a score.
                        "historical_score": getattr(item, "latest_score", None),
                        "campaign": {"id": str(item.campaign_id), "name": item.campaign.name},
                        "provenance": _provenance_data(item),
                        "created_at": item.created_at,
                    }
                    for item in rows
                ],
                "meta": meta,
            }
        )


class ProspectExportView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ExportDataPermission)

    def get(self, request: Request) -> HttpResponse:
        workspace = workspace_for_user(authenticated_user(request), Capability.EXPORT_DATA)
        # The same filters as the list view, so "export" downloads what the screen is showing.
        query = ProspectListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        prospects = prospect_queryset(request.query_params).filter(campaign__workspace=workspace)
        return csv_download(
            filename="prospectos.csv",
            headers=("campaña", "prospecto", "email", "rubro", "barrio", "estado"),
            rows=(
                (
                    item.campaign.name,
                    item.name,
                    next(
                        (email.normalized_email for email in item.emails.all() if email.is_primary),
                        "",
                    ),
                    item.category,
                    item.neighborhood,
                    item.get_pipeline_state_display(),
                )
                for item in prospects
            ),
        )
