from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.campaigns import services as campaign_services
from apps.campaigns.forms import CampaignForm
from apps.campaigns.models import (
    Campaign,
    CampaignCategorySelection,
    CampaignZoneSelection,
    SearchQuery,
)
from apps.campaigns.services import (
    InvalidCampaignTransition,
    create_campaign,
    finish_discovery,
    transition_campaign,
)
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog
from apps.configuration.models import (
    IntegrationConfiguration,
    SearchCategory,
    SearchZone,
    normalize_name,
)
from apps.configuration.services import (
    delete_or_archive_config_item,
    save_business_profile,
    save_config_item,
    save_prompt_configuration,
)
from apps.integrations.factory import get_website_fetcher
from apps.integrations.website import HttpWebsiteFetcher
from apps.overture.models import (
    OvertureCoveragePartition,
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OvertureRelease,
)


def profile_values() -> dict[str, object]:
    return {
        "company_name": "Carbones SA",
        "salesperson_name": "Fran",
        "phone": "",
        "whatsapp": "",
        "description": "",
        "products": "Carbones",
        "differentiators": "",
        "address": "CABA",
        "website": "",
        "signature": "Fran · Carbones SA",
        "additional_instructions": "",
        "relevance_threshold": 70,
    }


def make_catalog(owner: User) -> Catalog:
    upload = SimpleUploadedFile(
        "catalogo.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF", content_type="application/pdf"
    )
    return create_catalog(name="General", upload=upload, actor=owner)


def campaign_values(catalog: Catalog, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "CABA talleres",
        "delivery_mode": Campaign.DeliveryMode.DRY_RUN,
        "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
        "objective": 300,
        "max_raw_records": 3000,
        "cost_limit": Decimal("10.00"),
        "cost_currency": "USD",
        "daily_limit": 30,
        "message_interval_minutes": 5,
        "weekdays": [0, 1, 2, 3, 4],
        "window_start": time(9, 0),
        "window_end": time(17, 0),
        "timezone_name": "America/Argentina/Buenos_Aires",
        "relevance_threshold": 70,
        "extractor_provider": "fake",
        "overture_min_confidence": Decimal("0.750"),
        "website_fetcher": "fake",
        "llm_provider": "fake",
        "llm_base_url": "",
        "llm_model": "fake-deterministic",
        "catalog": catalog,
    }
    values.update(overrides)
    return values


def make_campaign(owner: User, catalog: Catalog, **overrides: object) -> Campaign:
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    return create_campaign(
        actor=owner,
        values=campaign_values(catalog, **overrides),
        category_ids=[category.pk],
        zone_ids=[zone.pk],
    )


def make_ready_partition(zone: SearchZone, release_id: str) -> OvertureCoveragePartition:
    province = zone.parent
    assert province is not None
    release, _ = OvertureRelease.objects.get_or_create(
        release_id=release_id,
        defaults={
            "schema_version": "v1.18.0",
            "taxonomy_version": release_id,
            "importer_version": "fixture-importer",
            "mapping_version": "fixture-mapping",
            "source_uri": (
                f"s3://overturemaps-us-west-2/release/{release_id}/theme=places/type=place/"
            ),
            "catalog_url": f"https://stac.overturemaps.org/{release_id}/catalog.json",
            "manifest_sha256": "a" * 64,
            "metadata_sha256": "b" * 64,
        },
    )
    snapshot = OvertureDatasetSnapshot.objects.create(
        release_id=release_id,
        release_record=release,
        province_code=province.official_code,
        province_name=province.name,
        province_bbox=province.boundary_bbox,
        schema_version="v1.18.0",
        taxonomy_version=release_id,
        importer_version="fixture-importer",
        mapping_version="fixture-mapping",
        boundary_version=f"fixture-{province.official_code}",
        boundary_manifest_sha256="c" * 64,
        source_uri=release.source_uri,
        manifest_sha256=release.manifest_sha256,
        status=OvertureDatasetSnapshot.Status.IMPORTING,
        zone_count=1,
    )
    partition = OvertureCoveragePartition.objects.create(
        release=release,
        province=province,
        province_code=province.official_code,
        province_name=province.name,
        province_bbox=province.boundary_bbox,
        snapshot=snapshot,
        status=OvertureCoveragePartition.Status.IMPORTING,
        boundary_version=snapshot.boundary_version,
        boundary_manifest_sha256=snapshot.boundary_manifest_sha256,
        zone_count=1,
    )
    OvertureDatasetZone.objects.create(
        snapshot=snapshot,
        partition=partition,
        search_zone=zone,
        code=zone.official_code,
        name=zone.name,
        normalized_name=zone.normalized_name,
        geometry=zone.boundary_geojson,
        bbox=zone.boundary_bbox,
        boundary_hash=zone.boundary_hash,
        source=zone.boundary_source,
        source_version=f"revision-{zone.boundary_revision}",
        attribution=zone.boundary_attribution,
    )
    snapshot.status = OvertureDatasetSnapshot.Status.READY
    snapshot.is_active = True
    snapshot.save(update_fields=("status", "is_active", "updated_at"))
    partition.status = OvertureCoveragePartition.Status.READY
    partition.is_active = True
    partition.save(update_fields=("status", "is_active", "updated_at"))
    return partition


@pytest.mark.django_db
def test_campaign_form_defaults_and_validates_limits() -> None:
    form = CampaignForm()
    assert form.fields["objective"].initial == 300
    assert Campaign.DeliveryMode.REVIEW_ONLY in dict(form.fields["delivery_mode"].choices)
    campaign = Campaign(
        objective=400,
        max_raw_records=300,
        window_start=time(18),
        window_end=time(9),
        weekdays=[],
        timezone_name="Invalid/Zone",
    )
    with pytest.raises(ValidationError) as error:
        campaign.clean()
    assert {"max_raw_records", "window_end", "weekdays", "timezone_name"} <= set(
        error.value.message_dict
    )


@pytest.mark.django_db
def test_create_campaign_selects_snapshots_and_custom_objective(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = make_catalog(owner)
    campaign = make_campaign(owner, catalog, objective=425)
    assert campaign.state == Campaign.State.DRAFT
    assert campaign.objective == 425
    assert campaign.category_selections.get().name_snapshot == "Bobinados de motores"
    assert campaign.category_selections.get().rules_snapshot
    assert campaign.zone_selections.get().name_snapshot == "Palermo"
    assert campaign.zone_selections.get().boundary_hash_snapshot
    assert AuditEvent.objects.filter(action="campaign.created").exists()


@pytest.mark.django_db
def test_campaign_selection_snapshots_are_immutable_after_start(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    category = campaign.category_selections.get()
    zone = campaign.zone_selections.get()

    category.sort_order = 1
    assert CampaignCategorySelection.objects.bulk_update([category], ("sort_order",)) == 1
    category.refresh_from_db()
    assert category.sort_order == 1
    category.sort_order = 0
    category.save(update_fields=("sort_order", "updated_at"))

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.RUNNING,
        actor=owner,
    )

    for selection in (category, zone):
        model = type(selection)
        original_order = selection.sort_order
        selection.sort_order = original_order + 1
        with pytest.raises(ValidationError, match="inmutables"):
            selection.save(update_fields=("sort_order", "updated_at"))
        selection.refresh_from_db()
        assert selection.sort_order == original_order

        with pytest.raises(ValidationError, match="inmutables"):
            model.objects.filter(pk=selection.pk).update(sort_order=original_order + 1)
        with pytest.raises(ValidationError, match="inmutables"):
            model.objects.filter(pk=selection.pk).delete()

        selection.sort_order = original_order + 1
        with pytest.raises(ValidationError, match="inmutables"):
            model.objects.bulk_update([selection], ("sort_order",))
        with pytest.raises(ValidationError, match="inmutables"):
            selection.delete()

    with pytest.raises(ValidationError, match="inmutables"):
        CampaignCategorySelection.objects.bulk_create(
            [
                CampaignCategorySelection(
                    campaign=campaign,
                    category_id=category.category_id,
                    name_snapshot=category.name_snapshot,
                    normalized_name_snapshot=category.normalized_name_snapshot,
                    rules_snapshot=category.rules_snapshot,
                    rules_revision_snapshot=category.rules_revision_snapshot,
                )
            ]
        )
    with pytest.raises(ValidationError, match="inmutables"):
        CampaignZoneSelection.objects.bulk_create(
            [
                CampaignZoneSelection(
                    campaign=campaign,
                    zone_id=zone.zone_id,
                    name_snapshot=zone.name_snapshot,
                    location_snapshot=zone.location_snapshot,
                    boundary_geojson_snapshot=zone.boundary_geojson_snapshot,
                    boundary_bbox_snapshot=zone.boundary_bbox_snapshot,
                    boundary_hash_snapshot=zone.boundary_hash_snapshot,
                    boundary_revision_snapshot=zone.boundary_revision_snapshot,
                )
            ]
        )


@pytest.mark.django_db
def test_overture_campaign_requires_a_ready_coverage_snapshot(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = make_catalog(owner)
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, catalog, extractor_provider="overture")
    with pytest.raises(ValidationError) as error:
        transition_campaign(
            campaign_id=campaign.pk,
            target_state=Campaign.State.RUNNING,
            actor=owner,
        )
    assert str(error.value) == (
        "['Faltan los datos de Ciudad Autónoma de Buenos Aires. "
        "Actualizalos desde Datos de búsqueda.']"
    )


@pytest.mark.django_db
def test_overture_campaign_rejects_mixed_province_releases(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    catalog = make_catalog(owner)
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zones = [
        SearchZone.objects.filter(
            level=SearchZone.Level.DISTRICT,
            province_code=province_code,
            selectable=True,
        ).first()
        for province_code in ("38", "50")
    ]
    assert all(zone is not None for zone in zones)
    selected_zones = [zone for zone in zones if zone is not None]
    make_ready_partition(selected_zones[0], "2026-06-17.0")
    make_ready_partition(selected_zones[1], "2026-07-22.0")
    campaign = create_campaign(
        actor=owner,
        values=campaign_values(catalog, extractor_provider="overture"),
        category_ids=[category.pk],
        zone_ids=[zone.pk for zone in selected_zones],
    )

    with pytest.raises(ValidationError, match="versiones distintas"):
        campaign_services._resolve_draft_coverages(campaign)


@pytest.mark.django_db
def test_campaign_queries_pin_each_district_to_its_province_partition(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    catalog = make_catalog(owner)
    category = SearchCategory.objects.get(name="Bobinados de motores")
    by_province: dict[str, SearchZone] = {}
    for zone in SearchZone.objects.filter(
        level=SearchZone.Level.DISTRICT,
        province_code__in=("38", "50"),
        selectable=True,
    ).order_by("province_code", "sort_order"):
        by_province.setdefault(zone.province_code, zone)
    zones = list(by_province.values())
    assert len(zones) == 2
    partitions = {zone.pk: make_ready_partition(zone, "2026-07-22.0") for zone in zones}
    campaign = create_campaign(
        actor=owner,
        values=campaign_values(catalog, extractor_provider="overture"),
        category_ids=[category.pk],
        zone_ids=[zone.pk for zone in zones],
    )

    started = transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.DISCOVERING,
        actor=owner,
    )

    assert started.overture_release.release_id == "2026-07-22.0"
    assert started.coverage_selections.count() == 2
    queries = list(started.search_queries.select_related("coverage_selection"))
    assert len(queries) == 2
    for query in queries:
        coverage = query.coverage_selection
        assert coverage is not None
        assert coverage.partition_id == partitions[coverage.district_id].pk
        assert query.criteria_json["partition_id"] == str(coverage.partition_id)
        assert query.criteria_json["snapshot_id"] == str(coverage.partition.snapshot_id)


@pytest.mark.django_db
def test_campaign_requires_active_selections(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    catalog = make_catalog(owner)
    with pytest.raises(ValidationError, match="rubro y un distrito"):
        create_campaign(
            actor=owner,
            values=campaign_values(catalog),
            category_ids=[],
            zone_ids=[],
        )


@pytest.mark.django_db
def test_start_freezes_profile_settings_and_deterministic_queries(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    catalog = make_catalog(owner)
    campaign = make_campaign(owner, catalog)
    save_prompt_configuration(
        owner=owner,
        email_drafting_prompt="Priorizá el contexto técnico disponible.",
    )
    category = campaign.category_selections.get().category
    category.name = "Nombre modificado"
    category.save()

    started = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
    )
    assert started.state == Campaign.State.RUNNING
    assert started.discovery_state == Campaign.DiscoveryState.RUNNING
    assert started.settings_snapshot["objective"] == 300
    assert started.settings_snapshot["website_fetcher"] == "fake"
    assert started.profile_snapshot["company_name"] == "Carbones SA"
    assert started.prompt_snapshot == {
        "version": "fixed-campaign-message-v1",
        "schema_version": "fixed-message-no-placeholders-v1",
        "initial_outreach": "fixed-no-llm",
        "llm_calls": 0,
        "search_mode": "structured-overture-rules-v1",
    }
    query = SearchQuery.objects.get(campaign=started)
    assert query.category_snapshot == "Bobinados de motores"
    assert query.query_text == "Bobinados de motores en Palermo"
    assert query.normalized_query.startswith("structured-overture-rules-v1:")
    assert query.criteria_json["category_rules"]
    assert query.zone_boundary_hash
    save_prompt_configuration(
        owner=owner,
        email_drafting_prompt="Este cambio no debe afectar la campaña iniciada.",
    )
    started.refresh_from_db()
    assert started.prompt_snapshot["initial_outreach"] == "fixed-no-llm"
    started.objective = 999
    with pytest.raises(ValidationError, match="inmutable"):
        started.save()


@pytest.mark.django_db
def test_start_does_not_collide_with_a_preserved_legacy_query_label(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    query_text = "Bobinados de motores en Palermo"
    legacy_query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Bobinados de motores",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text=query_text,
        normalized_query=normalize_name(query_text),
        state=SearchQuery.State.FAILED_PERMANENT,
        last_error="provider_retired",
    )

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.RUNNING,
        actor=owner,
    )

    queries = list(SearchQuery.objects.filter(campaign=campaign).order_by("created_at"))
    assert queries[0] == legacy_query
    assert queries[1].query_text == query_text
    assert queries[1].normalized_query.startswith("structured-overture-rules-v1:")


@pytest.mark.django_db
def test_start_refreshes_and_freezes_the_latest_active_category_rules(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    selection = campaign.category_selections.select_related("category").get()
    initial_revision = selection.rules_revision_snapshot
    category = selection.category

    save_config_item(
        item=category,
        actor=owner,
        category_rules=[
            {
                "taxonomy_code": "services_and_business",
                "name_terms": ["motor actualizado", "bobinad*"],
            }
        ],
    )

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.RUNNING,
        actor=owner,
    )

    selection.refresh_from_db()
    assert selection.rules_revision_snapshot == initial_revision + 1
    assert selection.rules_snapshot == [
        {
            "taxonomy_code": "services_and_business",
            "name_terms": ["motor actualizado", "bobinad*"],
        }
    ]
    query = SearchQuery.objects.get(campaign=campaign)
    assert query.criteria_json["rules_revision"] == selection.rules_revision_snapshot
    assert query.criteria_json["category_rules"] == selection.rules_snapshot

    save_config_item(
        item=category,
        actor=owner,
        category_rules=[{"taxonomy_code": "shopping", "name_terms": ["otro término"]}],
    )
    query.refresh_from_db()
    selection.refresh_from_db()
    assert query.criteria_json["category_rules"] == selection.rules_snapshot
    assert selection.rules_revision_snapshot == initial_revision + 1


@pytest.mark.django_db
def test_start_rejects_a_category_that_became_inactive(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    category = campaign.category_selections.get().category
    category.active = False
    category.save(update_fields=("active", "updated_at"))

    with pytest.raises(ValidationError, match="ya no está activo"):
        transition_campaign(
            campaign_id=campaign.pk,
            target_state=Campaign.State.RUNNING,
            actor=owner,
        )


@pytest.mark.django_db
def test_start_rejects_a_category_without_active_rules(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    campaign.category_selections.get().category.rules.all().delete()

    with pytest.raises(ValidationError, match="reglas Overture activas"):
        transition_campaign(
            campaign_id=campaign.pk,
            target_state=Campaign.State.RUNNING,
            actor=owner,
        )


@pytest.mark.django_db
def test_start_backfills_only_a_missing_pre_overture_draft_boundary(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    selection = campaign.zone_selections.select_related("zone").get()
    expected_hash = selection.zone.boundary_hash
    expected_revision = selection.zone.boundary_revision
    campaign.zone_selections.update(
        boundary_geojson_snapshot={},
        boundary_bbox_snapshot=[],
        boundary_hash_snapshot="",
    )

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.RUNNING,
        actor=owner,
    )

    selection.refresh_from_db()
    assert selection.boundary_hash_snapshot == expected_hash
    assert selection.boundary_revision_snapshot == expected_revision
    assert selection.boundary_geojson_snapshot
    assert AuditEvent.objects.filter(
        action="campaign.legacy_boundaries_refreshed",
        entity_id=str(campaign.pk),
    ).exists()


@pytest.mark.django_db
def test_campaign_pause_resume_discovery_finish_and_complete(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    transition_campaign(campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner)
    paused = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.PAUSED, actor=owner, reason="Revisión"
    )
    assert paused.status_reason == "Revisión"
    resumed = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
    )
    assert resumed.state == Campaign.State.RUNNING
    finished = finish_discovery(
        campaign_id=campaign.pk,
        target_state=Campaign.DiscoveryState.EXHAUSTED_RAW_LIMIT,
        reason="max_raw_records=3000",
        actor=None,
    )
    assert finished.discovery_stop_reason == "max_raw_records=3000"
    completed = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.COMPLETED, actor=None
    )
    assert completed.finished_at is not None


@pytest.mark.django_db
def test_invalid_campaign_and_discovery_transitions_are_rejected(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    with pytest.raises(InvalidCampaignTransition, match="Transición inválida"):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.PAUSED, actor=owner
        )
    with pytest.raises(InvalidCampaignTransition, match="descubrimiento"):
        finish_discovery(
            campaign_id=campaign.pk,
            target_state=Campaign.DiscoveryState.TARGET_REACHED,
            reason="objetivo",
            actor=owner,
        )
    cancelled = transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.CANCELLED, actor=owner
    )
    assert cancelled.state == Campaign.State.CANCELLED
    with pytest.raises(InvalidCampaignTransition):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
        )


@pytest.mark.django_db
def test_start_rejects_missing_profile_and_live_safety_barriers(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    with pytest.raises(ValidationError, match="perfil comercial"):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
        )
    save_business_profile(owner=owner, values=profile_values())
    campaign.delivery_mode = Campaign.DeliveryMode.LIVE
    campaign.save(update_fields=("delivery_mode", "updated_at"))
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=True):
        with pytest.raises(ValidationError, match="bloqueado"):
            transition_campaign(
                campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
            )
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        with pytest.raises(ValidationError, match="conexión Gmail"):
            transition_campaign(
                campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
            )
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.DRAFT


@pytest.mark.django_db
def test_review_only_starts_with_global_sending_blocked_and_without_gmail(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(
        owner,
        make_catalog(owner),
        delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY,
    )

    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        started = transition_campaign(
            campaign_id=campaign.pk,
            target_state=Campaign.State.RUNNING,
            actor=owner,
        )

    assert started.state == Campaign.State.RUNNING
    assert started.settings_snapshot["delivery_mode"] == Campaign.DeliveryMode.REVIEW_ONLY


@pytest.mark.django_db
def test_start_rejects_tampered_catalog(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    campaign = make_campaign(owner, make_catalog(owner))
    with open(campaign.catalog.file.path, "ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValidationError, match="integridad"):
        transition_campaign(
            campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
        )


@pytest.mark.django_db
def test_referenced_category_is_archived_instead_of_deleted(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    category = campaign.category_selections.get().category
    result = delete_or_archive_config_item(model=SearchCategory, item_id=category.pk, actor=owner)
    category.refresh_from_db()
    assert result == "archived"
    assert category.archived_at is not None
    assert not category.active


@pytest.mark.django_db
@pytest.mark.e2e
def test_campaign_creation_and_actions_through_dashboard(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del private_catalog_dir
    save_business_profile(owner=owner, values=profile_values())
    IntegrationConfiguration.objects.create(owner=owner, website_fetcher="http")
    catalog = make_catalog(owner)
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    client.force_login(owner)
    response = client.post(
        reverse("campaign-create"),
        {
            "name": "Dashboard 450",
            "delivery_mode": Campaign.DeliveryMode.REVIEW_ONLY,
            "approval_mode": Campaign.ApprovalMode.CAMPAIGN,
            "reminder_delay_days": 3,
            "location_text": "CABA",
            "objective": 450,
            "max_raw_records": 4000,
            "overture_min_confidence": "0.900",
            "daily_limit": 25,
            "message_interval_minutes": 7,
            "weekdays": [0, 1, 2, 3, 4],
            "window_start": "09:00",
            "window_end": "17:00",
            "timezone_name": "America/Argentina/Buenos_Aires",
            "relevance_threshold": 80,
            "extractor_provider": "overture",
            "website_fetcher": "fake",
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://attacker.example/v1",
            "llm_model": "attacker-controlled",
            "catalog": catalog.pk,
            "catalogs": [catalog.pk],
            "categories": [category.pk],
            "provinces": [zone.parent_id],
            "zones": [zone.pk],
        },
    )
    assert response.status_code == 302
    campaign = Campaign.objects.get(name="Dashboard 450")
    assert campaign.objective == 450
    assert campaign.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY
    assert campaign.extractor_provider == "fake"
    assert campaign.website_fetcher == "http"
    assert isinstance(get_website_fetcher(campaign.website_fetcher), HttpWebsiteFetcher)
    assert campaign.llm_provider == "fake"
    assert campaign.llm_base_url == ""
    assert campaign.llm_model == "fake-deterministic"
    detail = client.get(reverse("campaign-detail", args=(campaign.pk,)))
    assert detail.status_code == 200
    assert b"Dashboard 450" in detail.content
    monkeypatch.setattr(
        "apps.campaigns.views.orchestrate_extraction.delay",
        lambda campaign_id: campaign_id,
    )
    started = client.post(reverse("campaign-action", args=(campaign.pk, "start")))
    assert started.status_code == 302
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.DISCOVERING
    assert campaign.search_queries.count() == 1


@pytest.mark.django_db
def test_campaign_views_require_login_and_mutations_require_csrf(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = make_campaign(owner, make_catalog(owner))
    anonymous = Client()
    assert anonymous.get(reverse("campaigns")).status_code == 302
    logged_in = Client()
    logged_in.force_login(owner)
    assert (
        logged_in.get(reverse("campaign-action", args=(campaign.pk, "cancel"))).status_code == 405
    )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert (
        csrf_client.post(reverse("campaign-action", args=(campaign.pk, "cancel"))).status_code
        == 403
    )
