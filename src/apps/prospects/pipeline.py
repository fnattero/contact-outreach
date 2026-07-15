from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import Campaign, OutboundMessage, SearchRun
from apps.prospects.exceptions import ProspectPipelineInactive, StaleProspectAnalysis
from apps.prospects.models import Prospect

RESERVATION_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class PipelineReservation:
    prospect_id: uuid.UUID
    token: str
    generation: int | None = None
    regeneration_nonce: str = ""


def _campaign_allows_work(campaign_state: str, *, manual: bool) -> bool:
    if manual:
        return campaign_state in {Campaign.State.RUNNING, Campaign.State.PAUSED}
    return campaign_state == Campaign.State.RUNNING


@transaction.atomic
def reserve_prospect_pipeline(
    prospect_id: uuid.UUID | str,
    *,
    allowed_states: Collection[str] = (Prospect.PipelineState.EMAIL_FOUND,),
    manual: bool = False,
) -> PipelineReservation | None:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    if prospect.pipeline_state not in allowed_states or not _campaign_allows_work(
        prospect.campaign.state, manual=manual
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
        pipeline_state=Prospect.PipelineState.EMAIL_FOUND,
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
        or not _campaign_allows_work(prospect.campaign.state, manual=manual)
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


@transaction.atomic
def begin_analysis_generation(
    prospect_id: uuid.UUID | str,
    *,
    expected_generation: int | None,
    manual: bool,
) -> int:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    if not _campaign_allows_work(prospect.campaign.state, manual=manual):
        raise ProspectPipelineInactive("La campaña ya no admite análisis.")
    if expected_generation is not None:
        if prospect.analysis_generation != expected_generation:
            raise StaleProspectAnalysis("Una generación más nueva reemplazó este análisis.")
        return expected_generation
    prospect.analysis_generation += 1
    prospect.save(update_fields=("analysis_generation", "updated_at"))
    return prospect.analysis_generation


@transaction.atomic
def defer_prospect_pipeline(
    prospect_id: uuid.UUID | str,
    *,
    current_token: str,
    generation: int,
) -> str:
    prospect = Prospect.objects.select_for_update().get(pk=prospect_id)
    if prospect.analysis_generation != generation:
        raise StaleProspectAnalysis("Una generación más nueva reemplazó este reintento.")
    if prospect.pipeline_reservation_key != current_token:
        raise StaleProspectAnalysis("La reserva del reintento ya fue reemplazada.")
    next_token = uuid.uuid4().hex
    prospect.pipeline_reservation_key = next_token
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
    return next_token


@transaction.atomic
def request_manual_regeneration(
    *,
    prospect_id: uuid.UUID | str,
    actor: User,
) -> PipelineReservation:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    if prospect.campaign.created_by_id != actor.pk:
        raise ValidationError("El prospecto pertenece a otro propietario.")
    if not _campaign_allows_work(prospect.campaign.state, manual=True):
        raise ValidationError("La campaña ya está cerrada y no admite regeneraciones.")
    if not prospect.web_snapshots.exists():
        raise ValidationError("El prospecto todavía no tiene enriquecimiento auditable.")
    if prospect.outbound_messages.filter(
        state__in=(
            OutboundMessage.State.QUEUED,
            OutboundMessage.State.SENDING,
            OutboundMessage.State.RECONCILING,
            OutboundMessage.State.SENT,
            OutboundMessage.State.DRY_RUN_COMPLETED,
        )
    ).exists():
        raise ValidationError("No se puede regenerar un mensaje que ya entró en entrega.")
    before = {"analysis_generation": prospect.analysis_generation}
    prospect.analysis_generation += 1
    token = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    prospect.pipeline_reservation_key = token
    prospect.pipeline_reserved_at = timezone.now()
    prospect.pipeline_claimed_at = None
    prospect.save(
        update_fields=(
            "analysis_generation",
            "pipeline_reservation_key",
            "pipeline_reserved_at",
            "pipeline_claimed_at",
            "updated_at",
        )
    )
    record_event(
        action="prospect.regeneration_requested",
        entity=prospect,
        actor=actor,
        before=before,
        after={"analysis_generation": prospect.analysis_generation},
    )
    return PipelineReservation(
        prospect_id=prospect.pk,
        token=token,
        generation=prospect.analysis_generation,
        regeneration_nonce=nonce,
    )
