from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.audit.models import BackgroundJob
from apps.audit.services import record_event
from apps.campaigns.models import Campaign, OutboundMessage
from apps.campaigns.services import TERMINAL_DISCOVERY_STATES, transition_campaign
from apps.catalogs.services import verify_catalog
from apps.compliance.models import ContactLedger, ContactOverride, SuppressionEntry
from apps.compliance.services import lock_email_eligibility
from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
    GmailProvider,
    GmailSendRequest,
    GmailSendResult,
    PermanentProviderError,
    ProviderError,
    RateLimitError,
    RetryableProviderError,
    ValidationProviderError,
)
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.mime import build_message, deterministic_message_id
from apps.mailbox.models import GmailConnection
from apps.mailbox.services import provider_for_connection

MAX_SEND_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 900
STALE_SENDING_AFTER = timedelta(minutes=2)
FAILURE_WINDOW_SIZE = 20
FAILURE_RATE_THRESHOLD = 0.30
CONSECUTIVE_FAILURE_THRESHOLD = 5


@dataclass(frozen=True, slots=True)
class SendEffect:
    message_id: uuid.UUID
    connection_id: uuid.UUID
    raw_message: bytes
    rfc_message_id: str
    recipient: str
    recipient_normalized: str
    idempotency_key: str


def _error_text(error: Exception) -> str:
    return " ".join(str(error).split())[:500] or error.__class__.__name__


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
            OutboundMessage.State.SENT,
        }:
            raise ValidationError("El email ya tiene otro primer contacto reservado.")
        ledger.reserved_message = None
        ledger.reserved_at = None
    override: ContactOverride | None = None
    if ledger.last_sent_message_id:
        override = (
            ContactOverride.objects.select_for_update()
            .filter(ledger=ledger, campaign=message.campaign, consumed_at__isnull=True)
            .first()
        )
        if override is None:
            raise ValidationError("El email ya recibió un primer contacto.")
    message.contact_sequence = ledger.next_sequence
    ledger.reserved_message = message
    ledger.reserved_at = now
    ledger.save(update_fields=("reserved_message", "reserved_at", "updated_at"))
    if override is not None:
        override.consumed_at = now
        override.consumed_by_message = message
        override.save(update_fields=("consumed_at", "consumed_by_message", "updated_at"))
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


def _message_bytes(message: OutboundMessage, *, sender: str) -> tuple[bytes, str, str]:
    verify_catalog(message.catalog)
    with message.catalog.file.open("rb") as handle:
        pdf = handle.read()
    rfc_message_id = message.message_id or deterministic_message_id(message.idempotency_key)
    built = build_message(
        sender=sender,
        recipient=message.recipient,
        subject=message.subject,
        body_text=message.body_text,
        message_id=rfc_message_id,
        sent_at=message.created_at,
        campaign_header=str(message.campaign_id),
        message_header=str(message.pk),
        pdf_bytes=pdf,
        pdf_filename=message.catalog.original_filename,
    )
    return built.raw, built.sha256, rfc_message_id


@transaction.atomic
def complete_dry_run(message_id: uuid.UUID | str, *, now: datetime | None = None) -> str:
    moment = now or timezone.now()
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__created_by", "catalog")
        .get(pk=message_id)
    )
    if message.state in {
        OutboundMessage.State.DRY_RUN_COMPLETED,
        OutboundMessage.State.SENT,
        OutboundMessage.State.CANCELLED,
    }:
        return message.state
    if message.campaign.state != Campaign.State.RUNNING:
        return message.state
    if message.delivery_mode != Campaign.DeliveryMode.DRY_RUN:
        raise ValidationError("El mensaje no pertenece a una campaña dry-run.")
    if message.state == OutboundMessage.State.PREPARED:
        message.state = OutboundMessage.State.QUEUED
    connection = GmailConnection.objects.filter(owner=message.campaign.created_by).first()
    sender = (
        connection.email if connection and connection.email else "dry-run@contact-outreach.invalid"
    )
    raw, mime_hash, rfc_message_id = _message_bytes(message, sender=sender)
    del raw
    message.message_id = rfc_message_id
    message.mime_sha256 = mime_hash
    message.state = OutboundMessage.State.DRY_RUN_COMPLETED
    message.simulated_at = moment
    message.error = ""
    message.save(
        update_fields=(
            "message_id",
            "mime_sha256",
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


@transaction.atomic
def _prepare_live_effect(message_id: uuid.UUID | str, now: datetime) -> SendEffect | None:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__created_by", "catalog", "prospect_email")
        .get(pk=message_id)
    )
    campaign = Campaign.objects.select_for_update().get(pk=message.campaign_id)
    message.campaign = campaign
    if message.state in {
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
    if message.state == OutboundMessage.State.PREPARED:
        message.state = OutboundMessage.State.QUEUED
        message.save(update_fields=("state", "updated_at"))
    if message.next_attempt_at and message.next_attempt_at > now:
        return None
    if message.delivery_mode != Campaign.DeliveryMode.LIVE:
        raise ValidationError("El mensaje no pertenece a una campaña live.")
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        _pause_campaign(campaign.pk, "Kill switch o SEND_MODE bloqueó la entrega live.")
        return None
    if not _inside_schedule(campaign, now):
        return None
    if SuppressionEntry.objects.filter(normalized_email=message.recipient_normalized).exists():
        message.state = OutboundMessage.State.SEND_FAILED
        message.error = "El destinatario está suprimido."
        message.save(update_fields=("state", "error", "updated_at"))
        return None
    email = message.prospect_email
    if (
        email.is_invalid
        or not email.is_primary
        or not email.syntax_valid
        or email.mx_status != email.MXStatus.VALID
        or bool(email.exclusion_reason)
        or email.normalized_email != message.recipient_normalized
    ):
        message.state = OutboundMessage.State.SEND_FAILED
        message.error = "El destinatario ya no es el email primario validado."
        message.save(update_fields=("state", "error", "updated_at"))
        return None
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(
            owner=campaign.created_by,
            status=GmailConnection.Status.CONNECTED,
        )
        .first()
    )
    if connection is None or not connection.is_ready or set(connection.scopes) != set(GMAIL_SCOPES):
        _pause_campaign(campaign.pk, "Gmail no está conectado y probado.")
        return None
    try:
        if (
            message.catalog_id != campaign.catalog_id
            or message.catalog_version != message.catalog.version
        ):
            raise ValidationError("La versión de catálogo del mensaje es inconsistente.")
        verify_catalog(message.catalog)
    except ValidationError as exc:
        reason = _error_text(exc)
        _pause_campaign(campaign.pk, reason)
        return None
    if not _daily_slot_available(campaign, now) or not _interval_elapsed(campaign, now):
        return None
    try:
        _reserve_ledger(message, now)
    except ValidationError as exc:
        message.state = OutboundMessage.State.SEND_FAILED
        message.error = _error_text(exc)
        message.save(update_fields=("state", "error", "updated_at"))
        return None
    raw, mime_hash, rfc_message_id = _message_bytes(message, sender=connection.email)
    message.message_id = rfc_message_id
    message.mime_sha256 = mime_hash
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
    )


@transaction.atomic
def _confirm_sent(message_id: uuid.UUID, result: GmailSendResult, now: datetime) -> str:
    message = OutboundMessage.objects.select_for_update().get(pk=message_id)
    if message.state == OutboundMessage.State.SENT:
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
    ledger = ContactLedger.objects.select_for_update().get(
        normalized_email=message.recipient_normalized
    )
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
    return message.state


def _final_email_error(message: OutboundMessage) -> str:
    if SuppressionEntry.objects.filter(normalized_email=message.recipient_normalized).exists():
        return "El destinatario fue suprimido antes del efecto Gmail."
    email = message.prospect_email
    if (
        email.is_invalid
        or not email.is_primary
        or not email.syntax_valid
        or email.mx_status != email.MXStatus.VALID
        or bool(email.exclusion_reason)
        or email.normalized_email != message.recipient_normalized
    ):
        return "El destinatario ya no es el email primario validado."
    return ""


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
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(pk=effect.connection_id, owner=message.campaign.created_by)
        .first()
    )
    connection_ready = bool(
        connection is not None
        and connection.status == GmailConnection.Status.CONNECTED
        and connection.last_tested_at is not None
        and connection.refresh_token_encrypted
        and set(connection.scopes) == set(GMAIL_SCOPES)
    )
    eligibility_error = _final_email_error(message)
    if eligibility_error:
        message.state = OutboundMessage.State.SEND_FAILED
        message.error = eligibility_error
        message.delivery_reserved_at = None
        message.sending_started_at = None
        _release_ledger(message)
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
            error=eligibility_error,
        )
        return message.state
    if (
        message.campaign.state == Campaign.State.RUNNING
        and settings.SEND_MODE == "live"
        and not settings.SEND_KILL_SWITCH
        and connection_ready
    ):
        if provider is None:
            return None
        return provider.send(
            GmailSendRequest(
                recipient=effect.recipient,
                raw_message=effect.raw_message,
                message_id=effect.rfc_message_id,
                correlation_id=str(effect.message_id),
                idempotency_key=effect.idempotency_key,
            )
        )
    if message.campaign.state == Campaign.State.CANCELLED:
        message.state = OutboundMessage.State.CANCELLED
        message.error = "La campaña fue cancelada antes del efecto Gmail."
        _release_ledger(message)
    else:
        message.state = OutboundMessage.State.QUEUED
        message.error = "La entrega se detuvo antes del efecto Gmail."
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
    message = OutboundMessage.objects.select_for_update().get(pk=message_id)
    if message.state == OutboundMessage.State.SENT:
        return message.state
    error_text = _error_text(error)
    if permanent:
        message.state = OutboundMessage.State.SEND_FAILED
        message.next_attempt_at = None
        _release_ledger(message)
    elif ambiguous:
        message.state = OutboundMessage.State.RECONCILING
        message.next_attempt_at = now + _backoff(message.attempts)
    elif message.attempts >= MAX_SEND_ATTEMPTS:
        message.state = OutboundMessage.State.SEND_FAILED
        message.next_attempt_at = None
        _release_ledger(message)
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
        GmailConnection.objects.filter(owner=message.campaign.created_by).update(
            status=GmailConnection.Status.ERROR,
            error=error_text,
        )
    if message.state == OutboundMessage.State.SEND_FAILED and isinstance(error, RateLimitError):
        pause = True
    if message.state == OutboundMessage.State.SEND_FAILED:
        pause = _pause_if_failure_threshold_reached(message.campaign_id) or pause
    if pause:
        campaign = Campaign.objects.get(pk=message.campaign_id)
        if campaign.state == Campaign.State.RUNNING:
            _pause_campaign(message.campaign_id, error_text)
    return message.state


def deliver_message(
    message_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
    provider: GmailProvider | None = None,
) -> str:
    moment = now or timezone.now()
    message = OutboundMessage.objects.get(pk=message_id)
    if message.kind != OutboundMessage.Kind.FIRST_CONTACT:
        raise ValidationError("Las respuestas manuales sólo se envían desde el POST explícito.")
    if message.delivery_mode == Campaign.DeliveryMode.DRY_RUN:
        return complete_dry_run(message.pk, now=moment)
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
    message = OutboundMessage.objects.select_related("campaign__created_by").get(pk=message_id)
    if message.state not in {OutboundMessage.State.SENDING, OutboundMessage.State.RECONCILING}:
        return message.state
    connection = GmailConnection.objects.get(owner=message.campaign.created_by)
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
        if locked.campaign.state == Campaign.State.CANCELLED:
            locked.state = OutboundMessage.State.CANCELLED
            locked.next_attempt_at = None
            locked.error = "La campaña fue cancelada; Gmail confirmó ausencia del Message-ID."
            _release_ledger(locked)
        elif locked.attempts >= MAX_SEND_ATTEMPTS:
            locked.state = OutboundMessage.State.SEND_FAILED
            locked.next_attempt_at = None
            locked.error = "Gmail confirmó que el Message-ID no existe; reintentos agotados."
            _release_ledger(locked)
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
            _pause_if_failure_threshold_reached(locked.campaign_id)
        return locked.state


def pending_message_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    return tuple(
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.FIRST_CONTACT,
            campaign__state=Campaign.State.RUNNING,
            state__in=(OutboundMessage.State.PREPARED, OutboundMessage.State.QUEUED),
        )
        .filter(next_attempt_at__isnull=True)
        .values_list("pk", flat=True)
    ) + tuple(
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.FIRST_CONTACT,
            campaign__state=Campaign.State.RUNNING,
            state=OutboundMessage.State.QUEUED,
            next_attempt_at__lte=moment,
        ).values_list("pk", flat=True)
    )


def recoverable_message_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    return tuple(
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.FIRST_CONTACT,
            state=OutboundMessage.State.SENDING,
            sending_started_at__lte=moment - STALE_SENDING_AFTER,
        ).values_list("pk", flat=True)
    ) + tuple(
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.FIRST_CONTACT,
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
        .select_related("campaign", "catalog", "prospect_email")
        .get(pk=message_id)
    )
    if message.campaign.created_by_id != actor.pk:
        raise ValidationError("El mensaje pertenece a otro propietario.")
    if message.state != OutboundMessage.State.SEND_FAILED:
        raise ValidationError("Sólo se pueden reintentar mensajes con fallo final.")
    if message.gmail_message_id:
        raise ValidationError("El mensaje ya tiene confirmación Gmail y no se puede reencolar.")
    if message.campaign.state not in {Campaign.State.RUNNING, Campaign.State.PAUSED}:
        raise ValidationError("La campaña cerrada no admite reintentos.")
    clean_reason = " ".join(reason.split())
    if len(clean_reason) < 10:
        raise ValidationError("Documentá qué causa fue corregida antes de reintentar.")
    eligibility_error = _final_email_error(message)
    if eligibility_error:
        raise ValidationError(eligibility_error)
    verify_catalog(message.catalog)
    before = {"state": message.state, "attempts": message.attempts, "error": message.error}
    message.state = OutboundMessage.State.QUEUED
    message.attempts = 0
    message.next_attempt_at = timezone.now()
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
        state=BackgroundJob.State.RETRY_WAIT,
        attempts=0,
        next_retry_at=message.next_attempt_at,
        finished_at=None,
        error="",
    )
    record_event(
        action="message.retry_requested",
        entity=message,
        actor=actor,
        before=before,
        after={"state": message.state, "reason": clean_reason},
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
    if campaign.messages.filter(
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        state__in=(
            OutboundMessage.State.PREPARED,
            OutboundMessage.State.QUEUED,
            OutboundMessage.State.SENDING,
            OutboundMessage.State.RECONCILING,
        ),
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
