from __future__ import annotations

import re
import uuid
from decimal import Decimal
from email.utils import getaddresses
from functools import partial

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import OutboundMessage
from apps.compliance.services import normalize_email
from apps.configuration.integrations import redact_provider_error
from apps.contacts.models import EmailAddress
from apps.contacts.services import apply_inbound_contact_effect
from apps.integrations.contracts import (
    AuthenticationError,
    GmailCursor,
    GmailHistoryExpired,
    GmailInboundMessage,
    GmailProvider,
    LLMProvider,
    PermanentProviderError,
    ValidationProviderError,
)
from apps.mailbox.classification import deterministic_classification
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.mailbox.sanitizer import sanitize_email_bodies
from apps.prospects.email_validation import MXResolver

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
    workspace_scope = Q(organization__workspace_id=connection.workspace_id) | Q(
        organization__isnull=True,
        campaign__workspace_id=connection.workspace_id,
    )
    referenced = normalize_references((candidate.in_reply_to, *candidate.references))
    if referenced:
        # In-Reply-To/References identify the direct parent needed by the
        # bounded LLM context. A thread can contain several of our messages, so
        # resolving the thread first would silently collapse the parent to the
        # original proposal.
        referenced_message = (
            OutboundMessage.objects.filter(
                workspace_scope,
                message_id__in=referenced,
            )
            .exclude(message_id="")
            .order_by("-created_at")
            .first()
        )
        if referenced_message is not None:
            return referenced_message
    return (
        OutboundMessage.objects.filter(
            workspace_scope,
            gmail_thread_id=candidate.thread_id,
        )
        .exclude(gmail_thread_id="")
        .order_by("created_at")
        .first()
    )


def _direct_contact_sender(
    connection: GmailConnection,
    sender: str,
) -> EmailAddress | None:
    literal = _sender_email(sender)
    if not literal:
        return None
    try:
        normalized = normalize_email(literal)
    except ValidationError:
        return None
    return (
        EmailAddress.objects.select_related("organization", "organization__contact")
        .filter(
            workspace=connection.workspace,
            normalized_email=normalized,
            validity=EmailAddress.Validity.VALID,
            invalid_reason="",
            organization__contact__isnull=False,
        )
        .first()
    )


def _enqueue_inbound_processing(message_id: uuid.UUID) -> None:
    from apps.mailbox.tasks import process_inbound_reply_task

    process_inbound_reply_task.delay(str(message_id))


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
    direct_sender = None if related is not None else _direct_contact_sender(connection, sender)
    if related is None and direct_sender is None:
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
                organization=(
                    direct_sender.organization
                    if direct_sender is not None and related is None
                    else None
                ),
                contact=(
                    direct_sender.organization.contact
                    if direct_sender is not None and related is None
                    else None
                ),
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
    needs_processing = deterministic is None
    if deterministic is not None:
        classification, confidence = deterministic
        error = ""
    else:
        # The durable inbound and its Contact are committed before any LLM provider is
        # constructed. The post-commit task is the compatibility seam for the richer
        # structured reply-decision workflow.
        classification = InboundMessage.Classification.OTHER
        confidence = 0.0
        error = ""
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
    effect = apply_inbound_contact_effect(message)
    record_event(
        action="gmail.reply_imported",
        entity=message,
        actor=None,
        after={
            "classification": message.classification,
            "campaign_id": str(related.campaign_id) if related is not None else "",
            "prospect_id": str(related.prospect_id) if related is not None else "",
            "organization_id": str(effect.contact.organization_id)
            if effect.contact is not None
            else str(related.organization_id or "")
            if related is not None
            else "",
            "contact_id": str(effect.contact.pk) if effect.contact is not None else "",
        },
    )
    if needs_processing and effect.contact is not None and effect.conversation is not None:
        # Preserve literal mailto targets before the raw HTML leaves this provider
        # boundary. The sanitized body intentionally removes link attributes.
        from apps.automation.candidates import (
            extract_mailto_literals,
            persist_email_candidates,
        )

        persist_email_candidates(
            message,
            mailto_literals=extract_mailto_literals(candidate.body_html),
        )
        transaction.on_commit(
            partial(_enqueue_inbound_processing, message.pk),
            robust=True,
        )
    return message


def process_inbound_reply(
    inbound_id: uuid.UUID | str,
    *,
    provider: LLMProvider | None = None,
    resolver: MXResolver | None = None,
) -> str:
    """Record a structured reply decision outside the Gmail synchronization lock.

    The decision service owns candidate extraction, bounded context and policy evaluation.
    This compatibility seam mirrors only safe, non-deterministic classifications for legacy
    screens; deterministic unsubscribe, bounce and auto-reply effects remain authoritative.
    It never authorizes or performs a Gmail send.
    """

    message = InboundMessage.objects.select_related(
        "related_outbound__campaign",
        "contact",
        "conversation",
    ).get(pk=inbound_id)
    if message.contact_id is None or message.conversation_id is None or not message.is_human:
        return message.classification
    deterministic = deterministic_classification(
        sender=message.sender,
        subject=message.subject,
        body_text=message.body_text,
        headers=message.headers,
    )
    if deterministic is not None:
        return message.classification

    # Local import avoids coupling Gmail synchronization module import order to automation.
    from apps.automation.services import process_inbound_decision

    decision = process_inbound_decision(
        message.pk,
        provider=provider,
        resolver=resolver,
    )
    if decision is None:
        return message.classification
    allowed = {
        InboundMessage.Classification.INTERESTED,
        InboundMessage.Classification.NOT_INTERESTED,
        InboundMessage.Classification.OTHER,
    }
    classification = decision.classification
    confidence = decision.confidence
    error = ""
    if classification not in allowed:
        classification = InboundMessage.Classification.OTHER
        confidence = Decimal("0")
        error = "La decisión intentó aplicar un efecto reservado para reglas determinísticas."
    with transaction.atomic():
        locked = InboundMessage.objects.select_for_update().get(pk=message.pk)
        if not locked.is_human or locked.classification in {
            InboundMessage.Classification.AUTO_REPLY,
            InboundMessage.Classification.BOUNCE,
            InboundMessage.Classification.UNSUBSCRIBE,
        }:
            return locked.classification
        locked.classification = classification
        locked.classification_confidence = Decimal(str(confidence))
        locked.classification_error = error
        locked.save(
            update_fields=(
                "classification",
                "classification_confidence",
                "classification_error",
                "updated_at",
            )
        )
        record_event(
            action="gmail.reply_classified",
            entity=locked,
            actor=None,
            after={
                "classification": classification,
                "reply_decision_id": str(decision.pk),
            },
        )
        return locked.classification


@transaction.atomic
def _sync_gmail_connection_locked(
    connection_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> int:
    connection = (
        GmailConnection.objects.select_for_update()
        .select_related("owner", "workspace")
        .get(pk=connection_id)
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
