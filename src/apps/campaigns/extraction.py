from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import Campaign, ProviderUsage, SearchQuery, SearchRun
from apps.configuration.integrations import (
    redact_provider_error,
    runtime_integration_configuration,
)
from apps.integrations.contracts import (
    AuthenticationError,
    CostLimitError,
    ExtractedBusiness,
    ExtractionBatch,
    ExtractorProvider,
    PermanentProviderError,
    RateLimitError,
    RetryableProviderError,
    SearchRequest,
    ValidationProviderError,
)
from apps.integrations.factory import get_extractor_provider
from apps.prospects.email_validation import (
    DNSMXResolver,
    MockMXResolver,
    MXResolver,
    TransientMXError,
)
from apps.prospects.models import Prospect
from apps.prospects.services import (
    DeduplicationConflict,
    IngestOutcome,
    PreparedBusiness,
    ingest_prepared_business,
    prepare_business,
)

MAX_PROVIDER_ATTEMPTS = 3
ACTIVE_RUN_STATES = (
    SearchRun.State.PENDING,
    SearchRun.State.RUNNING,
    SearchRun.State.RETRY_WAIT,
)


def _unit_cost(provider: str, *, owner_id: int) -> Decimal:
    if provider == "fake":
        return Decimal("0")
    return runtime_integration_configuration(owner_id).outscraper_max_cost_per_result


def _campaign_cost(campaign: Campaign) -> Decimal:
    total = Decimal("0")
    for run in campaign.search_runs.exclude(state=SearchRun.State.CANCELLED):
        if run.cost_actual is not None:
            total += run.cost_actual
        elif run.cost_estimated is not None:
            total += run.cost_estimated
        else:
            total += run.cost_reserved
    return total


def _qualified_count(campaign: Campaign) -> int:
    # Phase 6/7 moves only threshold-approved prospects to QUEUED. EMAIL_FOUND is not qualified.
    return Prospect.objects.filter(
        campaign=campaign, pipeline_state=Prospect.PipelineState.QUEUED
    ).count()


def _finish_discovery_locked(campaign: Campaign, state: str, reason: str) -> None:
    if campaign.discovery_state != Campaign.DiscoveryState.RUNNING:
        return
    before = {"discovery_state": campaign.discovery_state}
    campaign.discovery_state = state
    campaign.discovery_stop_reason = reason
    campaign.save(update_fields=("discovery_state", "discovery_stop_reason", "updated_at"))
    record_event(
        action="campaign.discovery_finished",
        entity=campaign,
        actor=None,
        before=before,
        after={"discovery_state": state, "discovery_stop_reason": reason},
    )


def _apply_stop_conditions(campaign: Campaign) -> bool:
    qualified = _qualified_count(campaign)
    if qualified >= campaign.objective:
        _finish_discovery_locked(
            campaign,
            Campaign.DiscoveryState.TARGET_REACHED,
            f"qualified={qualified}; objective={campaign.objective}",
        )
        return True
    raw = sum(campaign.search_runs.values_list("raw_count", flat=True))
    if raw >= campaign.max_raw_records:
        _finish_discovery_locked(
            campaign,
            Campaign.DiscoveryState.EXHAUSTED_RAW_LIMIT,
            f"raw={raw}; max_raw_records={campaign.max_raw_records}",
        )
        return True
    cost = _campaign_cost(campaign)
    if cost >= campaign.cost_limit:
        _finish_discovery_locked(
            campaign,
            Campaign.DiscoveryState.EXHAUSTED_COST,
            f"estimated_or_actual_cost={cost}; limit={campaign.cost_limit} "
            f"{campaign.cost_currency}",
        )
        return True
    return False


@transaction.atomic
def ensure_next_search_run(campaign_id: uuid.UUID | str) -> SearchRun | None:
    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    if (
        campaign.state != Campaign.State.RUNNING
        or campaign.discovery_state != Campaign.DiscoveryState.RUNNING
    ):
        return None
    if _apply_stop_conditions(campaign):
        return None
    active = (
        SearchRun.objects.select_for_update()
        .filter(
            campaign=campaign,
            state__in=(
                SearchRun.State.PENDING,
                SearchRun.State.RUNNING,
                SearchRun.State.RETRY_WAIT,
            ),
        )
        .first()
    )
    if active is not None:
        return active
    query = (
        SearchQuery.objects.select_for_update()
        .filter(campaign=campaign, state=SearchQuery.State.PENDING)
        .order_by("sort_order")
        .first()
    )
    if query is None:
        _finish_discovery_locked(
            campaign,
            Campaign.DiscoveryState.EXHAUSTED_QUERIES,
            "No quedan consultas pendientes.",
        )
        return None

    raw_used = sum(campaign.search_runs.values_list("raw_count", flat=True))
    runtime = runtime_integration_configuration(campaign.created_by_id)
    requested_limit = min(runtime.outscraper_batch_size, campaign.max_raw_records - raw_used)
    per_unit = _unit_cost(campaign.extractor_provider, owner_id=campaign.created_by_id)
    cost_remaining = campaign.cost_limit - _campaign_cost(campaign)
    if per_unit > 0:
        affordable = int((cost_remaining / per_unit).to_integral_value(rounding=ROUND_FLOOR))
        requested_limit = min(requested_limit, affordable)
    if requested_limit <= 0:
        _finish_discovery_locked(
            campaign,
            Campaign.DiscoveryState.EXHAUSTED_COST,
            f"remaining_cost={cost_remaining}; max_unit_cost={per_unit}",
        )
        return None

    idempotency_key = f"extract:{query.pk}:1"
    run = SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider=campaign.extractor_provider,
        idempotency_key=idempotency_key,
        request_json={
            "query": query.query_text,
            "limit": requested_limit,
            "async": True,
            "enrichment": ["contacts_n_leads"],
            "language": "es-419",
            "region": "AR",
        },
        requested_limit=requested_limit,
        cost_reserved=per_unit * requested_limit,
        currency=campaign.cost_currency,
    )
    query.state = SearchQuery.State.RUNNING
    query.run_count += 1
    query.save(update_fields=("state", "run_count", "updated_at"))
    record_event(
        action="extraction.run_created",
        entity=run,
        actor=None,
        after={
            "provider": run.provider,
            "requested_limit": requested_limit,
            "cost_reserved": str(run.cost_reserved),
            "currency": run.currency,
        },
    )
    return run


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@transaction.atomic
def _persist_provider_response(run_id: uuid.UUID, batch: ExtractionBatch) -> SearchRun:
    run = SearchRun.objects.select_for_update().get(pk=run_id)
    if run.processed_at is not None:
        return run
    if run.provider_request_id and run.provider_request_id != batch.request_id:
        raise PermanentProviderError("El proveedor cambió el identificador del trabajo.")
    if batch.currency and batch.currency != run.currency:
        raise PermanentProviderError(
            f"El proveedor informó moneda {batch.currency}; la campaña usa {run.currency}."
        )
    run.provider_request_id = batch.request_id
    run.response_json = batch.raw_payload
    run.response_hash = _payload_hash(batch.raw_payload)
    run.raw_persisted_at = timezone.now()
    if batch.estimated_cost is not None:
        run.cost_estimated = batch.estimated_cost
    if batch.actual_cost is not None:
        run.cost_actual = batch.actual_cost
    if batch.currency:
        run.currency = batch.currency
    run.error = ""
    run.save(
        update_fields=(
            "provider_request_id",
            "response_json",
            "response_hash",
            "raw_persisted_at",
            "cost_estimated",
            "cost_actual",
            "currency",
            "error",
            "updated_at",
        )
    )
    usage, _ = ProviderUsage.objects.get_or_create(
        run=run,
        defaults={
            "provider": run.provider,
            "operation": "google_maps_search",
            "campaign": run.campaign,
            "currency": run.currency,
        },
    )
    if batch.units is not None:
        usage.units = batch.units
    if batch.estimated_cost is not None:
        usage.estimated_cost = batch.estimated_cost
    if batch.actual_cost is not None:
        usage.actual_cost = batch.actual_cost
    if batch.usage_metadata is not None:
        usage.metadata = batch.usage_metadata
    usage.request_id = batch.request_id
    usage.currency = run.currency
    usage.save(
        update_fields=(
            "units",
            "estimated_cost",
            "actual_cost",
            "metadata",
            "request_id",
            "currency",
            "updated_at",
        )
    )
    return run


@transaction.atomic
def _mark_pending(run_id: uuid.UUID, *, retry_after: float | None = None) -> SearchRun:
    run_pointer = SearchRun.objects.only("campaign_id").get(pk=run_id)
    campaign = Campaign.objects.select_for_update().get(pk=run_pointer.campaign_id)
    run = SearchRun.objects.select_for_update().select_related("query").get(pk=run_id)
    if (
        campaign.state != Campaign.State.RUNNING
        or campaign.discovery_state != Campaign.DiscoveryState.RUNNING
        or run.state == SearchRun.State.CANCELLED
    ):
        return run
    runtime = runtime_integration_configuration(campaign.created_by_id)
    delay = retry_after if retry_after is not None else runtime.outscraper_poll_seconds
    run.state = SearchRun.State.RETRY_WAIT
    run.next_poll_at = timezone.now() + timedelta(seconds=max(1.0, delay))
    run.query.state = SearchQuery.State.RETRY_WAIT
    run.save(update_fields=("state", "next_poll_at", "updated_at"))
    run.query.save(update_fields=("state", "updated_at"))
    return run


@transaction.atomic
def _mark_retryable(run_id: uuid.UUID, error: Exception) -> SearchRun:
    run_pointer = SearchRun.objects.only("campaign_id").get(pk=run_id)
    campaign = Campaign.objects.select_for_update().get(pk=run_pointer.campaign_id)
    run = SearchRun.objects.select_for_update().select_related("query").get(pk=run_id)
    if (
        campaign.state != Campaign.State.RUNNING
        or campaign.discovery_state != Campaign.DiscoveryState.RUNNING
        or run.state == SearchRun.State.CANCELLED
    ):
        return run
    run.campaign = campaign
    run.attempts += 1
    run.error = _sanitized_error(error, owner_id=campaign.created_by_id)
    if run.attempts >= MAX_PROVIDER_ATTEMPTS:
        run.state = SearchRun.State.FAILED_PERMANENT
        run.finished_at = timezone.now()
        run.query.state = SearchQuery.State.FAILED_PERMANENT
        run.query.last_error = run.error
        _finish_discovery_locked(
            run.campaign,
            Campaign.DiscoveryState.FAILED_PROVIDER,
            f"run={run.pk}; error={run.error}",
        )
    else:
        retry_after = error.retry_after if isinstance(error, RateLimitError) else None
        delay = retry_after or min(900, 2**run.attempts * 30)
        run.state = SearchRun.State.RETRY_WAIT
        run.next_poll_at = timezone.now() + timedelta(seconds=delay)
        run.query.state = SearchQuery.State.RETRY_WAIT
        run.query.last_error = run.error
    run.save(
        update_fields=(
            "attempts",
            "error",
            "state",
            "next_poll_at",
            "finished_at",
            "updated_at",
        )
    )
    run.query.save(update_fields=("state", "last_error", "updated_at"))
    return run


@transaction.atomic
def _mark_permanent(run_id: uuid.UUID, error: Exception) -> SearchRun:
    run_pointer = SearchRun.objects.only("campaign_id").get(pk=run_id)
    campaign = Campaign.objects.select_for_update().get(pk=run_pointer.campaign_id)
    run = SearchRun.objects.select_for_update().select_related("query").get(pk=run_id)
    if (
        campaign.state != Campaign.State.RUNNING
        or campaign.discovery_state != Campaign.DiscoveryState.RUNNING
        or run.state == SearchRun.State.CANCELLED
    ):
        return run
    run.campaign = campaign
    run.state = SearchRun.State.FAILED_PERMANENT
    run.error = _sanitized_error(error, owner_id=campaign.created_by_id)
    run.finished_at = timezone.now()
    run.query.state = SearchQuery.State.FAILED_PERMANENT
    run.query.last_error = run.error
    run.save(update_fields=("state", "error", "finished_at", "updated_at"))
    run.query.save(update_fields=("state", "last_error", "updated_at"))
    _finish_discovery_locked(
        run.campaign,
        Campaign.DiscoveryState.FAILED_PROVIDER,
        f"run={run.pk}; error={run.error}",
    )
    record_event(
        action="extraction.run_failed",
        entity=run,
        actor=None,
        after={"state": run.state, "error": run.error},
    )
    return run


def _sanitized_error(error: Exception, *, owner_id: int) -> str:
    return redact_provider_error(error, owner_id=owner_id)


def _process_persisted_response(
    run_id: uuid.UUID, *, provider: ExtractorProvider, resolver: MXResolver
) -> SearchRun:
    run = SearchRun.objects.select_related("campaign", "query").get(pk=run_id)
    if run.processed_at is not None:
        return run
    if (
        run.campaign.state != Campaign.State.RUNNING
        or run.campaign.discovery_state != Campaign.DiscoveryState.RUNNING
        or run.state == SearchRun.State.CANCELLED
    ):
        return run
    records = provider.parse_response(run.response_json)
    prepared: list[PreparedBusiness | None] = [
        prepare_business(business=business, resolver=resolver)
        for business in records[: run.requested_limit]
    ]
    return _persist_processed_records(run_id, records=records, prepared=prepared)


@transaction.atomic
def _persist_processed_records(
    run_id: uuid.UUID,
    *,
    records: tuple[ExtractedBusiness, ...],
    prepared: list[PreparedBusiness | None],
) -> SearchRun:
    run_pointer = SearchRun.objects.only("campaign_id").get(pk=run_id)
    campaign = Campaign.objects.select_for_update().get(pk=run_pointer.campaign_id)
    run = SearchRun.objects.select_for_update().select_related("query").get(pk=run_id)
    if run.processed_at is not None:
        return run
    if (
        campaign.state != Campaign.State.RUNNING
        or campaign.discovery_state != Campaign.DiscoveryState.RUNNING
        or run.state == SearchRun.State.CANCELLED
    ):
        return run
    run.campaign = campaign
    raw_count = len(records)
    email_count = 0
    no_email_count = 0
    duplicate_count = 0
    for item in prepared:
        if item is None:
            no_email_count += 1
            continue
        result = ingest_prepared_business(run=run, prepared=item)
        if result.outcome == IngestOutcome.CREATED:
            email_count += 1
        elif result.outcome == IngestOutcome.DUPLICATE:
            duplicate_count += 1
    no_email_count += sum(
        1 for business in records[run.requested_limit :] if not business.email_candidates
    )
    run.raw_count = raw_count
    run.email_count = email_count
    run.no_email_count = no_email_count
    run.duplicate_count = duplicate_count
    run.processed_at = timezone.now()
    run.finished_at = run.processed_at
    run.state = SearchRun.State.SUCCEEDED
    if run.cost_estimated is None:
        run.cost_estimated = (
            _unit_cost(run.provider, owner_id=run.campaign.created_by_id) * raw_count
        )
    run.query.state = SearchQuery.State.SUCCEEDED
    run.query.last_error = ""
    run.save(
        update_fields=(
            "raw_count",
            "email_count",
            "no_email_count",
            "duplicate_count",
            "processed_at",
            "finished_at",
            "state",
            "cost_estimated",
            "updated_at",
        )
    )
    run.query.save(update_fields=("state", "last_error", "updated_at"))
    usage, _ = ProviderUsage.objects.get_or_create(
        run=run,
        defaults={
            "provider": run.provider,
            "operation": "google_maps_search",
            "campaign": run.campaign,
            "currency": run.currency,
        },
    )
    if usage.units is None:
        usage.units = Decimal(raw_count)
    usage.estimated_cost = run.cost_estimated
    usage.request_id = run.provider_request_id
    usage.save(update_fields=("units", "estimated_cost", "request_id", "updated_at"))
    record_event(
        action="extraction.run_succeeded",
        entity=run,
        actor=None,
        after={
            "raw_count": raw_count,
            "email_count": email_count,
            "no_email_count": no_email_count,
            "duplicate_count": duplicate_count,
            "estimated_cost": str(run.cost_estimated),
            "currency": run.currency,
        },
    )
    _apply_stop_conditions(run.campaign)
    return run


@transaction.atomic
def _call_provider_with_campaign_lock(
    run_id: uuid.UUID,
    *,
    provider: ExtractorProvider,
    request: SearchRequest,
) -> tuple[SearchRun, ExtractionBatch] | None:
    """Serialize paid provider effects with campaign pause/cancellation."""

    run_pointer = SearchRun.objects.only("campaign_id").get(pk=run_id)
    campaign = Campaign.objects.select_for_update().get(pk=run_pointer.campaign_id)
    run = SearchRun.objects.select_for_update().get(pk=run_id)
    if (
        campaign.state != Campaign.State.RUNNING
        or campaign.discovery_state != Campaign.DiscoveryState.RUNNING
        or run.state != SearchRun.State.RUNNING
    ):
        return None
    batch = (
        provider.poll(request_id=run.provider_request_id)
        if run.provider_request_id
        else provider.submit(request)
    )
    persisted = _persist_provider_response(run.pk, batch)
    return persisted, batch


def advance_search_run(
    run_id: uuid.UUID | str,
    *,
    provider: ExtractorProvider | None = None,
    resolver: MXResolver | None = None,
) -> SearchRun:
    run = SearchRun.objects.select_related("campaign", "query").get(pk=run_id)
    if run.state in {SearchRun.State.SUCCEEDED, SearchRun.State.FAILED_PERMANENT}:
        return run
    if run.campaign.state != Campaign.State.RUNNING:
        return run
    try:
        active_provider = provider or get_extractor_provider(
            run.provider, owner_id=run.campaign.created_by_id
        )
    except (AuthenticationError, ValidationProviderError) as exc:
        return _mark_permanent(run.pk, exc)
    active_resolver = resolver or (MockMXResolver() if run.provider == "fake" else DNSMXResolver())
    stored_success = str(run.response_json.get("status", "")).casefold() == "success"
    if run.raw_persisted_at is not None and run.processed_at is None and stored_success:
        try:
            return _process_persisted_response(
                run.pk, provider=active_provider, resolver=active_resolver
            )
        except TransientMXError as exc:
            return _mark_retryable(run.pk, exc)
        except DeduplicationConflict as exc:
            return _mark_permanent(run.pk, exc)
    stale_before = timezone.now() - timedelta(minutes=5)
    if (
        run.state == SearchRun.State.RUNNING
        and run.updated_at < stale_before
        and not run.provider_request_id
    ):
        return _mark_permanent(
            run.pk,
            PermanentProviderError(
                "La creación externa quedó ambigua tras el reinicio; no se reenvía el trabajo."
            ),
        )

    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().get(pk=run.campaign_id)
        current = SearchRun.objects.select_for_update().get(pk=run.pk)
        if (
            campaign.state != Campaign.State.RUNNING
            or campaign.discovery_state != Campaign.DiscoveryState.RUNNING
        ):
            return current
        if current.state == SearchRun.State.RUNNING:
            if current.updated_at >= stale_before:
                return current
        current.state = SearchRun.State.RUNNING
        current.started_at = current.started_at or timezone.now()
        current.next_poll_at = None
        current.save(update_fields=("state", "started_at", "next_poll_at", "updated_at"))

    request = SearchRequest(
        query=run.query.query_text,
        correlation_id=str(run.pk),
        idempotency_key=run.idempotency_key,
        limit=run.requested_limit,
    )
    try:
        provider_result = _call_provider_with_campaign_lock(
            run.pk,
            provider=active_provider,
            request=request,
        )
        if provider_result is None:
            return SearchRun.objects.get(pk=run.pk)
        persisted, batch = provider_result
        if batch.status == "PENDING":
            if (
                persisted.started_at is not None
                and persisted.started_at <= timezone.now() - timedelta(hours=4)
            ):
                return _mark_permanent(
                    run.pk,
                    PermanentProviderError(
                        "El resultado de Outscraper expiró antes de poder recuperarse."
                    ),
                )
            return _mark_pending(run.pk)
        if batch.status == "FAILED":
            return _mark_permanent(
                run.pk, PermanentProviderError("El trabajo del proveedor falló.")
            )
        return _process_persisted_response(
            run.pk, provider=active_provider, resolver=active_resolver
        )
    except RateLimitError as exc:
        return _mark_retryable(run.pk, exc)
    except RetryableProviderError as exc:
        # Without a provider ID, retrying submission could create a duplicate external job.
        if not run.provider_request_id:
            return _mark_permanent(
                run.pk,
                PermanentProviderError(
                    "Resultado ambiguo al crear el trabajo; no se reenvía para evitar duplicados."
                ),
            )
        return _mark_retryable(run.pk, exc)
    except TransientMXError as exc:
        return _mark_retryable(run.pk, exc)
    except DeduplicationConflict as exc:
        return _mark_permanent(run.pk, exc)
    except (
        AuthenticationError,
        CostLimitError,
        ValidationProviderError,
        PermanentProviderError,
    ) as exc:
        return _mark_permanent(run.pk, exc)


def recoverable_search_run_ids() -> list[uuid.UUID]:
    now = timezone.now()
    return list(
        SearchRun.objects.filter(
            Q(state=SearchRun.State.PENDING)
            | Q(state=SearchRun.State.RETRY_WAIT, next_poll_at__lte=now)
            | Q(state=SearchRun.State.RUNNING, updated_at__lt=now - timedelta(minutes=5)),
            campaign__state=Campaign.State.RUNNING,
            campaign__discovery_state=Campaign.DiscoveryState.RUNNING,
        ).values_list("pk", flat=True)
    )


def recoverable_campaign_ids() -> list[uuid.UUID]:
    """Find running campaigns whose next durable query has no active run."""

    return list(
        Campaign.objects.filter(
            state=Campaign.State.RUNNING,
            discovery_state=Campaign.DiscoveryState.RUNNING,
        )
        .exclude(search_runs__state__in=ACTIVE_RUN_STATES)
        .values_list("pk", flat=True)
        .distinct()
    )
