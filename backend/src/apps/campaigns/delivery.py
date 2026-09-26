from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.models import BackgroundJob
from apps.audit.services import record_event
from apps.campaigns.content import freeze_message_content
from apps.campaigns.models import (
    Campaign,
    CampaignDeliveryReservation,
    OutboundMessage,
)
from apps.campaigns.services import TERMINAL_DISCOVERY_STATES, transition_campaign
from apps.catalogs.services import verify_catalog
from apps.compliance.models import ContactLedger
from apps.compliance.services import lock_email_eligibility
from apps.configuration.integrations import redact_provider_error
from apps.contacts.models import CampaignEnrollment
from apps.contacts.services import outbound_eligibility_error
from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
    GmailProvider,
    GmailReplyRequest,
    GmailSendRequest,
    GmailSendResult,
    PermanentProviderError,
    ProviderError,
    RateLimitError,
    RetryableProviderError,
    ValidationProviderError,
)
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.mime import (
    MAX_SOURCE_PDF_BYTES,
    PdfAttachment,
    build_message,
    build_reply_message,
    deterministic_message_id,
)
from apps.mailbox.models import GmailConnection
from apps.mailbox.services import provider_for_connection

MAX_SEND_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 900
STALE_SENDING_AFTER = timedelta(minutes=2)
FAILURE_WINDOW_SIZE = 20
FAILURE_RATE_THRESHOLD = 0.30
CONSECUTIVE_FAILURE_THRESHOLD = 5
INITIAL_MESSAGE_KINDS = (
    OutboundMessage.Kind.FIRST_CONTACT,
    OutboundMessage.Kind.INITIAL,
)
CAMPAIGN_DELIVERY_KINDS = (*INITIAL_MESSAGE_KINDS, OutboundMessage.Kind.CAMPAIGN_REMINDER)
SAME_DAY_RESERVATION_KINDS = (
    OutboundMessage.Kind.INITIAL,
    OutboundMessage.Kind.CAMPAIGN_REMINDER,
)
SAME_DAY_RESCHEDULE_REASON = "Se pasó al próximo día permitido para evitar correos duplicados."
CAMPAIGN_RESERVATION_ZONE = ZoneInfo("America/Argentina/Buenos_Aires")


@dataclass(frozen=True, slots=True)
class SendEffect:
    message_id: uuid.UUID
    connection_id: uuid.UUID
    raw_message: bytes
    rfc_message_id: str
    recipient: str
    recipient_normalized: str
    idempotency_key: str
    kind: str
    gmail_thread_id: str


def _error_text(error: Exception, *, owner_id: int | None = None) -> str:
    return redact_provider_error(error, owner_id=owner_id)


def _backoff(attempt: int, *, retry_after: float | None = None) -> timedelta:
    if retry_after is not None:
        seconds = max(1, min(MAX_BACKOFF_SECONDS, int(retry_after)))
    else:
        seconds = min(MAX_BACKOFF_SECONDS, 30 * (2 ** max(0, attempt - 1)))
    return timedelta(seconds=seconds)


def _local_day_bounds(campaign: Campaign, now: datetime) -> tuple[datetime, datetime]:
    zone = ZoneInfo(campaign.timezone_name)
    local_now = now.astimezone(zone)
    start_local = datetime.combine(local_now.date(), time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _inside_schedule(campaign: Campaign, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(campaign.timezone_name))
    return (
        local.weekday() in campaign.weekdays
        and campaign.window_start <= local.time().replace(tzinfo=None) < campaign.window_end
    )


def _daily_slot_available(campaign: Campaign, now: datetime) -> bool:
    start, end = _local_day_bounds(campaign, now)
    reserved = OutboundMessage.objects.filter(
        campaign=campaign,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        delivery_reserved_at__gte=start,
        delivery_reserved_at__lt=end,
        state__in=(
            OutboundMessage.State.SENDING,
            OutboundMessage.State.RECONCILING,
            OutboundMessage.State.SENT,
        ),
    ).count()
    return reserved < campaign.daily_limit


def _interval_elapsed(campaign: Campaign, now: datetime) -> bool:
    latest = (
        OutboundMessage.objects.filter(
            campaign=campaign,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            delivery_reserved_at__isnull=False,
            state__in=(
                OutboundMessage.State.SENDING,
                OutboundMessage.State.RECONCILING,
                OutboundMessage.State.SENT,
            ),
        )
        .order_by("-delivery_reserved_at")
        .values_list("delivery_reserved_at", flat=True)
        .first()
    )
    return latest is None or latest + timedelta(minutes=campaign.message_interval_minutes) <= now


def _shift_to_business_window(campaign: Campaign, candidate: datetime) -> datetime:
    zone = ZoneInfo(campaign.timezone_name)
    local = candidate.astimezone(zone)
    allowed_days = set(campaign.weekdays)
    if not allowed_days:
        raise ValidationError("La campaña no tiene días de envío configurados.")
    local_time = local.time().replace(tzinfo=None)
    if local.weekday() in allowed_days:
        if campaign.window_start <= local_time < campaign.window_end:
            return local.astimezone(UTC)
        if local_time < campaign.window_start:
            return datetime.combine(local.date(), campaign.window_start, tzinfo=zone).astimezone(
                UTC
            )
    next_date = local.date() + timedelta(days=1)
    for _ in range(7):
        if next_date.weekday() in allowed_days:
            return datetime.combine(next_date, campaign.window_start, tzinfo=zone).astimezone(UTC)
        next_date += timedelta(days=1)
    raise ValidationError("La campaña no tiene un próximo día de envío disponible.")


def _next_permitted_sending_day(campaign: Campaign, now: datetime) -> datetime:
    zone = ZoneInfo(campaign.timezone_name)
    local_date = now.astimezone(zone).date() + timedelta(days=1)
    return _shift_to_business_window(
        campaign,
        datetime.combine(local_date, campaign.window_start, tzinfo=zone),
    )


def _reserve_ledger(message: OutboundMessage, now: datetime) -> ContactLedger:
    try:
        ledger, _ = ContactLedger.objects.select_for_update().get_or_create(
            normalized_email=message.recipient_normalized
        )
    except IntegrityError:
        ledger = ContactLedger.objects.select_for_update().get(
            normalized_email=message.recipient_normalized
        )
    if ledger.reserved_message_id and ledger.reserved_message_id != message.pk:
        other = OutboundMessage.objects.get(pk=ledger.reserved_message_id)
        if other.state in {
            OutboundMessage.State.SENDING,
            OutboundMessage.State.RECONCILING,
        }:
            raise ValidationError("El correo ya tiene otro primer contacto reservado.")
        ledger.reserved_message = None
        ledger.reserved_at = None
    message.contact_sequence = ledger.next_sequence
    ledger.reserved_message = message
    ledger.reserved_at = now
    ledger.save(update_fields=("reserved_message", "reserved_at", "updated_at"))
    return ledger


def _release_ledger(message: OutboundMessage) -> None:
    ledger = (
        ContactLedger.objects.select_for_update()
        .filter(normalized_email=message.recipient_normalized)
        .first()
    )
    if ledger is not None and ledger.reserved_message_id == message.pk:
        ledger.reserved_message = None
        ledger.reserved_at = None
        ledger.save(update_fields=("reserved_message", "reserved_at", "updated_at"))


def _reservation_local_date(now: datetime) -> date:
    return now.astimezone(CAMPAIGN_RESERVATION_ZONE).date()


def _reserve_campaign_delivery_day(message: OutboundMessage, now: datetime) -> bool:
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    target_date = _reservation_local_date(now)
    existing = (
        CampaignDeliveryReservation.objects.select_for_update().filter(message=message).first()
    )
    if existing is not None and (existing.status == CampaignDeliveryReservation.Status.CONSUMED):
        return False
    try:
        with transaction.atomic():
            if existing is None:
                CampaignDeliveryReservation.objects.create(
                    workspace=campaign.workspace,
                    campaign=campaign,
                    message=message,
                    email_address=message.email_address,
                    normalized_email=message.recipient_normalized,
                    local_date=target_date,
                )
            else:
                existing.workspace = campaign.workspace
                existing.campaign = campaign
                existing.email_address = message.email_address
                existing.normalized_email = message.recipient_normalized
                existing.local_date = target_date
                existing.status = CampaignDeliveryReservation.Status.RESERVED
                existing.consumed_at = None
                existing.released_at = None
                existing.save(
                    update_fields=(
                        "workspace",
                        "campaign",
                        "email_address",
                        "normalized_email",
                        "local_date",
                        "status",
                        "consumed_at",
                        "released_at",
                        "updated_at",
                    )
                )
    except IntegrityError:
        return False
    return True


def _release_campaign_delivery_day(
    message: OutboundMessage,
    *,
    now: datetime | None = None,
) -> None:
    released_at = now or timezone.now()
    reservation = (
        CampaignDeliveryReservation.objects.select_for_update()
        .filter(
            message=message,
            status=CampaignDeliveryReservation.Status.RESERVED,
        )
        .first()
    )
    if reservation is None:
        return
    reservation.status = CampaignDeliveryReservation.Status.RELEASED
    reservation.released_at = released_at
    reservation.save(update_fields=("status", "released_at", "updated_at"))


def _release_delivery_guard(message: OutboundMessage, *, now: datetime | None = None) -> None:
    if message.kind == OutboundMessage.Kind.FIRST_CONTACT:
        _release_ledger(message)
    elif message.kind in SAME_DAY_RESERVATION_KINDS:
        _release_campaign_delivery_day(message, now=now)


def _consume_campaign_delivery_day(message: OutboundMessage, now: datetime) -> None:
    reservation = (
        CampaignDeliveryReservation.objects.select_for_update().filter(message=message).first()
    )
    # A send already accepted by Gmail must still be persisted after an upgrade from
    # the legacy path, even when no reservation row existed before deployment.
    if reservation is None:
        return
    reservation.status = CampaignDeliveryReservation.Status.CONSUMED
    reservation.consumed_at = now
    reservation.released_at = None
    reservation.save(update_fields=("status", "consumed_at", "released_at", "updated_at"))


def _reschedule_same_day_conflict(message: OutboundMessage, now: datetime) -> None:
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    next_window = _next_permitted_sending_day(campaign, now)
    message.state = OutboundMessage.State.QUEUED
    message.next_attempt_at = next_window
    message.scheduled_for = next_window
    message.delivery_reserved_at = None
    message.sending_started_at = None
    message.error = SAME_DAY_RESCHEDULE_REASON
    message.save(
        update_fields=(
            "state",
            "next_attempt_at",
            "scheduled_for",
            "delivery_reserved_at",
            "sending_started_at",
            "error",
            "updated_at",
        )
    )
    record_event(
        action="message.same_day_rescheduled",
        entity=message,
        actor=None,
        after={
            "next_attempt_at": next_window.isoformat(),
            "reason": SAME_DAY_RESCHEDULE_REASON,
        },
    )


def _message_attachments(message: OutboundMessage) -> tuple[PdfAttachment, ...]:
    snapshots = tuple(message.attachments.select_related("catalog").order_by("position"))
    if message.kind == OutboundMessage.Kind.CAMPAIGN_REMINDER:
        if snapshots:
            raise ValidationError("El recordatorio no debe incluir archivos adjuntos.")
        return ()
    if message.kind == OutboundMessage.Kind.INITIAL and not snapshots:
        raise ValidationError("La propuesta inicial perdió sus PDFs aprobados.")
    if not snapshots:
        # Historical FIRST_CONTACT rows created after the attachment backfill retain
        # their original single-catalog behavior without manufacturing new history.
        catalog = message.catalog
        if message.kind != OutboundMessage.Kind.FIRST_CONTACT or catalog is None:
            return ()
        if message.catalog_version != catalog.version:
            raise ValidationError("La versión de catálogo del mensaje es inconsistente.")
        verify_catalog(catalog)
        with catalog.file.open("rb") as handle:
            content = handle.read()
        return (PdfAttachment(filename=catalog.original_filename, content=content),)

    payloads: list[PdfAttachment] = []
    for snapshot in snapshots:
        catalog = snapshot.catalog
        if (
            snapshot.catalog_version != catalog.version
            or snapshot.storage_key != catalog.storage_key
            or snapshot.filename != catalog.original_filename
            or snapshot.byte_size != catalog.byte_size
            or snapshot.sha256 != catalog.sha256
        ):
            raise ValidationError("Los PDFs aprobados cambiaron y no se puede enviar el correo.")
        verify_catalog(catalog)
        with catalog.file.open("rb") as handle:
            content = handle.read()
        if len(content) != snapshot.byte_size:
            raise ValidationError("Uno de los PDFs aprobados perdió integridad.")
        payloads.append(PdfAttachment(filename=snapshot.filename, content=content))
    if sum(len(item.content) for item in payloads) > MAX_SOURCE_PDF_BYTES:
        raise ValidationError("Los PDFs superan el límite combinado de 17 MiB.")
    return tuple(payloads)


def _message_bytes(message: OutboundMessage, *, sender: str) -> tuple[bytes, str, str, int]:
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    rfc_message_id = message.message_id or deterministic_message_id(message.idempotency_key)
    message_date = message.scheduled_for or message.created_at
    if message.kind == OutboundMessage.Kind.CAMPAIGN_REMINDER:
        references = tuple(str(value) for value in message.references if str(value))
        if not message.gmail_thread_id:
            raise ValidationError("El recordatorio perdió el hilo original de Gmail.")
        built = build_reply_message(
            sender=sender,
            recipient=message.recipient,
            subject=message.subject,
            body_text=message.body_text,
            message_id=rfc_message_id,
            sent_at=message_date,
            thread_references=references,
            in_reply_to=message.in_reply_to,
            campaign_header=str(campaign.pk),
            message_header=str(message.pk),
        )
    else:
        built = build_message(
            sender=sender,
            recipient=message.recipient,
            subject=message.subject,
            body_text=message.body_text,
            message_id=rfc_message_id,
            sent_at=message_date,
            campaign_header=str(campaign.pk),
            message_header=str(message.pk),
            pdf_attachments=_message_attachments(message),
        )
    return built.raw, built.sha256, rfc_message_id, built.size


@transaction.atomic
def complete_dry_run(message_id: uuid.UUID | str, *, now: datetime | None = None) -> str:
    moment = now or timezone.now()
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__created_by", "catalog")
        .get(pk=message_id)
    )
    if message.state in {
        OutboundMessage.State.REVIEW_READY,
        OutboundMessage.State.DRY_RUN_COMPLETED,
        OutboundMessage.State.SENT,
        OutboundMessage.State.CANCELLED,
    }:
        return message.state
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    if campaign.state != Campaign.State.RUNNING:
        return message.state
    if (
        message.delivery_mode != Campaign.DeliveryMode.DRY_RUN
        or campaign.delivery_mode != Campaign.DeliveryMode.DRY_RUN
    ):
        raise ValidationError("El mensaje no pertenece a una campaña en simulación.")
    if message.state == OutboundMessage.State.PREPARED:
        message.state = OutboundMessage.State.QUEUED
    eligibility_error = final_email_error(message)
    if eligibility_error:
        _mark_message_ineligible(message, eligibility_error, now=moment)
        return message.state
    connection = GmailConnection.objects.filter(workspace=campaign.workspace).first()
    sender = (
        connection.email if connection and connection.email else "dry-run@contact-outreach.invalid"
    )
    try:
        raw, mime_hash, rfc_message_id, mime_size = _message_bytes(message, sender=sender)
    except ValidationError as exc:
        reason = _error_text(exc)
        message.error = reason
        message.save(update_fields=("state", "error", "updated_at"))
        _pause_campaign(campaign.pk, reason)
        return message.state
    del raw
    message.message_id = rfc_message_id
    message.mime_sha256 = mime_hash
    message.mime_size = mime_size
    message.state = OutboundMessage.State.DRY_RUN_COMPLETED
    message.simulated_at = moment
    message.error = ""
    message.save(
        update_fields=(
            "message_id",
            "mime_sha256",
            "mime_size",
            "state",
            "simulated_at",
            "error",
            "updated_at",
        )
    )
    record_event(
        action="message.dry_run_completed",
        entity=message,
        actor=None,
        after={"state": message.state, "mime_sha256": mime_hash},
    )
    return message.state


@transaction.atomic
def complete_review_only(message_id: uuid.UUID | str) -> str:
    """Make a review-only candidate terminal without constructing MIME or touching Gmail."""

    message = (
        OutboundMessage.objects.select_for_update().select_related("campaign").get(pk=message_id)
    )
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    if (
        message.delivery_mode != Campaign.DeliveryMode.REVIEW_ONLY
        or campaign.delivery_mode != Campaign.DeliveryMode.REVIEW_ONLY
    ):
        raise ValidationError("El mensaje no pertenece a una campaña de solo revisión.")
    if message.state == OutboundMessage.State.REVIEW_READY:
        return message.state
    if campaign.state != Campaign.State.RUNNING:
        return message.state
    if message.state not in (OutboundMessage.State.PREPARED, OutboundMessage.State.QUEUED):
        raise ValidationError("El mensaje de revisión no está en un estado preparable.")
    message.state = OutboundMessage.State.REVIEW_READY
    message.error = ""
    message.save(update_fields=("state", "error", "updated_at"))
    record_event(
        action="message.review_ready",
        entity=message,
        actor=None,
        after={"state": message.state},
    )
    return message.state


def _pause_campaign(campaign_id: uuid.UUID, reason: str) -> None:
    campaign = Campaign.objects.filter(pk=campaign_id).first()
    if campaign is not None and campaign.state == Campaign.State.RUNNING:
        transition_campaign(
            campaign_id=campaign_id,
            target_state=Campaign.State.PAUSED,
            actor=None,
            reason=reason,
        )


def _provider_delivery_results(campaign_id: uuid.UUID) -> tuple[bool, ...]:
    messages = list(
        OutboundMessage.objects.filter(
            campaign_id=campaign_id,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            state__in=(OutboundMessage.State.SENT, OutboundMessage.State.SEND_FAILED),
        )
        .order_by("-updated_at")
        .values("pk", "state")[: FAILURE_WINDOW_SIZE * 3]
    )
    jobs = {
        job.entity_id: job.state
        for job in BackgroundJob.objects.filter(
            task_name="mailbox.deliver_message",
            entity_id__in=[str(item["pk"]) for item in messages],
        )
    }
    results: list[bool] = []
    for item in messages:
        if item["state"] == OutboundMessage.State.SENT:
            results.append(False)
        elif jobs.get(str(item["pk"])) == BackgroundJob.State.FAILED:
            results.append(True)
        if len(results) == FAILURE_WINDOW_SIZE:
            break
    return tuple(results)


def _failure_threshold_reason(campaign_id: uuid.UUID) -> str:
    results = _provider_delivery_results(campaign_id)
    if len(results) >= CONSECUTIVE_FAILURE_THRESHOLD and all(
        results[:CONSECUTIVE_FAILURE_THRESHOLD]
    ):
        return "Pausa de seguridad: cinco fallos de entrega consecutivos."
    if (
        len(results) >= FAILURE_WINDOW_SIZE
        and sum(results[:FAILURE_WINDOW_SIZE]) / FAILURE_WINDOW_SIZE >= FAILURE_RATE_THRESHOLD
    ):
        return "Pausa de seguridad: la tasa de fallos alcanzó 30% en 20 entregas."
    return ""


def _pause_if_failure_threshold_reached(campaign_id: uuid.UUID) -> bool:
    reason = _failure_threshold_reason(campaign_id)
    if not reason:
        return False
    _pause_campaign(campaign_id, reason)
    return True


def _mark_message_ineligible(
    message: OutboundMessage,
    reason: str,
    *,
    now: datetime | None = None,
) -> None:
    moment = now or timezone.now()
    message.state = (
        OutboundMessage.State.SEND_FAILED
        if message.kind == OutboundMessage.Kind.FIRST_CONTACT
        else OutboundMessage.State.INELIGIBLE
    )
    message.next_attempt_at = None
    message.delivery_reserved_at = None
    message.sending_started_at = None
    message.error = reason
    _release_delivery_guard(message, now=moment)
    message.save(
        update_fields=(
            "state",
            "next_attempt_at",
            "delivery_reserved_at",
            "sending_started_at",
            "error",
            "updated_at",
        )
    )
    if message.campaign_enrollment_id is not None:
        CampaignEnrollment.objects.filter(pk=message.campaign_enrollment_id).exclude(
            state=CampaignEnrollment.State.RESPONDED
        ).update(
            state=CampaignEnrollment.State.INELIGIBLE,
            exclusion_reason=reason[:200],
            updated_at=moment,
        )
    BackgroundJob.objects.filter(idempotency_key=f"deliver:{message.pk}").update(
        state=BackgroundJob.State.CANCELLED,
        finished_at=moment,
        error=reason,
    )
    record_event(
        action="message.ineligible",
        entity=message,
        actor=None,
        after={"state": message.state, "reason": reason},
    )


@transaction.atomic
def _prepare_live_effect(message_id: uuid.UUID | str, now: datetime) -> SendEffect | None:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__created_by", "catalog", "prospect_email")
        .get(pk=message_id)
    )
    if message.campaign_id is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    campaign = Campaign.objects.select_for_update().get(pk=message.campaign_id)
    message.campaign = campaign
    if message.state in {
        OutboundMessage.State.REVIEW_READY,
        OutboundMessage.State.SENT,
        OutboundMessage.State.DRY_RUN_COMPLETED,
        OutboundMessage.State.CANCELLED,
        OutboundMessage.State.SEND_FAILED,
        OutboundMessage.State.SENDING,
        OutboundMessage.State.RECONCILING,
    }:
        return None
    if campaign.state != Campaign.State.RUNNING:
        return None
    if (
        message.delivery_mode != Campaign.DeliveryMode.LIVE
        or campaign.delivery_mode != Campaign.DeliveryMode.LIVE
    ):
        raise ValidationError("El mensaje no pertenece a una campaña con envío en vivo.")
    if message.approved_at is None or message.approved_by_id is None:
        message.state = OutboundMessage.State.REVIEW_READY
        message.next_attempt_at = None
        message.error = "El mensaje requiere aprobación explícita antes de Gmail."
        message.save(update_fields=("state", "next_attempt_at", "error", "updated_at"))
        return None
    if message.state == OutboundMessage.State.PREPARED:
        message.state = OutboundMessage.State.QUEUED
        message.save(update_fields=("state", "updated_at"))
    if message.next_attempt_at and message.next_attempt_at > now:
        return None
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        _pause_campaign(
            campaign.pk,
            "La configuración global de envío o el bloqueo general impidieron la entrega en vivo.",
        )
        return None
    if not _inside_schedule(campaign, now):
        return None
    eligibility_error = final_email_error(message)
    if eligibility_error:
        _mark_message_ineligible(message, eligibility_error, now=now)
        return None
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(
            workspace=campaign.workspace,
            status=GmailConnection.Status.CONNECTED,
        )
        .first()
    )
    if connection is None or not connection.is_ready or set(connection.scopes) != set(GMAIL_SCOPES):
        _pause_campaign(campaign.pk, "Gmail no está conectado y probado.")
        return None
    if not _daily_slot_available(campaign, now) or not _interval_elapsed(campaign, now):
        return None
    try:
        raw, mime_hash, rfc_message_id, mime_size = _message_bytes(
            message,
            sender=connection.email,
        )
    except ValidationError as exc:
        reason = _error_text(exc)
        message.error = reason
        message.save(update_fields=("error", "updated_at"))
        _pause_campaign(campaign.pk, reason)
        return None
    if message.kind == OutboundMessage.Kind.FIRST_CONTACT:
        try:
            _reserve_ledger(message, now)
        except ValidationError as exc:
            message.state = OutboundMessage.State.SEND_FAILED
            message.error = _error_text(exc)
            message.save(update_fields=("state", "error", "updated_at"))
            return None
    elif not _reserve_campaign_delivery_day(message, now):
        _reschedule_same_day_conflict(message, now)
        return None
    message.message_id = rfc_message_id
    message.mime_sha256 = mime_hash
    message.mime_size = mime_size
    message.state = OutboundMessage.State.SENDING
    message.attempts += 1
    message.delivery_reserved_at = now
    message.sending_started_at = now
    message.last_attempt_at = now
    message.next_attempt_at = None
    message.error = ""
    message.save(
        update_fields=(
            "message_id",
            "mime_sha256",
            "mime_size",
            "state",
            "attempts",
            "contact_sequence",
            "delivery_reserved_at",
            "sending_started_at",
            "last_attempt_at",
            "next_attempt_at",
            "error",
            "updated_at",
        )
    )
    BackgroundJob.objects.update_or_create(
        idempotency_key=f"deliver:{message.pk}",
        defaults={
            "task_name": "mailbox.deliver_message",
            "entity_type": "OutboundMessage",
            "entity_id": str(message.pk),
            "state": BackgroundJob.State.RUNNING,
            "attempts": message.attempts,
            "heartbeat_at": now,
            "started_at": now,
            "finished_at": None,
            "error": "",
        },
    )
    return SendEffect(
        message_id=message.pk,
        connection_id=connection.pk,
        raw_message=raw,
        rfc_message_id=rfc_message_id,
        recipient=message.recipient,
        recipient_normalized=message.recipient_normalized,
        idempotency_key=message.idempotency_key,
        kind=message.kind,
        gmail_thread_id=message.gmail_thread_id,
    )


def _reminder_due_at(campaign: Campaign, sent_at: datetime) -> datetime:
    zone = ZoneInfo(campaign.timezone_name)
    local_due = sent_at.astimezone(zone) + timedelta(days=campaign.reminder_delay_days)
    return _shift_to_business_window(campaign, local_due)


def _ensure_campaign_reminder(
    initial: OutboundMessage,
    *,
    sent_at: datetime,
) -> OutboundMessage | None:
    campaign = initial.campaign
    if (
        campaign is None
        or initial.kind != OutboundMessage.Kind.INITIAL
        or initial.delivery_mode != Campaign.DeliveryMode.LIVE
        or not campaign.reminder_enabled
    ):
        return None
    existing = OutboundMessage.objects.filter(reminder_for=initial).first()
    if existing is not None:
        return existing
    scheduling_error = ""
    try:
        due_at = _reminder_due_at(campaign, sent_at)
    except (ValidationError, ValueError):
        due_at = sent_at
        scheduling_error = "No se pudo calcular una ventana válida para el recordatorio."
    try:
        content = freeze_message_content(
            subject=initial.subject,
            body=campaign.reminder_body_snapshot,
            signature=campaign.signature_snapshot,
        )
        reminder_body = content.rendered_body
        reminder_signature = content.signature
        reminder_content_hash = content.content_hash
    except ValueError:
        reminder_signature = campaign.signature_snapshot.strip()
        reminder_body = campaign.reminder_body_snapshot.strip()
        reminder_content_hash = ""
        scheduling_error = scheduling_error or (
            "El contenido aprobado del recordatorio ya no es válido."
        )
    if not initial.message_id or not initial.gmail_thread_id:
        scheduling_error = scheduling_error or (
            "El envío inicial no conservó los datos necesarios para responder en el mismo hilo."
        )
    references = list(
        dict.fromkeys(
            [
                *(str(value) for value in initial.references if str(value)),
                initial.message_id,
            ]
        )
    )
    state = OutboundMessage.State.QUEUED
    error = ""
    next_attempt_at: datetime | None = due_at
    if campaign.state == Campaign.State.CANCELLED:
        state = OutboundMessage.State.CANCELLED
        error = "La campaña fue cancelada antes de programar el recordatorio."
        next_attempt_at = None
    elif scheduling_error:
        state = OutboundMessage.State.INELIGIBLE
        error = scheduling_error
        next_attempt_at = None
    else:
        eligibility_error = final_email_error(initial)
        if eligibility_error:
            state = OutboundMessage.State.INELIGIBLE
            error = eligibility_error
            next_attempt_at = None
    key = f"campaign-reminder:{initial.pk}"
    try:
        with transaction.atomic():
            reminder, created = OutboundMessage.objects.get_or_create(
                reminder_for=initial,
                defaults={
                    "kind": OutboundMessage.Kind.CAMPAIGN_REMINDER,
                    "campaign": campaign,
                    "organization": initial.organization,
                    "campaign_enrollment": initial.campaign_enrollment,
                    "prospect": initial.prospect,
                    "prospect_email": initial.prospect_email,
                    "email_address": initial.email_address,
                    "recipient": initial.recipient,
                    "recipient_normalized": initial.recipient_normalized,
                    "subject": initial.subject,
                    "body_text": reminder_body,
                    "signature_snapshot": reminder_signature,
                    "content_hash": reminder_content_hash,
                    "state": state,
                    "delivery_mode": initial.delivery_mode,
                    "approved_at": initial.approved_at,
                    "approved_by": initial.approved_by,
                    "idempotency_key": key,
                    "message_id": deterministic_message_id(key),
                    "in_reply_to": initial.message_id,
                    "references": references,
                    "gmail_thread_id": initial.gmail_thread_id,
                    "next_attempt_at": next_attempt_at,
                    "scheduled_for": due_at,
                    "error": error,
                },
            )
    except IntegrityError:
        reminder = OutboundMessage.objects.get(reminder_for=initial)
        created = False
    if not created:
        return reminder
    enrollment_id = initial.campaign_enrollment_id
    if enrollment_id is not None:
        enrollment_state = (
            CampaignEnrollment.State.REMINDER_DUE
            if state == OutboundMessage.State.QUEUED
            else CampaignEnrollment.State.INELIGIBLE
            if state == OutboundMessage.State.INELIGIBLE
            else CampaignEnrollment.State.CANCELLED
        )
        CampaignEnrollment.objects.filter(pk=enrollment_id).exclude(
            state=CampaignEnrollment.State.RESPONDED
        ).update(
            state=enrollment_state,
            exclusion_reason=error[:200],
            updated_at=timezone.now(),
        )
    record_event(
        action="campaign.reminder_scheduled",
        entity=reminder,
        actor=None,
        after={"state": reminder.state, "scheduled_for": due_at.isoformat()},
    )
    return reminder


@transaction.atomic
def _confirm_sent(message_id: uuid.UUID, result: GmailSendResult, now: datetime) -> str:
    message = (
        OutboundMessage.objects.select_for_update().select_related("campaign").get(pk=message_id)
    )
    if message.state == OutboundMessage.State.SENT:
        if message.kind == OutboundMessage.Kind.INITIAL:
            _ensure_campaign_reminder(message, sent_at=message.sent_at or now)
        return message.state
    if message.state not in {OutboundMessage.State.SENDING, OutboundMessage.State.RECONCILING}:
        raise ValidationError("El mensaje no estaba en una entrega reconciliable.")
    message.state = OutboundMessage.State.SENT
    message.gmail_message_id = result.message_id
    message.gmail_thread_id = result.thread_id
    message.sent_at = now
    message.next_attempt_at = None
    message.error = ""
    message.save(
        update_fields=(
            "state",
            "gmail_message_id",
            "gmail_thread_id",
            "sent_at",
            "next_attempt_at",
            "error",
            "updated_at",
        )
    )
    if message.campaign_enrollment_id is not None and message.kind in INITIAL_MESSAGE_KINDS:
        CampaignEnrollment.objects.filter(pk=message.campaign_enrollment_id).exclude(
            state=CampaignEnrollment.State.RESPONDED
        ).update(
            state=CampaignEnrollment.State.INITIAL_SENT,
            initial_sent_at=now,
            exclusion_reason="",
            updated_at=now,
        )
    elif (
        message.campaign_enrollment_id is not None
        and message.kind == OutboundMessage.Kind.CAMPAIGN_REMINDER
    ):
        CampaignEnrollment.objects.filter(pk=message.campaign_enrollment_id).exclude(
            state=CampaignEnrollment.State.RESPONDED
        ).update(
            state=CampaignEnrollment.State.REMINDER_SENT,
            exclusion_reason="",
            updated_at=now,
        )
    if message.kind == OutboundMessage.Kind.FIRST_CONTACT:
        ledger = (
            ContactLedger.objects.select_for_update()
            .filter(normalized_email=message.recipient_normalized)
            .first()
        )
        if ledger is not None:
            ledger.last_sent_message = message
            ledger.last_sent_at = now
            ledger.next_sequence = (message.contact_sequence or ledger.next_sequence) + 1
            ledger.reserved_message = None
            ledger.reserved_at = None
            ledger.save(
                update_fields=(
                    "last_sent_message",
                    "last_sent_at",
                    "next_sequence",
                    "reserved_message",
                    "reserved_at",
                    "updated_at",
                )
            )
    elif message.kind in SAME_DAY_RESERVATION_KINDS:
        _consume_campaign_delivery_day(message, now)
    BackgroundJob.objects.filter(idempotency_key=f"deliver:{message.pk}").update(
        state=BackgroundJob.State.SUCCEEDED,
        finished_at=now,
        heartbeat_at=now,
        error="",
    )
    record_event(
        action="message.sent",
        entity=message,
        actor=None,
        after={
            "state": message.state,
            "gmail_message_id": message.gmail_message_id,
            "gmail_thread_id": message.gmail_thread_id,
        },
    )
    if message.kind == OutboundMessage.Kind.INITIAL:
        _ensure_campaign_reminder(message, sent_at=now)
    return message.state


def final_email_error(message: OutboundMessage) -> str:
    try:
        return outbound_eligibility_error(message)
    except ValidationError as exc:
        return exc.messages[0] if exc.messages else "No se pudo confirmar el destinatario."


@transaction.atomic
def _execute_external_effect(
    effect: SendEffect,
    provider: GmailProvider | None,
) -> GmailSendResult | str | None:
    # This transaction-scoped advisory lock is also taken by suppress_email().
    # Keeping it through provider.send() makes suppression commit and Gmail send ordered.
    lock_email_eligibility(effect.recipient_normalized)
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__created_by", "prospect_email")
        .get(pk=effect.message_id)
    )
    if message.state != OutboundMessage.State.SENDING:
        return message.state
    campaign = message.campaign
    if campaign is None:
        _mark_message_ineligible(message, "El mensaje perdió su campaña de origen.")
        return message.state
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(pk=effect.connection_id, workspace=campaign.workspace)
        .first()
    )
    connection_ready = bool(
        connection is not None
        and connection.status == GmailConnection.Status.CONNECTED
        and connection.last_tested_at is not None
        and connection.refresh_token_encrypted
        and set(connection.scopes) == set(GMAIL_SCOPES)
    )
    if message.approved_at is None or message.approved_by_id is None:
        message.state = OutboundMessage.State.REVIEW_READY
        message.error = "El mensaje requiere aprobación explícita antes de Gmail."
        message.delivery_reserved_at = None
        message.sending_started_at = None
        _release_delivery_guard(message)
        message.save(
            update_fields=(
                "state",
                "error",
                "delivery_reserved_at",
                "sending_started_at",
                "updated_at",
            )
        )
        return message.state
    eligibility_error = final_email_error(message)
    if eligibility_error:
        _mark_message_ineligible(message, eligibility_error)
        return message.state
    if connection is not None:
        try:
            _message_attachments(message)
            if (
                message.message_id != effect.rfc_message_id
                or message.recipient != effect.recipient
                or message.recipient_normalized != effect.recipient_normalized
                or message.idempotency_key != effect.idempotency_key
                or message.kind != effect.kind
                or message.gmail_thread_id != effect.gmail_thread_id
            ):
                raise ValidationError(
                    "El contenido aprobado cambió antes del envío y se detuvo la campaña."
                )
        except ValidationError as exc:
            reason = _error_text(exc)
            message.state = OutboundMessage.State.QUEUED
            message.error = reason
            message.delivery_reserved_at = None
            message.sending_started_at = None
            _release_delivery_guard(message)
            message.save(
                update_fields=(
                    "state",
                    "error",
                    "delivery_reserved_at",
                    "sending_started_at",
                    "updated_at",
                )
            )
            BackgroundJob.objects.filter(idempotency_key=f"deliver:{message.pk}").update(
                state=BackgroundJob.State.CANCELLED,
                finished_at=timezone.now(),
                error=reason,
            )
            _pause_campaign(campaign.pk, reason)
            return message.state
    if (
        campaign.state == Campaign.State.RUNNING
        and message.delivery_mode == Campaign.DeliveryMode.LIVE
        and campaign.delivery_mode == Campaign.DeliveryMode.LIVE
        and message.approved_at is not None
        and message.approved_by_id is not None
        and settings.SEND_MODE == "live"
        and not settings.SEND_KILL_SWITCH
        and connection_ready
    ):
        if provider is None:
            return None
        if message.kind == OutboundMessage.Kind.CAMPAIGN_REMINDER:
            return provider.reply(
                GmailReplyRequest(
                    recipient=effect.recipient,
                    raw_message=effect.raw_message,
                    message_id=effect.rfc_message_id,
                    thread_id=effect.gmail_thread_id,
                    correlation_id=str(effect.message_id),
                    idempotency_key=effect.idempotency_key,
                )
            )
        return provider.send(
            GmailSendRequest(
                recipient=effect.recipient,
                raw_message=effect.raw_message,
                message_id=effect.rfc_message_id,
                correlation_id=str(effect.message_id),
                idempotency_key=effect.idempotency_key,
            )
        )
    if campaign.state == Campaign.State.CANCELLED:
        message.state = OutboundMessage.State.CANCELLED
        message.error = "La campaña fue cancelada antes del efecto Gmail."
    else:
        message.state = OutboundMessage.State.QUEUED
        message.error = "La entrega se detuvo antes del efecto Gmail."
    _release_delivery_guard(message)
    message.delivery_reserved_at = None
    message.sending_started_at = None
    message.save(
        update_fields=(
            "state",
            "error",
            "delivery_reserved_at",
            "sending_started_at",
            "updated_at",
        )
    )
    return message.state


def _recheck_before_external_effect(effect: SendEffect) -> str | None:
    result = _execute_external_effect(effect, None)
    assert result is None or isinstance(result, str)
    return result


@transaction.atomic
def _record_send_error(
    message_id: uuid.UUID,
    error: Exception,
    now: datetime,
    *,
    ambiguous: bool,
    pause: bool,
    permanent: bool = False,
) -> str:
    message = (
        OutboundMessage.objects.select_for_update().select_related("campaign").get(pk=message_id)
    )
    if message.state == OutboundMessage.State.SENT:
        return message.state
    campaign = message.campaign
    owner_id = campaign.created_by_id if campaign is not None else None
    error_text = _error_text(error, owner_id=owner_id)
    if permanent:
        message.state = OutboundMessage.State.SEND_FAILED
        message.next_attempt_at = None
        _release_delivery_guard(message, now=now)
    elif ambiguous:
        message.state = OutboundMessage.State.RECONCILING
        message.next_attempt_at = now + _backoff(message.attempts)
    elif message.attempts >= MAX_SEND_ATTEMPTS:
        message.state = OutboundMessage.State.SEND_FAILED
        message.next_attempt_at = None
        _release_delivery_guard(message, now=now)
    else:
        message.state = OutboundMessage.State.QUEUED
        retry_after = error.retry_after if isinstance(error, RateLimitError) else None
        message.next_attempt_at = now + _backoff(message.attempts, retry_after=retry_after)
    message.error = error_text
    message.save(update_fields=("state", "next_attempt_at", "error", "updated_at"))
    BackgroundJob.objects.filter(idempotency_key=f"deliver:{message.pk}").update(
        state=(
            BackgroundJob.State.FAILED
            if message.state == OutboundMessage.State.SEND_FAILED
            else BackgroundJob.State.RETRY_WAIT
        ),
        next_retry_at=message.next_attempt_at,
        heartbeat_at=now,
        finished_at=now if message.state == OutboundMessage.State.SEND_FAILED else None,
        error=error_text,
    )
    if isinstance(error, AuthenticationError):
        GmailConnection.objects.filter(
            workspace=campaign.workspace if campaign is not None else None
        ).update(
            status=GmailConnection.Status.ERROR,
            error=error_text,
        )
    if message.state == OutboundMessage.State.SEND_FAILED and isinstance(error, RateLimitError):
        pause = True
    if message.state == OutboundMessage.State.SEND_FAILED:
        if message.campaign_id is not None:
            pause = _pause_if_failure_threshold_reached(message.campaign_id) or pause
    if pause and campaign is not None and campaign.state == Campaign.State.RUNNING:
        _pause_campaign(campaign.pk, error_text)
    return message.state


def deliver_message(
    message_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
    provider: GmailProvider | None = None,
) -> str:
    moment = now or timezone.now()
    message = OutboundMessage.objects.get(pk=message_id)
    if message.kind not in CAMPAIGN_DELIVERY_KINDS:
        if message.kind == OutboundMessage.Kind.MANUAL_REPLY:
            raise ValidationError(
                "Las respuestas manuales requieren confirmación explícita desde la conversación."
            )
        raise ValidationError(
            "Este tipo de correo se envía mediante su flujo de autorización específico."
        )
    if message.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY:
        return complete_review_only(message.pk)
    if message.delivery_mode == Campaign.DeliveryMode.DRY_RUN:
        return complete_dry_run(message.pk, now=moment)
    if message.delivery_mode != Campaign.DeliveryMode.LIVE:
        raise ValidationError("El modo de entrega del mensaje no es válido.")
    effect = _prepare_live_effect(message.pk, moment)
    if effect is None:
        return OutboundMessage.objects.get(pk=message.pk).state
    connection = GmailConnection.objects.get(pk=effect.connection_id)
    active_provider = provider or provider_for_connection(connection, persist_fake=True)
    try:
        result = _execute_external_effect(effect, active_provider)
    except AuthenticationError as exc:
        return _record_send_error(effect.message_id, exc, moment, ambiguous=False, pause=True)
    except RateLimitError as exc:
        return _record_send_error(effect.message_id, exc, moment, ambiguous=False, pause=False)
    except AmbiguousProviderError as exc:
        return _record_send_error(effect.message_id, exc, moment, ambiguous=True, pause=False)
    except RetryableProviderError as exc:
        return _record_send_error(effect.message_id, exc, moment, ambiguous=True, pause=False)
    except (PermanentProviderError, ValidationProviderError) as exc:
        return _record_send_error(
            effect.message_id,
            exc,
            moment,
            ambiguous=False,
            pause=True,
            permanent=True,
        )
    except ProviderError as exc:
        return _record_send_error(effect.message_id, exc, moment, ambiguous=True, pause=True)
    if isinstance(result, str):
        return result
    if result is None:
        raise RuntimeError("La autorización final Gmail no produjo un resultado.")
    return _confirm_sent(effect.message_id, result, moment)


def reconcile_message(
    message_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
    provider: GmailProvider | None = None,
) -> str:
    moment = now or timezone.now()
    message = OutboundMessage.objects.select_related("campaign__workspace").get(pk=message_id)
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    if (
        message.delivery_mode != Campaign.DeliveryMode.LIVE
        or campaign.delivery_mode != Campaign.DeliveryMode.LIVE
    ):
        raise ValidationError("Sólo los mensajes con envío en vivo pueden verificarse con Gmail.")
    if message.state not in {OutboundMessage.State.SENDING, OutboundMessage.State.RECONCILING}:
        return message.state
    connection = GmailConnection.objects.get(workspace=campaign.workspace)
    active_provider = provider or provider_for_connection(connection, persist_fake=True)
    try:
        result = active_provider.find_by_message_id(message.message_id)
    except (AuthenticationError, PermanentProviderError, ValidationProviderError) as exc:
        return _record_send_error(message.pk, exc, moment, ambiguous=True, pause=True)
    except RetryableProviderError as exc:
        return _record_send_error(message.pk, exc, moment, ambiguous=True, pause=False)
    except ProviderError as exc:
        return _record_send_error(message.pk, exc, moment, ambiguous=True, pause=True)
    if result is not None:
        return _confirm_sent(message.pk, result, moment)
    with transaction.atomic():
        locked = (
            OutboundMessage.objects.select_for_update()
            .select_related("campaign")
            .get(pk=message.pk)
        )
        if locked.state not in {OutboundMessage.State.SENDING, OutboundMessage.State.RECONCILING}:
            return locked.state
        locked_campaign = locked.campaign
        if locked_campaign is None:
            raise ValidationError("El mensaje perdió su campaña durante la reconciliación.")
        if locked_campaign.state == Campaign.State.CANCELLED:
            locked.state = OutboundMessage.State.CANCELLED
            locked.next_attempt_at = None
            locked.error = "La campaña fue cancelada; Gmail confirmó ausencia del Message-ID."
            _release_delivery_guard(locked, now=moment)
        elif locked.attempts >= MAX_SEND_ATTEMPTS:
            locked.state = OutboundMessage.State.SEND_FAILED
            locked.next_attempt_at = None
            locked.error = "Gmail confirmó que el Message-ID no existe; reintentos agotados."
            _release_delivery_guard(locked, now=moment)
        else:
            locked.state = OutboundMessage.State.QUEUED
            locked.next_attempt_at = moment + _backoff(locked.attempts)
            locked.error = "Gmail confirmó ausencia; el mismo intento queda reprogramado."
        locked.save(update_fields=("state", "next_attempt_at", "error", "updated_at"))
        job_state = (
            BackgroundJob.State.FAILED
            if locked.state == OutboundMessage.State.SEND_FAILED
            else BackgroundJob.State.RETRY_WAIT
        )
        BackgroundJob.objects.filter(idempotency_key=f"deliver:{locked.pk}").update(
            state=job_state,
            next_retry_at=locked.next_attempt_at,
            heartbeat_at=moment,
            finished_at=moment if job_state == BackgroundJob.State.FAILED else None,
            error=locked.error,
        )
        if locked.state == OutboundMessage.State.SEND_FAILED:
            if locked.campaign_id is not None:
                _pause_if_failure_threshold_reached(locked.campaign_id)
        return locked.state


@transaction.atomic
def _queue_candidate_is_ready(message_id: uuid.UUID, now: datetime) -> bool:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__workspace")
        .get(pk=message_id)
    )
    campaign = message.campaign
    if (
        campaign is None
        or campaign.state != Campaign.State.RUNNING
        or message.state not in (OutboundMessage.State.PREPARED, OutboundMessage.State.QUEUED)
    ):
        return False
    eligibility_error = final_email_error(message)
    if eligibility_error:
        _mark_message_ineligible(message, eligibility_error, now=now)
        return False
    try:
        _message_attachments(message)
    except ValidationError as exc:
        reason = _error_text(exc)
        message.error = reason
        message.save(update_fields=("error", "updated_at"))
        _pause_campaign(campaign.pk, reason)
        return False
    return True


def pending_message_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    ready_for_delivery = Q(delivery_mode=Campaign.DeliveryMode.DRY_RUN) | Q(
        delivery_mode=Campaign.DeliveryMode.LIVE,
        approved_at__isnull=False,
        approved_by__isnull=False,
    )
    candidate_ids = tuple(
        OutboundMessage.objects.filter(
            kind__in=CAMPAIGN_DELIVERY_KINDS,
            campaign__state=Campaign.State.RUNNING,
            state__in=(OutboundMessage.State.PREPARED, OutboundMessage.State.QUEUED),
        )
        .filter(delivery_mode=F("campaign__delivery_mode"))
        .exclude(delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY)
        .filter(ready_for_delivery)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=moment))
        .order_by("next_attempt_at", "created_at")
        .values_list("pk", flat=True)
    )
    return tuple(
        message_id for message_id in candidate_ids if _queue_candidate_is_ready(message_id, moment)
    )


def recoverable_message_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    return tuple(
        OutboundMessage.objects.filter(
            kind__in=CAMPAIGN_DELIVERY_KINDS,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            campaign__delivery_mode=Campaign.DeliveryMode.LIVE,
            approved_at__isnull=False,
            approved_by__isnull=False,
            state=OutboundMessage.State.SENDING,
            sending_started_at__lte=moment - STALE_SENDING_AFTER,
        ).values_list("pk", flat=True)
    ) + tuple(
        OutboundMessage.objects.filter(
            kind__in=CAMPAIGN_DELIVERY_KINDS,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            campaign__delivery_mode=Campaign.DeliveryMode.LIVE,
            approved_at__isnull=False,
            approved_by__isnull=False,
            state=OutboundMessage.State.RECONCILING,
            next_attempt_at__lte=moment,
        ).values_list("pk", flat=True)
    )


@transaction.atomic
def retry_failed_message(
    message_id: uuid.UUID | str,
    *,
    actor: User,
    reason: str,
) -> OutboundMessage:
    """Requeue the same logical message only after an explicit, audited operator decision."""

    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__workspace", "catalog", "prospect_email")
        .get(pk=message_id)
    )
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no pertenece a una campaña.")
    require_user_capability(
        actor,
        Capability.VIEW_JOBS,
        workspace_id=campaign.workspace_id,
    )
    if message.state != OutboundMessage.State.SEND_FAILED:
        raise ValidationError("Sólo se pueden reintentar mensajes con fallo final.")
    if message.gmail_message_id:
        raise ValidationError("El mensaje ya tiene confirmación Gmail y no se puede reencolar.")
    if campaign.state not in {Campaign.State.RUNNING, Campaign.State.PAUSED}:
        raise ValidationError("La campaña cerrada no admite reintentos.")
    clean_reason = " ".join(reason.split())
    if len(clean_reason) < 10:
        raise ValidationError("Documentá qué causa fue corregida antes de reintentar.")
    eligibility_error = final_email_error(message)
    if eligibility_error:
        raise ValidationError(eligibility_error)
    _message_attachments(message)
    before = {"state": message.state, "attempts": message.attempts, "error": message.error}
    requires_approval = bool(
        message.delivery_mode == Campaign.DeliveryMode.LIVE
        and (message.approved_at is None or message.approved_by_id is None)
    )
    message.state = (
        OutboundMessage.State.REVIEW_READY if requires_approval else OutboundMessage.State.QUEUED
    )
    message.attempts = 0
    message.next_attempt_at = None if requires_approval else timezone.now()
    message.delivery_reserved_at = None
    message.sending_started_at = None
    message.error = ""
    message.save(
        update_fields=(
            "state",
            "attempts",
            "next_attempt_at",
            "delivery_reserved_at",
            "sending_started_at",
            "error",
            "updated_at",
        )
    )
    BackgroundJob.objects.filter(idempotency_key=f"deliver:{message.pk}").update(
        state=(
            BackgroundJob.State.CANCELLED if requires_approval else BackgroundJob.State.RETRY_WAIT
        ),
        attempts=0,
        next_retry_at=message.next_attempt_at,
        finished_at=timezone.now() if requires_approval else None,
        error=("El mensaje requiere aprobación antes de reintentar." if requires_approval else ""),
    )
    record_event(
        action="message.retry_requested",
        entity=message,
        actor=actor,
        before=before,
        after={
            "state": message.state,
            "reason": clean_reason,
            "requires_approval": requires_approval,
        },
    )
    return message


@transaction.atomic
def _complete_campaign_if_drained(campaign_id: uuid.UUID) -> bool:
    from apps.prospects.models import AIAnalysis, Prospect

    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    if (
        campaign.state != Campaign.State.RUNNING
        or campaign.discovery_state not in TERMINAL_DISCOVERY_STATES
    ):
        return False
    if campaign.reminder_enabled and campaign.delivery_mode == Campaign.DeliveryMode.LIVE:
        sent_initials = campaign.messages.select_for_update().filter(
            kind=OutboundMessage.Kind.INITIAL,
            state=OutboundMessage.State.SENT,
            campaign_reminder__isnull=True,
        )
        for initial in sent_initials:
            _ensure_campaign_reminder(initial, sent_at=initial.sent_at or timezone.now())
    active_message_states = [
        OutboundMessage.State.PREPARED,
        OutboundMessage.State.QUEUED,
        OutboundMessage.State.SENDING,
        OutboundMessage.State.RECONCILING,
    ]
    if campaign.delivery_mode == Campaign.DeliveryMode.LIVE:
        active_message_states.append(OutboundMessage.State.REVIEW_READY)
    if campaign.messages.filter(
        kind__in=CAMPAIGN_DELIVERY_KINDS,
        state__in=active_message_states,
    ).exists():
        return False
    unfinished_pipeline_states = (
        Prospect.PipelineState.DISCOVERED,
        Prospect.PipelineState.EMAIL_FOUND,
        Prospect.PipelineState.ENRICHED,
        Prospect.PipelineState.ANALYZED,
    )
    if (
        Prospect.objects.filter(campaign=campaign)
        .filter(Q(pipeline_state__in=unfinished_pipeline_states) | ~Q(pipeline_reservation_key=""))
        .exists()
    ):
        return False
    if AIAnalysis.objects.filter(
        prospect__campaign=campaign,
        status=AIAnalysis.Status.RETRY_WAIT,
        generation=F("prospect__analysis_generation"),
    ).exists():
        return False
    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.COMPLETED,
        actor=None,
    )
    return True


def complete_drained_campaigns() -> int:
    campaign_ids = Campaign.objects.filter(
        state=Campaign.State.RUNNING,
        discovery_state__in=TERMINAL_DISCOVERY_STATES,
    ).values_list("pk", flat=True)
    return sum(_complete_campaign_if_drained(campaign_id) for campaign_id in campaign_ids)
