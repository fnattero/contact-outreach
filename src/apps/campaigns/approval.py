from __future__ import annotations

import json
import uuid
from collections.abc import Collection
from hashlib import sha256

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.campaigns.content import freeze_message_content
from apps.campaigns.models import (
    Campaign,
    CampaignAttachment,
    OutboundAttachment,
    OutboundMessage,
)
from apps.catalogs.models import Catalog
from apps.catalogs.services import verify_catalog
from apps.configuration.message_templates import ensure_default_message_templates
from apps.configuration.models import BusinessProfile
from apps.contacts.models import CampaignEnrollment
from apps.contacts.services import enrollment_eligibility, refresh_enrollment_eligibility

MAX_ATTACHMENT_SOURCE_BYTES = 17 * 1024 * 1024


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode()).hexdigest()


def freeze_campaign_content(campaign: Campaign) -> None:
    templates = ensure_default_message_templates(campaign.workspace)
    profile = BusinessProfile.objects.filter(workspace=campaign.workspace).first()
    signature = profile.signature.strip() if profile is not None else ""
    campaign.initial_subject_snapshot = templates.initial.subject
    campaign.initial_body_snapshot = templates.initial.body
    campaign.reminder_body_snapshot = templates.reminder.body
    campaign.referred_subject_snapshot = templates.referred_proposal.subject
    campaign.referred_body_snapshot = templates.referred_proposal.body
    campaign.signature_snapshot = signature
    campaign.template_revision_snapshot = {
        "INITIAL": {"id": str(templates.initial.pk), "revision": templates.initial.revision},
        "REMINDER": {"id": str(templates.reminder.pk), "revision": templates.reminder.revision},
        "REFERRED_PROPOSAL": {
            "id": str(templates.referred_proposal.pk),
            "revision": templates.referred_proposal.revision,
        },
        "profile_version": profile.profile_version if profile is not None else None,
    }
    frozen = freeze_message_content(
        subject=campaign.initial_subject_snapshot,
        body=campaign.initial_body_snapshot,
        signature=campaign.signature_snapshot,
    )
    campaign.content_hash = frozen.content_hash


def _verified_campaign_attachments(campaign: Campaign) -> tuple[CampaignAttachment, ...]:
    attachments = tuple(
        campaign.attachments.select_related("catalog").order_by("position", "created_at")
    )
    if not attachments:
        raise ValidationError("Elegí al menos un PDF para la propuesta inicial.")
    total = 0
    for attachment in attachments:
        verify_catalog(attachment.catalog)
        total += attachment.catalog.byte_size
        if attachment.catalog.byte_size > 15 * 1024 * 1024:
            raise ValidationError(f"{attachment.catalog.name} supera el límite de 15 MiB.")
    if total > MAX_ATTACHMENT_SOURCE_BYTES:
        raise ValidationError("Los PDFs seleccionados superan el límite combinado de 17 MiB.")
    return attachments


@transaction.atomic
def set_campaign_attachments(
    campaign: Campaign,
    *,
    catalog_ids: Collection[uuid.UUID | str],
    actor: User,
) -> tuple[CampaignAttachment, ...]:
    locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
    require_user_capability(
        actor,
        Capability.MANAGE_CAMPAIGNS,
        workspace_id=locked.workspace_id,
    )
    if locked.state != Campaign.State.DRAFT:
        raise ValidationError("Los PDFs no se pueden cambiar después de iniciar la búsqueda.")
    ordered_ids = list(dict.fromkeys(catalog_ids))
    if not ordered_ids:
        raise ValidationError("Elegí al menos un PDF para la propuesta inicial.")
    by_id = {
        str(item.pk): item
        for item in Catalog.objects.filter(
            pk__in=ordered_ids,
            workspace=locked.workspace,
            active=True,
            missing=False,
        )
    }
    catalogs = [by_id[str(item_id)] for item_id in ordered_ids if str(item_id) in by_id]
    if len(catalogs) != len(ordered_ids):
        raise ValidationError("Uno de los PDFs ya no está disponible.")
    total = sum(item.byte_size for item in catalogs)
    if any(item.byte_size > 15 * 1024 * 1024 for item in catalogs):
        raise ValidationError("Cada PDF debe pesar como máximo 15 MiB.")
    if total > MAX_ATTACHMENT_SOURCE_BYTES:
        raise ValidationError("Los PDFs seleccionados superan el límite combinado de 17 MiB.")
    locked.attachments.all().delete()
    created = CampaignAttachment.objects.bulk_create(
        CampaignAttachment(campaign=locked, catalog=catalog, position=index)
        for index, catalog in enumerate(catalogs)
    )
    if locked.catalog_id != catalogs[0].pk:
        locked.catalog = catalogs[0]
        locked.save(update_fields=("catalog", "updated_at"))
    return tuple(created)


def snapshot_outbound_attachments(message: OutboundMessage) -> tuple[OutboundAttachment, ...]:
    campaign = message.campaign
    if campaign is None:
        raise ValidationError("El mensaje no tiene una campaña de origen para sus PDFs.")
    existing = tuple(message.attachments.order_by("position"))
    if existing:
        return existing
    source = _verified_campaign_attachments(campaign)
    return tuple(
        OutboundAttachment.objects.bulk_create(
            OutboundAttachment(
                message=message,
                catalog=item.catalog,
                position=item.position,
                catalog_version=item.catalog.version,
                storage_key=item.catalog.storage_key,
                filename=item.catalog.original_filename,
                byte_size=item.catalog.byte_size,
                sha256=item.catalog.sha256,
            )
            for item in source
        )
    )


def _audience_rows(campaign: Campaign) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    enrollments = (
        campaign.enrollments.select_related("selected_email")
        .filter(state__in=(CampaignEnrollment.State.ELIGIBLE, CampaignEnrollment.State.PREPARED))
        .exclude(selected_email=None)
        .order_by("organization_id", "selected_email_id")
    )
    for enrollment in enrollments:
        email = enrollment.selected_email
        if email is None:  # Defensive for concurrent deletion/schema drift.
            continue
        rows.append(
            {
                "enrollment_id": str(enrollment.pk),
                "organization_id": str(enrollment.organization_id),
                "email_id": str(email.pk),
                "normalized_email": email.normalized_email,
            }
        )
    return rows


def _attachment_rows(campaign: Campaign) -> list[dict[str, object]]:
    return [
        {
            "catalog_id": str(item.catalog_id),
            "position": item.position,
            "version": item.catalog.version,
            "sha256": item.catalog.sha256,
            "size": item.catalog.byte_size,
        }
        for item in campaign.attachments.select_related("catalog").order_by("position")
    ]


def _schedule_payload(campaign: Campaign) -> dict[str, object]:
    def clock(value: object) -> str:
        if hasattr(value, "strftime"):
            return str(value.strftime("%H:%M"))
        return str(value).strip()[:5]

    return {
        "daily_limit": campaign.daily_limit,
        "interval_minutes": campaign.message_interval_minutes,
        "weekdays": campaign.weekdays,
        "window_start": clock(campaign.window_start),
        "window_end": clock(campaign.window_end),
        "timezone": campaign.timezone_name,
        "reminder_enabled": campaign.reminder_enabled,
        "reminder_delay_days": campaign.reminder_delay_days,
    }


def refresh_campaign_hashes(campaign: Campaign) -> None:
    audience = _audience_rows(campaign)
    attachments = _attachment_rows(campaign)
    campaign.audience_hash = _canonical_hash(audience)
    campaign.attachment_hash = _canonical_hash(attachments)
    campaign.schedule_hash = _canonical_hash(_schedule_payload(campaign))


def prepare_fixed_initial_messages(campaign: Campaign) -> tuple[OutboundMessage, ...]:
    _verified_campaign_attachments(campaign)
    frozen = freeze_message_content(
        subject=campaign.initial_subject_snapshot,
        body=campaign.initial_body_snapshot,
        signature=campaign.signature_snapshot,
    )
    prepared: list[OutboundMessage] = []
    enrollments = campaign.enrollments.select_related(
        "organization",
        "selected_email",
        "selected_email__legacy_prospect_email__prospect",
    ).order_by("created_at")
    for enrollment in enrollments:
        eligibility = refresh_enrollment_eligibility(enrollment)
        enrollment.refresh_from_db()
        if not eligibility.eligible or enrollment.selected_email is None:
            continue
        email = enrollment.selected_email
        legacy_email = email.legacy_prospect_email
        message, _ = OutboundMessage.objects.get_or_create(
            idempotency_key=f"initial:{campaign.pk}:{enrollment.pk}",
            defaults={
                "kind": OutboundMessage.Kind.INITIAL,
                "campaign": campaign,
                "organization": enrollment.organization,
                "campaign_enrollment": enrollment,
                "prospect": legacy_email.prospect if legacy_email is not None else None,
                "prospect_email": legacy_email,
                "email_address": email,
                "recipient": email.original_email,
                "recipient_normalized": email.normalized_email,
                "subject": frozen.subject,
                "body_text": frozen.rendered_body,
                "signature_snapshot": frozen.signature,
                "content_hash": frozen.content_hash,
                "catalog": campaign.catalog,
                "catalog_version": campaign.catalog.version,
                "state": (
                    OutboundMessage.State.REVIEW_READY
                    if campaign.approval_mode == Campaign.ApprovalMode.PER_MESSAGE
                    else OutboundMessage.State.PREPARED
                ),
                "delivery_mode": campaign.delivery_mode,
            },
        )
        snapshot_outbound_attachments(message)
        enrollment.state = CampaignEnrollment.State.PREPARED
        enrollment.save(update_fields=("state", "updated_at"))
        prepared.append(message)
    refresh_campaign_hashes(campaign)
    return tuple(prepared)


@transaction.atomic
def move_campaign_to_approval(campaign_id: uuid.UUID | str) -> Campaign:
    campaign = (
        Campaign.objects.select_for_update()
        .select_related("workspace", "catalog")
        .get(pk=campaign_id)
    )
    if campaign.state != Campaign.State.DISCOVERING:
        raise ValidationError("La campaña no está terminando una búsqueda.")
    if campaign.discovery_state not in {
        Campaign.DiscoveryState.TARGET_REACHED,
        Campaign.DiscoveryState.EXHAUSTED_QUERIES,
        Campaign.DiscoveryState.EXHAUSTED_RAW_LIMIT,
        Campaign.DiscoveryState.EXHAUSTED_COST,
    }:
        raise ValidationError("La búsqueda todavía no terminó correctamente.")
    prepare_fixed_initial_messages(campaign)
    if not campaign.messages.filter(kind=OutboundMessage.Kind.INITIAL).exists():
        raise ValidationError("No encontramos destinatarios válidos para aprobar.")
    campaign.state = Campaign.State.AWAITING_APPROVAL
    campaign.save(
        update_fields=(
            "state",
            "audience_hash",
            "content_hash",
            "attachment_hash",
            "schedule_hash",
            "updated_at",
        )
    )
    record_event(
        action="campaign.awaiting_approval",
        entity=campaign,
        actor=None,
        after={"audience_count": len(_audience_rows(campaign))},
    )
    return campaign


def maybe_move_campaign_to_approval(campaign_id: uuid.UUID | str) -> Campaign | None:
    """Finalize only after every discovered row reached a deterministic terminal state."""

    from apps.prospects.models import Prospect

    campaign = Campaign.objects.get(pk=campaign_id)
    if campaign.state != Campaign.State.DISCOVERING:
        return campaign if campaign.state == Campaign.State.AWAITING_APPROVAL else None
    if campaign.discovery_state == Campaign.DiscoveryState.FAILED_PROVIDER:
        with transaction.atomic():
            locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
            if locked.state == Campaign.State.DISCOVERING:
                locked.state = Campaign.State.STOPPED_ERROR
                locked.status_reason = (
                    "La búsqueda se detuvo por un problema con los datos. Revisá la campaña."
                )
                locked.finished_at = timezone.now()
                locked.save(update_fields=("state", "status_reason", "finished_at", "updated_at"))
            return locked
    if campaign.discovery_state not in {
        Campaign.DiscoveryState.TARGET_REACHED,
        Campaign.DiscoveryState.EXHAUSTED_QUERIES,
        Campaign.DiscoveryState.EXHAUSTED_RAW_LIMIT,
        Campaign.DiscoveryState.EXHAUSTED_COST,
    }:
        return None
    if Prospect.objects.filter(
        campaign=campaign,
        pipeline_state__in=(
            Prospect.PipelineState.DISCOVERED,
            Prospect.PipelineState.EMAIL_FOUND,
            Prospect.PipelineState.ENRICHED,
        ),
    ).exists():
        return None
    return move_campaign_to_approval(campaign.pk)


def _recheck_prepared_message(message: OutboundMessage) -> str:
    if message.campaign_enrollment is None:
        return "El destinatario no pertenece a la audiencia aprobada."
    result = enrollment_eligibility(message.campaign_enrollment)
    if not result.eligible:
        return result.message
    if message.email_address_id != message.campaign_enrollment.selected_email_id:
        return "El email elegido cambió después de preparar el mensaje."
    snapshot_outbound_attachments(message)
    campaign = message.campaign
    if campaign is None:
        return "El mensaje perdió la campaña que aprobó sus PDFs."
    expected_attachments = _attachment_rows(campaign)
    actual_attachments = [
        {
            "catalog_id": str(item.catalog_id),
            "position": item.position,
            "version": item.catalog_version,
            "sha256": item.sha256,
            "size": item.byte_size,
        }
        for item in message.attachments.order_by("position", "created_at")
    ]
    if actual_attachments != expected_attachments:
        raise ValidationError(
            "Los PDFs preparados ya no coinciden con la selección aprobada. "
            "Volvé a revisar la campaña."
        )
    return ""


def _validate_campaign_approval_snapshot(campaign: Campaign) -> None:
    if campaign.audience_hash != _canonical_hash(_audience_rows(campaign)):
        raise ValidationError("La audiencia cambió. Volvé a revisar la campaña.")
    if campaign.attachment_hash != _canonical_hash(_attachment_rows(campaign)):
        raise ValidationError("Los PDFs cambiaron. Volvé a revisar la campaña.")
    if campaign.schedule_hash != _canonical_hash(_schedule_payload(campaign)):
        raise ValidationError("El horario o el recordatorio cambiaron. Volvé a revisar la campaña.")
    frozen = freeze_message_content(
        subject=campaign.initial_subject_snapshot,
        body=campaign.initial_body_snapshot,
        signature=campaign.signature_snapshot,
    )
    if campaign.content_hash != frozen.content_hash:
        raise ValidationError("El mensaje o la firma cambiaron. Volvé a revisar la campaña.")


@transaction.atomic
def approve_campaign(campaign_id: uuid.UUID | str, *, actor: User) -> Campaign:
    campaign = (
        Campaign.objects.select_for_update()
        .select_related("workspace", "catalog")
        .get(pk=campaign_id)
    )
    require_user_capability(
        actor,
        Capability.APPROVE_CAMPAIGNS,
        workspace_id=campaign.workspace_id,
    )
    if campaign.state != Campaign.State.AWAITING_APPROVAL:
        raise ValidationError("La campaña todavía no está lista para aprobar.")
    if campaign.approval_mode != Campaign.ApprovalMode.CAMPAIGN:
        raise ValidationError("Esta campaña se aprueba mensaje por mensaje.")
    _verified_campaign_attachments(campaign)
    _validate_campaign_approval_snapshot(campaign)
    prepared = list(
        campaign.messages.select_for_update()
        .select_related("campaign_enrollment__selected_email")
        .filter(kind=OutboundMessage.Kind.INITIAL, state=OutboundMessage.State.PREPARED)
    )
    now = timezone.now()
    approved_count = 0
    for message in prepared:
        error = _recheck_prepared_message(message)
        if error:
            message.state = OutboundMessage.State.CANCELLED
            message.error = error
            message.save(update_fields=("state", "error", "updated_at"))
            enrollment = message.campaign_enrollment
            if enrollment is not None:
                enrollment.state = CampaignEnrollment.State.INELIGIBLE
                enrollment.exclusion_reason = error
                enrollment.save(update_fields=("state", "exclusion_reason", "updated_at"))
            continue
        message.approved_at = now
        message.approved_by = actor
        message.state = OutboundMessage.State.QUEUED
        message.next_attempt_at = now
        message.save(
            update_fields=(
                "approved_at",
                "approved_by",
                "state",
                "next_attempt_at",
                "updated_at",
            )
        )
        approved_count += 1
    if approved_count == 0:
        raise ValidationError("Ningún destinatario sigue habilitado para recibir la propuesta.")
    # The approval-time eligibility check may safely remove recipients.  Store
    # the exact subset that was authorized, not the earlier preview.
    refresh_campaign_hashes(campaign)
    campaign.approved_at = now
    campaign.approved_by = actor
    campaign.state = Campaign.State.RUNNING
    campaign.save(
        update_fields=(
            "audience_hash",
            "attachment_hash",
            "schedule_hash",
            "approved_at",
            "approved_by",
            "state",
            "updated_at",
        )
    )
    record_event(
        action="campaign.approved",
        entity=campaign,
        actor=actor,
        after={
            "audience_hash": campaign.audience_hash,
            "content_hash": campaign.content_hash,
            "attachment_hash": campaign.attachment_hash,
            "schedule_hash": campaign.schedule_hash,
            "approved_messages": approved_count,
        },
    )
    return campaign


@transaction.atomic
def start_per_message_campaign(campaign_id: uuid.UUID | str, *, actor: User) -> Campaign:
    """Freeze and queue only the individually approved audience."""

    campaign = (
        Campaign.objects.select_for_update()
        .select_related("workspace", "catalog")
        .get(pk=campaign_id)
    )
    require_user_capability(
        actor,
        Capability.APPROVE_CAMPAIGNS,
        workspace_id=campaign.workspace_id,
    )
    if campaign.state != Campaign.State.AWAITING_APPROVAL:
        raise ValidationError("La campaña todavía no está lista para iniciar los envíos.")
    if campaign.approval_mode != Campaign.ApprovalMode.PER_MESSAGE:
        raise ValidationError("Esta campaña se aprueba completa con una sola confirmación.")
    _verified_campaign_attachments(campaign)
    messages = list(
        campaign.messages.select_for_update()
        .select_related("campaign_enrollment__selected_email")
        .filter(kind=OutboundMessage.Kind.INITIAL)
        .order_by("created_at")
    )
    now = timezone.now()
    selected: list[OutboundMessage] = []
    for message in messages:
        enrollment = message.campaign_enrollment
        if message.approved_at is None or message.state != OutboundMessage.State.PREPARED:
            if message.state == OutboundMessage.State.REVIEW_READY:
                message.state = OutboundMessage.State.CANCELLED
                message.error = "No se incluyó al iniciar la campaña."
                message.save(update_fields=("state", "error", "updated_at"))
            if enrollment is not None:
                enrollment.state = CampaignEnrollment.State.CANCELLED
                enrollment.exclusion_reason = "No se aprobó el mensaje individual."
                enrollment.save(update_fields=("state", "exclusion_reason", "updated_at"))
            continue
        error = _recheck_prepared_message(message)
        if error:
            message.state = OutboundMessage.State.CANCELLED
            message.error = error
            message.save(update_fields=("state", "error", "updated_at"))
            if enrollment is not None:
                enrollment.state = CampaignEnrollment.State.INELIGIBLE
                enrollment.exclusion_reason = error
                enrollment.save(update_fields=("state", "exclusion_reason", "updated_at"))
            continue
        selected.append(message)
    if not selected:
        raise ValidationError("Aprobá al menos un mensaje antes de iniciar la entrega.")

    refresh_campaign_hashes(campaign)
    campaign.content_hash = _canonical_hash(
        [
            {
                "message_id": str(message.pk),
                "subject": message.subject,
                "body": message.body_text,
                "revision": message.content_revision,
            }
            for message in selected
        ]
    )
    for message in selected:
        message.state = OutboundMessage.State.QUEUED
        message.next_attempt_at = now
        message.save(update_fields=("state", "next_attempt_at", "updated_at"))
    campaign.approved_at = now
    campaign.approved_by = actor
    campaign.state = Campaign.State.RUNNING
    campaign.save(
        update_fields=(
            "audience_hash",
            "content_hash",
            "attachment_hash",
            "schedule_hash",
            "approved_at",
            "approved_by",
            "state",
            "updated_at",
        )
    )
    record_event(
        action="campaign.per_message_delivery_started",
        entity=campaign,
        actor=actor,
        after={
            "audience_hash": campaign.audience_hash,
            "content_hash": campaign.content_hash,
            "attachment_hash": campaign.attachment_hash,
            "schedule_hash": campaign.schedule_hash,
            "approved_messages": len(selected),
        },
    )
    return campaign
