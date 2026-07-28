from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Collection
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.campaigns.models import (
    Campaign,
    CampaignCategorySelection,
    CampaignCoverageSelection,
    CampaignZoneSelection,
    SearchQuery,
    SearchRun,
)
from apps.catalogs.models import Catalog
from apps.configuration.models import (
    BusinessProfile,
    SearchCategory,
    SearchCategoryRule,
    SearchZone,
)
from apps.configuration.services import (
    profile_snapshot,
)
from apps.overture.geometry import validate_geojson
from apps.overture.models import OvertureCoveragePartition, OvertureDatasetSnapshot

# Historical AI analyses remain readable but are never regenerated.
PROMPT_VERSION = "prospect-analysis-v3"
SCHEMA_VERSION = "prospect-analysis-schema-v3"


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
    Campaign.State.DRAFT: {
        Campaign.State.DISCOVERING,
        Campaign.State.RUNNING,
        Campaign.State.CANCELLED,
    },
    Campaign.State.DISCOVERING: {
        Campaign.State.AWAITING_APPROVAL,
        Campaign.State.PAUSED,
        Campaign.State.CANCELLED,
        Campaign.State.STOPPED_ERROR,
    },
    Campaign.State.AWAITING_APPROVAL: {
        Campaign.State.RUNNING,
        Campaign.State.CANCELLED,
    },
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
    category_ids: Collection[uuid.UUID | str],
    zone_ids: Collection[uuid.UUID | str],
    catalog_ids: Collection[uuid.UUID | str] | None = None,
) -> Campaign:
    membership = require_user_capability(actor, Capability.MANAGE_CAMPAIGNS)
    categories = list(
        SearchCategory.objects.filter(
            pk__in=category_ids,
            workspace=membership.workspace,
            active=True,
            archived_at__isnull=True,
        ).prefetch_related("rules")
    )
    ordered_zone_ids = list(dict.fromkeys(zone_ids))
    zones_by_id = {
        str(zone.pk): zone
        for zone in SearchZone.objects.filter(
            pk__in=ordered_zone_ids,
            workspace=membership.workspace,
            active=True,
            selectable=True,
            archived_at__isnull=True,
        ).select_related("parent")
    }
    zones = [
        zones_by_id[str(zone_id)] for zone_id in ordered_zone_ids if str(zone_id) in zones_by_id
    ]
    if len(categories) != len(set(category_ids)):
        raise ValidationError("Alguno de los rubros seleccionados no está activo.")
    if len(zones) != len(ordered_zone_ids):
        raise ValidationError("Alguno de los distritos seleccionados ya no está disponible.")
    if not categories or not zones:
        raise ValidationError("Seleccioná al menos un rubro y un distrito.")
    if any(not category.rules.filter(active=True).exists() for category in categories):
        raise ValidationError("Todos los rubros seleccionados necesitan reglas Overture activas.")
    if any(not zone.boundary_hash or not zone.boundary_geojson for zone in zones):
        raise ValidationError("Todas las zonas seleccionadas necesitan un límite GeoJSON válido.")
    values["cost_limit"] = 0
    values["cost_currency"] = "USD"
    catalog = values.get("catalog")
    if not isinstance(catalog, Catalog) or catalog.workspace_id != membership.workspace_id:
        raise ValidationError("El catálogo seleccionado no pertenece a este espacio de trabajo.")
    campaign = Campaign(created_by=actor, workspace=membership.workspace, **values)
    from apps.campaigns.approval import freeze_campaign_content

    freeze_campaign_content(campaign)
    campaign.full_clean()
    campaign.save()
    from apps.campaigns.approval import set_campaign_attachments

    set_campaign_attachments(
        campaign,
        catalog_ids=catalog_ids or (catalog.pk,),
        actor=actor,
    )
    category_selections = []
    for index, category in enumerate(categories):
        rules_snapshot = [
            {
                "taxonomy_code": rule.taxonomy_code,
                "name_terms": list(rule.name_terms),
            }
            for rule in category.rules.filter(active=True)
        ]
        category_selections.append(
            CampaignCategorySelection(
                campaign=campaign,
                category=category,
                name_snapshot=category.name,
                normalized_name_snapshot=category.normalized_name,
                rules_snapshot=rules_snapshot,
                rules_revision_snapshot=category.rules_revision,
                sort_order=index,
            )
        )
    CampaignCategorySelection.objects.bulk_create(category_selections)
    CampaignZoneSelection.objects.bulk_create(
        CampaignZoneSelection(
            campaign=campaign,
            zone=zone,
            name_snapshot=zone.name,
            location_snapshot=zone.location_text,
            boundary_geojson_snapshot=zone.boundary_geojson,
            boundary_bbox_snapshot=zone.boundary_bbox,
            boundary_hash_snapshot=zone.boundary_hash,
            boundary_revision_snapshot=zone.boundary_revision,
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
    overture_release = campaign.overture_release
    return {
        "location_text": campaign.location_text,
        "objective": campaign.objective,
        "max_raw_records": campaign.max_raw_records,
        "overture_min_confidence": str(campaign.overture_min_confidence),
        "daily_limit": campaign.daily_limit,
        "message_interval_minutes": campaign.message_interval_minutes,
        "weekdays": campaign.weekdays,
        "window_start": campaign.window_start.isoformat(),
        "window_end": campaign.window_end.isoformat(),
        "timezone_name": campaign.timezone_name,
        "relevance_threshold": campaign.relevance_threshold,
        "extractor_provider": campaign.extractor_provider,
        "overture_snapshot_id": (
            str(campaign.overture_snapshot_id) if campaign.overture_snapshot_id else ""
        ),
        "overture_release_id": (
            overture_release.release_id if overture_release is not None else ""
        ),
        "overture_coverages": [
            {
                "partition_id": str(selection.partition_id),
                "province_code": selection.province_code_snapshot,
                "province_name": selection.province_name_snapshot,
                "district_code": selection.district_code_snapshot,
                "district_name": selection.district_name_snapshot,
                "boundary_hash": selection.boundary_hash_snapshot,
            }
            for selection in campaign.coverage_selections.order_by("sort_order")
        ],
        "website_fetcher": campaign.website_fetcher,
        "llm_provider": campaign.llm_provider,
        "llm_base_url": campaign.llm_base_url,
        "llm_model": campaign.llm_model,
        "delivery_mode": campaign.delivery_mode,
        "catalog_id": str(campaign.catalog_id),
        "catalog_version": campaign.catalog.version,
        "runtime_send_mode": settings.SEND_MODE,
        "runtime_kill_switch": settings.SEND_KILL_SWITCH,
    }


def _refresh_draft_category_rules(campaign: Campaign) -> None:
    selections = list(
        CampaignCategorySelection.objects.select_for_update()
        .filter(campaign=campaign)
        .order_by("sort_order", "created_at")
    )
    if not selections:
        raise ValidationError("La campaña necesita rubros.")

    category_ids = {selection.category_id for selection in selections}
    categories = {
        category.pk: category
        for category in SearchCategory.objects.select_for_update().filter(
            pk__in=category_ids,
            workspace=campaign.workspace,
            active=True,
            archived_at__isnull=True,
        )
    }
    if set(categories) != category_ids:
        raise ValidationError("Un rubro seleccionado ya no está activo.")

    rules_by_category: dict[object, list[dict[str, object]]] = {
        category_id: [] for category_id in category_ids
    }
    rules = (
        SearchCategoryRule.objects.select_for_update()
        .filter(category_id__in=category_ids, active=True)
        .order_by("category_id", "sort_order", "created_at")
    )
    for rule in rules:
        rules_by_category[rule.category_id].append(
            {
                "taxonomy_code": rule.taxonomy_code,
                "name_terms": list(rule.name_terms),
            }
        )
    if any(not rules_by_category[category_id] for category_id in category_ids):
        raise ValidationError("Todos los rubros seleccionados necesitan reglas Overture activas.")

    for selection in selections:
        category = categories[selection.category_id]
        selection.rules_snapshot = rules_by_category[selection.category_id]
        selection.rules_revision_snapshot = category.rules_revision
        selection.save(update_fields=("rules_snapshot", "rules_revision_snapshot", "updated_at"))


def _refresh_missing_legacy_draft_boundaries(campaign: Campaign) -> None:
    """Backfill only pre-Overture draft selections that never had a boundary."""

    selections = list(
        CampaignZoneSelection.objects.select_for_update()
        .filter(campaign=campaign)
        .order_by("sort_order", "created_at")
    )
    if not selections:
        raise ValidationError("La campaña necesita zonas.")
    missing = [
        selection
        for selection in selections
        if not selection.boundary_hash_snapshot or not selection.boundary_geojson_snapshot
    ]
    if not missing:
        return

    zone_ids = {selection.zone_id for selection in missing}
    zones = {
        zone.pk: zone
        for zone in SearchZone.objects.select_for_update().filter(
            pk__in=zone_ids,
            workspace=campaign.workspace,
            active=True,
            archived_at__isnull=True,
        )
    }
    if set(zones) != zone_ids:
        raise ValidationError(
            "Una zona legacy no está activa; recreá la campaña con una zona vigente."
        )

    refreshed: list[str] = []
    for selection in missing:
        zone = zones[selection.zone_id]
        if not zone.boundary_geojson or not zone.boundary_hash:
            raise ValidationError(
                f"Subí un límite GeoJSON para {zone.name} antes de iniciar la campaña legacy."
            )
        geometry = validate_geojson(zone.boundary_geojson)
        if geometry.sha256 != zone.boundary_hash or list(geometry.bbox) != zone.boundary_bbox:
            raise ValidationError(f"La frontera configurada para {zone.name} perdió integridad.")
        selection.boundary_geojson_snapshot = geometry.geojson
        selection.boundary_bbox_snapshot = list(geometry.bbox)
        selection.boundary_hash_snapshot = geometry.sha256
        selection.boundary_revision_snapshot = zone.boundary_revision
        selection.save(
            update_fields=(
                "boundary_geojson_snapshot",
                "boundary_bbox_snapshot",
                "boundary_hash_snapshot",
                "boundary_revision_snapshot",
                "updated_at",
            )
        )
        refreshed.append(str(selection.pk))
    record_event(
        action="campaign.legacy_boundaries_refreshed",
        entity=campaign,
        actor=None,
        after={"zone_selection_ids": refreshed},
    )


def _human_join(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    return ", ".join(values[:-1]) + f" y {values[-1]}"


def _resolve_draft_coverages(
    campaign: Campaign,
) -> dict[object, OvertureCoveragePartition]:
    selections = list(
        campaign.zone_selections.select_related("zone", "zone__parent").order_by(
            "sort_order", "created_at"
        )
    )
    province_names: dict[str, str] = {}
    for selection in selections:
        zone = selection.zone
        if not zone.province_code or zone.parent_id is None:
            raise ValidationError(
                f"La zona {zone.name} no está asociada a una provincia. "
                "Editala antes de usar datos Overture."
            )
        province_names.setdefault(zone.province_code, zone.province_name)

    partitions = {
        partition.province_code: partition
        for partition in OvertureCoveragePartition.objects.select_for_update()
        .select_related("release", "snapshot")
        .filter(
            province_code__in=province_names,
            status=OvertureCoveragePartition.Status.READY,
            is_active=True,
        )
    }
    missing_codes = [code for code in province_names if code not in partitions]
    if missing_codes:
        missing_names = [province_names[code] for code in missing_codes]
        noun = "los datos de" if len(missing_names) == 1 else "datos de"
        raise ValidationError(
            f"Faltan {noun} {_human_join(missing_names)}. Actualizalos desde Datos de búsqueda."
        )
    release_ids = {partition.release_id for partition in partitions.values()}
    if len(release_ids) != 1:
        raise ValidationError(
            "Las provincias seleccionadas usan versiones distintas. "
            "Actualizalas desde Datos de búsqueda antes de iniciar."
        )

    by_zone: dict[object, OvertureCoveragePartition] = {}
    for selection in selections:
        zone = selection.zone
        partition = partitions[zone.province_code]
        exact_zone_exists = (
            partition.zones.filter(
                boundary_hash=selection.boundary_hash_snapshot,
            )
            .filter(Q(search_zone=zone) | Q(search_zone__isnull=True))
            .exists()
        )
        if not exact_zone_exists:
            raise ValidationError(
                f"Los datos de {zone.province_name} no incluyen la revisión actual de "
                f"{zone.name}. Actualizalos desde Datos de búsqueda."
            )
        by_zone[zone.pk] = partition
    return by_zone


def _pinned_coverages(campaign: Campaign) -> dict[object, OvertureCoveragePartition]:
    selections = list(
        campaign.coverage_selections.select_related("partition", "release").order_by("sort_order")
    )
    if not selections:
        raise ValidationError("La campaña no fijó su cobertura provincial.")
    release_ids = {selection.release_id for selection in selections}
    if len(release_ids) != 1:
        raise ValidationError("La campaña conserva versiones de datos incompatibles.")
    unavailable = [
        selection.province_name_snapshot
        for selection in selections
        if selection.partition.status
        not in {
            OvertureCoveragePartition.Status.READY,
            OvertureCoveragePartition.Status.SUPERSEDED,
        }
    ]
    if unavailable:
        raise ValidationError(
            f"La cobertura fijada de {_human_join(list(dict.fromkeys(unavailable)))} "
            "ya no está disponible."
        )
    return {
        selection.district_id: selection.partition
        for selection in selections
        if selection.district_id is not None
    }


def _preflight(
    campaign: Campaign,
) -> tuple[
    BusinessProfile,
    OvertureDatasetSnapshot | None,
    dict[object, OvertureCoveragePartition],
]:
    try:
        profile = BusinessProfile.objects.get(workspace=campaign.workspace)
    except BusinessProfile.DoesNotExist as exc:
        raise ValidationError("Completá el perfil comercial antes de iniciar.") from exc
    required = (profile.company_name, profile.salesperson_name, profile.address, profile.signature)
    if not all(value.strip() for value in required):
        raise ValidationError(
            "El perfil comercial no tiene identidad, domicilio y firma completos."
        )
    if not campaign.category_selections.exists() or not campaign.zone_selections.exists():
        raise ValidationError("La campaña necesita rubros y zonas.")
    from apps.campaigns.approval import _verified_campaign_attachments

    _verified_campaign_attachments(campaign)
    if campaign.catalog.workspace_id != campaign.workspace_id:
        raise ValidationError("Uno de los PDFs pertenece a otro espacio de trabajo.")
    campaign.full_clean()
    overture_snapshot: OvertureDatasetSnapshot | None = None
    coverages: dict[object, OvertureCoveragePartition] = {}
    if campaign.extractor_provider == "overture":
        if campaign.state == Campaign.State.DRAFT:
            coverages = _resolve_draft_coverages(campaign)
            overture_snapshot = next(iter(coverages.values())).snapshot
        else:
            coverages = _pinned_coverages(campaign)
            overture_snapshot = campaign.overture_snapshot
        if overture_snapshot is None:
            raise ValidationError(
                "Sincronizá y activá una copia local de datos de Overture antes de iniciar."
            )
        if campaign.state != Campaign.State.DRAFT and overture_snapshot.status not in {
            OvertureDatasetSnapshot.Status.READY,
            OvertureDatasetSnapshot.Status.SUPERSEDED,
        }:
            raise ValidationError(
                "La copia local de datos de Overture fijada ya no está disponible."
            )
    if campaign.delivery_mode == Campaign.DeliveryMode.LIVE:
        if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
            raise ValidationError(
                "El envío en vivo está bloqueado por los controles generales de seguridad."
            )
        from apps.integrations.gmail import GMAIL_SCOPES
        from apps.mailbox.models import GmailConnection

        connection = GmailConnection.objects.filter(workspace=campaign.workspace).first()
        if (
            connection is None
            or not connection.is_ready
            or set(connection.scopes) != set(GMAIL_SCOPES)
        ):
            raise ValidationError("La conexión Gmail debe estar conectada y probada.")
    return profile, overture_snapshot, coverages


def _freeze_draft(
    campaign: Campaign,
    profile: BusinessProfile,
    overture_snapshot: OvertureDatasetSnapshot | None,
    coverages: dict[object, OvertureCoveragePartition],
) -> None:
    campaign.overture_snapshot = overture_snapshot
    zone_selections = list(
        campaign.zone_selections.select_related("zone", "zone__parent").order_by(
            "sort_order", "created_at"
        )
    )
    coverage_models: list[CampaignCoverageSelection] = []
    if coverages:
        release_ids = {partition.release_id for partition in coverages.values()}
        if len(release_ids) != 1:
            raise ValidationError("Una campaña no puede mezclar versiones de datos.")
        first_partition = next(iter(coverages.values()))
        campaign.overture_release = first_partition.release
        for order, zone_selection in enumerate(zone_selections):
            zone = zone_selection.zone
            partition = coverages[zone.pk]
            coverage_models.append(
                CampaignCoverageSelection(
                    campaign=campaign,
                    release=partition.release,
                    partition=partition,
                    province=zone.parent,
                    district=zone,
                    province_code_snapshot=zone.province_code,
                    province_name_snapshot=zone.province_name,
                    district_code_snapshot=zone.official_code,
                    district_name_snapshot=zone_selection.name_snapshot,
                    district_level_snapshot=zone.level,
                    district_label_snapshot=(
                        zone.parent.label_plural if zone.parent_id else zone.label_plural
                    ),
                    boundary_geojson_snapshot=zone_selection.boundary_geojson_snapshot,
                    boundary_bbox_snapshot=zone_selection.boundary_bbox_snapshot,
                    boundary_hash_snapshot=zone_selection.boundary_hash_snapshot,
                    boundary_revision_snapshot=zone_selection.boundary_revision_snapshot,
                    boundary_source_snapshot=zone.boundary_source,
                    boundary_attribution_snapshot=zone.boundary_attribution,
                    sort_order=order,
                )
            )
        CampaignCoverageSelection.objects.bulk_create(coverage_models)
    coverage_selection_by_zone = {selection.district_id: selection for selection in coverage_models}
    campaign.settings_snapshot = _settings_snapshot(campaign)
    campaign.profile_snapshot = profile_snapshot(profile)
    campaign.prompt_snapshot = {
        "version": "fixed-campaign-message-v1",
        "schema_version": "fixed-message-no-placeholders-v1",
        "initial_outreach": "fixed-no-llm",
        "llm_calls": 0,
        "search_mode": "structured-overture-rules-v1",
    }
    queries: list[SearchQuery] = []
    order = 0
    for category in campaign.category_selections.all():
        for zone in zone_selections:
            coverage_selection = coverage_selection_by_zone.get(zone.zone_id)
            query_snapshot = (
                coverage_selection.partition.snapshot
                if coverage_selection is not None
                else overture_snapshot
            )
            query_text = f"{category.name_snapshot} en {zone.name_snapshot}"
            criteria = {
                "category_rules": category.rules_snapshot,
                "rules_revision": category.rules_revision_snapshot,
                "zone_boundary_hash": zone.boundary_hash_snapshot,
                "zone_bbox": zone.boundary_bbox_snapshot,
                "minimum_confidence": str(campaign.overture_min_confidence),
                "snapshot_id": str(query_snapshot.pk) if query_snapshot else None,
                "partition_id": (
                    str(coverage_selection.partition_id) if coverage_selection else None
                ),
                "release_id": query_snapshot.release_id if query_snapshot else "fake",
            }
            query_identity = {
                "category_selection_id": str(category.pk),
                "zone_selection_id": str(zone.pk),
                "criteria": criteria,
            }
            identity_hash = hashlib.sha256(
                json.dumps(
                    query_identity,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            queries.append(
                SearchQuery(
                    campaign=campaign,
                    coverage_selection=coverage_selection,
                    category_snapshot=category.name_snapshot,
                    zone_snapshot=zone.name_snapshot,
                    location_snapshot=zone.location_snapshot,
                    query_text=query_text,
                    # Keep the readable label separate from the structured query
                    # identity. In particular, this cannot collide with a
                    # preserved legacy provider query that used the same label.
                    normalized_query=f"structured-overture-rules-v1:{identity_hash}",
                    criteria_json=criteria,
                    zone_boundary_hash=zone.boundary_hash_snapshot,
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
        .select_related("catalog", "created_by", "workspace")
        .get(pk=campaign_id)
    )
    if target_state not in Campaign.State.values:
        raise InvalidCampaignTransition("El estado solicitado no existe.")
    if target_state not in ALLOWED_TRANSITIONS[campaign.state]:
        raise InvalidCampaignTransition(f"Transición inválida: {campaign.state} → {target_state}.")
    if actor is not None:
        require_user_capability(
            actor,
            Capability.MANAGE_CAMPAIGNS,
            workspace_id=campaign.workspace_id,
        )

    before = {"state": campaign.state, "discovery_state": campaign.discovery_state}
    if target_state in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}:
        if campaign.state == Campaign.State.DRAFT:
            _refresh_draft_category_rules(campaign)
            _refresh_missing_legacy_draft_boundaries(campaign)
        profile, overture_snapshot, coverages = _preflight(campaign)
        if campaign.state == Campaign.State.DRAFT:
            from apps.campaigns.approval import freeze_campaign_content

            freeze_campaign_content(campaign)
            _freeze_draft(campaign, profile, overture_snapshot, coverages)
            campaign.discovery_state = Campaign.DiscoveryState.RUNNING
            campaign.started_at = timezone.now()
        elif campaign.state == Campaign.State.AWAITING_APPROVAL and campaign.approved_at is None:
            raise InvalidCampaignTransition(
                "Aprobá la audiencia y el contenido antes de iniciar los envíos."
            )
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
            state__in=(
                OutboundMessage.State.PREPARED,
                OutboundMessage.State.REVIEW_READY,
                OutboundMessage.State.QUEUED,
            ),
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
    if actor is not None:
        require_user_capability(
            actor,
            Capability.MANAGE_CAMPAIGNS,
            workspace_id=campaign.workspace_id,
        )
    if campaign.state not in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}:
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
    if campaign.state == Campaign.State.DISCOVERING:
        from apps.campaigns.approval import maybe_move_campaign_to_approval

        transaction.on_commit(lambda: maybe_move_campaign_to_approval(campaign.pk))
    return campaign
