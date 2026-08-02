from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import (
    Capability,
    has_capability,
    require_capability,
    workspace_for_user,
)
from apps.audit.models import AuditEvent, BackgroundJob
from apps.automation.models import ReplyAutomationConfiguration
from apps.campaigns.models import Campaign, OutboundMessage, SearchRun
from apps.campaigns.review import (
    approve_message_for_delivery,
    can_approve_message,
    can_edit_message,
    edit_message_draft,
)
from apps.catalogs.models import Catalog
from apps.compliance.models import SuppressionEntry
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.mailbox.tasks import deliver_message_task
from apps.prospects.models import Prospect

from .csv_export import csv_download
from .forms import OutboundDraftForm
from .metrics import duration_label, workspace_summary_metrics
from .queries import (
    outbound_queryset,
    outbound_workspace_filter,
    parsed_uuid,
    prospect_queryset,
    workspace_campaigns,
)


def _page_context(
    request: HttpRequest, queryset: QuerySet[Any], *, per_page: int = 25
) -> dict[str, object]:
    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return {"page_obj": page, "query_string": query.urlencode()}


def _outbound_party_label(message: OutboundMessage) -> str:
    if message.prospect is not None:
        return message.prospect.name
    if message.contact is not None:
        return str(message.contact)
    if message.organization is not None:
        return message.organization.name or "Organización sin nombre"
    return message.recipient_normalized


@require_capability(Capability.VIEW_SUMMARY)
@require_GET
@never_cache
def dashboard(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.VIEW_SUMMARY)
    is_admin = has_capability(owner, Capability.MANAGE_CAMPAIGNS)
    providers: dict[str, str] = {}
    if is_admin:
        integration_runtime = runtime_integration_configuration(owner.pk)
        providers = {
            "Extractor": integration_runtime.extractor_provider,
            "Sitios web": integration_runtime.website_fetcher,
            "IA": integration_runtime.llm_provider,
            "Gmail": integration_runtime.gmail_provider,
        }
    campaign_scope = Campaign.objects.filter(workspace=workspace)
    if not is_admin:
        campaign_scope = campaign_scope.exclude(state=Campaign.State.DRAFT)
    selected_campaign = request.GET.get("campaign", "")
    selected_campaign_id = parsed_uuid(selected_campaign)
    if campaign_id := selected_campaign_id:
        campaign_scope = campaign_scope.filter(pk=campaign_id)
    prospect_scope = Prospect.objects.filter(campaign__in=campaign_scope)
    message_scope = OutboundMessage.objects.filter(
        campaign__in=campaign_scope,
        kind__in=(OutboundMessage.Kind.FIRST_CONTACT, OutboundMessage.Kind.INITIAL),
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
        "review_ready": message_scope.filter(state=OutboundMessage.State.REVIEW_READY).count(),
        "sent": message_scope.filter(state=OutboundMessage.State.SENT).count(),
        "simulated": message_scope.filter(state=OutboundMessage.State.DRY_RUN_COMPLETED).count(),
        "failed": message_scope.filter(state=OutboundMessage.State.SEND_FAILED).count()
        + prospect_scope.filter(pipeline_state=Prospect.PipelineState.ERROR).count(),
        "responded": response_scope.filter(is_human=True).count(),
        "interested": response_scope.filter(
            classification=InboundMessage.Classification.INTERESTED
        ).count(),
    }
    profile_configured = BusinessProfile.objects.filter(workspace=workspace).exists()
    active_catalogs = Catalog.objects.filter(
        workspace=workspace, active=True, missing=False
    ).count()
    active_categories = SearchCategory.objects.filter(
        workspace=workspace, active=True, archived_at__isnull=True
    ).count()
    active_zones = SearchZone.objects.filter(
        workspace=workspace, active=True, archived_at__isnull=True
    ).count()
    owner_campaign_count = Campaign.objects.filter(workspace=workspace).count()
    campaign_started = (
        Campaign.objects.filter(workspace=workspace).exclude(state=Campaign.State.DRAFT).exists()
    )
    gmail_connection = GmailConnection.objects.filter(workspace=workspace).first()
    automation_configuration = (
        ReplyAutomationConfiguration.objects.filter(workspace=workspace).first()
        if is_admin
        else None
    )
    summary = {
        "campaigns": owner_campaign_count,
        "catalogs": active_catalogs,
        "categories": active_categories,
        "zones": active_zones,
        "suppressions": SuppressionEntry.objects.count(),
        "responses": InboundMessage.objects.filter(connection__workspace=workspace).count(),
    }
    informative_metrics = workspace_summary_metrics(
        workspace,
        campaign_id=selected_campaign_id,
    )
    informative_metric_display = {
        "response_rate": (
            f"{informative_metrics.response_rate * 100:.1f}%"
            if informative_metrics.response_rate is not None
            else "—"
        ),
        "positive_response_rate": (
            f"{informative_metrics.positive_response_rate * 100:.1f}%"
            if informative_metrics.positive_response_rate is not None
            else "—"
        ),
        "bounce_rate": (
            f"{informative_metrics.bounce_rate * 100:.1f}%"
            if informative_metrics.bounce_rate is not None
            else "—"
        ),
        "unsubscribe_rate": (
            f"{informative_metrics.unsubscribe_rate * 100:.1f}%"
            if informative_metrics.unsubscribe_rate is not None
            else "—"
        ),
        "median_first_response": duration_label(informative_metrics.median_first_response),
        "median_human_intervention": duration_label(informative_metrics.median_human_intervention),
    }
    return render(
        request,
        "dashboard/index.html",
        {
            "providers": providers,
            "summary": summary,
            "metrics": metrics,
            "informative_metrics": informative_metrics,
            "informative_metric_display": informative_metric_display,
            "campaigns": workspace_campaigns(workspace.pk, include_drafts=is_admin),
            "selected_campaign": selected_campaign,
            "jobs": dict(
                BackgroundJob.objects.values_list("state")
                .annotate(total=Count("id"))
                .values_list("state", "total")
            )
            if is_admin
            else {},
            "profile_configured": profile_configured,
            "gmail_connection": gmail_connection,
            "gmail_ready": bool(gmail_connection and gmail_connection.is_ready),
            "automation_configuration": automation_configuration,
            "checklist": (
                (
                    "Perfil comercial",
                    profile_configured,
                    "business-profile",
                    "Completá identidad, firma y datos del vendedor.",
                ),
                (
                    "Rubros activos",
                    active_categories > 0,
                    "categories",
                    "Revisá qué actividades forman la audiencia.",
                ),
                (
                    "Zonas activas",
                    active_zones > 0,
                    "zones",
                    "Confirmá las áreas geográficas y sus límites.",
                ),
                (
                    "Catálogo vigente",
                    active_catalogs > 0,
                    "catalogs",
                    "Cargá el PDF que acompañará los mensajes.",
                ),
                (
                    "Primera campaña",
                    owner_campaign_count > 0,
                    "campaign-create",
                    "Definí audiencia, objetivo y límites.",
                ),
                (
                    "Campaña iniciada",
                    campaign_started,
                    "campaigns",
                    "Iniciá un borrador cuando la configuración esté lista.",
                ),
            ),
            "recent_campaigns": campaign_scope.select_related("catalog")[:5],
            "attention_campaigns": Campaign.objects.filter(
                workspace=workspace,
                state__in=(Campaign.State.PAUSED, Campaign.State.STOPPED_ERROR),
            )[:5]
            if is_admin
            else (),
            "problem_jobs": BackgroundJob.objects.filter(
                state__in=(BackgroundJob.State.FAILED, BackgroundJob.State.RETRY_WAIT)
            )[:5]
            if is_admin
            else (),
            "recent_activity": AuditEvent.objects.select_related("actor")[:6] if is_admin else (),
            "recent_responses": InboundMessage.objects.select_related(
                "organization",
                "contact__organization",
                "related_outbound__campaign",
                "related_outbound__organization",
                "related_outbound__prospect",
            ).filter(connection__workspace=workspace)[:5],
            "is_admin": is_admin,
        },
    )


@require_capability(Capability.MANAGE_CAMPAIGNS)
@require_GET
@never_cache
def prospect_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_CAMPAIGNS)
    queryset = prospect_queryset(request.GET).filter(campaign__workspace=workspace)
    context = _page_context(request, queryset)
    context.update(
        {
            "campaigns": workspace_campaigns(workspace.pk),
            "states": Prospect.PipelineState.choices,
        }
    )
    return render(request, "dashboard/prospects.html", context)


@require_capability(Capability.EXPORT_DATA)
@require_GET
@never_cache
def prospect_export(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.EXPORT_DATA)
    rows = prospect_queryset(request.GET).filter(campaign__workspace=workspace)
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


@require_capability(Capability.VIEW_SENT_MESSAGES)
@require_GET
@never_cache
def outbound_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.VIEW_SENT_MESSAGES)
    is_admin = has_capability(owner, Capability.MANAGE_CAMPAIGNS)
    queryset = (
        outbound_queryset(request.GET).filter(outbound_workspace_filter(workspace.pk)).distinct()
    )
    if not is_admin:
        queryset = queryset.filter(state=OutboundMessage.State.SENT)
    context = _page_context(request, queryset)
    context.update(
        {
            "campaigns": workspace_campaigns(workspace.pk, include_drafts=is_admin),
            "states": OutboundMessage.State.choices if is_admin else (),
            "kinds": OutboundMessage.Kind.choices,
            "is_admin": is_admin,
        }
    )
    return render(request, "dashboard/outbound.html", context)


@require_capability(Capability.VIEW_SENT_MESSAGES)
@require_GET
@never_cache
def outbound_detail(request: HttpRequest, message_id: str) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.VIEW_SENT_MESSAGES)
    is_admin = has_capability(owner, Capability.MANAGE_CAMPAIGNS)
    visibility: dict[str, object] = {}
    if not is_admin:
        visibility["state"] = OutboundMessage.State.SENT
    message = get_object_or_404(
        OutboundMessage.objects.filter(outbound_workspace_filter(workspace.pk))
        .select_related(
            "campaign",
            "campaign__overture_snapshot",
            "prospect",
            "prospect_email",
            "organization",
            "contact",
            "email_address",
            "catalog",
            "analysis",
            "approved_by",
            "last_edited_by",
        )
        .prefetch_related("prospect__web_snapshots", "attachments__catalog")
        .distinct(),
        pk=message_id,
        **visibility,
    )
    return render(
        request,
        "dashboard/outbound_detail.html",
        {
            "message": message,
            "edit_form": OutboundDraftForm(
                initial={"subject": message.subject, "body_text": message.body_text}
            ),
            "can_edit": is_admin and can_edit_message(message),
            "can_approve": is_admin and can_approve_message(message),
            "show_technical_details": is_admin,
        },
    )


@require_capability(Capability.MANAGE_CAMPAIGNS)
@require_POST
@never_cache
def outbound_edit(request: HttpRequest, message_id: str) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    message = get_object_or_404(
        OutboundMessage.objects.select_related("campaign", "catalog", "prospect_email"),
        pk=message_id,
        campaign__workspace=workspace_for_user(owner, Capability.MANAGE_CAMPAIGNS),
    )
    form = OutboundDraftForm(request.POST)
    if form.is_valid():
        try:
            edit_message_draft(
                message.pk,
                actor=owner,
                subject=form.cleaned_data["subject"],
                body_text=form.cleaned_data["body_text"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "El borrador fue actualizado y continúa sin enviar.")
            return redirect("outbound-detail", message_id=message.pk)
    message.refresh_from_db()
    return render(
        request,
        "dashboard/outbound_detail.html",
        {
            "message": message,
            "edit_form": form,
            "can_edit": can_edit_message(message),
            "can_approve": can_approve_message(message),
        },
        status=400,
    )


@require_capability(Capability.APPROVE_CAMPAIGNS)
@require_POST
def outbound_approve(request: HttpRequest, message_id: str) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    message = get_object_or_404(
        OutboundMessage.objects.select_related("campaign"),
        pk=message_id,
        campaign__workspace=workspace_for_user(owner, Capability.APPROVE_CAMPAIGNS),
    )
    try:
        approved = approve_message_for_delivery(message.pk, actor=owner)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        campaign = approved.campaign
        if campaign is not None and campaign.state == Campaign.State.RUNNING:
            deliver_message_task.delay(str(approved.pk))
            messages.success(request, "Correo aprobado y encolado para envío.")
        else:
            messages.success(
                request,
                "Correo aprobado. Se enviará cuando la campaña vuelva a estar en curso.",
            )
    return redirect("outbound-detail", message_id=message.pk)


@require_capability(Capability.EXPORT_DATA)
@require_GET
@never_cache
def outbound_export(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.EXPORT_DATA)
    rows = outbound_queryset(request.GET).filter(outbound_workspace_filter(workspace.pk)).distinct()
    return csv_download(
        filename="envios.csv",
        headers=("campaña", "prospecto", "destinatario", "asunto", "tipo", "estado", "fecha"),
        rows=(
            (
                item.campaign.name if item.campaign is not None else "Sin campaña",
                _outbound_party_label(item),
                item.recipient_normalized,
                item.subject,
                item.get_kind_display(),
                item.get_state_display(),
                item.sent_at or item.simulated_at or item.created_at,
            )
            for item in rows
        ),
    )
