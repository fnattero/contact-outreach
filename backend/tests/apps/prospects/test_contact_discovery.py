from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

import apps.prospects.tasks as prospect_tasks
from apps.audit.models import AuditEvent, BackgroundJob
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email
from apps.integrations.contracts import (
    ExtractedBusiness,
    ExtractedEmail,
    WebsiteEmailCandidate,
    WebsitePage,
    WebsiteRequest,
    WebsiteResult,
)
from apps.prospects.email_validation import (
    MockMXResolver,
    MXStatus,
    TransientMXError,
)
from apps.prospects.enrichment import enrich_prospect
from apps.prospects.models import Prospect, WebsiteSnapshot
from apps.prospects.services import (
    IngestOutcome,
    discover_website_email,
    ingest_business,
    ingest_prepared_business,
    prepare_business,
    registrable_domain,
)
from apps.prospects.tasks import process_prospect_pipeline, recover_prospect_pipeline


class CandidateFetcher:
    def __init__(self, email: str | None) -> None:
        self.email = email
        self.calls = 0

    def fetch(self, request: WebsiteRequest) -> WebsiteResult:
        self.calls += 1
        content_hash = "a" * 64
        candidates = (
            (
                WebsiteEmailCandidate(
                    value=self.email,
                    source="mailto",
                    page_url=request.url,
                    page_content_hash=content_hash,
                ),
            )
            if self.email is not None
            else ()
        )
        return WebsiteResult(
            pages=(
                WebsitePage(
                    requested_url=request.url,
                    final_url=request.url,
                    status_code=200,
                    text="Reparación y mantenimiento de motores eléctricos.",
                    content_type="text/html",
                    content_hash=content_hash,
                    byte_count=54,
                    email_candidates=candidates,
                ),
            )
        )


class TransientResolver:
    def resolve(self, domain: str) -> MXStatus:
        del domain
        return MXStatus.TRANSIENT


def test_registrable_domain_uses_the_offline_public_suffix_list() -> None:
    assert registrable_domain("https://one.business.com.br/contacto") == "business.com.br"
    assert registrable_domain("https://two.other.com.br") == "other.com.br"
    assert registrable_domain("https://taller.example.com.ar") == "example.com.ar"


def _run(owner: User) -> SearchRun:
    upload = SimpleUploadedFile(
        "catalogo.pdf",
        b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
        content_type="application/pdf",
    )
    catalog = create_catalog(name="Descubrimiento", upload=upload, actor=owner)
    campaign = Campaign.objects.create(
        name="Descubrimiento de contactos",
        state=Campaign.State.DISCOVERING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
        delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY,
        extractor_provider="fake",
        website_fetcher="fake",
        llm_provider="fake",
        llm_model="fake-deterministic",
        catalog=catalog,
        profile_snapshot={
            "company_name": "Componentes Delta SA",
            "salesperson_name": "Fran",
            "address": "CABA",
            "signature": "Fran · Componentes Delta SA",
            "description": "Proveedor de componentes industriales",
            "products": "Componentes industriales para equipos eléctricos",
            "differentiators": "Atención técnica",
            "additional_instructions": "Tono sobrio",
        },
        created_by=owner,
    )
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Taller electromecánico",
        zone_snapshot="Palermo",
        location_snapshot="CABA",
        query_text="taller electromecánico en Palermo",
        normalized_query="taller electromecánico en palermo",
    )
    return SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="fake",
        idempotency_key=f"contact-discovery:{campaign.pk}",
        requested_limit=20,
    )


def _business(
    suffix: str,
    *,
    website: str | None,
    email_candidates: tuple[ExtractedEmail, ...] = (),
) -> ExtractedBusiness:
    return ExtractedBusiness(
        provider_id=f"place-{suffix}",
        name=f"Taller {suffix}",
        address=f"Calle {suffix}, CABA",
        website=website,
        email_candidates=email_candidates,
    )


@pytest.mark.django_db
def test_no_provider_email_runs_full_pipeline_from_one_website_snapshot(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    result = ingest_business(
        run=run,
        business=_business("Web", website="https://taller-web.example"),
        resolver=MockMXResolver(),
    )

    assert result.outcome == IngestOutcome.NO_EMAIL
    assert result.prospect is not None
    assert result.prospect.pipeline_state == Prospect.PipelineState.DISCOVERED
    assert not result.prospect.emails.exists()

    status = process_prospect_pipeline(str(result.prospect.pk))

    result.prospect.refresh_from_db()
    email = result.prospect.emails.get(is_primary=True)
    assert status == Prospect.PipelineState.QUEUED
    assert result.prospect.pipeline_state == Prospect.PipelineState.QUEUED
    assert WebsiteSnapshot.objects.filter(prospect=result.prospect).count() == 1
    assert email.normalized_email == "contacto@taller-web.example"
    assert email.source == "website_visible_text"
    assert email.source_url == "https://taller-web.example"
    assert len(email.source_content_hash) == 64
    assert not OutboundMessage.objects.filter(prospect=result.prospect).exists()


@pytest.mark.django_db
def test_business_without_provider_email_or_website_is_persisted_then_skipped(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    result = ingest_business(
        run=run,
        business=_business("SinSitio", website=None),
        resolver=MockMXResolver(),
    )

    assert result.outcome == IngestOutcome.NO_EMAIL
    assert result.prospect is not None
    snapshot = enrich_prospect(result.prospect.pk)
    prospect = discover_website_email(
        result.prospect.pk,
        snapshot=snapshot,
        resolver=MockMXResolver(),
    )

    assert snapshot.status == WebsiteSnapshot.Status.FALLBACK
    assert snapshot.pages == []
    assert snapshot.email_candidates == []
    assert prospect.pipeline_state == Prospect.PipelineState.SKIPPED_NO_EMAIL
    assert not prospect.emails.exists()


@pytest.mark.django_db
def test_suppressed_website_candidate_is_never_persisted(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    result = ingest_business(
        run=run,
        business=_business("Suprimido", website="https://suprimido.example"),
        resolver=MockMXResolver(),
    )
    assert result.prospect is not None
    suppress_email(
        email="ventas@suprimido.example",
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        actor=owner,
    )
    fetcher = CandidateFetcher("ventas@suprimido.example")

    snapshot = enrich_prospect(result.prospect.pk, fetcher=fetcher)
    prospect = discover_website_email(
        result.prospect.pk,
        snapshot=snapshot,
        resolver=MockMXResolver(),
    )

    assert prospect.pipeline_state == Prospect.PipelineState.SKIPPED_NO_EMAIL
    assert fetcher.calls == 1
    assert not prospect.emails.exists()


@pytest.mark.django_db
def test_suppression_created_after_mx_validation_blocks_direct_overture_email(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    business = _business(
        "Carrera",
        website="https://carrera.example",
        email_candidates=(ExtractedEmail("ventas@carrera.example", source="overture"),),
    )
    prepared = prepare_business(business=business, resolver=MockMXResolver())
    assert prepared is not None and prepared.selected_email is not None
    suppress_email(
        email="ventas@carrera.example",
        reason=SuppressionEntry.Reason.MANUAL,
        actor=owner,
    )

    result = ingest_prepared_business(run=run, prepared=prepared)

    assert result.outcome == IngestOutcome.NO_EMAIL
    assert result.prospect is not None
    assert result.prospect.pipeline_state == Prospect.PipelineState.DISCOVERED
    assert not result.prospect.emails.exists()
    assert not result.prospect.identities.filter(kind="EMAIL").exists()


@pytest.mark.django_db
def test_provider_email_wins_and_website_candidate_is_not_imported(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    result = ingest_business(
        run=run,
        business=_business(
            "Directo",
            website="https://directo.example",
            email_candidates=(ExtractedEmail("ventas@directo.example", source="provider_places"),),
        ),
        resolver=MockMXResolver(),
    )
    assert result.prospect is not None
    fetcher = CandidateFetcher("alternativo@directo.example")

    snapshot = enrich_prospect(result.prospect.pk, fetcher=fetcher)
    discover_website_email(
        result.prospect.pk,
        snapshot=snapshot,
        resolver=MockMXResolver(),
    )
    result.prospect.refresh_from_db()

    assert result.outcome == IngestOutcome.CREATED
    assert result.prospect.pipeline_state == Prospect.PipelineState.ENRICHED
    assert list(result.prospect.emails.values_list("normalized_email", flat=True)) == [
        "ventas@directo.example"
    ]
    assert result.prospect.emails.get().source == "provider_places"
    assert fetcher.calls == 1


@pytest.mark.django_db
def test_transient_direct_email_allows_the_independent_website_fallback(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    result = ingest_business(
        run=run,
        business=_business(
            "MXDirecto",
            website="https://mx-directo.example",
            email_candidates=(ExtractedEmail("ventas@mx-directo.example", source="overture"),),
        ),
        resolver=TransientResolver(),
    )

    assert result.outcome == IngestOutcome.NO_EMAIL
    assert result.prospect is not None
    assert result.prospect.pipeline_state == Prospect.PipelineState.DISCOVERED
    assert not result.prospect.emails.exists()

    fetcher = CandidateFetcher("contacto@mx-directo.example")
    snapshot = enrich_prospect(result.prospect.pk, fetcher=fetcher)
    prospect = discover_website_email(
        result.prospect.pk,
        snapshot=snapshot,
        resolver=MockMXResolver(),
    )

    assert prospect.pipeline_state == Prospect.PipelineState.EMAIL_FOUND
    email = prospect.emails.get(is_primary=True)
    assert email.normalized_email == "contacto@mx-directo.example"
    assert email.source == "website_mailto"


@pytest.mark.django_db
def test_later_direct_email_promotes_existing_discovered_business(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    original = _business("Repetido", website="https://repetido.example")
    first = ingest_business(run=run, business=original, resolver=MockMXResolver())
    second = ingest_business(
        run=run,
        business=_business(
            "Repetido",
            website="https://repetido.example",
            email_candidates=(ExtractedEmail("ventas@repetido.example"),),
        ),
        resolver=MockMXResolver(),
    )

    assert first.prospect is not None
    first.prospect.refresh_from_db()
    assert second.outcome == IngestOutcome.DUPLICATE
    assert Prospect.objects.count() == 1
    assert first.prospect.pipeline_state == Prospect.PipelineState.EMAIL_FOUND
    assert first.prospect.emails.get(is_primary=True).normalized_email == (
        "ventas@repetido.example"
    )


@pytest.mark.django_db
def test_transient_mx_reuses_snapshot_on_retry(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    result = ingest_business(
        run=run,
        business=_business("Temporal", website="https://temporal.example"),
        resolver=MockMXResolver(),
    )
    assert result.prospect is not None
    fetcher = CandidateFetcher("ventas@temporal.example")
    snapshot = enrich_prospect(result.prospect.pk, fetcher=fetcher)

    with pytest.raises(TransientMXError):
        discover_website_email(
            result.prospect.pk,
            snapshot=snapshot,
            resolver=TransientResolver(),
        )

    result.prospect.refresh_from_db()
    assert result.prospect.pipeline_state == Prospect.PipelineState.DISCOVERED
    prospect = discover_website_email(
        result.prospect.pk,
        snapshot=snapshot,
        resolver=MockMXResolver(),
    )
    enrich_prospect(prospect.pk, fetcher=fetcher)
    prospect.refresh_from_db()

    assert prospect.pipeline_state == Prospect.PipelineState.ENRICHED
    assert fetcher.calls == 1
    assert WebsiteSnapshot.objects.filter(prospect=prospect).count() == 1


@pytest.mark.django_db
def test_pipeline_bounds_transient_website_mx_retries_and_recovery_obeys_deadline(
    owner: User,
    private_catalog_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del private_catalog_dir
    run = _run(owner)
    result = ingest_business(
        run=run,
        business=_business("MXWeb", website="https://mx-web.example"),
        resolver=MockMXResolver(),
    )
    assert result.prospect is not None
    prospect = result.prospect

    def transient_discovery(*args: object, **kwargs: object) -> Prospect:
        del args, kwargs
        raise TransientMXError("temporary resolver failure")

    monkeypatch.setattr(prospect_tasks, "discover_website_email", transient_discovery)

    assert process_prospect_pipeline(str(prospect.pk)) == Prospect.PipelineState.DISCOVERED
    job = BackgroundJob.objects.get(
        idempotency_key=f"pipeline:{prospect.pk}:{prospect.analysis_generation}"
    )
    assert job.state == BackgroundJob.State.RETRY_WAIT
    assert job.attempts == 1
    assert job.next_retry_at is not None and job.next_retry_at > timezone.now()
    assert recover_prospect_pipeline() == 0

    BackgroundJob.objects.filter(pk=job.pk).update(
        next_retry_at=timezone.now() - timedelta(seconds=1)
    )
    assert recover_prospect_pipeline() == 1
    job.refresh_from_db()
    assert job.state == BackgroundJob.State.RETRY_WAIT
    assert job.attempts == 2
    assert job.next_retry_at is not None and job.next_retry_at > timezone.now()

    BackgroundJob.objects.filter(pk=job.pk).update(
        next_retry_at=timezone.now() - timedelta(seconds=1)
    )
    assert recover_prospect_pipeline() == 1

    prospect.refresh_from_db()
    job.refresh_from_db()
    assert prospect.pipeline_state == Prospect.PipelineState.SKIPPED_NO_EMAIL
    assert job.state == BackgroundJob.State.SUCCEEDED
    assert job.attempts == 3
    assert job.next_retry_at is None
    assert WebsiteSnapshot.objects.filter(prospect=prospect).count() == 1
    assert recover_prospect_pipeline() == 0
    assert AuditEvent.objects.filter(
        action="prospect.skipped_no_email",
        entity_id=str(prospect.pk),
        after__reason="mx_retry_exhausted",
    ).exists()
