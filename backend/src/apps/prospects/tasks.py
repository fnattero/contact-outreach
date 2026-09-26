from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from celery import shared_task
from django.contrib.auth.models import User
from django.utils import timezone

from apps.audit.models import BackgroundJob
from apps.audit.services import finish_job, start_job
from apps.campaigns.models import Campaign
from apps.prospects.email_validation import TransientMXError
from apps.prospects.enrichment import enrich_prospect
from apps.prospects.exceptions import ProspectPipelineInactive, StaleProspectAnalysis
from apps.prospects.models import Prospect
from apps.prospects.pipeline import (
    claim_prospect_pipeline,
    clear_prospect_pipeline_reservation,
    reserve_prospect_pipeline,
)
from apps.prospects.services import (
    discover_website_email,
    mark_fixed_campaign_prospect_ready,
    skip_website_email_after_mx_retries,
)

CONTACT_MX_MAX_ATTEMPTS = 3
CONTACT_MX_RETRY_BASE_SECONDS = 60


def _contact_mx_retry_at(attempt: int) -> datetime:
    delay_seconds = min(15 * 60, CONTACT_MX_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)))
    return timezone.now() + timedelta(seconds=delay_seconds)


@shared_task(name="prospects.process_pipeline")  # type: ignore[untyped-decorator]
def process_prospect_pipeline(
    prospect_id: str,
    *,
    regeneration_nonce: str = "",
    actor_id: int | None = None,
    reservation_token: str = "",
    analysis_generation: int | None = None,
) -> str:
    prospect = Prospect.objects.get(pk=uuid.UUID(prospect_id))
    generation = (
        analysis_generation if analysis_generation is not None else prospect.analysis_generation
    )
    actor = User.objects.filter(pk=actor_id).first() if actor_id is not None else None
    token = reservation_token
    if not token:
        reservation = reserve_prospect_pipeline(
            prospect.pk,
            allowed_states=(
                Prospect.PipelineState.DISCOVERED,
                Prospect.PipelineState.EMAIL_FOUND,
                Prospect.PipelineState.ENRICHED,
                Prospect.PipelineState.ERROR,
                Prospect.PipelineState.SKIPPED_IRRELEVANT,
                Prospect.PipelineState.QUEUED,
            ),
            manual=actor is not None,
        )
        if reservation is None:
            return prospect.pipeline_state
        token = reservation.token
    if not claim_prospect_pipeline(prospect.pk, token=token, manual=actor is not None):
        return prospect.pipeline_state
    job = start_job(
        idempotency_key=f"pipeline:{prospect_id}:{generation}",
        task_name="prospects.process_pipeline",
        entity_type="Prospect",
        entity_id=prospect_id,
        queue="analysis",
    )
    try:
        prospect.refresh_from_db()
        if prospect.pipeline_state == Prospect.PipelineState.DISCOVERED:
            snapshot = enrich_prospect(prospect.pk)
            try:
                prospect = discover_website_email(prospect.pk, snapshot=snapshot)
            except TransientMXError as exc:
                if job.attempts >= CONTACT_MX_MAX_ATTEMPTS:
                    prospect = skip_website_email_after_mx_retries(
                        prospect.pk,
                        snapshot=snapshot,
                    )
                    finish_job(job)
                else:
                    finish_job(
                        job,
                        state=BackgroundJob.State.RETRY_WAIT,
                        error=exc,
                        next_retry_at=_contact_mx_retry_at(job.attempts),
                    )
                return prospect.pipeline_state
            if prospect.pipeline_state in {
                Prospect.PipelineState.SKIPPED_NO_EMAIL,
                Prospect.PipelineState.SKIPPED_DUPLICATE,
            }:
                finish_job(job)
                return prospect.pipeline_state
        if prospect.pipeline_state == Prospect.PipelineState.EMAIL_FOUND:
            enrich_prospect(prospect.pk)
        prospect.refresh_from_db()
        prospect = mark_fixed_campaign_prospect_ready(prospect.pk)
        if prospect.campaign.state == Campaign.State.DISCOVERING:
            from apps.campaigns.approval import maybe_move_campaign_to_approval

            maybe_move_campaign_to_approval(prospect.campaign_id)
        finish_job(job)
        return prospect.pipeline_state
    except (ProspectPipelineInactive, StaleProspectAnalysis):
        finish_job(job, state=BackgroundJob.State.CANCELLED)
        return prospect.pipeline_state
    except Exception as exc:
        finish_job(job, state=BackgroundJob.State.FAILED, error=exc)
        raise
    finally:
        clear_prospect_pipeline_reservation(prospect.pk, token=token)


@shared_task(name="prospects.recover_pipeline")  # type: ignore[untyped-decorator]
def recover_prospect_pipeline() -> int:
    scheduled = 0
    now = timezone.now()
    contact_pipeline_rows = list(
        Prospect.objects.filter(
            pipeline_state__in=(
                Prospect.PipelineState.DISCOVERED,
                Prospect.PipelineState.EMAIL_FOUND,
            ),
            campaign__state=Campaign.State.DISCOVERING,
        ).values_list("pk", "analysis_generation")
    )
    contact_job_keys = {
        f"pipeline:{prospect_id}:{generation}" for prospect_id, generation in contact_pipeline_rows
    }
    contact_jobs = {
        job.idempotency_key: job
        for job in BackgroundJob.objects.filter(idempotency_key__in=contact_job_keys)
    }
    for prospect_id, generation in contact_pipeline_rows:
        job = contact_jobs.get(f"pipeline:{prospect_id}:{generation}")
        if job is not None and job.state == BackgroundJob.State.RETRY_WAIT:
            if job.next_retry_at is None or job.next_retry_at > now:
                continue
        reservation = reserve_prospect_pipeline(prospect_id)
        if reservation is None:
            continue
        process_prospect_pipeline.delay(
            str(prospect_id),
            reservation_token=reservation.token,
        )
        scheduled += 1
    return scheduled
