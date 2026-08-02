from __future__ import annotations

import uuid
from datetime import date

from django.db import models
from django.db.models import OuterRef, Prefetch, QuerySet, Subquery
from django.http import QueryDict

from apps.campaigns.models import Campaign, OutboundMessage
from apps.mailbox.models import InboundMessage
from apps.prospects.models import AIAnalysis, Prospect, ProspectEmail, WebsiteSnapshot


def parsed_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parsed_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def prospect_queryset(params: QueryDict) -> QuerySet[Prospect]:
    latest_analysis = AIAnalysis.objects.filter(
        prospect=OuterRef("pk"), status=AIAnalysis.Status.VALID
    ).order_by("-analyzed_at")
    primary_email = ProspectEmail.objects.filter(prospect=OuterRef("pk"), is_primary=True).order_by(
        "provider_order", "created_at"
    )
    queryset = (
        Prospect.objects.select_related("campaign", "campaign__overture_snapshot")
        .prefetch_related(
            Prefetch("emails", queryset=ProspectEmail.objects.order_by("provider_order")),
            Prefetch(
                "web_snapshots",
                queryset=WebsiteSnapshot.objects.order_by("-fetched_at"),
            ),
        )
        .annotate(
            latest_score=Subquery(latest_analysis.values("relevance_score")[:1]),
            latest_reason=Subquery(latest_analysis.values("relevance_reason")[:1]),
            primary_email=Subquery(primary_email.values("normalized_email")[:1]),
        )
    )
    query = params.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            models.Q(name__icontains=query)
            | models.Q(address__icontains=query)
            | models.Q(emails__normalized_email__icontains=query)
        ).distinct()
    if campaign := parsed_uuid(params.get("campaign", "")):
        queryset = queryset.filter(campaign_id=campaign)
    if state := params.get("state", ""):
        queryset = queryset.filter(pipeline_state=state)
    if category := params.get("category", "").strip():
        queryset = queryset.filter(category__icontains=category)
    if neighborhood := params.get("neighborhood", "").strip():
        queryset = queryset.filter(neighborhood__icontains=neighborhood)
    if minimum := params.get("min_score", ""):
        try:
            queryset = queryset.filter(latest_score__gte=int(minimum))
        except ValueError:
            pass
    if maximum := params.get("max_score", ""):
        try:
            queryset = queryset.filter(latest_score__lte=int(maximum))
        except ValueError:
            pass
    if start := parsed_date(params.get("date_from", "")):
        queryset = queryset.filter(created_at__date__gte=start)
    if end := parsed_date(params.get("date_to", "")):
        queryset = queryset.filter(created_at__date__lte=end)
    return queryset.order_by("-created_at")


def outbound_queryset(params: QueryDict) -> QuerySet[OutboundMessage]:
    queryset = OutboundMessage.objects.select_related(
        "campaign",
        "prospect",
        "prospect_email",
        "organization",
        "contact",
        "email_address",
    )
    query = params.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            models.Q(prospect__name__icontains=query)
            | models.Q(recipient_normalized__icontains=query)
            | models.Q(subject__icontains=query)
        )
    if campaign := parsed_uuid(params.get("campaign", "")):
        queryset = queryset.filter(campaign_id=campaign)
    if state := params.get("state", ""):
        queryset = queryset.filter(state=state)
    if kind := params.get("kind", ""):
        queryset = queryset.filter(kind=kind)
    if start := parsed_date(params.get("date_from", "")):
        queryset = queryset.filter(created_at__date__gte=start)
    if end := parsed_date(params.get("date_to", "")):
        queryset = queryset.filter(created_at__date__lte=end)
    return queryset.order_by("-created_at")


def outbound_workspace_filter(workspace_id: uuid.UUID | str) -> models.Q:
    """Resolve ownership without relying on nullable campaign attribution."""

    return (
        models.Q(campaign__workspace_id=workspace_id)
        | models.Q(campaign_enrollment__workspace_id=workspace_id)
        | models.Q(organization__workspace_id=workspace_id)
        | models.Q(contact__workspace_id=workspace_id)
        | models.Q(conversation__contact__workspace_id=workspace_id)
        | models.Q(email_address__workspace_id=workspace_id)
        | models.Q(parent_inbound__connection__workspace_id=workspace_id)
    )


def response_queryset(
    params: QueryDict,
    *,
    workspace_id: uuid.UUID | str,
) -> QuerySet[InboundMessage]:
    queryset = InboundMessage.objects.filter(connection__workspace_id=workspace_id).select_related(
        "related_outbound__campaign",
        "related_outbound__prospect",
        "related_outbound__organization",
        "related_outbound__contact",
        "organization",
        "contact",
    )
    query = params.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            models.Q(sender__icontains=query)
            | models.Q(subject__icontains=query)
            | models.Q(body_text__icontains=query)
            | models.Q(related_outbound__prospect__name__icontains=query)
            | models.Q(related_outbound__organization__name__icontains=query)
            | models.Q(related_outbound__contact__name__icontains=query)
        )
    if campaign := parsed_uuid(params.get("campaign", "")):
        queryset = queryset.filter(related_outbound__campaign_id=campaign)
    if classification := params.get("classification", ""):
        queryset = queryset.filter(classification=classification)
    interest = params.get("interest", "")
    if interest == "human":
        queryset = queryset.filter(is_human=True)
    elif interest == "interested":
        queryset = queryset.filter(classification=InboundMessage.Classification.INTERESTED)
    if start := parsed_date(params.get("date_from", "")):
        queryset = queryset.filter(external_at__date__gte=start)
    if end := parsed_date(params.get("date_to", "")):
        queryset = queryset.filter(external_at__date__lte=end)
    return queryset.order_by("-external_at")


def workspace_campaigns(
    workspace_id: uuid.UUID | str,
    *,
    include_drafts: bool = True,
) -> QuerySet[Campaign]:
    queryset = Campaign.objects.filter(workspace_id=workspace_id)
    if not include_drafts:
        queryset = queryset.exclude(state=Campaign.State.DRAFT)
    return queryset.only("id", "name")


def owner_campaigns(owner_id: int) -> QuerySet[Campaign]:
    """Compatibility shim: creator is resolved to a Workspace, never used as scope."""

    from apps.accounts.models import Membership

    workspace_id = (
        Membership.objects.filter(user_id=owner_id).values_list("workspace_id", flat=True).first()
    )
    return (
        workspace_campaigns(workspace_id) if workspace_id is not None else Campaign.objects.none()
    )
