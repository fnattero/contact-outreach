from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import (
    Capability,
    has_capability,
    require_capability,
    workspace_for_user,
)
from apps.campaigns.approval import approve_campaign, start_per_message_campaign
from apps.campaigns.forms import CampaignForm
from apps.campaigns.models import Campaign, OutboundMessage
from apps.campaigns.services import create_campaign, transition_campaign
from apps.campaigns.tasks import orchestrate_extraction
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import SearchZone
from apps.contacts.models import CampaignEnrollment
from apps.mailbox.tasks import deliver_message_task
from apps.prospects.models import Prospect
from apps.prospects.pipeline import (
    request_manual_regeneration,
    request_outdated_analysis_regenerations,
)
from apps.prospects.tasks import process_prospect_pipeline

CAMPAIGN_VALUE_FIELDS = (
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
    "extractor_provider",
    "website_fetcher",
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "catalog",
)
INTEGRATION_SNAPSHOT_FIELDS = (
    "extractor_provider",
    "website_fetcher",
    "llm_provider",
    "llm_base_url",
    "llm_model",
)
MAP_WIDTH = 1000
MAP_HEIGHT = 560
MAP_PADDING = 24


def _numeric_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    numbers = []
    for item in value:
        if not isinstance(item, int | float):
            return None
        numbers.append(float(item))
    return numbers[0], numbers[1], numbers[2], numbers[3]


def _geometry_rings(geometry: object) -> Iterable[list[Any]]:
    if not isinstance(geometry, dict):
        return ()
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, list):
        return (ring for ring in coordinates if isinstance(ring, list))
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        return (
            ring
            for polygon in coordinates
            if isinstance(polygon, list)
            for ring in polygon
            if isinstance(ring, list)
        )
    return ()


def _svg_path_for_geometry(
    geometry: object,
    project: Callable[[float, float], tuple[float, float]],
) -> str:
    commands: list[str] = []
    for ring in _geometry_rings(geometry):
        first_point = True
        for coordinate in ring:
            if not isinstance(coordinate, list) or len(coordinate) < 2:
                continue
            lon, lat = coordinate[0], coordinate[1]
            if not isinstance(lon, int | float) or not isinstance(lat, int | float):
                continue
            x, y = project(float(lon), float(lat))
            commands.append(f"{'M' if first_point else 'L'}{x:.2f} {y:.2f}")
            first_point = False
        if not first_point:
            commands.append("Z")
    return " ".join(commands)


def _zone_map_payload(zones: Iterable[SearchZone]) -> dict[str, object]:
    drawable_zones = [
        (zone, bbox)
        for zone in zones
        if (bbox := _numeric_bbox(zone.boundary_bbox)) is not None and zone.boundary_geojson
    ]
    if not drawable_zones:
        return {"viewBox": f"0 0 {MAP_WIDTH} {MAP_HEIGHT}", "zones": []}
    min_x = min(bbox[0] for _, bbox in drawable_zones)
    min_y = min(bbox[1] for _, bbox in drawable_zones)
    max_x = max(bbox[2] for _, bbox in drawable_zones)
    max_y = max(bbox[3] for _, bbox in drawable_zones)
    x_range = max(max_x - min_x, 0.000001)
    y_range = max(max_y - min_y, 0.000001)
    scale = min(
        (MAP_WIDTH - MAP_PADDING * 2) / x_range,
        (MAP_HEIGHT - MAP_PADDING * 2) / y_range,
    )
    offset_x = (MAP_WIDTH - x_range * scale) / 2
    offset_y = (MAP_HEIGHT - y_range * scale) / 2

    def project(longitude: float, latitude: float) -> tuple[float, float]:
        return (
            offset_x + (longitude - min_x) * scale,
            offset_y + (max_y - latitude) * scale,
        )

    map_zones = []
    for zone, _bbox in drawable_zones:
        path = _svg_path_for_geometry(zone.boundary_geojson, project)
        if not path:
            continue
        map_zones.append({"id": str(zone.pk), "name": zone.name, "path": path})
    return {"viewBox": f"0 0 {MAP_WIDTH} {MAP_HEIGHT}", "zones": map_zones}


@require_capability(Capability.VIEW_CAMPAIGNS)
@require_GET
@never_cache
def campaign_list(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.VIEW_CAMPAIGNS)
    is_admin = has_capability(owner, Capability.MANAGE_CAMPAIGNS)
    campaigns = Campaign.objects.select_related("catalog", "created_by").filter(workspace=workspace)
    if not is_admin:
        campaigns = campaigns.exclude(state=Campaign.State.DRAFT)
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
            "states": Campaign.State.choices
            if is_admin
            else tuple(
                choice for choice in Campaign.State.choices if choice[0] != Campaign.State.DRAFT
            ),
            "is_admin": is_admin,
        },
    )


@require_capability(Capability.MANAGE_CAMPAIGNS)
def campaign_create(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_CAMPAIGNS)
    initial: dict[str, object] = {}
    integration_runtime = runtime_integration_configuration(owner.pk)
    initial.update(
        {
            "extractor_provider": integration_runtime.extractor_provider,
            "overture_min_confidence": integration_runtime.overture_min_confidence,
            "website_fetcher": integration_runtime.website_fetcher,
            "llm_provider": integration_runtime.llm_provider,
            "llm_model": integration_runtime.llm_model,
            "llm_base_url": integration_runtime.llm_base_url(),
        }
    )
    form = CampaignForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
        workspace=workspace,
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
                    catalog_ids=[catalog.pk for catalog in form.cleaned_data["catalogs"]],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Campaña creada en borrador.")
                return redirect("campaign-detail", campaign_id=campaign.pk)
    selected_category_count = len(form["categories"].value() or [])
    selected_zone_count = len(form["zones"].value() or [])
    selected_catalog_count = len(form["catalogs"].value() or [])
    selected_zone_ids = {str(value) for value in (form["zones"].value() or [])}
    selected_province_ids = {str(value) for value in (form["provinces"].value() or [])}
    district_queryset = cast(
        "forms.ModelMultipleChoiceField[SearchZone]",
        form.fields["zones"],
    ).queryset
    province_queryset = cast(
        "forms.ModelMultipleChoiceField[SearchZone]",
        form.fields["provinces"],
    ).queryset
    assert district_queryset is not None
    assert province_queryset is not None
    district_rows = list(district_queryset)
    if not selected_province_ids:
        selected_province_ids = {
            str(zone.parent_id)
            for zone in district_rows
            if str(zone.pk) in selected_zone_ids and zone.parent_id is not None
        }
    districts_by_province: dict[object, list[dict[str, object]]] = {}
    for zone in district_rows:
        row = {"zone": zone, "selected": str(zone.pk) in selected_zone_ids}
        if zone.parent_id is not None:
            districts_by_province.setdefault(zone.parent_id, []).append(row)
    province_groups = [
        {
            "province": province,
            "districts": districts_by_province.get(province.pk, []),
            "selected": str(province.pk) in selected_province_ids,
        }
        for province in province_queryset
        if districts_by_province.get(province.pk)
    ]
    return render(
        request,
        "campaigns/form.html",
        {
            "form": form,
            "selected_category_count": selected_category_count,
            "selected_zone_count": selected_zone_count,
            "selected_catalog_count": selected_catalog_count,
            "query_count": selected_category_count * selected_zone_count,
            "province_groups": province_groups,
            "selected_zone_ids": selected_zone_ids,
        },
    )


@require_capability(Capability.MANAGE_CAMPAIGNS)
@require_GET
@never_cache
def campaign_zone_map(request: HttpRequest, province_id: uuid.UUID) -> JsonResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_CAMPAIGNS)
    province = get_object_or_404(
        SearchZone,
        pk=province_id,
        workspace=workspace,
        level=SearchZone.Level.PROVINCE,
        active=True,
        archived_at__isnull=True,
    )
    zones = SearchZone.objects.filter(
        workspace=workspace,
        parent=province,
        active=True,
        selectable=True,
        archived_at__isnull=True,
    ).order_by("sort_order", "name")
    payload = _zone_map_payload(zones)
    payload["province"] = province.name
    payload["label"] = province.label_plural
    return JsonResponse(payload)


@require_capability(Capability.VIEW_CAMPAIGNS)
@require_GET
@never_cache
def campaign_detail(request: HttpRequest, campaign_id: str) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.VIEW_CAMPAIGNS)
    is_admin = has_capability(owner, Capability.MANAGE_CAMPAIGNS)
    campaign_queryset = Campaign.objects.select_related("catalog", "created_by")
    if is_admin:
        campaign_queryset = campaign_queryset.prefetch_related(
            "attachments__catalog",
            "category_selections",
            "zone_selections",
            "search_queries",
            "search_runs",
            "prospects__emails",
            "prospects__analyses",
            "prospects__outbound_messages",
            "prospects__web_snapshots",
        )
    else:
        campaign_queryset = campaign_queryset.exclude(state=Campaign.State.DRAFT)
    campaign = get_object_or_404(
        campaign_queryset,
        pk=campaign_id,
        workspace=workspace,
    )
    first_contacts = campaign.messages.filter(
        kind__in=(OutboundMessage.Kind.FIRST_CONTACT, OutboundMessage.Kind.INITIAL)
    )
    metrics: dict[str, int] = {
        "raw": 0,
        "with_email": 0,
        "duplicates": 0,
        "irrelevant": 0,
        "qualified": 0,
        "review_ready": 0,
        "queued": 0,
        "sent": first_contacts.filter(state=OutboundMessage.State.SENT).count(),
        "simulated": 0,
        "errors": 0,
    }
    if is_admin:
        prospects = campaign.prospects.all()
        enrollment_count = campaign.enrollments.count()
        eligible_enrollments = campaign.enrollments.exclude(
            state__in=(
                CampaignEnrollment.State.DISCOVERED,
                CampaignEnrollment.State.INELIGIBLE,
                CampaignEnrollment.State.CANCELLED,
            )
        )
        metrics.update(
            {
                "raw": campaign.search_runs.aggregate(value=Sum("raw_count"))["value"] or 0,
                "with_email": (
                    campaign.enrollments.exclude(selected_email=None).count()
                    if enrollment_count
                    else prospects.exclude(
                        pipeline_state__in=(
                            Prospect.PipelineState.DISCOVERED,
                            Prospect.PipelineState.SKIPPED_NO_EMAIL,
                        )
                    ).count()
                ),
                "duplicates": campaign.search_runs.aggregate(value=Sum("duplicate_count"))["value"]
                or 0,
                "irrelevant": prospects.filter(
                    pipeline_state=Prospect.PipelineState.SKIPPED_IRRELEVANT
                ).count(),
                "qualified": (
                    eligible_enrollments.count()
                    if enrollment_count
                    else prospects.filter(pipeline_state=Prospect.PipelineState.QUEUED).count()
                ),
                "review_ready": first_contacts.filter(
                    state=OutboundMessage.State.REVIEW_READY
                ).count(),
                "queued": first_contacts.filter(
                    state__in=(
                        OutboundMessage.State.PREPARED,
                        OutboundMessage.State.QUEUED,
                        OutboundMessage.State.SENDING,
                        OutboundMessage.State.RECONCILING,
                    )
                ).count(),
                "simulated": first_contacts.filter(
                    state=OutboundMessage.State.DRY_RUN_COMPLETED
                ).count(),
                "errors": prospects.filter(pipeline_state=Prospect.PipelineState.ERROR).count()
                + first_contacts.filter(state=OutboundMessage.State.SEND_FAILED).count(),
            }
        )
    progress_percent = min(100, round(metrics["qualified"] * 100 / campaign.objective))
    return render(
        request,
        "campaigns/detail.html",
        {
            "campaign": campaign,
            "metrics": metrics,
            "progress_percent": progress_percent,
            "is_admin": is_admin,
            "individually_approved_count": first_contacts.filter(
                approved_at__isnull=False,
                state=OutboundMessage.State.PREPARED,
            ).count()
            if is_admin
            else 0,
        },
    )


@require_capability(Capability.MANAGE_CAMPAIGNS)
@require_POST
def campaign_action(request: HttpRequest, campaign_id: str, action: str) -> HttpResponse:
    targets = {
        "start": Campaign.State.DISCOVERING,
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
        if campaign.discovery_state == Campaign.DiscoveryState.RUNNING and targets[action] in {
            Campaign.State.DISCOVERING,
            Campaign.State.RUNNING,
        }:
            orchestrate_extraction.delay(str(campaign.pk))
    return redirect("campaign-detail", campaign_id=campaign_id)


@require_capability(Capability.APPROVE_CAMPAIGNS)
@require_POST
def campaign_approve(request: HttpRequest, campaign_id: str) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    try:
        campaign = approve_campaign(campaign_id, actor=actor)
    except (Campaign.DoesNotExist, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Campaña inexistente.",
        )
    else:
        if campaign.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY:
            for message_id in campaign.messages.filter(
                kind=OutboundMessage.Kind.INITIAL,
                state=OutboundMessage.State.QUEUED,
            ).values_list("pk", flat=True):
                deliver_message_task.delay(str(message_id))
        messages.success(
            request,
            "Campaña aprobada. Volvimos a comprobar cada destinatario antes de encolarlo.",
        )
    return redirect("campaign-detail", campaign_id=campaign_id)


@require_capability(Capability.APPROVE_CAMPAIGNS)
@require_POST
def campaign_start_approved(request: HttpRequest, campaign_id: str) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    try:
        start_per_message_campaign(campaign_id, actor=actor)
    except (Campaign.DoesNotExist, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Campaña inexistente.",
        )
    else:
        messages.success(
            request,
            "Se inició la entrega de los mensajes aprobados. Los demás quedaron fuera.",
        )
    return redirect("campaign-detail", campaign_id=campaign_id)


@require_capability(Capability.MANAGE_CAMPAIGNS)
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
        campaign__workspace=workspace_for_user(owner, Capability.MANAGE_CAMPAIGNS),
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


@require_capability(Capability.MANAGE_CAMPAIGNS)
@require_POST
def regenerate_outdated_campaign_analyses(request: HttpRequest, campaign_id: str) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    try:
        reservations = request_outdated_analysis_regenerations(
            campaign_id=campaign_id,
            actor=owner,
        )
    except (Campaign.DoesNotExist, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Campaña inexistente.",
        )
    else:
        for reservation in reservations:
            process_prospect_pipeline.delay(
                str(reservation.prospect_id),
                regeneration_nonce=reservation.regeneration_nonce,
                actor_id=owner.pk,
                reservation_token=reservation.token,
                analysis_generation=reservation.generation,
            )
        if reservations:
            messages.success(
                request,
                f"Se solicitaron {len(reservations)} reanálisis con el contrato IA vigente.",
            )
        else:
            messages.info(request, "No quedan análisis del contrato anterior para regenerar.")
    return redirect("campaign-detail", campaign_id=campaign_id)
