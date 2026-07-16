from __future__ import annotations

import uuid

from celery import shared_task

from apps.audit.models import BackgroundJob
from apps.audit.services import finish_job, start_job
from apps.campaigns.extraction import (
    advance_search_run,
    ensure_next_search_run,
    recoverable_campaign_ids,
    recoverable_search_run_ids,
)
from apps.campaigns.models import SearchRun
from apps.prospects.pipeline import reserve_run_prospects
from apps.prospects.tasks import process_prospect_pipeline


@shared_task(name="campaigns.orchestrate_extraction")  # type: ignore[untyped-decorator]
def orchestrate_extraction(campaign_id: str) -> str | None:
    run = ensure_next_search_run(campaign_id)
    if run is None:
        return None
    advance_extraction_run.delay(str(run.pk))
    return str(run.pk)


@shared_task(name="campaigns.advance_extraction_run")  # type: ignore[untyped-decorator]
def advance_extraction_run(run_id: str) -> str:
    job = start_job(
        idempotency_key=f"extract:{run_id}",
        task_name="campaigns.advance_extraction_run",
        entity_type="SearchRun",
        entity_id=run_id,
        queue="extraction",
    )
    try:
        run = advance_search_run(uuid.UUID(run_id))
        if run.state == SearchRun.State.SUCCEEDED:
            for reservation in reserve_run_prospects(run):
                process_prospect_pipeline.delay(
                    str(reservation.prospect_id),
                    reservation_token=reservation.token,
                )
            orchestrate_extraction.delay(str(run.campaign_id))
            finish_job(job)
        elif run.state == SearchRun.State.RETRY_WAIT:
            finish_job(
                job,
                state=BackgroundJob.State.RETRY_WAIT,
                error=run.error,
                next_retry_at=run.next_poll_at,
            )
        elif run.state == SearchRun.State.FAILED_PERMANENT:
            finish_job(job, state=BackgroundJob.State.FAILED, error=run.error)
        elif run.state == SearchRun.State.CANCELLED:
            finish_job(job, state=BackgroundJob.State.CANCELLED)
        else:
            finish_job(job, state=BackgroundJob.State.PENDING)
        return run.state
    except Exception as exc:
        finish_job(job, state=BackgroundJob.State.FAILED, error=exc)
        raise


@shared_task(name="campaigns.recover_extraction_runs")  # type: ignore[untyped-decorator]
def recover_extraction_runs() -> int:
    run_ids = recoverable_search_run_ids()
    for run_id in run_ids:
        advance_extraction_run.delay(str(run_id))
    campaign_ids = recoverable_campaign_ids()
    for campaign_id in campaign_ids:
        orchestrate_extraction.delay(str(campaign_id))
    return len(run_ids) + len(campaign_ids)
