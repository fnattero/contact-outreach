from __future__ import annotations

import importlib
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

LEGACY_TARGETS = [
    ("audit", "0002_backgroundjob"),
    ("catalogs", "0001_initial"),
    ("configuration", "0005_integrationconfiguration_website_fetcher"),
    ("overture", "0001_initial"),
    ("campaigns", "0009_review_only_mode"),
]
OVERTURE_TARGETS = [
    ("audit", "0002_backgroundjob"),
    ("catalogs", "0001_initial"),
    ("configuration", "0007_seed_caba_boundaries"),
    ("overture", "0001_initial"),
    ("campaigns", "0010_overture_campaign_snapshots"),
]
RETIRED_REASON = "provider_retired: el proveedor histórico fue retirado; no se reintentará"


def _migrate(targets: list[tuple[str, str]]) -> Any:
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _latest_targets() -> list[tuple[str, str]]:
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


def _campaign(Campaign: Any, *, owner: Any, catalog: Any, name: str, state: str) -> Any:
    now = timezone.now()
    terminal = state in {"STOPPED_ERROR", "COMPLETED"}
    return Campaign.objects.create(
        name=name,
        state=state,
        discovery_state={
            "DRAFT": "PENDING",
            "RUNNING": "RUNNING",
            "PAUSED": "RUNNING",
            "STOPPED_ERROR": "FAILED_PROVIDER",
            "COMPLETED": "TARGET_REACHED",
        }[state],
        discovery_stop_reason="legacy stop reason" if terminal else "",
        status_reason="legacy status reason" if terminal else "",
        extractor_provider="outscraper",
        cost_limit=Decimal("19.75"),
        cost_currency="USD",
        catalog_id=catalog.pk,
        created_by_id=owner.pk,
        started_at=None if state == "DRAFT" else now,
        finished_at=now if terminal else None,
    )


def _query_and_run(
    SearchQuery: Any,
    SearchRun: Any,
    *,
    campaign: Any,
    suffix: str,
    state: str,
    response_json: dict[str, object] | None = None,
) -> tuple[Any, Any]:
    query = SearchQuery.objects.create(
        campaign_id=campaign.pk,
        category_snapshot="Autoelectricidad",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text=f"consulta histórica {suffix}",
        normalized_query=f"consulta-historica-{suffix}",
        state=state,
    )
    run = SearchRun.objects.create(
        campaign_id=campaign.pk,
        query_id=query.pk,
        provider="outscraper",
        idempotency_key=f"legacy-{suffix}-{uuid.uuid4()}",
        provider_request_id=f"request-{suffix}-{uuid.uuid4()}",
        request_json={"query": query.query_text, "limit": 20},
        response_json=response_json or {},
        response_hash=f"response-hash-{suffix}" if response_json else "",
        state=state,
        requested_limit=20,
        attempts=2,
        raw_count=7,
        email_count=3,
        no_email_count=4,
        cost_reserved=Decimal("0.070000"),
        cost_actual=Decimal("0.065000") if state == "SUCCEEDED" else None,
        next_poll_at=timezone.now() if state in {"PENDING", "RUNNING", "RETRY_WAIT"} else None,
        error="legacy provider failure" if state == "FAILED_PERMANENT" else "",
        finished_at=timezone.now() if state in {"SUCCEEDED", "FAILED_PERMANENT"} else None,
    )
    return query, run


@pytest.mark.django_db
def test_fresh_database_has_only_overture_configuration_and_seeded_search_data() -> None:
    from apps.configuration.models import IntegrationConfiguration, SearchCategory, SearchZone
    from apps.overture.models import OvertureDatasetSnapshot

    integration_fields = {field.name for field in IntegrationConfiguration._meta.get_fields()}
    assert "outscraper_api_key_encrypted" not in integration_fields
    assert "outscraper_api_key_source" not in integration_fields
    assert "outscraper_base_url" not in integration_fields
    assert IntegrationConfiguration._meta.get_field("extractor_provider").default == "fake"
    assert IntegrationConfiguration._meta.get_field("overture_min_confidence").default == Decimal(
        "0.750"
    )

    snapshot_fields = {field.name for field in OvertureDatasetSnapshot._meta.get_fields()}
    assert {
        "release_id",
        "schema_version",
        "taxonomy_version",
        "importer_version",
        "mapping_version",
        "boundary_manifest_sha256",
        "source_licenses",
        "validation_results",
    } <= snapshot_fields
    assert SearchCategory.objects.filter(archived_at__isnull=True).count() == 23
    assert SearchCategory.objects.exclude(rules__active=True).count() == 0
    assert SearchZone.objects.filter(kind="NEIGHBORHOOD", archived_at__isnull=True).count() == 48
    assert not SearchZone.objects.filter(
        kind="NEIGHBORHOOD",
        archived_at__isnull=True,
        boundary_hash="",
    ).exists()


def test_configuration_cutover_clears_ciphertext_before_removing_legacy_fields() -> None:
    migration = importlib.import_module(
        "apps.configuration.migrations.0006_overture_search_configuration"
    )
    integration_model = MagicMock()
    category_model = MagicMock()
    category_model.objects.filter.return_value = []
    historical_apps = MagicMock()
    historical_apps.get_model.side_effect = [
        integration_model,
        category_model,
        MagicMock(),
    ]

    migration.cut_over_configuration(historical_apps, None)

    integration_model.objects.update.assert_called_once_with(
        outscraper_api_key_encrypted="",
        outscraper_api_key_source="NONE",
    )
    run_python_index = next(
        index
        for index, operation in enumerate(migration.Migration.operations)
        if getattr(operation, "code", None) is migration.cut_over_configuration
    )
    removed_credential_indexes = [
        index
        for index, operation in enumerate(migration.Migration.operations)
        if getattr(operation, "name", "")
        in {"outscraper_api_key_encrypted", "outscraper_api_key_source"}
    ]
    assert removed_credential_indexes
    assert all(run_python_index < index for index in removed_credential_indexes)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_upgrade_retires_unfinished_legacy_work_and_preserves_terminal_history() -> None:
    old_apps = _migrate(LEGACY_TARGETS)
    try:
        User = old_apps.get_model("auth", "User")
        BackgroundJob = old_apps.get_model("audit", "BackgroundJob")
        Catalog = old_apps.get_model("catalogs", "Catalog")
        Campaign = old_apps.get_model("campaigns", "Campaign")
        CategorySelection = old_apps.get_model("campaigns", "CampaignCategorySelection")
        ZoneSelection = old_apps.get_model("campaigns", "CampaignZoneSelection")
        ProviderUsage = old_apps.get_model("campaigns", "ProviderUsage")
        SearchQuery = old_apps.get_model("campaigns", "SearchQuery")
        SearchRun = old_apps.get_model("campaigns", "SearchRun")
        IntegrationConfiguration = old_apps.get_model("configuration", "IntegrationConfiguration")
        SearchCategory = old_apps.get_model("configuration", "SearchCategory")
        SearchZone = old_apps.get_model("configuration", "SearchZone")

        owner = User.objects.create(username="legacy-owner")
        catalog = Catalog.objects.create(
            name="Catálogo histórico",
            version=1,
            file="catalogs/legacy.pdf",
            original_filename="legacy.pdf",
            detected_mime="application/pdf",
            byte_size=10,
            sha256="a" * 64,
            uploaded_by_id=owner.pk,
        )
        configuration = IntegrationConfiguration.objects.create(
            owner_id=owner.pk,
            extractor_provider="outscraper",
            outscraper_api_key_encrypted="encrypted-secret-that-must-not-survive",
            outscraper_api_key_source="ENCRYPTED",
        )
        category, _ = SearchCategory.objects.get_or_create(
            normalized_name="autoelectricidad",
            archived_at=None,
            defaults={"name": "Autoelectricidad", "active": True},
        )
        zone, _ = SearchZone.objects.get_or_create(
            normalized_name="palermo",
            archived_at=None,
            defaults={
                "name": "Palermo",
                "kind": "NEIGHBORHOOD",
                "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
                "active": True,
            },
        )

        draft = _campaign(
            Campaign,
            owner=owner,
            catalog=catalog,
            name="Borrador histórico",
            state="DRAFT",
        )
        running = _campaign(
            Campaign,
            owner=owner,
            catalog=catalog,
            name="Campaña iniciada",
            state="RUNNING",
        )
        paused = _campaign(
            Campaign,
            owner=owner,
            catalog=catalog,
            name="Campaña pausada",
            state="PAUSED",
        )
        failed = _campaign(
            Campaign,
            owner=owner,
            catalog=catalog,
            name="Campaña fallida histórica",
            state="STOPPED_ERROR",
        )
        completed = _campaign(
            Campaign,
            owner=owner,
            catalog=catalog,
            name="Campaña completada histórica",
            state="COMPLETED",
        )
        for campaign in (draft, running, paused, failed, completed):
            CategorySelection.objects.create(
                campaign_id=campaign.pk,
                category_id=category.pk,
                name_snapshot=category.name,
                normalized_name_snapshot=category.normalized_name,
            )
            ZoneSelection.objects.create(
                campaign_id=campaign.pk,
                zone_id=zone.pk,
                name_snapshot=zone.name,
                location_snapshot=zone.location_text,
            )

        unfinished: list[tuple[Any, Any]] = []
        for index, state in enumerate(("PENDING", "RUNNING", "RETRY_WAIT")):
            query, run = _query_and_run(
                SearchQuery,
                SearchRun,
                campaign=running,
                suffix=f"unfinished-{index}",
                state=state,
            )
            BackgroundJob.objects.create(
                task_name="campaigns.advance_search_run",
                idempotency_key=f"job-{run.pk}",
                entity_type="SearchRun",
                entity_id=str(run.pk),
                state=state if state != "RETRY_WAIT" else "RETRY_WAIT",
                next_retry_at=timezone.now(),
            )
            unfinished.append((query, run))

        paused_query, paused_run = _query_and_run(
            SearchQuery,
            SearchRun,
            campaign=paused,
            suffix="paused-running",
            state="RUNNING",
        )
        BackgroundJob.objects.create(
            task_name="campaigns.advance_search_run",
            idempotency_key=f"job-{paused_run.pk}",
            entity_type="SearchRun",
            entity_id=str(paused_run.pk),
            state="RUNNING",
        )
        unfinished.append((paused_query, paused_run))
        orphaned_query = SearchQuery.objects.create(
            campaign_id=running.pk,
            category_snapshot="Autoelectricidad",
            zone_snapshot="Belgrano",
            location_snapshot="CABA",
            query_text="consulta histórica todavía sin run",
            normalized_query="consulta-historica-todavia-sin-run",
            state="PENDING",
        )
        draft_orphaned_query = SearchQuery.objects.create(
            campaign_id=draft.pk,
            category_snapshot="Autoelectricidad",
            zone_snapshot="Palermo",
            location_snapshot="CABA",
            query_text="consulta de borrador sin run",
            normalized_query="consulta-de-borrador-sin-run",
            state="RETRY_WAIT",
        )

        failed_query, failed_run = _query_and_run(
            SearchQuery,
            SearchRun,
            campaign=failed,
            suffix="already-failed",
            state="FAILED_PERMANENT",
            response_json={"status": "legacy failure", "raw": [1, 2]},
        )
        completed_query, completed_run = _query_and_run(
            SearchQuery,
            SearchRun,
            campaign=completed,
            suffix="completed",
            state="SUCCEEDED",
            response_json={"status": "ok", "raw": [{"name": "Preservar"}]},
        )
        usage = ProviderUsage.objects.create(
            campaign_id=completed.pk,
            run_id=completed_run.pk,
            provider="outscraper",
            operation="maps_search",
            units=Decimal("7"),
            estimated_cost=Decimal("0.070000"),
            actual_cost=Decimal("0.065000"),
            currency="USD",
            request_id=completed_run.provider_request_id,
            metadata={"legacy": True},
        )
        completed_job = BackgroundJob.objects.create(
            task_name="campaigns.advance_search_run",
            idempotency_key=f"job-{completed_run.pk}",
            entity_type="SearchRun",
            entity_id=str(completed_run.pk),
            state="SUCCEEDED",
            finished_at=timezone.now(),
        )
        failed_before = {
            "state": failed.state,
            "discovery_state": failed.discovery_state,
            "status_reason": failed.status_reason,
            "finished_at": failed.finished_at,
        }
        completed_before = {
            "state": completed.state,
            "discovery_state": completed.discovery_state,
            "status_reason": completed.status_reason,
            "finished_at": completed.finished_at,
        }

        new_apps = _migrate(OVERTURE_TARGETS)
        NewAuditEvent = new_apps.get_model("audit", "AuditEvent")
        NewBackgroundJob = new_apps.get_model("audit", "BackgroundJob")
        NewCampaign = new_apps.get_model("campaigns", "Campaign")
        NewCategorySelection = new_apps.get_model("campaigns", "CampaignCategorySelection")
        NewZoneSelection = new_apps.get_model("campaigns", "CampaignZoneSelection")
        NewProviderUsage = new_apps.get_model("campaigns", "ProviderUsage")
        NewSearchQuery = new_apps.get_model("campaigns", "SearchQuery")
        NewSearchRun = new_apps.get_model("campaigns", "SearchRun")
        NewIntegrationConfiguration = new_apps.get_model(
            "configuration", "IntegrationConfiguration"
        )

        migrated_configuration = NewIntegrationConfiguration.objects.get(pk=configuration.pk)
        assert migrated_configuration.extractor_provider == "overture"
        assert migrated_configuration.overture_min_confidence == Decimal("0.750")
        configuration_fields = {
            field.name for field in NewIntegrationConfiguration._meta.get_fields()
        }
        assert "outscraper_api_key_encrypted" not in configuration_fields
        assert "outscraper_api_key_source" not in configuration_fields

        migrated_draft = NewCampaign.objects.get(pk=draft.pk)
        assert migrated_draft.state == "DRAFT"
        assert migrated_draft.extractor_provider == "overture"
        assert migrated_draft.cost_limit == Decimal("0")
        assert migrated_draft.cost_currency == "USD"
        assert migrated_draft.overture_snapshot_id is None
        assert NewAuditEvent.objects.filter(
            action="campaign.extractor_migrated",
            entity_id=str(draft.pk),
            before={"extractor_provider": "outscraper"},
            after={"extractor_provider": "overture", "reason": "provider_retired"},
        ).exists()

        draft_category = NewCategorySelection.objects.get(campaign_id=draft.pk)
        assert draft_category.rules_revision_snapshot == 1
        assert draft_category.rules_snapshot
        assert any(
            rule["taxonomy_code"] == "services_and_business"
            for rule in draft_category.rules_snapshot
        )
        draft_zone = NewZoneSelection.objects.get(campaign_id=draft.pk)
        assert draft_zone.boundary_revision_snapshot == 1
        assert draft_zone.boundary_hash_snapshot
        assert len(draft_zone.boundary_bbox_snapshot) == 4
        assert draft_zone.boundary_geojson_snapshot["type"] in {"Polygon", "MultiPolygon"}

        for legacy in (running, paused):
            migrated = NewCampaign.objects.get(pk=legacy.pk)
            assert migrated.extractor_provider == "outscraper"
            assert migrated.state == "STOPPED_ERROR"
            assert migrated.discovery_state == "FAILED_PROVIDER"
            assert migrated.status_reason == RETIRED_REASON
            assert migrated.discovery_stop_reason == RETIRED_REASON
            assert migrated.finished_at is not None
            event = NewAuditEvent.objects.get(
                action="campaign.provider_retired",
                entity_id=str(legacy.pk),
            )
            assert event.before == {
                "state": legacy.state,
                "discovery_state": legacy.discovery_state,
            }
            assert event.after == {
                "state": "STOPPED_ERROR",
                "discovery_state": "FAILED_PROVIDER",
                "reason": RETIRED_REASON,
            }

        for old_query, old_run in unfinished:
            migrated_query = NewSearchQuery.objects.get(pk=old_query.pk)
            migrated_run = NewSearchRun.objects.get(pk=old_run.pk)
            migrated_job = NewBackgroundJob.objects.get(
                entity_type="SearchRun", entity_id=str(old_run.pk)
            )
            assert migrated_query.state == "FAILED_PERMANENT"
            assert migrated_query.last_error == RETIRED_REASON
            assert migrated_query.criteria_json == {}
            assert migrated_query.zone_boundary_hash == ""
            assert migrated_run.state == "FAILED_PERMANENT"
            assert migrated_run.error == RETIRED_REASON
            assert migrated_run.next_poll_at is None
            assert migrated_run.finished_at is not None
            assert migrated_run.overture_snapshot_id is None
            assert migrated_job.state == "FAILED"
            assert migrated_job.error == RETIRED_REASON
            assert migrated_job.next_retry_at is None
            assert migrated_job.finished_at is not None
            run_event = NewAuditEvent.objects.get(
                action="search_run.provider_retired",
                entity_id=str(old_run.pk),
            )
            assert run_event.before == {
                "state": old_run.state,
                "provider": "outscraper",
            }
            assert run_event.after == {
                "state": "FAILED_PERMANENT",
                "reason": RETIRED_REASON,
            }
            query_event = NewAuditEvent.objects.get(
                action="search_query.provider_retired",
                entity_id=str(old_query.pk),
            )
            assert query_event.before == {"state": old_query.state}
            assert query_event.after == {
                "state": "FAILED_PERMANENT",
                "reason": RETIRED_REASON,
            }

        for old_query in (orphaned_query, draft_orphaned_query):
            migrated_orphaned_query = NewSearchQuery.objects.get(pk=old_query.pk)
            assert migrated_orphaned_query.state == "FAILED_PERMANENT"
            assert migrated_orphaned_query.last_error == RETIRED_REASON
            orphaned_query_event = NewAuditEvent.objects.get(
                action="search_query.provider_retired",
                entity_id=str(old_query.pk),
            )
            assert orphaned_query_event.before == {"state": old_query.state}
            assert orphaned_query_event.after == {
                "state": "FAILED_PERMANENT",
                "reason": RETIRED_REASON,
            }

        migrated_failed = NewCampaign.objects.get(pk=failed.pk)
        assert {
            "state": migrated_failed.state,
            "discovery_state": migrated_failed.discovery_state,
            "status_reason": migrated_failed.status_reason,
            "finished_at": migrated_failed.finished_at,
        } == failed_before
        preserved_failed_query = NewSearchQuery.objects.get(pk=failed_query.pk)
        preserved_failed_run = NewSearchRun.objects.get(pk=failed_run.pk)
        assert preserved_failed_query.state == "FAILED_PERMANENT"
        assert preserved_failed_run.error == "legacy provider failure"
        assert preserved_failed_run.response_json == {
            "status": "legacy failure",
            "raw": [1, 2],
        }

        migrated_completed = NewCampaign.objects.get(pk=completed.pk)
        assert {
            "state": migrated_completed.state,
            "discovery_state": migrated_completed.discovery_state,
            "status_reason": migrated_completed.status_reason,
            "finished_at": migrated_completed.finished_at,
        } == completed_before
        assert migrated_completed.extractor_provider == "outscraper"
        preserved_query = NewSearchQuery.objects.get(pk=completed_query.pk)
        preserved_run = NewSearchRun.objects.get(pk=completed_run.pk)
        preserved_usage = NewProviderUsage.objects.get(pk=usage.pk)
        assert preserved_query.state == "SUCCEEDED"
        assert preserved_run.state == "SUCCEEDED"
        assert preserved_run.provider == "outscraper"
        assert preserved_run.response_json == {
            "status": "ok",
            "raw": [{"name": "Preservar"}],
        }
        assert preserved_run.response_hash == "response-hash-completed"
        assert preserved_run.cost_actual == Decimal("0.065000")
        assert preserved_run.overture_snapshot_id is None
        assert preserved_usage.provider == "outscraper"
        assert preserved_usage.operation == "maps_search"
        assert preserved_usage.actual_cost == Decimal("0.065000")
        assert preserved_usage.metadata == {"legacy": True}
        assert NewBackgroundJob.objects.get(pk=completed_job.pk).state == "SUCCEEDED"
        assert not NewAuditEvent.objects.filter(
            action="campaign.provider_retired",
            entity_id__in=(str(failed.pk), str(completed.pk)),
        ).exists()

        for campaign in (running, paused, failed, completed):
            selection = NewCategorySelection.objects.get(campaign_id=campaign.pk)
            assert selection.rules_revision_snapshot == 1
            assert selection.rules_snapshot == []
            zone_selection = NewZoneSelection.objects.get(campaign_id=campaign.pk)
            assert zone_selection.boundary_revision_snapshot == 1
            assert zone_selection.boundary_hash_snapshot == ""
            assert zone_selection.boundary_geojson_snapshot == {}
    finally:
        _migrate(_latest_targets())
