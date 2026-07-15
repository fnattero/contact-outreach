from __future__ import annotations

import uuid

from celery import shared_task

from apps.campaigns.extraction import (
    advance_search_run,
    ensure_next_search_run,
    recoverable_campaign_ids,
    recoverable_search_run_ids,
)
from apps.campaigns.models import SearchRun


@shared_task(name="campaigns.orchestrate_extraction")  # type: ignore[untyped-decorator]
def orchestrate_extraction(campaign_id: str) -> str | None:
    run = ensure_next_search_run(campaign_id)
    if run is None:
        return None
    advance_extraction_run.delay(str(run.pk))
    return str(run.pk)


@shared_task(name="campaigns.advance_extraction_run")  # type: ignore[untyped-decorator]
def advance_extraction_run(run_id: str) -> str:
    run = advance_search_run(uuid.UUID(run_id))
    if run.state == SearchRun.State.SUCCEEDED:
        orchestrate_extraction.delay(str(run.campaign_id))
    return run.state


@shared_task(name="campaigns.recover_extraction_runs")  # type: ignore[untyped-decorator]
def recover_extraction_runs() -> int:
    run_ids = recoverable_search_run_ids()
    for run_id in run_ids:
        advance_extraction_run.delay(str(run_id))
    campaign_ids = recoverable_campaign_ids()
    for campaign_id in campaign_ids:
        orchestrate_extraction.delay(str(campaign_id))
    return len(run_ids) + len(campaign_ids)
