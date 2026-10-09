from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.campaigns.models import Campaign, SearchRun
from apps.prospects.models import Prospect

RESERVATION_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class PipelineReservation:
    prospect_id: uuid.UUID
    token: str
    generation: int | None = None
    regeneration_nonce: str = ""


def _campaign_allows_work(campaign: Campaign, *, manual: bool) -> bool:
    if manual:
        return campaign.state in {
            Campaign.State.DISCOVERING,
            Campaign.State.AWAITING_APPROVAL,
            Campaign.State.RUNNING,
            Campaign.State.PAUSED,
        } or (
            campaign.state == Campaign.State.COMPLETED
            and campaign.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY
        )
    return campaign.state in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}


@transaction.atomic
def reserve_prospect_pipeline(
    prospect_id: uuid.UUID | str,
    *,
    allowed_states: Collection[str] = (
        Prospect.PipelineState.DISCOVERED,
        Prospect.PipelineState.EMAIL_FOUND,
    ),
    manual: bool = False,
) -> PipelineReservation | None:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    if prospect.pipeline_state not in allowed_states or not _campaign_allows_work(
        prospect.campaign, manual=manual
    ):
        return None
    stale_before = timezone.now() - RESERVATION_TTL
    if (
        prospect.pipeline_reservation_key
        and prospect.pipeline_reserved_at is not None
        and prospect.pipeline_reserved_at > stale_before
    ):
        return None
    token = uuid.uuid4().hex
    prospect.pipeline_reservation_key = token
    prospect.pipeline_reserved_at = timezone.now()
    prospect.pipeline_claimed_at = None
    prospect.save(
        update_fields=(
            "pipeline_reservation_key",
            "pipeline_reserved_at",
            "pipeline_claimed_at",
            "updated_at",
        )
    )
    return PipelineReservation(prospect_id=prospect.pk, token=token)


def reserve_run_prospects(run: SearchRun) -> tuple[PipelineReservation, ...]:
    reservations: list[PipelineReservation] = []
    prospect_ids = Prospect.objects.filter(
        source_run=run,
        pipeline_state__in=(
            Prospect.PipelineState.DISCOVERED,
            Prospect.PipelineState.EMAIL_FOUND,
        ),
    ).values_list("pk", flat=True)
    for prospect_id in prospect_ids:
        reservation = reserve_prospect_pipeline(prospect_id)
        if reservation is not None:
            reservations.append(reservation)
    return tuple(reservations)


@transaction.atomic
def claim_prospect_pipeline(
    prospect_id: uuid.UUID | str,
    *,
    token: str,
    manual: bool,
) -> bool:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    if (
        prospect.pipeline_reservation_key != token
        or prospect.pipeline_claimed_at is not None
        or not _campaign_allows_work(prospect.campaign, manual=manual)
    ):
        return False
    prospect.pipeline_claimed_at = timezone.now()
    prospect.save(update_fields=("pipeline_claimed_at", "updated_at"))
    return True


@transaction.atomic
def clear_prospect_pipeline_reservation(prospect_id: uuid.UUID | str, *, token: str) -> None:
    prospect = Prospect.objects.select_for_update().get(pk=prospect_id)
    if prospect.pipeline_reservation_key != token:
        return
    prospect.pipeline_reservation_key = ""
    prospect.pipeline_reserved_at = None
    prospect.pipeline_claimed_at = None
    prospect.save(
        update_fields=(
            "pipeline_reservation_key",
            "pipeline_reserved_at",
            "pipeline_claimed_at",
            "updated_at",
        )
    )
