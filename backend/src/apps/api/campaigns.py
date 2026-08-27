from __future__ import annotations

import uuid
from datetime import timedelta
from hashlib import sha256
from typing import Any, cast

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
from apps.api.permissions import (
    ExportDataPermission,
    ManageCampaignsPermission,
    ViewCampaignsPermission,
    authenticated_user,
)
from apps.api.schema import SchemaAPIView
from apps.audit.models import ApiIdempotencyRecord
from apps.campaigns.approval import approve_campaign, start_per_message_campaign
from apps.campaigns.forms import CampaignForm
from apps.campaigns.models import Campaign, OutboundMessage
from apps.campaigns.services import create_campaign, transition_campaign
from apps.campaigns.tasks import orchestrate_extraction
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import BusinessProfile
from apps.contacts.models import CampaignEnrollment
from apps.dashboard.csv_export import csv_download
from apps.prospects.models import Prospect


class CampaignListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    q = serializers.CharField(required=False, allow_blank=True, max_length=150)
    state = serializers.ChoiceField(required=False, choices=Campaign.State.choices)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)


def _initial_values(owner_id: int, workspace_id: uuid.UUID | str) -> dict[str, object]:
    runtime = runtime_integration_configuration(owner_id)
    initial: dict[str, object] = {
        "extractor_provider": runtime.extractor_provider,
        "overture_min_confidence": runtime.overture_min_confidence,
        "website_fetcher": runtime.website_fetcher,
        "llm_provider": runtime.llm_provider,
        "llm_model": runtime.llm_model,
        "llm_base_url": runtime.llm_base_url(),
    }
    profile = BusinessProfile.objects.filter(workspace_id=workspace_id).first()
    if profile is not None:
        initial["relevance_threshold"] = profile.relevance_threshold
    return initial


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
            "relevance_threshold": campaign.relevance_threshold,
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
        form = CampaignForm(
            data=request.data,
            initial=_initial_values(user.pk, workspace.pk),
            workspace=workspace,
        )
        for field_name in (
            "extractor_provider",
            "website_fetcher",
            "llm_provider",
            "llm_model",
            "llm_base_url",
        ):
            form.fields[field_name].disabled = True
        if not form.is_valid():
            raise serializers.ValidationError(form.errors.get_json_data())
        cleaned = form.cleaned_data
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
                "relevance_threshold",
                "extractor_provider",
                "website_fetcher",
                "llm_provider",
                "llm_base_url",
                "llm_model",
                "catalog",
            )
        }
        try:
            campaign = create_campaign(
                actor=user,
                values=values,
                category_ids=[item.pk for item in cleaned["categories"]],
                zone_ids=[item.pk for item in cleaned["zones"]],
                catalog_ids=[item.pk for item in cleaned["catalogs"]],
            )
        except ValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
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


class CampaignActionView(SchemaAPIView):
    permission_classes = (IsAuthenticated,)

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
                elif action == "start-approved":
                    campaign = start_per_message_campaign(campaign_id, actor=locked_user)
                else:
                    targets = {
                        "start-discovery": Campaign.State.DISCOVERING,
                        "pause": Campaign.State.PAUSED,
                        "resume": Campaign.State.RUNNING,
                        "cancel": Campaign.State.CANCELLED,
                    }
                    campaign = transition_campaign(
                        campaign_id=campaign_id,
                        target_state=targets[action],
                        actor=locked_user,
                        reason=str(cast(dict[str, Any], request.data).get("reason", "")),
                    )
                    if action == "start-discovery":
                        transaction.on_commit(
                            lambda: orchestrate_extraction.delay(str(campaign.pk))
                        )
            except (Campaign.DoesNotExist, ValidationError) as exc:
                raise serializers.ValidationError(str(exc)) from exc
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


class ProspectExportView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ExportDataPermission)

    def get(self, request: Request) -> HttpResponse:
        workspace = workspace_for_user(authenticated_user(request), Capability.EXPORT_DATA)
        prospects = (
            Prospect.objects.filter(campaign__workspace=workspace)
            .select_related("campaign")
            .prefetch_related("emails")
        )
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
