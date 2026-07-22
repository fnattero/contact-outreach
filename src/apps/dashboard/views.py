from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.audit.models import BackgroundJob
from apps.campaigns.models import Campaign, OutboundMessage, SearchRun
from apps.catalogs.models import Catalog
from apps.compliance.models import SuppressionEntry
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone
from apps.mailbox.models import InboundMessage
from apps.prospects.models import Prospect

from .csv_export import csv_download
from .queries import outbound_queryset, owner_campaigns, parsed_uuid, prospect_queryset


def _page_context(
    request: HttpRequest, queryset: QuerySet[Any], *, per_page: int = 25
) -> dict[str, object]:
    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return {"page_obj": page, "query_string": query.urlencode()}


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    integration_runtime = runtime_integration_configuration(owner.pk)
    providers = {
        "Extractor": integration_runtime.extractor_provider,
        "Sitios web": settings.WEBSITE_FETCHER,
        "IA": integration_runtime.llm_provider,
        "Gmail": integration_runtime.gmail_provider,
    }
    campaign_scope = Campaign.objects.filter(created_by=owner)
    selected_campaign = request.GET.get("campaign", "")
    if campaign_id := parsed_uuid(selected_campaign):
        campaign_scope = campaign_scope.filter(pk=campaign_id)
    prospect_scope = Prospect.objects.filter(campaign__in=campaign_scope)
    message_scope = OutboundMessage.objects.filter(
        campaign__in=campaign_scope,
        kind=OutboundMessage.Kind.FIRST_CONTACT,
    )
    response_scope = InboundMessage.objects.filter(related_outbound__campaign__in=campaign_scope)
    raw_total = SearchRun.objects.filter(campaign__in=campaign_scope).aggregate(
        value=Sum("raw_count")
    )["value"]
    metrics = {
        "raw": raw_total or 0,
        "with_email": prospect_scope.exclude(
            pipeline_state__in=(
                Prospect.PipelineState.DISCOVERED,
                Prospect.PipelineState.SKIPPED_NO_EMAIL,
            )
        ).count(),
        "duplicates": SearchRun.objects.filter(campaign__in=campaign_scope).aggregate(
            value=Sum("duplicate_count")
        )["value"]
        or 0,
        "irrelevant": prospect_scope.filter(
            pipeline_state=Prospect.PipelineState.SKIPPED_IRRELEVANT
        ).count(),
        "qualified": prospect_scope.filter(pipeline_state=Prospect.PipelineState.QUEUED).count(),
        "queued": message_scope.filter(
            state__in=(
                OutboundMessage.State.PREPARED,
                OutboundMessage.State.QUEUED,
                OutboundMessage.State.SENDING,
                OutboundMessage.State.RECONCILING,
            )
        ).count(),
        "sent": message_scope.filter(state=OutboundMessage.State.SENT).count(),
        "simulated": message_scope.filter(state=OutboundMessage.State.DRY_RUN_COMPLETED).count(),
        "failed": message_scope.filter(state=OutboundMessage.State.SEND_FAILED).count()
        + prospect_scope.filter(pipeline_state=Prospect.PipelineState.ERROR).count(),
        "responded": response_scope.filter(is_human=True).count(),
        "interested": response_scope.filter(
            classification=InboundMessage.Classification.INTERESTED
        ).count(),
    }
    summary = {
        "campaigns": Campaign.objects.count(),
        "catalogs": Catalog.objects.filter(active=True, missing=False).count(),
        "categories": SearchCategory.objects.filter(active=True, archived_at__isnull=True).count(),
        "zones": SearchZone.objects.filter(active=True, archived_at__isnull=True).count(),
        "suppressions": SuppressionEntry.objects.count(),
        "responses": InboundMessage.objects.count(),
    }
    return render(
        request,
        "dashboard/index.html",
        {
            "providers": providers,
            "summary": summary,
            "metrics": metrics,
            "campaigns": owner_campaigns(owner.pk),
            "selected_campaign": selected_campaign,
            "jobs": dict(
                BackgroundJob.objects.values_list("state")
                .annotate(total=Count("id"))
                .values_list("state", "total")
            ),
            "profile_configured": BusinessProfile.objects.filter(owner=owner).exists(),
            "recent_campaigns": Campaign.objects.select_related("catalog")[:5],
            "recent_responses": InboundMessage.objects.select_related(
                "related_outbound__campaign",
                "related_outbound__prospect",
            )[:5],
        },
    )


@login_required
def prospect_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    queryset = prospect_queryset(request.GET).filter(campaign__created_by=owner)
    context = _page_context(request, queryset)
    context.update(
        {
            "campaigns": owner_campaigns(owner.pk),
            "states": Prospect.PipelineState.choices,
        }
    )
    return render(request, "dashboard/prospects.html", context)


@login_required
def prospect_export(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    rows = prospect_queryset(request.GET).filter(campaign__created_by=owner)
    return csv_download(
        filename="prospectos.csv",
        headers=("campaña", "prospecto", "email", "rubro", "barrio", "estado", "relevancia"),
        rows=(
            (
                item.campaign.name,
                item.name,
                getattr(item, "primary_email", "") or "",
                item.category,
                item.neighborhood,
                item.get_pipeline_state_display(),
                getattr(item, "latest_score", None),
            )
            for item in rows
        ),
    )


@login_required
def outbound_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    queryset = outbound_queryset(request.GET).filter(campaign__created_by=owner)
    context = _page_context(request, queryset)
    context.update(
        {
            "campaigns": owner_campaigns(owner.pk),
            "states": OutboundMessage.State.choices,
            "kinds": OutboundMessage.Kind.choices,
        }
    )
    return render(request, "dashboard/outbound.html", context)


@login_required
def outbound_export(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    rows = outbound_queryset(request.GET).filter(campaign__created_by=owner)
    return csv_download(
        filename="envios.csv",
        headers=("campaña", "prospecto", "destinatario", "asunto", "tipo", "estado", "fecha"),
        rows=(
            (
                item.campaign.name,
                item.prospect.name,
                item.recipient_normalized,
                item.subject,
                item.get_kind_display(),
                item.get_state_display(),
                item.sent_at or item.simulated_at or item.created_at,
            )
            for item in rows
        ),
    )
