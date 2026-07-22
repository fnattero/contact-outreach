from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import Campaign, OutboundMessage
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import lock_email_eligibility, normalize_email
from apps.configuration.integrations import redact_provider_error
from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
    GmailProvider,
    GmailReplyRequest,
    GmailSendResult,
    PermanentProviderError,
    ProviderError,
    RetryableProviderError,
    ValidationProviderError,
)
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.mime import build_reply_message, deterministic_message_id
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.mailbox.sync import normalize_message_id, normalize_references

RECONCILE_AFTER = timedelta(minutes=1)
STALE_SENDING_AFTER = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class ManualReplyEffect:
    message_id: uuid.UUID
    raw_message: bytes
    rfc_message_id: str
    recipient: str
    recipient_normalized: str
    gmail_thread_id: str
    idempotency_key: str


def _reply_references(inbound: InboundMessage, root: OutboundMessage) -> tuple[str, ...]:
    values = tuple(str(value) for value in inbound.references)
    return normalize_references((root.message_id, *values, inbound.message_id))


def _validate_authorization(
    *,
    inbound: InboundMessage,
    body_text: str,
) -> tuple[OutboundMessage, str, str]:
    root = inbound.related_outbound
    campaign = root.campaign
    connection = inbound.connection
    cleaned_body = body_text.strip()
    if not cleaned_body:
        raise ValidationError("Escribí una respuesta antes de enviar.")
    if len(cleaned_body) > 10_000:
        raise ValidationError("La respuesta excede el máximo permitido.")
    if not inbound.is_human:
        raise ValidationError("No se puede responder manualmente a un rebote o auto-respuesta.")
    if campaign.delivery_mode != Campaign.DeliveryMode.LIVE:
        raise ValidationError("Las respuestas manuales requieren una campaña live.")
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        raise ValidationError("SEND_MODE o el kill switch bloquean la respuesta manual.")
    if not connection.is_ready or set(connection.scopes) != set(GMAIL_SCOPES):
        raise ValidationError("Gmail debe estar conectado y probado.")
    recipient = root.recipient
    normalized = normalize_email(recipient)
    email = root.prospect_email
    if (
        normalized != root.recipient_normalized
        or email.normalized_email != normalized
        or email.is_invalid
        or not email.is_primary
        or not email.syntax_valid
        or email.mx_status != email.MXStatus.VALID
        or email.exclusion_reason
    ):
        raise ValidationError("El email del prospecto ya no es válido para responder.")
    if SuppressionEntry.objects.filter(normalized_email=normalized).exists():
        raise ValidationError("El email del prospecto está suprimido.")
    if not inbound.gmail_thread_id or not normalize_message_id(inbound.message_id):
        raise ValidationError("El hilo no tiene IDs Gmail/RFC suficientes para responder.")
    return root, cleaned_body, normalized


@transaction.atomic
def authorize_manual_reply(
    *,
    actor: User,
    inbound_id: uuid.UUID | str,
    body_text: str,
    request_key: uuid.UUID | str,
) -> tuple[OutboundMessage, bool]:
    key = f"manual-reply:{inbound_id}:{request_key}"
    existing = OutboundMessage.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing, False
    inbound = (
        InboundMessage.objects.select_for_update()
        .select_related(
            "connection",
            "related_outbound__campaign",
            "related_outbound__catalog",
            "related_outbound__prospect_email",
        )
        .get(pk=inbound_id, connection__owner=actor)
    )
    existing = OutboundMessage.objects.filter(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        parent_inbound=inbound,
    ).first()
    if existing is not None:
        return existing, False
    root, cleaned_body, normalized = _validate_authorization(
        inbound=inbound,
        body_text=body_text,
    )
    now = timezone.now()
    references = _reply_references(inbound, root)
    try:
        with transaction.atomic():
            message = OutboundMessage.objects.create(
                kind=OutboundMessage.Kind.MANUAL_REPLY,
                campaign=root.campaign,
                prospect=root.prospect,
                prospect_email=root.prospect_email,
                analysis=None,
                parent_inbound=inbound,
                sent_by=actor,
                recipient=root.recipient,
                recipient_normalized=normalized,
                subject=root.subject,
                body_text=cleaned_body,
                catalog=root.catalog,
                catalog_version=root.catalog_version,
                state=OutboundMessage.State.QUEUED,
                delivery_mode=Campaign.DeliveryMode.LIVE,
                idempotency_key=key,
                message_id=deterministic_message_id(key),
                in_reply_to=normalize_message_id(inbound.message_id),
                references=list(references),
                gmail_thread_id=inbound.gmail_thread_id,
                next_attempt_at=now,
            )
    except IntegrityError:
        message = OutboundMessage.objects.get(
            kind=OutboundMessage.Kind.MANUAL_REPLY,
            parent_inbound=inbound,
        )
        return message, False
    record_event(
        action="gmail.manual_reply_authorized",
        entity=message,
        actor=actor,
        after={"state": message.state, "inbound_id": str(inbound.pk)},
    )
    return message, True


def _effect_for(message: OutboundMessage, connection: GmailConnection) -> ManualReplyEffect:
    built = build_reply_message(
        sender=connection.email,
        recipient=message.recipient,
        subject=message.subject,
        body_text=message.body_text,
        message_id=message.message_id,
        sent_at=message.created_at,
        thread_references=tuple(str(value) for value in message.references),
        in_reply_to=message.in_reply_to,
        campaign_header=str(message.campaign_id),
        message_header=str(message.pk),
    )
    message.mime_sha256 = built.sha256
    return ManualReplyEffect(
        message_id=message.pk,
        raw_message=built.raw,
        rfc_message_id=message.message_id,
        recipient=message.recipient,
        recipient_normalized=message.recipient_normalized,
        gmail_thread_id=message.gmail_thread_id,
        idempotency_key=message.idempotency_key,
    )


def _effect_eligibility_error(
    message: OutboundMessage,
    connection: GmailConnection | None,
) -> str:
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        return "SEND_MODE o el kill switch bloquean la respuesta manual."
    if message.delivery_mode != Campaign.DeliveryMode.LIVE:
        return "La respuesta no pertenece a una campaña live."
    if connection is None or not connection.is_ready or set(connection.scopes) != set(GMAIL_SCOPES):
        return "Gmail no está conectado y probado."
    if SuppressionEntry.objects.filter(normalized_email=message.recipient_normalized).exists():
        return "El destinatario está suprimido."
    email = message.prospect_email
    if (
        email.normalized_email != message.recipient_normalized
        or email.is_invalid
        or not email.is_primary
        or not email.syntax_valid
        or email.mx_status != email.MXStatus.VALID
        or email.exclusion_reason
    ):
        return "El email del prospecto ya no es válido para responder."
    return ""


def _finish_manual_reply(
    message: OutboundMessage,
    *,
    state: str,
    error: str,
    result: GmailSendResult | None = None,
) -> str:
    message.state = state
    message.error = error[:500]
    message.next_attempt_at = None
    if state == OutboundMessage.State.RECONCILING:
        message.next_attempt_at = timezone.now() + RECONCILE_AFTER
    elif state == OutboundMessage.State.SENT:
        assert result is not None
        message.gmail_message_id = result.message_id
        message.gmail_thread_id = result.thread_id
        message.sent_at = timezone.now()
    message.save(
        update_fields=(
            "state",
            "error",
            "next_attempt_at",
            "gmail_message_id",
            "gmail_thread_id",
            "sent_at",
            "updated_at",
        )
    )
    actor = message.sent_by if isinstance(message.sent_by, User) else None
    action = (
        "gmail.manual_reply_sent"
        if state == OutboundMessage.State.SENT
        else "gmail.manual_reply_reconciling"
        if state == OutboundMessage.State.RECONCILING
        else "gmail.manual_reply_failed"
    )
    record_event(
        action=action,
        entity=message,
        actor=actor,
        after={"state": state},
    )
    return message.state


@transaction.atomic
def _prepare_manual_effect(
    message_id: uuid.UUID | str,
) -> ManualReplyEffect | str:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign__created_by", "prospect_email", "sent_by")
        .get(pk=message_id, kind=OutboundMessage.Kind.MANUAL_REPLY)
    )
    if message.state in {
        OutboundMessage.State.SENT,
        OutboundMessage.State.SEND_FAILED,
        OutboundMessage.State.CANCELLED,
    }:
        return message.state
    if message.state in {
        OutboundMessage.State.SENDING,
        OutboundMessage.State.RECONCILING,
    }:
        return message.state
    if message.state != OutboundMessage.State.QUEUED:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.SEND_FAILED,
            error="La autorización manual no estaba en cola.",
        )
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(owner=message.campaign.created_by)
        .first()
    )
    eligibility_error = _effect_eligibility_error(message, connection)
    if eligibility_error:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.SEND_FAILED,
            error=eligibility_error,
        )
    assert connection is not None
    effect = _effect_for(message, connection)
    message.state = OutboundMessage.State.SENDING
    message.attempts += 1
    message.sending_started_at = timezone.now()
    message.last_attempt_at = message.sending_started_at
    message.next_attempt_at = None
    message.error = ""
    message.save(
        update_fields=(
            "state",
            "attempts",
            "sending_started_at",
            "last_attempt_at",
            "next_attempt_at",
            "mime_sha256",
            "error",
            "updated_at",
        )
    )
    return effect


@transaction.atomic
def _execute_manual_effect(
    effect: ManualReplyEffect,
    provider: GmailProvider | None,
) -> str:
    lock_email_eligibility(effect.recipient_normalized)
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign__created_by", "prospect_email", "sent_by")
        .get(pk=effect.message_id, kind=OutboundMessage.Kind.MANUAL_REPLY)
    )
    if message.state != OutboundMessage.State.SENDING:
        return message.state
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(owner=message.campaign.created_by)
        .first()
    )
    eligibility_error = _effect_eligibility_error(message, connection)
    if eligibility_error:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.SEND_FAILED,
            error=eligibility_error,
        )
    assert connection is not None
    try:
        if provider is None:
            from apps.mailbox.services import provider_for_connection

            provider = provider_for_connection(connection, persist_fake=True)
        result = provider.reply(
            GmailReplyRequest(
                recipient=effect.recipient,
                raw_message=effect.raw_message,
                message_id=effect.rfc_message_id,
                thread_id=effect.gmail_thread_id,
                correlation_id=str(effect.message_id),
                idempotency_key=effect.idempotency_key,
            )
        )
    except (AmbiguousProviderError, RetryableProviderError) as exc:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.RECONCILING,
            error=redact_provider_error(exc, owner_id=message.campaign.created_by_id),
        )
    except (AuthenticationError, PermanentProviderError, ValidationProviderError) as exc:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.SEND_FAILED,
            error=redact_provider_error(exc, owner_id=message.campaign.created_by_id),
        )
    except (ProviderError, ValidationError) as exc:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.RECONCILING,
            error=redact_provider_error(exc, owner_id=message.campaign.created_by_id),
        )
    return _finish_manual_reply(
        message,
        state=OutboundMessage.State.SENT,
        error="",
        result=result,
    )


def deliver_manual_reply(
    message_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> str:
    prepared = _prepare_manual_effect(message_id)
    if isinstance(prepared, str):
        return prepared
    return _execute_manual_effect(prepared, provider)


def reconcile_manual_reply(
    message_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> str:
    message = OutboundMessage.objects.select_related("campaign__created_by").get(
        pk=message_id,
        kind=OutboundMessage.Kind.MANUAL_REPLY,
    )
    if message.state not in {
        OutboundMessage.State.SENDING,
        OutboundMessage.State.RECONCILING,
    }:
        return message.state
    connection = GmailConnection.objects.get(owner=message.campaign.created_by)
    try:
        if provider is None:
            from apps.mailbox.services import provider_for_connection

            provider = provider_for_connection(connection, persist_fake=True)
        result = provider.find_by_message_id(message.message_id)
    except RetryableProviderError as exc:
        with transaction.atomic():
            locked = OutboundMessage.objects.select_for_update().get(pk=message.pk)
            return _finish_manual_reply(
                locked,
                state=OutboundMessage.State.RECONCILING,
                error=redact_provider_error(exc, owner_id=message.campaign.created_by_id),
            )
    except ProviderError as exc:
        with transaction.atomic():
            locked = OutboundMessage.objects.select_for_update().get(pk=message.pk)
            return _finish_manual_reply(
                locked,
                state=OutboundMessage.State.SEND_FAILED,
                error=redact_provider_error(exc, owner_id=message.campaign.created_by_id),
            )
    except ValidationError as exc:
        with transaction.atomic():
            locked = OutboundMessage.objects.select_for_update().get(pk=message.pk)
            return _finish_manual_reply(
                locked,
                state=OutboundMessage.State.SEND_FAILED,
                error=redact_provider_error(exc, owner_id=message.campaign.created_by_id),
            )
    with transaction.atomic():
        locked = (
            OutboundMessage.objects.select_for_update().select_related("sent_by").get(pk=message.pk)
        )
        if result is not None:
            return _finish_manual_reply(
                locked,
                state=OutboundMessage.State.SENT,
                error="",
                result=result,
            )
        return _finish_manual_reply(
            locked,
            state=OutboundMessage.State.SEND_FAILED,
            error="Gmail confirmó que el Message-ID de la respuesta no existe.",
        )


def pending_manual_reply_ids() -> tuple[uuid.UUID, ...]:
    return tuple(
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.MANUAL_REPLY,
            state=OutboundMessage.State.QUEUED,
        ).values_list("pk", flat=True)
    )


def recoverable_manual_reply_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    stale_sending = OutboundMessage.objects.filter(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        state=OutboundMessage.State.SENDING,
        sending_started_at__lte=moment - STALE_SENDING_AFTER,
    ).values_list("pk", flat=True)
    reconciling = OutboundMessage.objects.filter(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        state=OutboundMessage.State.RECONCILING,
        next_attempt_at__lte=moment,
    ).values_list("pk", flat=True)
    return tuple(stale_sending) + tuple(reconciling)
