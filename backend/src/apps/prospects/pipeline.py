from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import OuterRef, QuerySet, Subquery
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.campaigns.models import Campaign, SearchRun
from apps.campaigns.services import PROMPT_VERSION, SCHEMA_VERSION
from apps.prospects.exceptions import ProspectPipelineInactive, StaleProspectAnalysis
from apps.prospects.models import AIAnalysis, Prospect

RESERVATION_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class PipelineReservation:
    prospect_id: uuid.UUID
    token: str
    generation: int | None = None
    regeneration_nonce: str = ""


def _campaign_allows_work(campaign: Campaign, *, manual: bool) -> bool:
    if manual:
        return campaign.state in {Campaign.State.RUNNING, Campaign.State.PAUSED} or (
            campaign.state == Campaign.State.COMPLETED
            and campaign.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY
        )
    return campaign.state in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}


def outdated_analysis_candidates(campaign: Campaign) -> QuerySet[Prospect]:
    latest = AIAnalysis.objects.filter(prospect=OuterRef("pk")).order_by("-analyzed_at")
    return (
        Prospect.objects.filter(
            campaign=campaign,
            pipeline_state__in=(
                Prospect.PipelineState.ERROR,
                Prospect.PipelineState.SKIPPED_IRRELEVANT,
            ),
        )
        .annotate(
            latest_analysis_status=Subquery(latest.values("status")[:1]),
            latest_analysis_score=Subquery(latest.values("relevance_score")[:1]),
            latest_prompt_version=Subquery(latest.values("prompt_version")[:1]),
            latest_schema_version=Subquery(latest.values("schema_version")[:1]),
        )
        .filter(
            models.Q(latest_analysis_status=AIAnalysis.Status.ERROR)
            | models.Q(latest_analysis_score__gt=0, latest_analysis_score__lt=10)
        )
        .filter(
            ~models.Q(latest_prompt_version__startswith=PROMPT_VERSION)
            | ~models.Q(latest_schema_version=SCHEMA_VERSION)
        )
        .order_by("created_at")
    )


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


@transaction.atomic
def begin_analysis_generation(
    prospect_id: uuid.UUID | str,
    *,
    expected_generation: int | None,
    manual: bool,
) -> int:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    if not _campaign_allows_work(prospect.campaign, manual=manual):
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
    prospect = (
        Prospect.objects.select_for_update()
        .select_related("campaign", "campaign__workspace")
        .get(pk=prospect_id)
    )
    require_user_capability(
        actor,
        Capability.MANAGE_CAMPAIGNS,
        workspace_id=prospect.campaign.workspace_id,
    )
    raise ValidationError(
        "Los análisis anteriores se conservan como historial de solo lectura. "
        "Las campañas nuevas usan el mensaje fijo y no regeneran contenido con IA."
    )


@transaction.atomic
def request_outdated_analysis_regenerations(
    *,
    campaign_id: uuid.UUID | str,
    actor: User,
) -> tuple[PipelineReservation, ...]:
    campaign = Campaign.objects.select_for_update().select_related("workspace").get(pk=campaign_id)
    require_user_capability(
        actor,
        Capability.MANAGE_CAMPAIGNS,
        workspace_id=campaign.workspace_id,
    )
    raise ValidationError(
        "Los análisis anteriores se conservan como historial de solo lectura. "
        "No se vuelven a analizar ni a enviar."
    )
