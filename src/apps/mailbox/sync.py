from __future__ import annotations

import re
import uuid
from decimal import Decimal
from email.utils import getaddresses

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import OutboundMessage
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import normalize_email, suppress_email
from apps.configuration.integrations import redact_provider_error
from apps.integrations.contracts import (
    AuthenticationError,
    GmailCursor,
    GmailHistoryExpired,
    GmailInboundMessage,
    GmailProvider,
    PermanentProviderError,
    ProviderError,
    ValidationProviderError,
)
from apps.integrations.factory import get_llm_provider
from apps.mailbox.classification import classify_message, deterministic_classification
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.mailbox.sanitizer import sanitize_email_bodies

_RFC_MESSAGE_ID = re.compile(r"<[^<>\s]+>")
MAX_RFC_MESSAGE_ID_LENGTH = 255
MAX_REFERENCES = 100


def normalize_message_id(value: str) -> str:
    match = _RFC_MESSAGE_ID.search(value.strip())
    normalized = match.group(0) if match else value.strip().split()[0] if value.strip() else ""
    return normalized if len(normalized) <= MAX_RFC_MESSAGE_ID_LENGTH else ""


def normalize_references(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidates = _RFC_MESSAGE_ID.findall(value) or value.split()
        for candidate in candidates:
            message_id = normalize_message_id(candidate)
            if message_id and message_id not in seen:
                seen.add(message_id)
                normalized.append(message_id)
                if len(normalized) >= MAX_REFERENCES:
                    return tuple(normalized)
    return tuple(normalized)


def _sender_email(value: str) -> str:
    addresses = getaddresses([value])
    return addresses[0][1].strip() if addresses else ""


def _related_outbound(
    connection: GmailConnection,
    candidate: GmailInboundMessage,
) -> OutboundMessage | None:
    by_thread = (
        OutboundMessage.objects.filter(
            campaign__created_by=connection.owner,
            gmail_thread_id=candidate.thread_id,
        )
        .exclude(gmail_thread_id="")
        .order_by("created_at")
        .first()
    )
    if by_thread is not None:
        return by_thread
    referenced = normalize_references((candidate.in_reply_to, *candidate.references))
    if not referenced:
        return None
    return (
        OutboundMessage.objects.filter(
            campaign__created_by=connection.owner,
            message_id__in=referenced,
        )
        .exclude(message_id="")
        .order_by("created_at")
        .first()
    )


def _apply_classification_effect(message: InboundMessage) -> None:
    email = message.related_outbound.recipient
    if message.classification == InboundMessage.Classification.UNSUBSCRIBE:
        suppress_email(
            email=email,
            reason=SuppressionEntry.Reason.UNSUBSCRIBE,
            actor=None,
            source="gmail_reply",
            evidence=f"InboundMessage:{message.pk}",
        )
    elif message.classification == InboundMessage.Classification.BOUNCE:
        suppress_email(
            email=email,
            reason=SuppressionEntry.Reason.BOUNCE,
            actor=None,
            source="gmail_bounce",
            evidence=f"InboundMessage:{message.pk}",
        )


def _persist_candidate(
    *,
    connection: GmailConnection,
    candidate: GmailInboundMessage,
) -> InboundMessage | None:
    if InboundMessage.objects.filter(gmail_message_id=candidate.message_id).exists():
        return None
    if OutboundMessage.objects.filter(gmail_message_id=candidate.message_id).exists():
        return None
    sender = _sender_email(candidate.sender)
    if sender:
        try:
            if normalize_email(sender) == normalize_email(connection.email):
                return None
        except ValidationError:
            pass
    related = _related_outbound(connection, candidate)
    if related is None:
        return None
    body_text, body_html = sanitize_email_bodies(
        body_text=candidate.body_text,
        body_html=candidate.body_html,
    )
    if not body_text:
        body_text = "(Mensaje sin cuerpo de texto legible)"
    references = normalize_references(candidate.references)
    message_id = normalize_message_id(candidate.rfc_message_id)
    in_reply_to = normalize_message_id(candidate.in_reply_to)
    try:
        with transaction.atomic():
            message = InboundMessage.objects.create(
                connection=connection,
                related_outbound=related,
                gmail_message_id=candidate.message_id[:255],
                gmail_thread_id=candidate.thread_id[:255],
                message_id=message_id,
                in_reply_to=in_reply_to,
                references=list(references),
                sender=candidate.sender[:320],
                recipients=list(candidate.recipients),
                subject=candidate.subject[:255],
                external_at=candidate.received_at,
                received_at=timezone.now(),
                body_text=body_text,
                body_html_sanitized=body_html,
                headers=candidate.headers,
            )
    except IntegrityError:
        return None
    deterministic = deterministic_classification(
        sender=message.sender,
        subject=message.subject,
        body_text=message.body_text,
        headers=message.headers,
    )
    classification: str
    if deterministic is not None:
        classification, confidence = deterministic
        error = ""
    else:
        try:
            provider = get_llm_provider(
                related.campaign.llm_provider,
                base_url=related.campaign.llm_base_url,
                model=related.campaign.llm_model,
                owner_id=related.campaign.created_by_id,
            )
        except (ImproperlyConfigured, ProviderError, ValueError) as exc:
            classification = InboundMessage.Classification.OTHER
            confidence = 0.0
            error = redact_provider_error(exc, owner_id=related.campaign.created_by_id)
        else:
            classification, confidence, error = classify_message(
                message=message,
                provider=provider,
                apply_deterministic=False,
                owner_id=related.campaign.created_by_id,
            )
    message.classification = classification
    message.classification_confidence = Decimal(str(confidence))
    message.classification_error = error
    message.is_human = classification not in {
        InboundMessage.Classification.AUTO_REPLY,
        InboundMessage.Classification.BOUNCE,
    }
    message.save(
        update_fields=(
            "classification",
            "classification_confidence",
            "classification_error",
            "is_human",
            "updated_at",
        )
    )
    _apply_classification_effect(message)
    record_event(
        action="gmail.reply_imported",
        entity=message,
        actor=None,
        after={
            "classification": message.classification,
            "campaign_id": str(related.campaign_id),
            "prospect_id": str(related.prospect_id),
        },
    )
    return message


@transaction.atomic
def _sync_gmail_connection_locked(
    connection_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> int:
    connection = (
        GmailConnection.objects.select_for_update().select_related("owner").get(pk=connection_id)
    )
    if connection.status != GmailConnection.Status.CONNECTED:
        return 0
    if provider is None:
        from apps.mailbox.services import provider_for_connection

        provider = provider_for_connection(connection, persist_fake=True)
    if not connection.history_id:
        baseline = provider.test_connection().history_id
        if not baseline:
            raise ValidationProviderError("Gmail no devolvió un historyId para el baseline.")
        connection.history_id = baseline
        connection.last_sync_at = timezone.now()
        connection.error = ""
        connection.save(update_fields=("history_id", "last_sync_at", "error", "updated_at"))
        record_event(
            action="gmail.sync_baseline_initialized",
            entity=connection,
            actor=None,
            after={"imported": 0},
        )
        return 0
    cursor = GmailCursor(connection.history_id)
    try:
        batch = provider.sync(cursor)
    except GmailHistoryExpired:
        batch = provider.sync(None)
    imported = 0
    for candidate in batch.messages:
        if _persist_candidate(connection=connection, candidate=candidate) is not None:
            imported += 1
    connection.history_id = batch.next_cursor.history_id
    connection.last_sync_at = timezone.now()
    connection.error = ""
    connection.save(update_fields=("history_id", "last_sync_at", "error", "updated_at"))
    record_event(
        action="gmail.synced",
        entity=connection,
        actor=None,
        after={"imported": imported, "fallback": batch.used_fallback},
    )
    return imported


def _persist_sync_failure(connection_id: uuid.UUID | str, error: Exception) -> None:
    with transaction.atomic():
        connection = GmailConnection.objects.select_for_update().get(pk=connection_id)
        text = redact_provider_error(error, owner_id=connection.owner_id)
        connection.status = GmailConnection.Status.ERROR
        connection.error = text
        connection.save(update_fields=("status", "error", "updated_at"))
        record_event(
            action="gmail.sync_failed",
            entity=connection,
            actor=None,
            after={"status": connection.status, "error_type": error.__class__.__name__},
        )


def sync_gmail_connection(
    connection_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> int:
    try:
        return _sync_gmail_connection_locked(connection_id, provider=provider)
    except (AuthenticationError, PermanentProviderError, ValidationProviderError) as exc:
        _persist_sync_failure(connection_id, exc)
        raise


def sync_all_connections() -> int:
    imported = 0
    connection_ids = GmailConnection.objects.filter(
        status=GmailConnection.Status.CONNECTED
    ).values_list("pk", flat=True)
    for connection_id in connection_ids:
        imported += sync_gmail_connection(connection_id)
    return imported
