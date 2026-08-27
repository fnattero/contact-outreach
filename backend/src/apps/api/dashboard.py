from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import Capability, has_capability, workspace_for_user
from apps.api.permissions import ViewSummaryPermission, authenticated_user
from apps.api.schema import SchemaAPIView
from apps.audit.models import BackgroundJob
from apps.automation.models import HumanTask
from apps.campaigns.models import Campaign, OutboundMessage
from apps.catalogs.models import Catalog
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone
from apps.dashboard.metrics import SummaryMetrics, workspace_summary_metrics
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.prospects.models import Prospect


class DashboardQuerySerializer(serializers.Serializer[dict[str, Any]]):
    campaign_id = serializers.UUIDField(required=False)


def _duration_seconds(value: timedelta | None) -> float | None:
    return value.total_seconds() if value is not None else None


def _metrics_data(metrics: SummaryMetrics) -> dict[str, object]:
    return {
        "unique_initial_recipients": metrics.unique_initial_recipients,
        "initial_messages_sent": metrics.initial_messages_sent,
        "reminders_sent": metrics.reminders_sent,
        "automatic_replies_sent": metrics.automatic_replies_sent,
        "scheduled_contacts_sent": metrics.scheduled_contacts_sent,
        "unique_human_responders": metrics.unique_human_responders,
        "response_rate": metrics.response_rate,
        "positive_response_rate": metrics.positive_response_rate,
        "contacts_created": metrics.contacts_created,
        "bounce_rate": metrics.bounce_rate,
        "unsubscribe_rate": metrics.unsubscribe_rate,
        "automatically_resolved": metrics.automatically_resolved,
        "human_required": metrics.human_required,
        "open_human_tasks": metrics.open_human_tasks,
        "median_first_response_seconds": _duration_seconds(metrics.median_first_response),
        "median_human_intervention_seconds": _duration_seconds(metrics.median_human_intervention),
        "responses_after_initial": metrics.responses_after_initial,
        "responses_after_reminder": metrics.responses_after_reminder,
    }


def _campaign_data(campaign: Campaign) -> dict[str, object]:
    return {
        "id": str(campaign.pk),
        "name": campaign.name,
        "state": campaign.state,
        "state_label": campaign.get_state_display(),
        "discovery_state": campaign.discovery_state,
        "discovery_state_label": campaign.get_discovery_state_display(),
        "delivery_mode": campaign.delivery_mode,
    }


class DashboardSummaryView(SchemaAPIView):
    permission_classes = (IsAuthenticated, ViewSummaryPermission)

    def get(self, request: Request) -> Response:
        query = DashboardQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        user = authenticated_user(request)
        workspace = workspace_for_user(user, Capability.VIEW_SUMMARY)
        campaign_id = cast(UUID | None, query.validated_data.get("campaign_id"))
        campaigns = Campaign.objects.filter(workspace=workspace)
        if campaign_id is not None:
            campaigns = campaigns.filter(pk=campaign_id)
            if not campaigns.exists():
                raise serializers.ValidationError({"campaign_id": "La campaña no existe."})

        metrics = workspace_summary_metrics(workspace, campaign_id=campaign_id)
        campaign_list = Campaign.objects.filter(workspace=workspace).only(
            "id", "name", "state", "discovery_state", "delivery_mode"
        )
        if not has_capability(user, Capability.MANAGE_CAMPAIGNS):
            campaign_list = campaign_list.exclude(state=Campaign.State.DRAFT)

        is_admin = has_capability(user, Capability.MANAGE_USERS)
        data: dict[str, object] = {
            "metrics": _metrics_data(metrics),
            "campaigns": [_campaign_data(item) for item in campaign_list],
            "summary": {
                "campaigns": campaigns.count()
                if campaign_id is not None
                else Campaign.objects.filter(workspace=workspace).count(),
                "catalogs": Catalog.objects.filter(
                    workspace=workspace, active=True, missing=False
                ).count(),
                "categories": SearchCategory.objects.filter(
                    workspace=workspace, active=True, archived_at__isnull=True
                ).count(),
                "zones": SearchZone.objects.filter(
                    workspace=workspace, active=True, archived_at__isnull=True
                ).count(),
                "responses": InboundMessage.objects.filter(connection__workspace=workspace).count(),
            },
            "attention": {
                "open_human_tasks": HumanTask.objects.filter(
                    workspace=workspace, status=HumanTask.Status.OPEN
                ).count(),
                "paused_campaigns": Campaign.objects.filter(
                    workspace=workspace,
                    state__in=(Campaign.State.PAUSED, Campaign.State.STOPPED_ERROR),
                ).count(),
            },
            "safety": {
                "send_mode": settings.SEND_MODE,
                "send_kill_switch": settings.SEND_KILL_SWITCH,
                "auto_reply_kill_switch": settings.AUTO_REPLY_KILL_SWITCH,
                "relationship_kill_switch": settings.RELATIONSHIP_KILL_SWITCH,
            },
        }
        if is_admin:
            data["admin"] = {
                "profile_configured": BusinessProfile.objects.filter(workspace=workspace).exists(),
                "gmail_connected": GmailConnection.objects.filter(workspace=workspace).exists(),
                "problem_jobs": BackgroundJob.objects.filter(
                    state__in=(BackgroundJob.State.FAILED, BackgroundJob.State.RETRY_WAIT)
                ).count(),
                "prospects": Prospect.objects.filter(campaign__workspace=workspace).count(),
                "sent_messages": OutboundMessage.objects.filter(
                    Q(campaign__workspace=workspace)
                    | Q(contact__workspace=workspace)
                    | Q(organization__workspace=workspace),
                    state=OutboundMessage.State.SENT,
                )
                .distinct()
                .count(),
            }
        return Response({"data": data})
