from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.campaigns.forms import CampaignForm
from apps.campaigns.models import Campaign
from apps.campaigns.services import create_campaign, transition_campaign
from apps.campaigns.tasks import orchestrate_extraction
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import BusinessProfile
from apps.prospects.models import Prospect
from apps.prospects.pipeline import request_manual_regeneration
from apps.prospects.tasks import process_prospect_pipeline

CAMPAIGN_VALUE_FIELDS = (
    "name",
    "delivery_mode",
    "location_text",
    "objective",
    "max_raw_records",
    "cost_limit",
    "cost_currency",
    "daily_limit",
    "message_interval_minutes",
    "weekdays",
    "window_start",
    "window_end",
    "timezone_name",
    "relevance_threshold",
    "extractor_provider",
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "catalog",
)
INTEGRATION_SNAPSHOT_FIELDS = (
    "extractor_provider",
    "llm_provider",
    "llm_base_url",
    "llm_model",
)


@login_required
def campaign_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    campaigns = Campaign.objects.select_related("catalog", "created_by").filter(created_by=owner)
    if query := request.GET.get("q", "").strip():
        campaigns = campaigns.filter(Q(name__icontains=query) | Q(location_text__icontains=query))
    if state := request.GET.get("state", ""):
        campaigns = campaigns.filter(state=state)
    page = Paginator(campaigns, 25).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "campaigns/list.html",
        {
            "campaigns": page,
            "page_obj": page,
            "query_string": query_params.urlencode(),
            "states": Campaign.State.choices,
        },
    )


@login_required
def campaign_create(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    initial: dict[str, object] = {}
    integration_runtime = runtime_integration_configuration(owner.pk)
    initial.update(
        {
            "extractor_provider": integration_runtime.extractor_provider,
            "llm_provider": integration_runtime.llm_provider,
            "llm_model": integration_runtime.llm_model,
            "llm_base_url": integration_runtime.llm_base_url(),
        }
    )
    profile = BusinessProfile.objects.filter(owner=owner).first()
    if profile is not None:
        initial["relevance_threshold"] = profile.relevance_threshold
    form = CampaignForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
    )
    for field_name in INTEGRATION_SNAPSHOT_FIELDS:
        form.fields[field_name].disabled = True
        form.fields[
            field_name
        ].help_text = (
            "Se configura con reautenticación en Integraciones y se congela en la campaña."
        )
    if request.method == "POST":
        if form.is_valid():
            try:
                campaign = create_campaign(
                    actor=owner,
                    values={field: form.cleaned_data[field] for field in CAMPAIGN_VALUE_FIELDS},
                    category_ids=[category.pk for category in form.cleaned_data["categories"]],
                    zone_ids=[zone.pk for zone in form.cleaned_data["zones"]],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Campaña creada en borrador.")
                return redirect("campaign-detail", campaign_id=campaign.pk)
    return render(request, "campaigns/form.html", {"form": form})


@login_required
def campaign_detail(request: HttpRequest, campaign_id: str) -> HttpResponse:
    campaign = get_object_or_404(
        Campaign.objects.select_related("catalog", "created_by").prefetch_related(
            "category_selections",
            "zone_selections",
            "search_queries",
            "search_runs",
            "prospects__emails",
            "prospects__analyses",
            "prospects__outbound_messages",
            "prospects__web_snapshots",
        ),
        pk=campaign_id,
        created_by=request.user,
    )
    return render(request, "campaigns/detail.html", {"campaign": campaign})


@login_required
@require_POST
def campaign_action(request: HttpRequest, campaign_id: str, action: str) -> HttpResponse:
    targets = {
        "start": Campaign.State.RUNNING,
        "pause": Campaign.State.PAUSED,
        "resume": Campaign.State.RUNNING,
        "cancel": Campaign.State.CANCELLED,
    }
    if action not in targets:
        raise Http404
    owner = request.user
    assert isinstance(owner, User)
    try:
        campaign = transition_campaign(
            campaign_id=campaign_id,
            target_state=targets[action],
            actor=owner,
            reason=request.POST.get("reason", ""),
        )
    except (Campaign.DoesNotExist, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Campaña inexistente.",
        )
    else:
        messages.success(request, "Estado de campaña actualizado.")
        if targets[action] == Campaign.State.RUNNING:
            orchestrate_extraction.delay(str(campaign.pk))
    return redirect("campaign-detail", campaign_id=campaign_id)


@login_required
@require_POST
def regenerate_prospect_message(
    request: HttpRequest, campaign_id: str, prospect_id: str
) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    prospect = get_object_or_404(
        Prospect.objects.select_related("campaign"),
        pk=prospect_id,
        campaign_id=campaign_id,
        campaign__created_by=owner,
    )
    try:
        reservation = request_manual_regeneration(prospect_id=prospect.pk, actor=owner)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        process_prospect_pipeline.delay(
            str(prospect.pk),
            regeneration_nonce=reservation.regeneration_nonce,
            actor_id=owner.pk,
            reservation_token=reservation.token,
            analysis_generation=reservation.generation,
        )
        messages.success(
            request,
            "Regeneración solicitada. Esto no aprueba ni envía el mensaje.",
        )
    return redirect("campaign-detail", campaign_id=campaign_id)
