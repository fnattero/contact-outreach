from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parseaddr

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.campaigns.models import Campaign, OutboundMessage
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import lock_email_eligibility, normalize_email
from apps.configuration.integrations import redact_provider_error
from apps.contacts.models import CommunicationRestriction, Contact, EmailAddress, Organization
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
ACTIVE_REPLY_STATES = (
    OutboundMessage.State.QUEUED,
    OutboundMessage.State.SENDING,
    OutboundMessage.State.RECONCILING,
    OutboundMessage.State.SENT,
)


@dataclass(frozen=True, slots=True)
class ManualReplyEffect:
    message_id: uuid.UUID
    raw_message: bytes
    rfc_message_id: str
    recipient: str
    recipient_normalized: str
    gmail_thread_id: str
    idempotency_key: str


def _reply_references(
    inbound: InboundMessage,
    root: OutboundMessage | None,
) -> tuple[str, ...]:
    values = [*(str(value) for value in inbound.references), inbound.message_id]
    if root is not None and root.message_id:
        values.insert(0, root.message_id)
    return normalize_references(tuple(values))


def _contact_email(message: OutboundMessage) -> EmailAddress | None:
    if message.email_address is not None:
        return message.email_address
    enrollment = message.campaign_enrollment
    return enrollment.selected_email if enrollment is not None else None


def _active_contact_restriction(
    *,
    workspace_id: uuid.UUID | str,
    contact_id: object | None,
    email_address: EmailAddress | None,
    organization_id: object | None,
) -> bool:
    targets = Q()
    if email_address is not None:
        targets |= Q(email_address_id=email_address.pk)
    if contact_id is not None:
        targets |= Q(contact_id=contact_id)
    if organization_id is not None:
        targets |= Q(contact__organization_id=organization_id)
    if not targets:
        return False
    return (
        CommunicationRestriction.objects.filter(
            workspace_id=workspace_id,
            revoked_at__isnull=True,
        )
        .filter(targets)
        .exists()
    )


def _channel_eligibility_error(
    message: OutboundMessage,
    *,
    workspace_id: uuid.UUID | str,
    contact_id: object | None,
) -> str:
    try:
        normalized = normalize_email(message.recipient)
    except ValidationError:
        return "El destinatario ya no es un email válido."
    if normalized != message.recipient_normalized:
        return "El destinatario ya no coincide con el email guardado."

    email_address = _contact_email(message)
    legacy_email = message.prospect_email
    if email_address is not None:
        if (
            email_address.workspace_id != workspace_id
            or email_address.normalized_email != normalized
            or email_address.validity != EmailAddress.Validity.VALID
            or bool(email_address.invalid_reason)
        ):
            return "El email del contacto ya no es válido para responder."
        if (
            message.organization_id is not None
            and email_address.organization_id != message.organization_id
        ):
            return "El email ya no pertenece a la organización de esta conversación."
    elif legacy_email is not None:
        if (
            legacy_email.normalized_email != normalized
            or legacy_email.is_invalid
            or not legacy_email.is_primary
            or not legacy_email.syntax_valid
            or legacy_email.mx_status != legacy_email.MXStatus.VALID
            or bool(legacy_email.exclusion_reason)
        ):
            return "El correo del prospecto ya no es válido para responder."
    else:
        return "La conversación no tiene un email validado para responder."

    if _active_contact_restriction(
        workspace_id=workspace_id,
        contact_id=contact_id,
        email_address=email_address,
        organization_id=message.organization_id,
    ):
        return "Este contacto o email tiene una restricción activa."
    if SuppressionEntry.objects.filter(normalized_email=normalized).exists():
        return "El destinatario está suprimido."
    return ""


def _sender_email(sender: str) -> str:
    _, parsed = parseaddr(sender)
    return parsed.strip() if parsed else sender.strip()


def _direct_contact_email(inbound: InboundMessage) -> EmailAddress | None:
    try:
        normalized = normalize_email(_sender_email(inbound.sender))
    except ValidationError:
        return None
    return (
        EmailAddress.objects.select_related("organization", "organization__contact")
        .filter(
            workspace_id=inbound.connection.workspace_id,
            normalized_email=normalized,
            validity=EmailAddress.Validity.VALID,
            invalid_reason="",
            organization__contact__isnull=False,
        )
        .first()
    )


def _validate_authorization(
    *,
    inbound: InboundMessage,
    body_text: str,
) -> tuple[OutboundMessage | None, EmailAddress | None, str, str]:
    root = inbound.related_outbound
    connection = inbound.connection
    cleaned_body = body_text.strip()
    if not cleaned_body:
        raise ValidationError("Escribí una respuesta antes de enviar.")
    if len(cleaned_body) > 10_000:
        raise ValidationError("La respuesta excede el máximo permitido.")
    if not inbound.is_human:
        raise ValidationError(
            "No se puede responder manualmente a un rebote o respuesta automática."
        )
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        raise ValidationError(
            "La configuración global de envío o el bloqueo general impiden la respuesta manual."
        )
    if not connection.is_ready or set(connection.scopes) != set(GMAIL_SCOPES):
        raise ValidationError("Gmail debe estar conectado y probado.")
    if not inbound.gmail_thread_id or not normalize_message_id(inbound.message_id):
        raise ValidationError(
            "La conversación no tiene los identificadores de Gmail y del correo necesarios "
            "para responder."
        )
    if root is not None:
        campaign = root.campaign
        if root.delivery_mode != Campaign.DeliveryMode.LIVE or (
            campaign is not None and campaign.delivery_mode != Campaign.DeliveryMode.LIVE
        ):
            raise ValidationError("Las respuestas manuales requieren un origen con envío en vivo.")
        normalized = normalize_email(root.recipient)
        if channel_error := _channel_eligibility_error(
            root,
            workspace_id=connection.workspace_id,
            contact_id=inbound.contact_id or root.contact_id,
        ):
            raise ValidationError(channel_error)
        return root, _contact_email(root), cleaned_body, normalized

    email = _direct_contact_email(inbound)
    if email is None:
        raise ValidationError(
            "Solo se puede responder un mail directo si el remitente coincide con un "
            "email válido de un contacto existente."
        )
    contact = email.organization.contact
    if inbound.contact_id is not None and inbound.contact_id != contact.pk:
        raise ValidationError("El remitente ya no coincide con el contacto de esta conversación.")
    normalized = email.normalized_email
    if _active_contact_restriction(
        workspace_id=connection.workspace_id,
        contact_id=contact.pk,
        email_address=email,
        organization_id=email.organization_id,
    ):
        raise ValidationError("Este contacto o email tiene una restricción activa.")
    if SuppressionEntry.objects.filter(normalized_email=normalized).exists():
        raise ValidationError("El destinatario está suprimido.")
    return None, email, cleaned_body, normalized


def _automatic_reply_conflict(inbound: InboundMessage) -> bool:
    from apps.automation.models import ReplyDecision

    if ReplyDecision.objects.filter(
        inbound=inbound,
        state__in=(
            ReplyDecision.State.AUTO_ELIGIBLE,
            ReplyDecision.State.AUTHORIZED,
            ReplyDecision.State.EXECUTING,
        ),
    ).exists():
        return True
    return (
        OutboundMessage.objects.filter(parent_inbound=inbound)
        .filter(
            Q(
                kind__in=(
                    OutboundMessage.Kind.AUTOMATIC_REPLY,
                    OutboundMessage.Kind.REDIRECT_ACK,
                ),
                state__in=ACTIVE_REPLY_STATES,
            )
            | Q(
                kind=OutboundMessage.Kind.REFERRED_PROPOSAL,
                state__in=(
                    OutboundMessage.State.QUEUED,
                    OutboundMessage.State.SENDING,
                    OutboundMessage.State.RECONCILING,
                ),
            )
        )
        .exists()
    )


@transaction.atomic
def authorize_manual_reply(
    *,
    actor: User,
    inbound_id: uuid.UUID | str,
    body_text: str,
    request_key: uuid.UUID | str,
) -> tuple[OutboundMessage, bool]:
    membership = require_user_capability(actor, Capability.SEND_REPLIES)
    key = f"manual-reply:{inbound_id}:{request_key}"
    existing = (
        OutboundMessage.objects.filter(
            idempotency_key=key,
        )
        .filter(
            Q(campaign__workspace=membership.workspace)
            | Q(parent_inbound__connection__workspace=membership.workspace)
        )
        .first()
    )
    if existing is not None:
        return existing, False
    inbound = (
        InboundMessage.objects.select_for_update()
        .select_related(
            "connection",
            "related_outbound__campaign",
            "related_outbound__catalog",
            "related_outbound__prospect_email",
            "related_outbound__email_address",
            "related_outbound__organization",
            "related_outbound__campaign_enrollment",
            "related_outbound__campaign_enrollment__selected_email",
            "related_outbound__contact",
            "related_outbound__conversation",
            "organization",
            "contact",
            "conversation",
        )
        .get(pk=inbound_id, connection__workspace=membership.workspace)
    )
    existing = OutboundMessage.objects.filter(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        parent_inbound=inbound,
    ).first()
    if existing is not None:
        return existing, False
    if _automatic_reply_conflict(inbound):
        raise ValidationError(
            "Esta respuesta ya tiene una contestación automática autorizada o enviada. "
            "Revisá el historial antes de continuar."
        )
    root, email_address, cleaned_body, normalized = _validate_authorization(
        inbound=inbound,
        body_text=body_text,
    )
    now = timezone.now()
    references = _reply_references(inbound, root)
    organization: Organization | None
    fallback_contact: Contact | None
    delivery_mode: str
    if root is None:
        assert email_address is not None
        organization = email_address.organization
        fallback_contact = email_address.organization.contact
        recipient = email_address.original_email
        subject = inbound.subject
        campaign = None
        campaign_enrollment = None
        conversation = inbound.conversation
        prospect = None
        prospect_email = None
        catalog = None
        catalog_version = None
        delivery_mode = Campaign.DeliveryMode.LIVE
    else:
        organization = root.organization
        fallback_contact = root.contact
        recipient = root.recipient
        subject = root.subject
        campaign = root.campaign
        campaign_enrollment = root.campaign_enrollment
        conversation = inbound.conversation or root.conversation
        prospect = root.prospect
        prospect_email = root.prospect_email
        catalog = root.catalog
        catalog_version = root.catalog_version
        delivery_mode = root.delivery_mode
    contact = inbound.contact or fallback_contact
    try:
        with transaction.atomic():
            message = OutboundMessage.objects.create(
                kind=OutboundMessage.Kind.MANUAL_REPLY,
                campaign=campaign,
                organization=organization,
                campaign_enrollment=campaign_enrollment,
                contact=contact,
                conversation=conversation,
                prospect=prospect,
                prospect_email=prospect_email,
                email_address=email_address,
                analysis=None,
                parent_inbound=inbound,
                sent_by=actor,
                recipient=recipient,
                recipient_normalized=normalized,
                subject=subject,
                body_text=cleaned_body,
                catalog=catalog,
                catalog_version=catalog_version,
                state=OutboundMessage.State.QUEUED,
                delivery_mode=delivery_mode,
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
        return "La configuración global de envío o el bloqueo general impiden la respuesta manual."
    campaign = message.campaign
    if message.delivery_mode != Campaign.DeliveryMode.LIVE or (
        campaign is not None and campaign.delivery_mode != Campaign.DeliveryMode.LIVE
    ):
        return "La respuesta no pertenece a una campaña con envío en vivo."
    if connection is None or not connection.is_ready or set(connection.scopes) != set(GMAIL_SCOPES):
        return "Gmail no está conectado y probado."
    parent = message.parent_inbound
    if parent is None or parent.connection_id != connection.pk:
        return "La respuesta perdió la referencia segura a la conversación original."
    return _channel_eligibility_error(
        message,
        workspace_id=connection.workspace_id,
        contact_id=message.contact_id or parent.contact_id,
    )


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
        .select_related(
            "campaign__created_by",
            "prospect_email",
            "email_address",
            "campaign_enrollment__selected_email",
            "organization",
            "contact",
            "parent_inbound__connection",
            "sent_by",
        )
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
    parent = message.parent_inbound
    connection = None
    if parent is not None:
        connection = (
            GmailConnection.objects.select_for_update()
            .filter(pk=parent.connection_id, workspace_id=parent.connection.workspace_id)
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
        .select_related(
            "campaign__created_by",
            "prospect_email",
            "email_address",
            "campaign_enrollment__selected_email",
            "organization",
            "contact",
            "parent_inbound__connection",
            "sent_by",
        )
        .get(pk=effect.message_id, kind=OutboundMessage.Kind.MANUAL_REPLY)
    )
    if message.state != OutboundMessage.State.SENDING:
        return message.state
    parent = message.parent_inbound
    connection = None
    if parent is not None:
        connection = (
            GmailConnection.objects.select_for_update()
            .filter(pk=parent.connection_id, workspace_id=parent.connection.workspace_id)
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
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    except (AuthenticationError, PermanentProviderError, ValidationProviderError) as exc:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.SEND_FAILED,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
        )
    except (ProviderError, ValidationError) as exc:
        return _finish_manual_reply(
            message,
            state=OutboundMessage.State.RECONCILING,
            error=redact_provider_error(exc, owner_id=connection.owner_id),
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
    message = OutboundMessage.objects.select_related(
        "campaign__created_by",
        "parent_inbound__connection",
    ).get(pk=message_id, kind=OutboundMessage.Kind.MANUAL_REPLY)
    if message.state not in {
        OutboundMessage.State.SENDING,
        OutboundMessage.State.RECONCILING,
    }:
        return message.state
    campaign = message.campaign
    parent = message.parent_inbound
    if (
        message.delivery_mode != Campaign.DeliveryMode.LIVE
        or (campaign is not None and campaign.delivery_mode != Campaign.DeliveryMode.LIVE)
        or parent is None
    ):
        with transaction.atomic():
            locked = (
                OutboundMessage.objects.select_for_update()
                .select_related("sent_by")
                .get(pk=message.pk)
            )
            return _finish_manual_reply(
                locked,
                state=OutboundMessage.State.SEND_FAILED,
                error="La respuesta no pertenece a una campaña con envío en vivo.",
            )
    connection = GmailConnection.objects.get(
        pk=parent.connection_id,
        workspace_id=parent.connection.workspace_id,
    )
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
                error=redact_provider_error(exc, owner_id=connection.owner_id),
            )
    except ProviderError as exc:
        with transaction.atomic():
            locked = OutboundMessage.objects.select_for_update().get(pk=message.pk)
            return _finish_manual_reply(
                locked,
                state=OutboundMessage.State.SEND_FAILED,
                error=redact_provider_error(exc, owner_id=connection.owner_id),
            )
    except ValidationError as exc:
        with transaction.atomic():
            locked = OutboundMessage.objects.select_for_update().get(pk=message.pk)
            return _finish_manual_reply(
                locked,
                state=OutboundMessage.State.SEND_FAILED,
                error=redact_provider_error(exc, owner_id=connection.owner_id),
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
            error="Gmail confirmó que el identificador del mensaje de respuesta no existe.",
        )


def pending_manual_reply_ids() -> tuple[uuid.UUID, ...]:
    return tuple(
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.MANUAL_REPLY,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            state=OutboundMessage.State.QUEUED,
        )
        .filter(Q(campaign__isnull=True) | Q(campaign__delivery_mode=Campaign.DeliveryMode.LIVE))
        .values_list("pk", flat=True)
    )


def recoverable_manual_reply_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    stale_sending = (
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.MANUAL_REPLY,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            state=OutboundMessage.State.SENDING,
            sending_started_at__lte=moment - STALE_SENDING_AFTER,
        )
        .filter(Q(campaign__isnull=True) | Q(campaign__delivery_mode=Campaign.DeliveryMode.LIVE))
        .values_list("pk", flat=True)
    )
    reconciling = (
        OutboundMessage.objects.filter(
            kind=OutboundMessage.Kind.MANUAL_REPLY,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            state=OutboundMessage.State.RECONCILING,
            next_attempt_at__lte=moment,
        )
        .filter(Q(campaign__isnull=True) | Q(campaign__delivery_mode=Campaign.DeliveryMode.LIVE))
        .values_list("pk", flat=True)
    )
    return tuple(stale_sending) + tuple(reconciling)
