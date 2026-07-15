from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.campaigns.extraction import advance_search_run, ensure_next_search_run
from apps.campaigns.models import Campaign, ProviderUsage, SearchQuery, SearchRun
from apps.campaigns.services import create_campaign, transition_campaign
from apps.campaigns.tasks import recover_extraction_runs
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email
from apps.configuration.models import SearchCategory, SearchZone
from apps.configuration.services import save_business_profile
from apps.integrations.contracts import (
    ExtractedBusiness,
    ExtractedEmail,
    ExtractionBatch,
    RateLimitError,
    SearchRequest,
)
from apps.integrations.fakes import MockExtractorProvider
from apps.prospects.email_validation import MockMXResolver
from apps.prospects.models import Prospect, ProspectEmail, ProspectIdentity


def _profile_values() -> dict[str, object]:
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


def _catalog(owner: User) -> Catalog:
    existing = Catalog.objects.filter(uploaded_by=owner).first()
    if existing is not None:
        return existing
    upload = SimpleUploadedFile(
        "catalogo.pdf",
        b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
        content_type="application/pdf",
    )
    return create_catalog(name="Extracción", upload=upload, actor=owner)


def _campaign(
    owner: User,
    *,
    provider: str = "fake",
    objective: int = 10,
    max_raw: int = 100,
    cost_limit: Decimal = Decimal("10"),
) -> Campaign:
    save_business_profile(owner=owner, values=_profile_values())
    category = SearchCategory.objects.get(name="Bobinados de motores")
    zone = SearchZone.objects.get(name="Palermo")
    campaign = create_campaign(
        actor=owner,
        values={
            "name": f"Extracción {provider}",
            "delivery_mode": Campaign.DeliveryMode.DRY_RUN,
            "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
            "objective": objective,
            "max_raw_records": max_raw,
            "cost_limit": cost_limit,
            "cost_currency": "USD",
            "daily_limit": 30,
            "message_interval_minutes": 5,
            "weekdays": [0, 1, 2, 3, 4],
            "window_start": time(9),
            "window_end": time(17),
            "timezone_name": "America/Argentina/Buenos_Aires",
            "relevance_threshold": 70,
            "extractor_provider": provider,
            "llm_provider": "fake",
            "llm_base_url": "",
            "llm_model": "fake-deterministic",
            "catalog": _catalog(owner),
        },
        category_ids=[category.pk],
        zone_ids=[zone.pk],
    )
    return transition_campaign(
        campaign_id=campaign.pk, target_state=Campaign.State.RUNNING, actor=owner
    )


@pytest.mark.django_db
def test_mock_search_run_is_deterministic_and_tracks_business_without_email(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    run = ensure_next_search_run(campaign.pk)
    assert run is not None

    completed = advance_search_run(
        run.pk, provider=MockExtractorProvider(), resolver=MockMXResolver()
    )

    assert completed.state == SearchRun.State.SUCCEEDED
    assert completed.provider_request_id.startswith("fake-request-")
    assert completed.response_json["fixture_unknown"]["schema_can_change"] is True
    assert completed.raw_count == 2
    assert completed.email_count == 1
    assert completed.no_email_count == 1
    assert completed.duplicate_count == 0
    prospect = Prospect.objects.get()
    assert prospect.pipeline_state == Prospect.PipelineState.EMAIL_FOUND
    assert prospect.emails.get(is_primary=True).normalized_email == "ventas@taller-demo.example"
    assert ProviderUsage.objects.get(run=completed).units == 2
    assert ensure_next_search_run(campaign.pk) is None
    campaign.refresh_from_db()
    assert campaign.discovery_state == Campaign.DiscoveryState.EXHAUSTED_QUERIES


@pytest.mark.django_db
def test_global_duplicates_do_not_create_new_prospects(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Reparación de motores eléctricos",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text="segunda consulta",
        normalized_query="segunda consulta",
        sort_order=1,
    )
    first = ensure_next_search_run(campaign.pk)
    assert first is not None
    advance_search_run(first.pk, provider=MockExtractorProvider(), resolver=MockMXResolver())
    second = ensure_next_search_run(campaign.pk)
    assert second is not None and second.pk != first.pk

    duplicate_run = advance_search_run(
        second.pk, provider=MockExtractorProvider(), resolver=MockMXResolver()
    )

    assert Prospect.objects.count() == 1
    assert ProspectEmail.objects.count() == 1
    assert duplicate_run.email_count == 0
    assert duplicate_run.duplicate_count == 1
    assert duplicate_run.no_email_count == 1


class AsyncProvider:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.poll_calls = 0
        self.parse_calls = 0
        self.run: SearchRun | None = None

    def extract(self, request: SearchRequest) -> ExtractionBatch:
        return self.submit(request)

    def submit(self, request: SearchRequest) -> ExtractionBatch:
        del request
        self.submit_calls += 1
        return ExtractionBatch(
            status="PENDING",
            request_id="durable-provider-id",
            raw_payload={"id": "durable-provider-id", "status": "Pending"},
        )

    def poll(self, *, request_id: str, timeout_seconds: float = 30.0) -> ExtractionBatch:
        del timeout_seconds
        self.poll_calls += 1
        assert request_id == "durable-provider-id"
        return ExtractionBatch(
            status="SUCCEEDED",
            request_id=request_id,
            raw_payload={
                "id": request_id,
                "status": "Success",
                "data": [{"provider_id": "async-1"}],
            },
            units=Decimal("7.5"),
        )

    def parse_response(self, raw_payload: dict[str, Any]) -> tuple[ExtractedBusiness, ...]:
        self.parse_calls += 1
        assert raw_payload["status"] == "Success"
        assert self.run is not None
        self.run.refresh_from_db()
        assert self.run.raw_persisted_at is not None
        return (
            ExtractedBusiness(
                provider_id="async-1",
                name="Bobinados Centro",
                address="Palermo, CABA",
                email_candidates=(ExtractedEmail("ventas@bobinados.example"),),
                website="https://bobinados.example",
            ),
        )


@pytest.mark.django_db
def test_restart_polls_existing_job_and_does_not_duplicate_results(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    run = ensure_next_search_run(campaign.pk)
    assert run is not None
    provider = AsyncProvider()
    provider.run = run

    pending = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())
    assert pending.state == SearchRun.State.RETRY_WAIT
    assert pending.provider_request_id == "durable-provider-id"

    completed = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())
    repeated = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())

    assert completed.state == repeated.state == SearchRun.State.SUCCEEDED
    assert provider.submit_calls == 1
    assert provider.poll_calls == 1
    assert provider.parse_calls == 1
    assert Prospect.objects.count() == 1
    assert ProviderUsage.objects.get(run=completed).units == Decimal("7.5")


@pytest.mark.django_db
def test_periodic_recovery_reconstructs_next_query_after_lost_message(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Reparación de motores eléctricos",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text="consulta durable siguiente",
        normalized_query="consulta durable siguiente",
        sort_order=1,
    )
    first = ensure_next_search_run(campaign.pk)
    assert first is not None
    advance_search_run(first.pk, provider=MockExtractorProvider(), resolver=MockMXResolver())
    assert SearchRun.objects.filter(campaign=campaign).count() == 1

    recovered_count = recover_extraction_runs()

    assert recovered_count == 1
    assert SearchRun.objects.filter(campaign=campaign).count() == 2
    assert SearchRun.objects.filter(campaign=campaign, state=SearchRun.State.SUCCEEDED).count() == 2
    campaign.refresh_from_db()
    assert campaign.discovery_state == Campaign.DiscoveryState.EXHAUSTED_QUERIES
    assert Prospect.objects.count() == 1


class CountingProvider(MockExtractorProvider):
    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0

    def submit(self, request: SearchRequest) -> ExtractionBatch:
        self.submit_calls += 1
        return super().submit(request)


@pytest.mark.django_db
def test_pause_and_cancel_prevent_provider_effects_and_keep_runs_recoverable(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    run = ensure_next_search_run(campaign.pk)
    assert run is not None
    provider = CountingProvider()

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.PAUSED,
        actor=owner,
        reason="Control de costo",
    )
    paused_run = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())
    assert paused_run.state == SearchRun.State.RETRY_WAIT
    assert provider.submit_calls == 0

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.RUNNING,
        actor=owner,
    )
    completed = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())
    assert completed.state == SearchRun.State.SUCCEEDED
    assert provider.submit_calls == 1

    second_campaign = _campaign(owner)
    cancelled_run = ensure_next_search_run(second_campaign.pk)
    assert cancelled_run is not None
    transition_campaign(
        campaign_id=second_campaign.pk,
        target_state=Campaign.State.CANCELLED,
        actor=owner,
    )
    result = advance_search_run(
        cancelled_run.pk,
        provider=provider,
        resolver=MockMXResolver(),
    )
    assert result.state == SearchRun.State.CANCELLED
    assert provider.submit_calls == 1


class MultiEmailProvider(MockExtractorProvider):
    def submit(self, request: SearchRequest) -> ExtractionBatch:
        del request
        return ExtractionBatch(
            status="SUCCEEDED",
            request_id="multi-email-request",
            raw_payload={"status": "Success", "data": [{"id": 1}, {"id": 2}]},
        )

    def parse_response(self, raw_payload: dict[str, Any]) -> tuple[ExtractedBusiness, ...]:
        assert raw_payload["status"] == "Success"
        return (
            ExtractedBusiness(
                provider_id="multi-1",
                name="Taller Uno",
                address="Calle Uno 1",
                website="https://first.example",
                email_candidates=(
                    ExtractedEmail("ventas@first.example", is_primary=True, order=0),
                    ExtractedEmail("contacto@shared.example", order=1),
                ),
            ),
            ExtractedBusiness(
                provider_id="multi-2",
                name="Taller Dos",
                address="Calle Dos 2",
                website="https://second.example",
                email_candidates=(
                    ExtractedEmail("ventas@second.example", is_primary=True, order=0),
                    ExtractedEmail("contacto@shared.example", order=1),
                ),
            ),
        )


@pytest.mark.django_db
def test_every_validated_email_is_a_global_deduplication_identity(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    run = ensure_next_search_run(campaign.pk)
    assert run is not None

    completed = advance_search_run(
        run.pk,
        provider=MultiEmailProvider(),
        resolver=MockMXResolver(),
    )

    assert completed.email_count == 1
    assert completed.duplicate_count == 1
    assert Prospect.objects.count() == 1
    assert ProspectIdentity.objects.filter(kind=ProspectIdentity.Kind.EMAIL).count() == 3
    assert ProspectEmail.objects.count() == 3


@pytest.mark.django_db
def test_bounce_suppression_invalidates_existing_prospect_email_even_when_merged(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    run = ensure_next_search_run(campaign.pk)
    assert run is not None
    advance_search_run(run.pk, provider=MockExtractorProvider(), resolver=MockMXResolver())
    email = ProspectEmail.objects.get()
    suppress_email(
        email=email.normalized_email,
        reason=SuppressionEntry.Reason.MANUAL,
        actor=owner,
    )

    suppress_email(
        email=email.normalized_email,
        reason=SuppressionEntry.Reason.BOUNCE,
        actor=None,
        source="inbound",
    )

    email.refresh_from_db()
    assert email.is_invalid is True
    assert email.invalid_reason == SuppressionEntry.Reason.BOUNCE
    assert email.invalidated_at is not None


@pytest.mark.django_db
def test_restart_never_resubmits_ambiguous_job_without_provider_id(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    run = ensure_next_search_run(campaign.pk)
    assert run is not None
    old = run.created_at - timedelta(minutes=10)
    SearchRun.objects.filter(pk=run.pk).update(state=SearchRun.State.RUNNING, updated_at=old)
    provider = AsyncProvider()
    provider.run = run

    failed = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())

    assert failed.state == SearchRun.State.FAILED_PERMANENT
    assert "no se reenvía" in failed.error
    assert provider.submit_calls == 0


class RateLimitedOnceProvider(MockExtractorProvider):
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, request: SearchRequest) -> ExtractionBatch:
        self.calls += 1
        if self.calls == 1:
            raise RateLimitError("Cuota temporal", retry_after=1)
        return super().submit(request)


@pytest.mark.django_db
def test_provider_error_is_visible_and_retryable(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    campaign = _campaign(owner)
    run = ensure_next_search_run(campaign.pk)
    assert run is not None
    provider = RateLimitedOnceProvider()

    waiting = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())
    assert waiting.state == SearchRun.State.RETRY_WAIT
    assert waiting.error == "Cuota temporal"
    assert waiting.next_poll_at is not None

    recovered = advance_search_run(run.pk, provider=provider, resolver=MockMXResolver())
    assert recovered.state == SearchRun.State.SUCCEEDED
    assert recovered.error == ""
    assert Prospect.objects.count() == 1


@pytest.mark.django_db
def test_discovery_stops_for_qualified_target_raw_limit_and_cost(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    target_campaign = _campaign(owner, objective=1)
    target_run = ensure_next_search_run(target_campaign.pk)
    assert target_run is not None
    advance_search_run(target_run.pk, provider=MockExtractorProvider(), resolver=MockMXResolver())
    Prospect.objects.update(pipeline_state=Prospect.PipelineState.QUEUED)
    assert ensure_next_search_run(target_campaign.pk) is None
    target_campaign.refresh_from_db()
    assert target_campaign.discovery_state == Campaign.DiscoveryState.TARGET_REACHED

    raw_campaign = _campaign(owner, objective=1, max_raw=1)
    raw_run = ensure_next_search_run(raw_campaign.pk)
    assert raw_run is not None
    advance_search_run(raw_run.pk, provider=MockExtractorProvider(), resolver=MockMXResolver())
    raw_campaign.refresh_from_db()
    assert raw_campaign.discovery_state == Campaign.DiscoveryState.EXHAUSTED_RAW_LIMIT


@pytest.mark.django_db
@override_settings(OUTSCRAPER_API_KEY="credential", OUTSCRAPER_MAX_COST_PER_RESULT=Decimal("1"))
def test_cost_reservation_stops_before_starting_unaffordable_job(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, provider="outscraper", cost_limit=Decimal("0.50"))
    assert ensure_next_search_run(campaign.pk) is None
    campaign.refresh_from_db()
    assert campaign.discovery_state == Campaign.DiscoveryState.EXHAUSTED_COST
    assert SearchRun.objects.filter(campaign=campaign).count() == 0


@pytest.mark.django_db
@override_settings(OUTSCRAPER_API_KEY="credential", OUTSCRAPER_MAX_COST_PER_RESULT=Decimal("0.01"))
def test_real_search_run_can_be_created_with_environment_credential(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, provider="outscraper", cost_limit=Decimal("10"))

    run = ensure_next_search_run(campaign.pk)

    assert run is not None
    assert run.provider == "outscraper"
    assert run.state == SearchRun.State.PENDING
    assert run.provider_request_id == ""
    assert run.request_json["enrichment"] == ["contacts_n_leads"]
