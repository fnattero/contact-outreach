from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Workspace
from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.automation.context import (
    MandatoryContextOverflow,
    build_bounded_scheduled_contact_context,
)
from apps.automation.memory import refresh_contact_memory
from apps.automation.models import (
    AutomaticActionReservation,
    ContactCommunicationPlan,
    FollowUpTopic,
    HumanTask,
    ReplyAutomationConfiguration,
    ScheduledContactAttempt,
)
from apps.automation.services import open_human_task
from apps.campaigns.models import Campaign, OutboundMessage
from apps.compliance.models import SuppressionEntry
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import BusinessProfile, IntegrationConfiguration
from apps.contacts.models import CommunicationRestriction, Contact, Conversation, EmailAddress
from apps.integrations.contracts import LLMProvider, ProviderError, ScheduledContactDraftRequest
from apps.integrations.factory import get_llm_provider
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.models import GmailConnection


@dataclass(frozen=True, slots=True)
class ScheduledEligibility:
    eligible: bool
    code: str = ""
    message: str = ""


BUSINESS_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")


def _friendly_validation(error: ValidationError) -> str:
    return " ".join(error.messages) if error.messages else str(error)


def _message_id(idempotency_key: str) -> str:
    digest = sha256(idempotency_key.encode()).hexdigest()[:40]
    return f"<scheduled-{digest}@contact-outreach.local>"


def _content_hash(*, subject: str, body: str, signature: str) -> str:
    payload = json.dumps(
        {"subject": subject, "body": body, "signature": signature},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


def follow_up_topic_goal(topic: FollowUpTopic) -> str:
    parts = [topic.objective.strip()]
    if topic.instructions.strip():
        parts.append(f"Instrucciones del tema: {topic.instructions.strip()}")
    return "\n".join(part for part in parts if part)


def _next_due_for_approval(
    plan: ContactCommunicationPlan,
    *,
    fallback_from: datetime | None = None,
) -> datetime:
    topic = plan.topic
    candidates = []
    if topic.next_due_at is not None:
        candidates.append(topic.next_due_at)
    if plan.last_interaction_at is not None:
        candidates.append(plan.last_interaction_at + timedelta(days=topic.cadence_days))
    if plan.last_sent_at is not None:
        candidates.append(plan.last_sent_at + timedelta(days=topic.cadence_days))
    if plan.snoozed_until is not None:
        candidates.append(plan.snoozed_until)
    if candidates:
        return max(candidates)
    return (fallback_from or timezone.now()) + timedelta(days=topic.cadence_days)


def _reschedule_topic_approvals(
    topic: FollowUpTopic,
    *,
    actor: User,
    reason: str,
) -> None:
    plans = (
        topic.contact_approvals.select_for_update()
        .select_related("contact", "preferred_email", "topic")
        .filter(state=ContactCommunicationPlan.State.ACTIVE)
    )
    for plan in plans:
        _cancel_open_attempts(plan, reason=reason)
        plan.next_due_at = _next_due_for_approval(plan) if topic.active else None
        plan.updated_by = actor
        plan.save(update_fields=("next_due_at", "updated_by", "updated_at"))


@transaction.atomic
def save_follow_up_topic(
    *,
    actor: User,
    topic_id: uuid.UUID | str | None = None,
    name: str,
    objective: str,
    instructions: str,
    cadence_days: int,
    mode: str,
    next_due_at: datetime | None,
    active: bool,
) -> FollowUpTopic:
    membership = require_user_capability(actor, Capability.MANAGE_AUTOMATION)
    clean_name = name.strip()
    clean_objective = objective.strip()
    clean_instructions = instructions.strip()
    if cadence_days < 7:
        raise ValidationError("La periodicidad mínima es de siete días.")
    if mode not in FollowUpTopic.Mode.values:
        raise ValidationError("Elegí cómo se revisará el próximo mensaje.")
    if topic_id is None:
        topic = FollowUpTopic(
            workspace=membership.workspace,
            name=clean_name,
            objective=clean_objective,
            instructions=clean_instructions,
            cadence_days=cadence_days,
            mode=mode,
            next_due_at=next_due_at,
            active=active,
            created_by=actor,
            updated_by=actor,
        )
        created = True
        schedule_changed = False
    else:
        topic = FollowUpTopic.objects.select_for_update().get(
            pk=topic_id,
            workspace=membership.workspace,
        )
        schedule_changed = (
            topic.name != clean_name
            or topic.cadence_days != cadence_days
            or topic.mode != mode
            or topic.next_due_at != next_due_at
            or topic.active != active
            or topic.objective != clean_objective
            or topic.instructions != clean_instructions
        )
        topic.name = clean_name
        topic.objective = clean_objective
        topic.instructions = clean_instructions
        topic.cadence_days = cadence_days
        topic.mode = mode
        topic.next_due_at = next_due_at
        topic.active = active
        topic.updated_by = actor
        created = False
    topic.full_clean()
    topic.save()
    if schedule_changed:
        _reschedule_topic_approvals(
            topic,
            actor=actor,
            reason="El tema de seguimiento cambió antes del envío.",
        )
    record_event(
        action="scheduled_contact.topic_saved",
        entity=topic,
        actor=actor,
        after={
            "created": created,
            "active": topic.active,
            "mode": topic.mode,
            "cadence_days": topic.cadence_days,
            "next_due_at": topic.next_due_at.isoformat() if topic.next_due_at else None,
        },
    )
    return topic


def _active_restriction_exists(plan: ContactCommunicationPlan) -> bool:
    return (
        CommunicationRestriction.objects.filter(
            workspace_id=plan.contact.workspace_id,
            revoked_at__isnull=True,
        )
        .filter(Q(contact=plan.contact) | Q(email_address=plan.preferred_email))
        .exists()
    )


def scheduled_contact_eligibility(
    plan: ContactCommunicationPlan,
    *,
    at: datetime | None = None,
    for_send: bool = False,
) -> ScheduledEligibility:
    now = at or timezone.now()
    contact = plan.contact
    email = plan.preferred_email
    if plan.state != ContactCommunicationPlan.State.ACTIVE:
        return ScheduledEligibility(False, "PLAN_INACTIVE", "El seguimiento no está activo.")
    if not plan.topic.active:
        return ScheduledEligibility(
            False,
            "TOPIC_INACTIVE",
            "El tema de seguimiento no está activo.",
        )
    if plan.snoozed_until is not None and plan.snoozed_until > now:
        return ScheduledEligibility(
            False,
            "PLAN_SNOOZED",
            "El seguimiento está pospuesto hasta la fecha elegida.",
        )
    if contact.status != Contact.Status.ACTIVE:
        return ScheduledEligibility(
            False,
            "CONTACT_RESTRICTED",
            "El contacto está marcado como No contactar o pidió la baja.",
        )
    if contact.preferred_email_id != email.pk:
        return ScheduledEligibility(
            False,
            "PREFERRED_EMAIL_CHANGED",
            "El email preferido cambió. Revisá el seguimiento antes de continuar.",
        )
    if (
        email.organization_id != contact.organization_id
        or email.workspace_id != contact.workspace_id
        or email.validity != EmailAddress.Validity.VALID
        or bool(email.invalid_reason)
    ):
        return ScheduledEligibility(
            False,
            "INVALID_EMAIL",
            "El email preferido no está validado para enviar.",
        )
    if (
        _active_restriction_exists(plan)
        or SuppressionEntry.objects.filter(normalized_email=email.normalized_email).exists()
    ):
        return ScheduledEligibility(
            False,
            "RESTRICTED",
            "Este contacto o email tiene una restricción activa.",
        )
    if HumanTask.objects.filter(contact=contact, status=HumanTask.Status.OPEN).exists():
        return ScheduledEligibility(
            False,
            "OPEN_HUMAN_TASK",
            "Hay una conversación que necesita revisión antes del próximo contacto.",
        )
    if (
        contact.automation_suspended
        or contact.conversations.filter(automation_suspended=True).exists()
    ):
        return ScheduledEligibility(
            False,
            "AUTOMATION_SUSPENDED",
            "La automatización está pausada para este contacto.",
        )
    if not for_send:
        return ScheduledEligibility(True)
    if settings.RELATIONSHIP_KILL_SWITCH:
        return ScheduledEligibility(
            False,
            "RELATIONSHIP_KILL_SWITCH",
            "Los contactos programados están detenidos por el bloqueo de seguridad.",
        )
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        return ScheduledEligibility(
            False,
            "GLOBAL_SEND_BLOCKED",
            "El modo de envío o el bloqueo general impiden enviar este mensaje.",
        )
    connection = GmailConnection.objects.filter(workspace=contact.workspace).first()
    if connection is None or not connection.is_ready or set(connection.scopes) != set(GMAIL_SCOPES):
        return ScheduledEligibility(
            False,
            "GMAIL_NOT_READY",
            "Gmail no está conectado y probado.",
        )
    return ScheduledEligibility(True)


def _cancel_open_attempts(
    plan: ContactCommunicationPlan,
    *,
    reason: str,
    before: datetime | None = None,
) -> None:
    attempts = plan.attempts.select_for_update().filter(
        state__in=(
            ScheduledContactAttempt.State.DUE,
            ScheduledContactAttempt.State.DRAFT_REVIEW,
            ScheduledContactAttempt.State.AUTHORIZED,
            ScheduledContactAttempt.State.HUMAN_REQUIRED,
            ScheduledContactAttempt.State.INELIGIBLE,
        )
    )
    if before is not None:
        attempts = attempts.filter(due_at__lt=before)
    outbound_ids = list(
        attempts.exclude(outbound_message_id__isnull=True).values_list(
            "outbound_message_id", flat=True
        )
    )
    attempts.update(
        state=ScheduledContactAttempt.State.CANCELLED,
        reason=reason[:300],
        updated_at=timezone.now(),
    )
    OutboundMessage.objects.filter(
        pk__in=outbound_ids,
        state__in=(
            OutboundMessage.State.PREPARED,
            OutboundMessage.State.REVIEW_READY,
            OutboundMessage.State.QUEUED,
        ),
    ).update(
        state=OutboundMessage.State.CANCELLED,
        error=reason[:500],
        next_attempt_at=None,
        updated_at=timezone.now(),
    )


@transaction.atomic
def approve_contact_follow_up_topic(
    *,
    actor: User,
    contact_id: uuid.UUID | str,
    topic_id: uuid.UUID | str,
    enabled: bool = True,
) -> ContactCommunicationPlan:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    contact = (
        Contact.objects.select_for_update()
        .select_related("organization", "preferred_email")
        .get(pk=contact_id)
    )
    if contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    topic = FollowUpTopic.objects.select_for_update().get(
        pk=topic_id,
        workspace=membership.workspace,
    )
    if not topic.active and enabled:
        raise ValidationError("El tema de seguimiento está inactivo.")
    email = contact.preferred_email
    if email is None:
        raise ValidationError("Elegí un email preferido validado antes de aprobar temas.")
    if email.validity != EmailAddress.Validity.VALID or email.invalid_reason:
        raise ValidationError("El email preferido debe estar validado antes de aprobar temas.")
    state = (
        ContactCommunicationPlan.State.ACTIVE
        if enabled
        else ContactCommunicationPlan.State.DISABLED
    )
    plan, created = ContactCommunicationPlan.objects.select_for_update().get_or_create(
        contact=contact,
        topic=topic,
        defaults={
            "preferred_email": email,
            "state": state,
            "created_by": actor,
            "updated_by": actor,
        },
    )
    due_at = _next_due_for_approval(plan) if enabled else None
    if not created:
        schedule_changed = (
            plan.preferred_email_id != email.pk
            or plan.next_due_at != due_at
            or plan.state != state
            or plan.snoozed_until is not None
        )
        plan.preferred_email = email
        plan.state = state
        plan.next_due_at = due_at
        plan.snoozed_until = None
        plan.updated_by = actor
        plan.full_clean()
        plan.save()
        if schedule_changed:
            _cancel_open_attempts(
                plan,
                reason="La programación cambió antes del envío.",
            )
    else:
        plan.next_due_at = due_at
        plan.full_clean()
        plan.save(update_fields=("next_due_at", "updated_at"))
    if state == ContactCommunicationPlan.State.ACTIVE:
        eligibility = scheduled_contact_eligibility(plan)
        if not eligibility.eligible:
            raise ValidationError(eligibility.message)
    record_event(
        action="scheduled_contact.plan_saved",
        entity=plan,
        actor=actor,
        after={
            "state": plan.state,
            "mode": plan.topic.mode,
            "topic_id": str(plan.topic_id),
            "next_due_at": plan.next_due_at.isoformat() if plan.next_due_at else None,
        },
    )
    return plan


LEGACY_PURPOSE_GOALS: dict[str, str] = {
    "CHECK_IN": "Preguntar de manera cordial cómo están y si hay algo en lo que podamos ayudar.",
    "PRODUCT_FEEDBACK": (
        "Pedir una opinión general sobre el producto o la atención, sin asumir detalles."
    ),
}


@transaction.atomic
def save_contact_communication_plan(
    *,
    actor: User,
    contact_id: uuid.UUID | str,
    preferred_email_id: uuid.UUID | str,
    purpose: str,
    goal_text: str,
    cadence_days: int,
    mode: str,
    enabled: bool,
    next_due_at: datetime | None,
) -> ContactCommunicationPlan:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    contact = (
        Contact.objects.select_for_update()
        .select_related("organization", "preferred_email")
        .get(pk=contact_id)
    )
    if contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    email = EmailAddress.objects.select_for_update().get(
        pk=preferred_email_id,
        workspace=membership.workspace,
        organization=contact.organization,
    )
    if contact.preferred_email_id != email.pk:
        raise ValidationError("Elegí primero este email como preferido en la sección Emails.")
    if email.validity != EmailAddress.Validity.VALID or email.invalid_reason:
        raise ValidationError("El email preferido debe estar validado antes de programar envíos.")
    if cadence_days < 7:
        raise ValidationError("La frecuencia mínima es de siete días.")
    if mode not in FollowUpTopic.Mode.values:
        raise ValidationError("Elegí cómo se revisará el próximo mensaje.")
    clean_goal = goal_text.strip()
    if purpose == ContactCommunicationPlan.Purpose.ADMIN_GOAL and not clean_goal:
        raise ValidationError("Escribí el objetivo de este contacto.")
    existing_plan = (
        ContactCommunicationPlan.objects.select_for_update()
        .select_related("topic")
        .filter(contact=contact)
        .order_by("created_at")
        .first()
    )
    if existing_plan is not None:
        topic = existing_plan.topic
    else:
        base_name = dict(ContactCommunicationPlan.Purpose.choices).get(purpose, "Seguimiento")
        topic = FollowUpTopic(
            workspace=membership.workspace,
            name=f"{base_name} {str(contact.pk)[:8]}",
            created_by=actor,
            updated_by=actor,
        )
    topic.objective = clean_goal or LEGACY_PURPOSE_GOALS.get(purpose, "Retomar el contacto.")
    topic.instructions = ""
    topic.cadence_days = cadence_days
    topic.mode = mode
    topic.next_due_at = next_due_at
    topic.active = True
    topic.updated_by = actor
    topic.full_clean()
    topic.save()
    return approve_contact_follow_up_topic(
        actor=actor,
        contact_id=contact.pk,
        topic_id=topic.pk,
        enabled=enabled,
    )


@transaction.atomic
def set_contact_communication_plan_state(
    *,
    actor: User,
    plan_id: uuid.UUID | str,
    state: str,
) -> ContactCommunicationPlan:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    if state not in ContactCommunicationPlan.State.values:
        raise ValidationError("La acción solicitada no es válida.")
    plan = (
        ContactCommunicationPlan.objects.select_for_update()
        .select_related("contact__preferred_email", "preferred_email", "topic")
        .get(pk=plan_id)
    )
    if plan.contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    if state == ContactCommunicationPlan.State.ACTIVE:
        original_state = plan.state
        plan.state = ContactCommunicationPlan.State.ACTIVE
        if plan.contact.preferred_email is None:
            raise ValidationError("Elegí un email preferido validado antes de activar este tema.")
        plan.preferred_email = plan.contact.preferred_email
        if plan.next_due_at is None:
            plan.next_due_at = _next_due_for_approval(plan)
        eligibility = scheduled_contact_eligibility(plan)
        plan.state = original_state
        if not eligibility.eligible and eligibility.code not in {
            "PLAN_SNOOZED",
        }:
            raise ValidationError(eligibility.message)
        if plan.snoozed_until is not None and plan.snoozed_until <= timezone.now():
            plan.snoozed_until = None
    else:
        _cancel_open_attempts(
            plan,
            reason=(
                "El seguimiento se pausó antes del envío."
                if state == ContactCommunicationPlan.State.PAUSED
                else "El seguimiento se desactivó antes del envío."
            ),
        )
        if state == ContactCommunicationPlan.State.DISABLED:
            plan.next_due_at = None
    plan.state = state
    plan.updated_by = actor
    plan.save(
        update_fields=(
            "state",
            "preferred_email",
            "next_due_at",
            "snoozed_until",
            "updated_by",
            "updated_at",
        )
    )
    record_event(
        action="scheduled_contact.plan_state_changed",
        entity=plan,
        actor=actor,
        after={"state": state},
    )
    return plan


@transaction.atomic
def snooze_contact_communication_plan(
    *,
    actor: User,
    plan_id: uuid.UUID | str,
    until: datetime,
) -> ContactCommunicationPlan:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    plan = (
        ContactCommunicationPlan.objects.select_for_update()
        .select_related("contact", "topic")
        .get(pk=plan_id)
    )
    if plan.contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    if until <= timezone.now():
        raise ValidationError("Elegí una fecha futura para posponer el contacto.")
    _cancel_open_attempts(plan, reason="El seguimiento se pospuso antes del envío.")
    plan.snoozed_until = until
    plan.next_due_at = until
    plan.updated_by = actor
    plan.save(update_fields=("snoozed_until", "next_due_at", "updated_by", "updated_at"))
    record_event(
        action="scheduled_contact.plan_snoozed",
        entity=plan,
        actor=actor,
        after={"until": until.isoformat()},
    )
    return plan


@transaction.atomic
def record_genuine_contact_interaction(
    contact_id: uuid.UUID | str,
    *,
    interacted_at: datetime,
) -> tuple[ContactCommunicationPlan, ...]:
    plans = list(
        ContactCommunicationPlan.objects.select_for_update()
        .select_related("topic")
        .filter(contact_id=contact_id)
    )
    updated = []
    for plan in plans:
        minimum_due = interacted_at + timedelta(days=plan.topic.cadence_days)
        plan.last_interaction_at = max(
            (item for item in (plan.last_interaction_at, interacted_at) if item is not None),
        )
        candidates = [minimum_due]
        if plan.next_due_at is not None:
            candidates.append(plan.next_due_at)
        if plan.snoozed_until is not None:
            candidates.append(plan.snoozed_until)
        plan.next_due_at = max(candidates)
        _cancel_open_attempts(
            plan,
            reason="Hubo una interacción reciente; el próximo contacto se reprogramó.",
            before=plan.next_due_at,
        )
        plan.save(update_fields=("last_interaction_at", "next_due_at", "updated_at"))
        record_event(
            action="scheduled_contact.interaction_rescheduled",
            entity=plan,
            actor=None,
            after={"next_due_at": plan.next_due_at.isoformat()},
        )
        updated.append(plan)
    return tuple(updated)


def create_due_scheduled_attempts(*, at: datetime | None = None) -> tuple[uuid.UUID, ...]:
    now = at or timezone.now()
    candidates = list(
        ContactCommunicationPlan.objects.filter(
            state=ContactCommunicationPlan.State.ACTIVE,
            topic__active=True,
            next_due_at__isnull=False,
            next_due_at__lte=now,
        )
        .filter(Q(snoozed_until__isnull=True) | Q(snoozed_until__lte=now))
        .order_by("contact_id", "next_due_at", "topic__name")
        .values_list("pk", "contact_id")
    )
    plan_ids = []
    seen_contact_ids: set[uuid.UUID] = set()
    for plan_id, contact_id in candidates:
        if contact_id in seen_contact_ids:
            continue
        seen_contact_ids.add(contact_id)
        plan_ids.append(plan_id)
    attempt_ids: list[uuid.UUID] = []
    for plan_id in plan_ids:
        with transaction.atomic():
            plan = (
                ContactCommunicationPlan.objects.select_for_update()
                .select_related("topic")
                .get(pk=plan_id)
            )
            if (
                plan.state != ContactCommunicationPlan.State.ACTIVE
                or not plan.topic.active
                or plan.next_due_at is None
                or plan.next_due_at > now
                or (plan.snoozed_until is not None and plan.snoozed_until > now)
            ):
                continue
            due_at = plan.next_due_at
            digest = sha256(f"{plan.pk}:{due_at.isoformat()}".encode()).hexdigest()
            attempt, _ = ScheduledContactAttempt.objects.get_or_create(
                plan=plan,
                due_at=due_at,
                defaults={"idempotency_key": f"scheduled-contact:{digest}"},
            )
            if attempt.state not in {
                ScheduledContactAttempt.State.SENT,
                ScheduledContactAttempt.State.CANCELLED,
                ScheduledContactAttempt.State.DRAFT_REVIEW,
                ScheduledContactAttempt.State.AUTHORIZED,
                ScheduledContactAttempt.State.HUMAN_REQUIRED,
                ScheduledContactAttempt.State.INELIGIBLE,
            }:
                attempt_ids.append(attempt.pk)
    return tuple(attempt_ids)


def _mark_attempt(
    attempt: ScheduledContactAttempt,
    *,
    state: str,
    reason: str,
) -> ScheduledContactAttempt:
    attempt.state = state
    attempt.reason = reason[:300]
    attempt.save(update_fields=("state", "reason", "updated_at"))
    record_event(
        action="scheduled_contact.attempt_blocked",
        entity=attempt,
        actor=None,
        after={"state": state, "reason": reason[:300]},
    )
    return attempt


def _open_scheduled_task(
    attempt: ScheduledContactAttempt,
    *,
    reason: str,
    summary: str,
) -> bool:
    conversation = attempt.plan.contact.conversations.order_by("-last_message_at").first()
    open_human_task(
        workspace=attempt.plan.contact.workspace,
        contact_id=attempt.plan.contact_id,
        conversation_id=conversation.pk if conversation is not None else None,
        kind="SCHEDULED_CONTACT",
        reason=reason,
        friendly_summary=summary,
    )
    return True


def _automatic_mode_error(plan: ContactCommunicationPlan) -> str:
    if settings.AUTO_REPLY_KILL_SWITCH:
        return "La automatización está detenida por el bloqueo de seguridad."
    configuration = ReplyAutomationConfiguration.objects.filter(
        workspace=plan.contact.workspace
    ).first()
    if configuration is None or configuration.mode != ReplyAutomationConfiguration.Mode.LIVE:
        return "Las respuestas automáticas todavía no están en modo activo."
    return ""


def _provider_configuration(workspace: Workspace) -> tuple[str, str, str, int | None]:
    integration = IntegrationConfiguration.objects.filter(workspace=workspace).first()
    owner_id = integration.owner_id if integration is not None else None
    runtime = runtime_integration_configuration(owner_id)
    return (
        runtime.llm_provider,
        runtime.llm_model,
        runtime.llm_base_url(),
        owner_id,
    )


def _reserve_scheduled_automatic_capacity(
    attempt: ScheduledContactAttempt,
    *,
    at: datetime,
) -> bool:
    """Reserve the Workspace-day slot before authorizing an automatic effect."""

    existing = AutomaticActionReservation.objects.filter(scheduled_attempt=attempt).first()
    if existing is not None:
        return True
    Workspace.objects.select_for_update().get(pk=attempt.plan.contact.workspace_id)
    local_date = timezone.localdate(at, timezone=BUSINESS_TIMEZONE)
    reservations = AutomaticActionReservation.objects.select_for_update().filter(
        workspace_id=attempt.plan.contact.workspace_id,
        local_date=local_date,
    )
    used = sum(reservations.values_list("slots", flat=True))
    workspace_limit = int(settings.AUTOMATIC_REPLY_WORKSPACE_DAILY_LIMIT)
    if used >= workspace_limit:
        return False
    try:
        AutomaticActionReservation.objects.create(
            workspace_id=attempt.plan.contact.workspace_id,
            scheduled_attempt=attempt,
            reserved_at=at,
            local_date=local_date,
            slots=1,
        )
    except IntegrityError:
        return AutomaticActionReservation.objects.filter(scheduled_attempt=attempt).exists()
    return True


def process_scheduled_contact_attempt(
    attempt_id: uuid.UUID | str,
    *,
    provider: LLMProvider | None = None,
) -> ScheduledContactAttempt:
    attempt = ScheduledContactAttempt.objects.select_related(
        "plan__contact__workspace",
        "plan__contact__organization",
        "plan__contact__preferred_email",
        "plan__preferred_email",
        "plan__topic",
    ).get(pk=attempt_id)
    if attempt.state in {
        ScheduledContactAttempt.State.SENT,
        ScheduledContactAttempt.State.CANCELLED,
        ScheduledContactAttempt.State.DRAFT_REVIEW,
        ScheduledContactAttempt.State.AUTHORIZED,
        ScheduledContactAttempt.State.HUMAN_REQUIRED,
        ScheduledContactAttempt.State.INELIGIBLE,
    }:
        return attempt
    plan = attempt.plan
    if plan.next_due_at != attempt.due_at:
        with transaction.atomic():
            locked = ScheduledContactAttempt.objects.select_for_update().get(pk=attempt.pk)
            return _mark_attempt(
                locked,
                state=ScheduledContactAttempt.State.CANCELLED,
                reason="La fecha cambió antes de preparar el mensaje.",
            )
    eligibility = scheduled_contact_eligibility(plan)
    if not eligibility.eligible:
        with transaction.atomic():
            locked = ScheduledContactAttempt.objects.select_for_update().get(pk=attempt.pk)
            return _mark_attempt(
                locked,
                state=ScheduledContactAttempt.State.INELIGIBLE,
                reason=eligibility.message,
            )
    if plan.topic.mode == FollowUpTopic.Mode.AUTOMATIC:
        send_eligibility = scheduled_contact_eligibility(plan, for_send=True)
        if not send_eligibility.eligible:
            with transaction.atomic():
                locked = ScheduledContactAttempt.objects.select_for_update().get(pk=attempt.pk)
                return _mark_attempt(
                    locked,
                    state=ScheduledContactAttempt.State.INELIGIBLE,
                    reason=send_eligibility.message,
                )
        automatic_error = _automatic_mode_error(plan)
        if automatic_error:
            with transaction.atomic():
                locked = (
                    ScheduledContactAttempt.objects.select_for_update()
                    .select_related("plan__contact__workspace", "plan__topic")
                    .get(pk=attempt.pk)
                )
                opened = _open_scheduled_task(
                    locked,
                    reason="AUTOMATIC_MODE_NOT_AVAILABLE",
                    summary=automatic_error,
                )
                return _mark_attempt(
                    locked,
                    state=(
                        ScheduledContactAttempt.State.HUMAN_REQUIRED
                        if opened
                        else ScheduledContactAttempt.State.INELIGIBLE
                    ),
                    reason=automatic_error,
                )

    provider_name, model_name, base_url, owner_id = _provider_configuration(plan.contact.workspace)
    try:
        goal = follow_up_topic_goal(plan.topic)
        refresh_contact_memory(plan.contact_id)
        context = build_bounded_scheduled_contact_context(plan, goal=goal)
        selected_provider = provider or get_llm_provider(
            provider_name,
            base_url=base_url,
            model=model_name,
            owner_id=owner_id,
        )
        request = ScheduledContactDraftRequest(
            context=context.blocks,
            facts=context.facts,
            purpose=plan.topic.name,
            goal=goal,
            correlation_id=secrets.token_hex(16),
            idempotency_key=f"{attempt.idempotency_key}:llm",
        )
        result = selected_provider.draft_scheduled_contact(request)
    except (MandatoryContextOverflow, ProviderError, ValueError, ValidationError):
        with transaction.atomic():
            locked = (
                ScheduledContactAttempt.objects.select_for_update()
                .select_related("plan__contact__workspace", "plan__topic")
                .get(pk=attempt.pk)
            )
            summary = "No pudimos preparar un mensaje seguro. Revisá el objetivo y la conversación."
            opened = _open_scheduled_task(
                locked,
                reason="SCHEDULED_CONTEXT_OR_PROVIDER_FAILURE",
                summary=summary,
            )
            return _mark_attempt(
                locked,
                state=(
                    ScheduledContactAttempt.State.HUMAN_REQUIRED
                    if opened
                    else ScheduledContactAttempt.State.INELIGIBLE
                ),
                reason=summary,
            )

    with transaction.atomic():
        locked = (
            ScheduledContactAttempt.objects.select_for_update()
            .select_related(
                "plan__contact__workspace",
                "plan__contact__organization",
                "plan__contact__preferred_email",
                "plan__preferred_email",
                "plan__topic",
            )
            .get(pk=attempt.pk)
        )
        if locked.outbound_message_id is not None:
            return locked
        plan = locked.plan
        if plan.next_due_at != locked.due_at:
            return _mark_attempt(
                locked,
                state=ScheduledContactAttempt.State.CANCELLED,
                reason="La fecha cambió mientras se preparaba el mensaje.",
            )
        eligibility = scheduled_contact_eligibility(plan)
        if not eligibility.eligible:
            return _mark_attempt(
                locked,
                state=ScheduledContactAttempt.State.INELIGIBLE,
                reason=eligibility.message,
            )
        locked.provider = provider_name
        locked.model = model_name
        locked.context_manifest = context.manifest
        locked.context_hash = context.context_hash
        locked.fact_revision_ids = list(result.fact_revision_ids)
        if result.status == "HUMAN":
            summary = "El objetivo necesita que una persona prepare el próximo mensaje."
            opened = _open_scheduled_task(
                locked,
                reason=result.human_reason or "INSUFFICIENT_CONTEXT",
                summary=summary,
            )
            locked.state = (
                ScheduledContactAttempt.State.HUMAN_REQUIRED
                if opened
                else ScheduledContactAttempt.State.INELIGIBLE
            )
            locked.reason = summary
            locked.save(
                update_fields=(
                    "provider",
                    "model",
                    "context_manifest",
                    "context_hash",
                    "fact_revision_ids",
                    "state",
                    "reason",
                    "updated_at",
                )
            )
            return locked
        if plan.topic.mode == FollowUpTopic.Mode.AUTOMATIC:
            now = timezone.now()
            send_eligibility = scheduled_contact_eligibility(plan, at=now, for_send=True)
            if not send_eligibility.eligible:
                return _mark_attempt(
                    locked,
                    state=ScheduledContactAttempt.State.INELIGIBLE,
                    reason=send_eligibility.message,
                )
            if not _reserve_scheduled_automatic_capacity(locked, at=now):
                summary = (
                    "Se alcanzó el límite diario de mensajes automáticos. Revisá este contacto."
                )
                opened = _open_scheduled_task(
                    locked,
                    reason="AUTOMATIC_DAILY_LIMIT",
                    summary=summary,
                )
                return _mark_attempt(
                    locked,
                    state=(
                        ScheduledContactAttempt.State.HUMAN_REQUIRED
                        if opened
                        else ScheduledContactAttempt.State.INELIGIBLE
                    ),
                    reason=summary,
                )
        subject = (result.subject or "").strip()
        body = (result.body_text or "").strip()
        if not subject or not body:
            return _mark_attempt(
                locked,
                state=ScheduledContactAttempt.State.INELIGIBLE,
                reason="El borrador quedó incompleto y no se puede enviar.",
            )
        profile = BusinessProfile.objects.filter(workspace=plan.contact.workspace).first()
        signature = profile.signature.strip() if profile is not None else ""
        key = f"{locked.idempotency_key}:outbound"
        outbound_state = (
            OutboundMessage.State.REVIEW_READY
            if plan.topic.mode == FollowUpTopic.Mode.REVIEW_BEFORE_SEND
            else OutboundMessage.State.QUEUED
        )
        try:
            outbound = OutboundMessage.objects.create(
                kind=OutboundMessage.Kind.SCHEDULED_CONTACT,
                campaign=None,
                organization=plan.contact.organization,
                contact=plan.contact,
                conversation=None,
                email_address=plan.preferred_email,
                recipient=plan.preferred_email.original_email,
                recipient_normalized=plan.preferred_email.normalized_email,
                subject=subject,
                body_text=body,
                signature_snapshot=signature,
                content_hash=_content_hash(subject=subject, body=body, signature=signature),
                state=outbound_state,
                delivery_mode=Campaign.DeliveryMode.LIVE,
                idempotency_key=key,
                semantic_action_key=key,
                message_id=_message_id(key),
                scheduled_for=locked.due_at,
                next_attempt_at=(
                    timezone.now() if outbound_state == OutboundMessage.State.QUEUED else None
                ),
            )
        except IntegrityError:
            outbound = OutboundMessage.objects.get(idempotency_key=key)
        locked.outbound_message = outbound
        locked.state = (
            ScheduledContactAttempt.State.DRAFT_REVIEW
            if outbound.state == OutboundMessage.State.REVIEW_READY
            else ScheduledContactAttempt.State.AUTHORIZED
        )
        if locked.state == ScheduledContactAttempt.State.AUTHORIZED:
            locked.authorized_at = timezone.now()
        locked.reason = ""
        locked.save(
            update_fields=(
                "outbound_message",
                "provider",
                "model",
                "context_manifest",
                "context_hash",
                "fact_revision_ids",
                "state",
                "authorized_at",
                "reason",
                "updated_at",
            )
        )
        record_event(
            action=(
                "scheduled_contact.draft_created"
                if locked.state == ScheduledContactAttempt.State.DRAFT_REVIEW
                else "scheduled_contact.automatic_authorized"
            ),
            entity=locked,
            actor=None,
            after={"state": locked.state, "outbound_message_id": str(outbound.pk)},
        )
        return locked


@transaction.atomic
def edit_scheduled_contact_draft(
    *,
    actor: User,
    attempt_id: uuid.UUID | str,
    subject: str,
    body_text: str,
) -> ScheduledContactAttempt:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    attempt = (
        ScheduledContactAttempt.objects.select_for_update()
        .select_related("plan__contact", "plan__topic", "outbound_message")
        .get(pk=attempt_id)
    )
    if attempt.plan.contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    outbound = attempt.outbound_message
    if (
        attempt.state != ScheduledContactAttempt.State.DRAFT_REVIEW
        or outbound is None
        or outbound.state != OutboundMessage.State.REVIEW_READY
    ):
        raise ValidationError("Este borrador ya no se puede editar.")
    clean_subject = subject.strip()
    clean_body = body_text.strip()
    if not clean_subject or not clean_body:
        raise ValidationError("Completá el asunto y el mensaje antes de guardar.")
    if len(clean_subject) > 255 or len(clean_body) > 10_000:
        raise ValidationError("El asunto o el mensaje son demasiado extensos.")
    outbound.subject = clean_subject
    outbound.body_text = clean_body
    outbound.content_revision += 1
    outbound.last_edited_at = timezone.now()
    outbound.last_edited_by = actor
    outbound.content_hash = _content_hash(
        subject=clean_subject,
        body=clean_body,
        signature=outbound.signature_snapshot,
    )
    outbound.save(
        update_fields=(
            "subject",
            "body_text",
            "content_revision",
            "last_edited_at",
            "last_edited_by",
            "content_hash",
            "updated_at",
        )
    )
    record_event(
        action="scheduled_contact.draft_edited",
        entity=attempt,
        actor=actor,
        after={"content_revision": outbound.content_revision},
    )
    return attempt


@transaction.atomic
def authorize_scheduled_contact_attempt(
    *,
    actor: User,
    attempt_id: uuid.UUID | str,
) -> ScheduledContactAttempt:
    membership = require_user_capability(actor, Capability.MANAGE_CONTACTS)
    attempt = (
        ScheduledContactAttempt.objects.select_for_update()
        .select_related(
            "plan__contact__workspace",
            "plan__contact__preferred_email",
            "plan__preferred_email",
            "plan__topic",
            "outbound_message",
        )
        .get(pk=attempt_id)
    )
    if attempt.plan.contact.workspace_id != membership.workspace_id:
        raise PermissionDenied
    outbound = attempt.outbound_message
    if (
        attempt.state != ScheduledContactAttempt.State.DRAFT_REVIEW
        or outbound is None
        or outbound.state != OutboundMessage.State.REVIEW_READY
    ):
        raise ValidationError("Este borrador ya fue autorizado o dejó de estar disponible.")
    eligibility = scheduled_contact_eligibility(attempt.plan, for_send=True)
    if not eligibility.eligible:
        raise ValidationError(eligibility.message)
    now = timezone.now()
    outbound.state = OutboundMessage.State.QUEUED
    outbound.approved_at = now
    outbound.approved_by = actor
    outbound.sent_by = actor
    outbound.next_attempt_at = now
    outbound.error = ""
    outbound.save(
        update_fields=(
            "state",
            "approved_at",
            "approved_by",
            "sent_by",
            "next_attempt_at",
            "error",
            "updated_at",
        )
    )
    attempt.state = ScheduledContactAttempt.State.AUTHORIZED
    attempt.authorized_at = now
    attempt.reason = ""
    attempt.save(update_fields=("state", "authorized_at", "reason", "updated_at"))
    record_event(
        action="scheduled_contact.draft_authorized",
        entity=attempt,
        actor=actor,
        after={"outbound_message_id": str(outbound.pk)},
    )
    return attempt


@transaction.atomic
def complete_scheduled_contact_attempt(
    message_id: uuid.UUID | str,
) -> ScheduledContactAttempt | None:
    attempt = (
        ScheduledContactAttempt.objects.select_for_update()
        .select_related("plan__contact__workspace", "plan__topic", "outbound_message")
        .filter(outbound_message_id=uuid.UUID(str(message_id)))
        .first()
    )
    if attempt is None:
        return None
    outbound = attempt.outbound_message
    assert outbound is not None
    if outbound.state == OutboundMessage.State.SENT:
        sent_at = outbound.sent_at or timezone.now()
        connection = GmailConnection.objects.filter(
            workspace=attempt.plan.contact.workspace
        ).first()
        if connection is not None and outbound.gmail_thread_id:
            conversation, _ = Conversation.objects.get_or_create(
                connection=connection,
                gmail_thread_id=outbound.gmail_thread_id,
                defaults={
                    "workspace": attempt.plan.contact.workspace,
                    "contact": attempt.plan.contact,
                    "subject": outbound.subject,
                    "last_message_at": sent_at,
                },
            )
            if conversation.contact_id == attempt.plan.contact_id:
                if conversation.last_message_at is None or sent_at > conversation.last_message_at:
                    conversation.last_message_at = sent_at
                    conversation.save(update_fields=("last_message_at", "updated_at"))
                if outbound.conversation_id != conversation.pk:
                    outbound.conversation = conversation
                    outbound.save(update_fields=("conversation", "updated_at"))
        attempt.state = ScheduledContactAttempt.State.SENT
        attempt.sent_at = sent_at
        attempt.reason = ""
        attempt.save(update_fields=("state", "sent_at", "reason", "updated_at"))
        plan = ContactCommunicationPlan.objects.select_for_update().get(pk=attempt.plan_id)
        plan.last_sent_at = sent_at
        plan.next_due_at = max(
            sent_at + timedelta(days=plan.topic.cadence_days),
            plan.topic.next_due_at or sent_at,
        )
        plan.snoozed_until = None
        plan.save(update_fields=("last_sent_at", "next_due_at", "snoozed_until", "updated_at"))
        record_event(
            action="scheduled_contact.sent",
            entity=attempt,
            actor=outbound.sent_by if isinstance(outbound.sent_by, User) else None,
            after={"next_due_at": plan.next_due_at.isoformat()},
        )
    elif outbound.state in {
        OutboundMessage.State.SEND_FAILED,
        OutboundMessage.State.INELIGIBLE,
        OutboundMessage.State.CANCELLED,
    }:
        summary = outbound.error or "El contacto programado no se pudo enviar."
        opened = _open_scheduled_task(
            attempt,
            reason="SCHEDULED_DELIVERY_FAILED",
            summary="El contacto programado no se pudo enviar. Revisá Gmail y el destinatario.",
        )
        attempt.state = (
            ScheduledContactAttempt.State.HUMAN_REQUIRED
            if opened
            else ScheduledContactAttempt.State.INELIGIBLE
        )
        attempt.reason = summary[:300]
        attempt.save(update_fields=("state", "reason", "updated_at"))
    return attempt


def scheduled_outbound_ids_for_completion() -> tuple[uuid.UUID, ...]:
    return tuple(
        ScheduledContactAttempt.objects.filter(
            state=ScheduledContactAttempt.State.AUTHORIZED,
            outbound_message__state__in=(
                OutboundMessage.State.SENT,
                OutboundMessage.State.SEND_FAILED,
                OutboundMessage.State.INELIGIBLE,
                OutboundMessage.State.CANCELLED,
            ),
        ).values_list("outbound_message_id", flat=True)
    )
