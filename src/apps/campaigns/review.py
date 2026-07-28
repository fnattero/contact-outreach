from __future__ import annotations

import hashlib
import re
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.campaigns.content import freeze_message_content
from apps.campaigns.delivery import final_email_error
from apps.campaigns.models import Campaign, OutboundMessage
from apps.catalogs.services import verify_catalog
from apps.integrations.contracts import ValidationProviderError
from apps.prospects.analysis import validate_operator_message


def _content_hash(subject: str, body_text: str) -> str:
    return hashlib.sha256(f"{subject}\0{body_text}".encode()).hexdigest()


def _locked_owned_message(
    message_id: uuid.UUID | str,
    *,
    actor: User,
    capability: Capability,
) -> OutboundMessage:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("campaign", "campaign__workspace", "catalog", "prospect_email")
        .get(pk=message_id)
    )
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no tiene una campaña de origen.")
    require_user_capability(
        actor,
        capability,
        workspace_id=campaign.workspace_id,
    )
    if message.kind not in {
        OutboundMessage.Kind.FIRST_CONTACT,
        OutboundMessage.Kind.INITIAL,
    }:
        raise ValidationError("Las respuestas manuales se editan desde su conversación.")
    return message


def _validate_copy(message: OutboundMessage, *, subject: str, body_text: str) -> tuple[str, str]:
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no tiene una campaña de origen.")
    if message.kind == OutboundMessage.Kind.INITIAL:
        normalized_subject = re.sub(r"\s+", " ", subject).strip()
        normalized_body = body_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized_subject or "\n" in subject or len(normalized_subject) > 255:
            raise ValidationError("El asunto no es válido.")
        if (
            not normalized_body
            or len(normalized_body) > 10_000
            or "\x00" in normalized_body
            or re.search(r"<[^>\n]+>", normalized_body)
        ):
            raise ValidationError("El mensaje final debe ser texto plano válido.")
        signature = message.signature_snapshot or campaign.signature_snapshot
        if signature:
            if not normalized_body.endswith(signature):
                raise ValidationError("El mensaje debe conservar la firma aprobada.")
            body = normalized_body[: -len(signature)].rstrip()
        else:
            body = normalized_body
        try:
            frozen = freeze_message_content(
                subject=normalized_subject,
                body=body,
                signature=signature,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return frozen.subject, frozen.rendered_body
    try:
        return validate_operator_message(
            subject=subject,
            body_text=body_text,
            profile=dict(campaign.profile_snapshot),
        )
    except ValidationProviderError as exc:
        raise ValidationError(str(exc)) from exc


def can_edit_message(message: OutboundMessage) -> bool:
    if message.kind not in {
        OutboundMessage.Kind.FIRST_CONTACT,
        OutboundMessage.Kind.INITIAL,
    }:
        return False
    if message.state != OutboundMessage.State.REVIEW_READY or message.approved_at is not None:
        return False
    campaign = message.campaign
    if campaign is None:
        return False
    if message.delivery_mode != campaign.delivery_mode:
        return False
    if message.kind == OutboundMessage.Kind.INITIAL:
        return bool(
            campaign.approval_mode == Campaign.ApprovalMode.PER_MESSAGE
            and campaign.state == Campaign.State.AWAITING_APPROVAL
        )
    if campaign.delivery_mode == Campaign.DeliveryMode.REVIEW_ONLY:
        return campaign.state in {
            Campaign.State.RUNNING,
            Campaign.State.PAUSED,
            Campaign.State.COMPLETED,
        }
    return campaign.delivery_mode == Campaign.DeliveryMode.LIVE and campaign.state in {
        Campaign.State.RUNNING,
        Campaign.State.PAUSED,
    }


def can_approve_message(message: OutboundMessage) -> bool:
    campaign = message.campaign
    return bool(
        can_edit_message(message)
        and campaign is not None
        and message.delivery_mode == Campaign.DeliveryMode.LIVE
        and campaign.delivery_mode == Campaign.DeliveryMode.LIVE
    )


@transaction.atomic
def edit_message_draft(
    message_id: uuid.UUID | str,
    *,
    actor: User,
    subject: str,
    body_text: str,
) -> OutboundMessage:
    message = _locked_owned_message(
        message_id,
        actor=actor,
        capability=Capability.MANAGE_CAMPAIGNS,
    )
    if not can_edit_message(message):
        raise ValidationError("El mensaje ya no admite edición.")
    clean_subject, clean_body = _validate_copy(message, subject=subject, body_text=body_text)
    if clean_subject == message.subject and clean_body == message.body_text:
        return message
    before_hash = _content_hash(message.subject, message.body_text)
    message.subject = clean_subject
    message.body_text = clean_body
    message.content_revision += 1
    message.last_edited_at = timezone.now()
    message.last_edited_by = actor
    message.error = ""
    message.save(
        update_fields=(
            "subject",
            "body_text",
            "content_revision",
            "last_edited_at",
            "last_edited_by",
            "error",
            "updated_at",
        )
    )
    record_event(
        action="message.draft_edited",
        entity=message,
        actor=actor,
        before={"content_revision": message.content_revision - 1, "content_sha256": before_hash},
        after={
            "content_revision": message.content_revision,
            "content_sha256": _content_hash(message.subject, message.body_text),
        },
    )
    return message


@transaction.atomic
def approve_message_for_delivery(
    message_id: uuid.UUID | str,
    *,
    actor: User,
) -> OutboundMessage:
    message = _locked_owned_message(
        message_id,
        actor=actor,
        capability=Capability.APPROVE_CAMPAIGNS,
    )
    if not can_approve_message(message):
        raise ValidationError("El mensaje no está disponible para aprobación de envío en vivo.")
    clean_subject, clean_body = _validate_copy(
        message,
        subject=message.subject,
        body_text=message.body_text,
    )
    campaign = message.campaign
    catalog = message.catalog
    if campaign is None:
        raise ValidationError("El mensaje no tiene una campaña de origen.")
    if catalog is None:
        raise ValidationError("El mensaje no tiene un PDF asociado.")
    if message.catalog_id != campaign.catalog_id:
        raise ValidationError("El catálogo del mensaje no coincide con la campaña.")
    if message.catalog_version != catalog.version:
        raise ValidationError("La versión de catálogo del mensaje es inconsistente.")
    verify_catalog(catalog)
    if message.kind == OutboundMessage.Kind.INITIAL:
        attachments = tuple(message.attachments.select_related("catalog").order_by("position"))
        if not attachments:
            raise ValidationError("El mensaje no tiene los PDFs aprobados de la campaña.")
        for attachment in attachments:
            if (
                attachment.catalog_version != attachment.catalog.version
                or attachment.sha256 != attachment.catalog.sha256
                or attachment.storage_key != attachment.catalog.storage_key
            ):
                raise ValidationError("Uno de los PDFs cambió desde que se preparó el mensaje.")
            verify_catalog(attachment.catalog)
    if eligibility_error := final_email_error(message):
        raise ValidationError(eligibility_error)
    now = timezone.now()
    message.subject = clean_subject
    message.body_text = clean_body
    awaiting_per_message_start = bool(
        message.kind == OutboundMessage.Kind.INITIAL
        and campaign.approval_mode == Campaign.ApprovalMode.PER_MESSAGE
        and campaign.state == Campaign.State.AWAITING_APPROVAL
    )
    message.state = (
        OutboundMessage.State.PREPARED
        if awaiting_per_message_start
        else OutboundMessage.State.QUEUED
    )
    message.approved_at = now
    message.approved_by = actor
    message.next_attempt_at = None if awaiting_per_message_start else now
    message.error = ""
    message.save(
        update_fields=(
            "subject",
            "body_text",
            "state",
            "approved_at",
            "approved_by",
            "next_attempt_at",
            "error",
            "updated_at",
        )
    )
    record_event(
        action="message.approved_for_delivery",
        entity=message,
        actor=actor,
        before={"state": OutboundMessage.State.REVIEW_READY},
        after={
            "state": message.state,
            "content_revision": message.content_revision,
            "content_sha256": _content_hash(message.subject, message.body_text),
        },
    )
    return message
