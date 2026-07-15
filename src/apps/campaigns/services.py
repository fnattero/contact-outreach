from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import (
    Campaign,
    CampaignCategorySelection,
    CampaignZoneSelection,
    SearchQuery,
    SearchRun,
)
from apps.catalogs.services import verify_catalog
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone, normalize_name
from apps.configuration.services import profile_snapshot

PROMPT_VERSION = "prospect-analysis-v1"
SCHEMA_VERSION = "prospect-analysis-schema-v1"


class InvalidCampaignTransition(ValidationError):
    pass


TERMINAL_STATES = {
    Campaign.State.CANCELLED,
    Campaign.State.COMPLETED,
    Campaign.State.STOPPED_ERROR,
}
TERMINAL_DISCOVERY_STATES = {
    Campaign.DiscoveryState.TARGET_REACHED,
    Campaign.DiscoveryState.EXHAUSTED_QUERIES,
    Campaign.DiscoveryState.EXHAUSTED_RAW_LIMIT,
    Campaign.DiscoveryState.EXHAUSTED_COST,
    Campaign.DiscoveryState.FAILED_PROVIDER,
}
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    Campaign.State.DRAFT: {Campaign.State.RUNNING, Campaign.State.CANCELLED},
    Campaign.State.RUNNING: {
        Campaign.State.PAUSED,
        Campaign.State.CANCELLED,
        Campaign.State.COMPLETED,
        Campaign.State.STOPPED_ERROR,
    },
    Campaign.State.PAUSED: {Campaign.State.RUNNING, Campaign.State.CANCELLED},
    Campaign.State.CANCELLED: set(),
    Campaign.State.COMPLETED: set(),
    Campaign.State.STOPPED_ERROR: set(),
}


@transaction.atomic
def create_campaign(
    *,
    actor: User,
    values: dict[str, Any],
    category_ids: Collection[object],
    zone_ids: Collection[object],
) -> Campaign:
    categories = list(
        SearchCategory.objects.filter(pk__in=category_ids, active=True, archived_at__isnull=True)
    )
    zones = list(SearchZone.objects.filter(pk__in=zone_ids, active=True, archived_at__isnull=True))
    if len(categories) != len(set(category_ids)):
        raise ValidationError("Alguno de los rubros seleccionados no está activo.")
    if len(zones) != len(set(zone_ids)):
        raise ValidationError("Alguna de las zonas seleccionadas no está activa.")
    if not categories or not zones:
        raise ValidationError("Seleccioná al menos un rubro y una zona.")
    campaign = Campaign(created_by=actor, **values)
    campaign.full_clean()
    campaign.save()
    CampaignCategorySelection.objects.bulk_create(
        CampaignCategorySelection(
            campaign=campaign,
            category=category,
            name_snapshot=category.name,
            normalized_name_snapshot=category.normalized_name,
            sort_order=index,
        )
        for index, category in enumerate(categories)
    )
    CampaignZoneSelection.objects.bulk_create(
        CampaignZoneSelection(
            campaign=campaign,
            zone=zone,
            name_snapshot=zone.name,
            location_snapshot=zone.location_text,
            sort_order=index,
        )
        for index, zone in enumerate(zones)
    )
    record_event(
        action="campaign.created",
        entity=campaign,
        actor=actor,
        after={"name": campaign.name, "state": campaign.state, "objective": campaign.objective},
    )
    return campaign


def _settings_snapshot(campaign: Campaign) -> dict[str, Any]:
    return {
        "location_text": campaign.location_text,
        "objective": campaign.objective,
        "max_raw_records": campaign.max_raw_records,
        "cost_limit": str(campaign.cost_limit),
        "cost_currency": campaign.cost_currency,
        "daily_limit": campaign.daily_limit,
        "message_interval_minutes": campaign.message_interval_minutes,
        "weekdays": campaign.weekdays,
        "window_start": campaign.window_start.isoformat(),
        "window_end": campaign.window_end.isoformat(),
        "timezone_name": campaign.timezone_name,
        "relevance_threshold": campaign.relevance_threshold,
        "extractor_provider": campaign.extractor_provider,
        "llm_provider": campaign.llm_provider,
        "llm_base_url": campaign.llm_base_url,
        "llm_model": campaign.llm_model,
        "delivery_mode": campaign.delivery_mode,
        "catalog_id": str(campaign.catalog_id),
        "catalog_version": campaign.catalog.version,
        "runtime_send_mode": settings.SEND_MODE,
        "runtime_kill_switch": settings.SEND_KILL_SWITCH,
    }


def _preflight(campaign: Campaign) -> BusinessProfile:
    try:
        profile = BusinessProfile.objects.get(owner=campaign.created_by)
    except BusinessProfile.DoesNotExist as exc:
        raise ValidationError("Completá el perfil comercial antes de iniciar.") from exc
    required = (profile.company_name, profile.salesperson_name, profile.address, profile.signature)
    if not all(value.strip() for value in required):
        raise ValidationError(
            "El perfil comercial no tiene identidad, domicilio y firma completos."
        )
    if not campaign.category_selections.exists() or not campaign.zone_selections.exists():
        raise ValidationError("La campaña necesita rubros y zonas.")
    verify_catalog(campaign.catalog)
    campaign.full_clean()
    if campaign.extractor_provider == "outscraper" and not settings.OUTSCRAPER_API_KEY:
        raise ValidationError("OUTSCRAPER_API_KEY no está configurada en el entorno.")
    if campaign.llm_provider == "openai-compatible" and not settings.LLM_API_KEY:
        raise ValidationError("LLM_API_KEY no está configurada en el entorno.")
    if campaign.delivery_mode == Campaign.DeliveryMode.LIVE:
        if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
            raise ValidationError("El modo live está bloqueado por los controles de despliegue.")
        from apps.integrations.gmail import GMAIL_SCOPES
        from apps.mailbox.models import GmailConnection

        connection = GmailConnection.objects.filter(owner=campaign.created_by).first()
        if (
            connection is None
            or not connection.is_ready
            or set(connection.scopes) != set(GMAIL_SCOPES)
        ):
            raise ValidationError("La conexión Gmail debe estar conectada y probada.")
    return profile


def _freeze_draft(campaign: Campaign, profile: BusinessProfile) -> None:
    campaign.settings_snapshot = _settings_snapshot(campaign)
    campaign.profile_snapshot = profile_snapshot(profile)
    campaign.prompt_snapshot = {
        "version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "provider": campaign.llm_provider,
        "model": campaign.llm_model,
    }
    queries: list[SearchQuery] = []
    order = 0
    for category in campaign.category_selections.all():
        for zone in campaign.zone_selections.all():
            query_text = (
                f"{category.name_snapshot} en {zone.name_snapshot}, {zone.location_snapshot}"
            )
            queries.append(
                SearchQuery(
                    campaign=campaign,
                    category_snapshot=category.name_snapshot,
                    zone_snapshot=zone.name_snapshot,
                    location_snapshot=zone.location_snapshot,
                    query_text=query_text,
                    normalized_query=normalize_name(query_text),
                    sort_order=order,
                )
            )
            order += 1
    SearchQuery.objects.bulk_create(queries)


@transaction.atomic
def transition_campaign(
    *,
    campaign_id: uuid.UUID | str,
    target_state: str,
    actor: User | None,
    reason: str = "",
) -> Campaign:
    campaign = (
        Campaign.objects.select_for_update()
        .select_related("catalog", "created_by")
        .get(pk=campaign_id)
    )
    if target_state not in Campaign.State.values:
        raise InvalidCampaignTransition("El estado solicitado no existe.")
    if target_state not in ALLOWED_TRANSITIONS[campaign.state]:
        raise InvalidCampaignTransition(f"Transición inválida: {campaign.state} → {target_state}.")
    if actor is not None and campaign.created_by_id != actor.pk:
        raise InvalidCampaignTransition("La campaña pertenece a otro usuario.")

    before = {"state": campaign.state, "discovery_state": campaign.discovery_state}
    if target_state == Campaign.State.RUNNING:
        profile = _preflight(campaign)
        if campaign.state == Campaign.State.DRAFT:
            _freeze_draft(campaign, profile)
            campaign.discovery_state = Campaign.DiscoveryState.RUNNING
            campaign.started_at = timezone.now()
        campaign.status_reason = ""
    elif target_state == Campaign.State.PAUSED:
        campaign.status_reason = reason or "Pausada por el usuario."
        SearchRun.objects.filter(
            campaign=campaign,
            state__in=(
                SearchRun.State.PENDING,
                SearchRun.State.RUNNING,
                SearchRun.State.RETRY_WAIT,
            ),
        ).update(state=SearchRun.State.RETRY_WAIT, next_poll_at=timezone.now())
        SearchQuery.objects.filter(
            campaign=campaign,
            state__in=(SearchQuery.State.RUNNING, SearchQuery.State.RETRY_WAIT),
        ).update(state=SearchQuery.State.RETRY_WAIT)
    elif target_state == Campaign.State.CANCELLED:
        now = timezone.now()
        campaign.status_reason = reason or "Cancelada por el usuario."
        campaign.finished_at = now
        SearchRun.objects.filter(
            campaign=campaign,
            state__in=(
                SearchRun.State.PENDING,
                SearchRun.State.RUNNING,
                SearchRun.State.RETRY_WAIT,
            ),
        ).update(
            state=SearchRun.State.CANCELLED,
            finished_at=now,
            next_poll_at=None,
        )
        SearchQuery.objects.filter(
            campaign=campaign,
            state__in=(
                SearchQuery.State.PENDING,
                SearchQuery.State.RUNNING,
                SearchQuery.State.RETRY_WAIT,
            ),
        ).update(state=SearchQuery.State.CANCELLED)
        from apps.campaigns.models import OutboundMessage

        OutboundMessage.objects.filter(
            campaign=campaign,
            state__in=(OutboundMessage.State.PREPARED, OutboundMessage.State.QUEUED),
        ).update(state=OutboundMessage.State.CANCELLED, error=campaign.status_reason)
    elif target_state == Campaign.State.COMPLETED:
        if campaign.discovery_state not in TERMINAL_DISCOVERY_STATES:
            raise InvalidCampaignTransition("No se puede completar con descubrimiento activo.")
        campaign.finished_at = timezone.now()
    elif target_state == Campaign.State.STOPPED_ERROR:
        if not reason.strip():
            raise InvalidCampaignTransition("El error terminal requiere un motivo.")
        campaign.status_reason = reason.strip()
        campaign.finished_at = timezone.now()

    campaign.state = target_state
    campaign.save()
    record_event(
        action="campaign.transitioned",
        entity=campaign,
        actor=actor,
        before=before,
        after={
            "state": campaign.state,
            "discovery_state": campaign.discovery_state,
            "reason": campaign.status_reason,
        },
    )
    return campaign


@transaction.atomic
def finish_discovery(
    *,
    campaign_id: uuid.UUID | str,
    target_state: str,
    reason: str,
    actor: User | None = None,
) -> Campaign:
    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    if campaign.state != Campaign.State.RUNNING:
        raise InvalidCampaignTransition("El descubrimiento sólo cambia en una campaña activa.")
    if campaign.discovery_state != Campaign.DiscoveryState.RUNNING:
        raise InvalidCampaignTransition("El descubrimiento ya no está activo.")
    if target_state not in TERMINAL_DISCOVERY_STATES:
        raise InvalidCampaignTransition("El estado final de descubrimiento no es válido.")
    if not reason.strip():
        raise InvalidCampaignTransition("El cierre de descubrimiento requiere un motivo exacto.")
    before = {"discovery_state": campaign.discovery_state}
    campaign.discovery_state = target_state
    campaign.discovery_stop_reason = reason.strip()
    campaign.save(update_fields=("discovery_state", "discovery_stop_reason", "updated_at"))
    record_event(
        action="campaign.discovery_finished",
        entity=campaign,
        actor=actor,
        before=before,
        after={
            "discovery_state": campaign.discovery_state,
            "discovery_stop_reason": campaign.discovery_stop_reason,
        },
    )
    return campaign
