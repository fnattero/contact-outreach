from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.utils import parseaddr
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from apps.accounts.models import Workspace
from apps.audit.services import record_event
from apps.automation.candidates import (
    extract_literal_candidates,
    extract_mailto_literals,
    split_inbound_regions,
)
from apps.automation.context import build_bounded_reply_context
from apps.automation.memory import verified_conversation_memory_text
from apps.automation.models import (
    AutomaticActionReservation,
    ContactCommunicationPlan,
    ConversationMemory,
    EmailCandidate,
    HumanTask,
    ReplyAutomationConfiguration,
    ReplyDecision,
    ScheduledContactAttempt,
)
from apps.campaigns.content import REDIRECT_ACK_BODY, freeze_message_content
from apps.campaigns.models import Campaign, OutboundAttachment, OutboundMessage
from apps.catalogs.services import verify_catalog
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import lock_email_eligibility, normalize_email
from apps.configuration.integrations import redact_provider_error
from apps.contacts.models import CommunicationRestriction, Contact, Conversation, EmailAddress
from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
    GmailProvider,
    GmailReplyRequest,
    GmailSendRequest,
    GmailSendResult,
    PermanentProviderError,
    ProviderError,
    RetryableProviderError,
    ValidationProviderError,
)
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.mime import (
    PdfAttachment,
    build_message,
    build_reply_message,
    deterministic_message_id,
)
from apps.mailbox.models import GmailConnection, InboundMessage
from apps.mailbox.services import provider_for_connection
from apps.mailbox.sync import normalize_message_id, normalize_references
from apps.prospects.email_validation import DNSMXResolver, MXResolver, MXStatus

MIN_AUTOMATIC_CONFIDENCE = 0.90
RECONCILE_AFTER = timedelta(minutes=1)
STALE_SENDING_AFTER = timedelta(minutes=2)
BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")

AUTOMATIC_KINDS = frozenset(
    {
        OutboundMessage.Kind.AUTOMATIC_REPLY,
        OutboundMessage.Kind.REFERRED_PROPOSAL,
        OutboundMessage.Kind.REDIRECT_ACK,
    }
)
NEW_THREAD_KINDS = frozenset(
    {
        OutboundMessage.Kind.REFERRED_PROPOSAL,
        OutboundMessage.Kind.SCHEDULED_CONTACT,
    }
)
REPLY_KINDS = frozenset(
    {
        OutboundMessage.Kind.AUTOMATIC_REPLY,
        OutboundMessage.Kind.REDIRECT_ACK,
    }
)
SAFE_REPLY_INTENTS = frozenset(
    {
        "APPROVED_PRODUCT_INFORMATION",
        "APPROVED_COMPANY_FACT",
        "GROUNDED_SIMPLE_CLARIFICATION",
    }
)
ACTIVE_DELIVERY_STATES = frozenset(
    {
        OutboundMessage.State.QUEUED,
        OutboundMessage.State.SENDING,
        OutboundMessage.State.RECONCILING,
        OutboundMessage.State.SENT,
    }
)


@dataclass(frozen=True, slots=True)
class OutboundEffect:
    message_id: uuid.UUID
    connection_id: uuid.UUID
    raw_message: bytes
    rfc_message_id: str
    recipient: str
    gmail_thread_id: str
    idempotency_key: str
    reply: bool


def _headers_lower(inbound: InboundMessage) -> dict[str, str]:
    return {str(key).casefold(): str(value).strip() for key, value in inbound.headers.items()}


def _automatic_header_error(inbound: InboundMessage) -> str:
    headers = _headers_lower(inbound)
    auto_submitted = headers.get("auto-submitted", "").casefold()
    precedence = headers.get("precedence", "").casefold()
    if auto_submitted and auto_submitted != "no":
        return "El correo indica que fue generado automáticamente."
    if precedence in {"auto_reply", "bulk", "junk", "list"}:
        return "El correo tiene encabezados de envío automático o masivo."
    if any(
        headers.get(name)
        for name in (
            "x-autoreply",
            "x-auto-response-suppress",
            "list-id",
            "list-unsubscribe",
            "feedback-type",
        )
    ):
        return "El correo tiene encabezados que no permiten una respuesta automática segura."
    return ""


def _manifest_block_text(block: dict[str, Any]) -> str | None:
    source_id = block.get("source_id")
    provenance = str(block.get("provenance", ""))
    if not source_id:
        return None
    if provenance == "SOURCE_LINKED_MEMORY":
        memory = ConversationMemory.objects.filter(pk=source_id, superseded_at__isnull=True).first()
        if memory is None:
            return None
        return verified_conversation_memory_text(memory)
    if str(block.get("role", "")) == "CLIENT":
        inbound = InboundMessage.objects.filter(pk=source_id).first()
        if inbound is None:
            return None
        return split_inbound_regions(inbound.body_text).new_content or inbound.body_text
    outbound = OutboundMessage.objects.filter(pk=source_id).first()
    return outbound.body_text if outbound is not None else None


def _manifest_integrity_error(decision: ReplyDecision) -> str:
    manifest = decision.context_manifest
    if not isinstance(manifest, dict):
        return "El contexto guardado no tiene un formato válido."
    blocks = manifest.get("blocks")
    facts = manifest.get("facts")
    if not isinstance(blocks, list) or not isinstance(facts, list):
        return "El contexto guardado está incompleto."
    for item in blocks:
        if not isinstance(item, dict):
            return "El contexto guardado está incompleto."
        text = _manifest_block_text(item)
        if text is None or len(text) != item.get("characters"):
            return "Cambió un mensaje usado para preparar la respuesta."
        if sha256(text.encode()).hexdigest() != item.get("sha256"):
            return "Cambió un mensaje usado para preparar la respuesta."
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    if sha256(canonical.encode()).hexdigest() != decision.context_hash:
        return "El contexto guardado no coincide con su verificación de integridad."
    return ""


def _selected_facts_error(decision: ReplyDecision) -> str:
    fact_rows = decision.context_manifest.get("facts", [])
    manifest: dict[str, dict[str, Any]] = {
        str(item.get("revision_id")): item for item in fact_rows if isinstance(item, dict)
    }
    selected = tuple(decision.selected_facts.select_related("fact"))
    if decision.action == "REPLY" and decision.intent in SAFE_REPLY_INTENTS and not selected:
        return "La respuesta no cita información aprobada."
    for revision in selected:
        expected = manifest.get(str(revision.pk))
        if expected is None:
            return "La respuesta cita información que no estaba en su contexto autorizado."
        if revision.fact.workspace_id != decision.workspace_id:
            return "La información citada pertenece a otro espacio de trabajo."
        if (
            not revision.fact.active
            or revision.approved_at is None
            or revision.superseded_at is not None
            or revision.version != expected.get("version")
            or sha256(revision.text.encode()).hexdigest() != expected.get("sha256")
        ):
            return "Una información usada dejó de estar aprobada o cambió."
    return ""


def _grounded_reply_body(decision: ReplyDecision) -> str:
    """Render LIVE copy from approved fact text only; LLM prose remains review evidence."""

    selected = {str(revision.pk): revision for revision in decision.selected_facts.all()}
    ordered_ids = [
        str(item.get("revision_id"))
        for item in decision.context_manifest.get("facts", [])
        if isinstance(item, dict)
    ]
    texts = [
        selected[revision_id].text.strip() for revision_id in ordered_ids if revision_id in selected
    ]
    return "\n\n".join(text for text in texts if text)


def _new_context_since_decision(
    decision: ReplyDecision,
    *,
    allowed_outbound_ids: tuple[uuid.UUID, ...] = (),
) -> bool:
    if (
        InboundMessage.objects.filter(
            contact_id=decision.contact_id,
            created_at__gt=decision.created_at,
        )
        .exclude(pk=decision.inbound_id)
        .exists()
    ):
        return True
    outbound = OutboundMessage.objects.filter(
        contact_id=decision.contact_id,
        created_at__gt=decision.created_at,
    )
    if allowed_outbound_ids:
        outbound = outbound.exclude(pk__in=allowed_outbound_ids)
    return outbound.exists()


def _live_qualification_error(decision: ReplyDecision) -> str:
    from apps.automation.services import qualification_snapshot

    configuration = ReplyAutomationConfiguration.objects.filter(
        workspace_id=decision.workspace_id
    ).first()
    if (
        decision.mode != ReplyAutomationConfiguration.Mode.LIVE
        or configuration is None
        or configuration.mode != ReplyAutomationConfiguration.Mode.LIVE
        or configuration.live_enabled_at is None
        or configuration.live_enabled_by_id is None
    ):
        return "Las respuestas automáticas no están habilitadas en modo activo."
    if configuration.policy_version != decision.policy_version:
        return "La política cambió desde que se preparó la respuesta."
    if not qualification_snapshot(decision.workspace).qualified:
        return "El modo activo ya no cumple la evaluación mínima de seguridad."
    return ""


def _decision_policy_error(
    decision: ReplyDecision,
    *,
    check_current_context: bool,
    allowed_outbound_ids: tuple[uuid.UUID, ...] = (),
) -> str:
    inbound = decision.inbound
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        return "El envío en vivo está desactivado por la configuración general."
    if settings.AUTO_REPLY_KILL_SWITCH:
        return "El bloqueo independiente de respuestas automáticas está activado."
    if error := _live_qualification_error(decision):
        return error
    if not inbound.is_human or inbound.classification in {
        InboundMessage.Classification.AUTO_REPLY,
        InboundMessage.Classification.BOUNCE,
        InboundMessage.Classification.UNSUBSCRIBE,
    }:
        return "El mensaje ya no está clasificado como una respuesta humana apta."
    if error := _automatic_header_error(inbound):
        return error
    if float(decision.confidence) < MIN_AUTOMATIC_CONFIDENCE:
        return "La confianza de la decisión quedó por debajo del mínimo seguro."
    if decision.action == "REPLY":
        if decision.intent not in SAFE_REPLY_INTENTS or not decision.proposed_body.strip():
            return "La respuesta propuesta no coincide con una acción permitida."
    elif decision.action == "REDIRECT_PROPOSAL":
        if decision.intent != "EXPLICIT_PROPOSAL_REDIRECTION" or decision.candidate_id is None:
            return "La redirección no tiene un único email autorizado."
    else:
        return "La acción propuesta no está permitida para envío automático."
    if error := _selected_facts_error(decision):
        return error
    if error := _manifest_integrity_error(decision):
        return error
    if check_current_context:
        try:
            current = build_bounded_reply_context(
                inbound,
                policy_version=decision.policy_version,
            )
        except ValidationError:
            return "El contexto actual ya no cabe en el límite seguro."
        if current.context_hash != decision.context_hash:
            return "La conversación cambió desde que se preparó la respuesta."
    elif _new_context_since_decision(
        decision,
        allowed_outbound_ids=allowed_outbound_ids,
    ):
        return "La conversación cambió antes de completar el envío."
    return ""


def _contact_or_channel_error(
    *,
    contact: Contact,
    email: EmailAddress,
    normalized_email: str,
) -> str:
    if contact.status != Contact.Status.ACTIVE:
        return "El contacto no admite comunicaciones automáticas."
    if contact.automation_suspended:
        return "La automatización del contacto está suspendida."
    if (
        email.workspace_id != contact.workspace_id
        or email.organization_id != contact.organization_id
    ):
        return "El email ya no pertenece a este contacto."
    if email.normalized_email != normalized_email:
        return "El destinatario ya no coincide con el email validado."
    if email.validity != EmailAddress.Validity.VALID or email.invalid_reason:
        return "El email ya no está validado para enviar."
    restrictions = CommunicationRestriction.objects.filter(
        workspace_id=contact.workspace_id,
        revoked_at__isnull=True,
    ).filter(Q(contact_id=contact.pk) | Q(email_address_id=email.pk))
    if restrictions.exists():
        return "El contacto o el email tiene una restricción activa."
    if SuppressionEntry.objects.filter(normalized_email=normalized_email).exists():
        return "El email está en la lista de direcciones que no se deben contactar."
    return ""


def _reply_email(decision: ReplyDecision) -> tuple[EmailAddress | None, str]:
    literal = parseaddr(decision.inbound.sender)[1]
    if not literal:
        return None, ""
    try:
        normalized = normalize_email(literal)
    except ValidationError:
        return None, ""
    email = EmailAddress.objects.filter(
        workspace_id=decision.workspace_id,
        organization_id=decision.contact.organization_id,
        normalized_email=normalized,
    ).first()
    return email, normalized


def _conversation_error(decision: ReplyDecision) -> str:
    conversation = decision.conversation
    if conversation.automation_suspended or decision.contact.automation_suspended:
        return "La conversación está en pausa para revisión humana."
    if HumanTask.objects.filter(
        Q(conversation_id=conversation.pk) | Q(contact_id=decision.contact_id),
        status=HumanTask.Status.OPEN,
    ).exists():
        return "Hay una revisión humana pendiente para este contacto."
    if (
        conversation.contact_id != decision.contact_id
        or conversation.workspace_id != decision.workspace_id
        or conversation.connection_id != decision.inbound.connection_id
    ):
        return "La conversación ya no coincide con el mensaje recibido."
    return ""


def _manual_reply_conflict_error(decision: ReplyDecision) -> str:
    if OutboundMessage.objects.filter(
        parent_inbound_id=decision.inbound_id,
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        state__in=ACTIVE_DELIVERY_STATES,
    ).exists():
        return "Esta respuesta ya tiene una contestación manual autorizada o enviada."
    return ""


def _gmail_connection_error(connection: GmailConnection | None) -> str:
    if connection is None or not connection.is_ready:
        return "Gmail no está conectado y probado."
    if set(connection.scopes) != set(GMAIL_SCOPES):
        return "La conexión Gmail no tiene exactamente los permisos esperados."
    return ""


def _open_decision_task(decision: ReplyDecision, *, reason: str, summary: str) -> None:
    from apps.automation.services import open_human_task

    open_human_task(
        workspace=decision.workspace,
        contact_id=decision.contact_id,
        conversation_id=decision.conversation_id,
        inbound_id=decision.inbound_id,
        decision_id=decision.pk,
        kind="REPLY_REVIEW",
        reason=reason,
        friendly_summary=summary,
    )


def _reject_decision_locked(
    decision: ReplyDecision,
    *,
    reason: str,
    summary: str,
    message: OutboundMessage | None = None,
    failed: bool = False,
) -> str:
    decision.state = (
        ReplyDecision.State.HUMAN_REQUIRED if failed else ReplyDecision.State.REJECTED_POLICY
    )
    decision.human_reason = reason
    decision.error = summary[:500]
    decision.save(update_fields=("state", "human_reason", "error", "updated_at"))
    if message is not None and message.state != OutboundMessage.State.SENT:
        message.state = OutboundMessage.State.SEND_FAILED
        message.next_attempt_at = None
        message.error = summary[:500]
        message.save(update_fields=("state", "next_attempt_at", "error", "updated_at"))
    _open_decision_task(decision, reason=reason, summary=summary)
    record_event(
        action="automation.reply_rejected" if not failed else "automation.reply_needs_attention",
        entity=decision,
        actor=None,
        after={"reason": reason, "state": decision.state},
    )
    return decision.state


def _reserve_rate_capacity(
    decision: ReplyDecision,
    *,
    slots: int,
    now: datetime,
) -> str:
    existing = AutomaticActionReservation.objects.filter(decision=decision).first()
    if existing is not None:
        return "" if existing.slots == slots else "La reserva automática guardada es inconsistente."
    Workspace.objects.select_for_update().get(pk=decision.workspace_id)
    Conversation.objects.select_for_update().get(pk=decision.conversation_id)
    rolling_start = now - timedelta(hours=24)
    conversation_slots = (
        AutomaticActionReservation.objects.filter(
            conversation_id=decision.conversation_id,
            reserved_at__gte=rolling_start,
        ).aggregate(total=Sum("slots"))["total"]
        or 0
    )
    local_date = now.astimezone(BUENOS_AIRES).date()
    workspace_slots = (
        AutomaticActionReservation.objects.filter(
            workspace_id=decision.workspace_id,
            local_date=local_date,
        ).aggregate(total=Sum("slots"))["total"]
        or 0
    )
    conversation_limit = int(settings.AUTOMATIC_REPLY_CONVERSATION_DAILY_LIMIT)
    workspace_limit = int(settings.AUTOMATIC_REPLY_WORKSPACE_DAILY_LIMIT)
    if conversation_slots + slots > conversation_limit:
        return "Se alcanzó el máximo de respuestas automáticas de esta conversación en 24 horas."
    if workspace_slots + slots > workspace_limit:
        return "Se alcanzó el máximo diario de respuestas automáticas del espacio de trabajo."
    AutomaticActionReservation.objects.create(
        workspace_id=decision.workspace_id,
        conversation_id=decision.conversation_id,
        decision=decision,
        reserved_at=now,
        local_date=local_date,
        slots=slots,
    )
    return ""


def _final_delivery_rate_error(
    message: OutboundMessage,
    *,
    decision: ReplyDecision | None,
    now: datetime,
) -> str:
    workspace_id = (
        decision.workspace_id
        if decision is not None
        else message.contact.workspace_id
        if message.contact is not None
        else None
    )
    if workspace_id is None:
        return "El envío automático perdió su espacio de trabajo."
    Workspace.objects.select_for_update().get(pk=workspace_id)
    if decision is not None:
        Conversation.objects.select_for_update().get(pk=decision.conversation_id)

    in_flight_states = (
        OutboundMessage.State.SENDING,
        OutboundMessage.State.RECONCILING,
    )
    if decision is not None:
        rolling_start = now - timedelta(hours=24)
        conversation_messages = OutboundMessage.objects.filter(
            parent_inbound__conversation_id=decision.conversation_id,
            kind__in=AUTOMATIC_KINDS,
        ).exclude(pk=message.pk)
        conversation_used = (
            conversation_messages.filter(
                Q(state=OutboundMessage.State.SENT, sent_at__gte=rolling_start)
                | Q(state__in=in_flight_states)
            )
            .values("pk")
            .distinct()
            .count()
        )
        if conversation_used + 1 > int(settings.AUTOMATIC_REPLY_CONVERSATION_DAILY_LIMIT):
            return (
                "Se alcanzó el máximo de respuestas automáticas de esta conversación en 24 horas."
            )

    local_date = now.astimezone(BUENOS_AIRES).date()
    local_start = datetime.combine(local_date, time.min, tzinfo=BUENOS_AIRES)
    local_end = local_start + timedelta(days=1)
    workspace_messages = (
        OutboundMessage.objects.filter(contact__workspace_id=workspace_id)
        .filter(
            Q(kind__in=AUTOMATIC_KINDS)
            | Q(
                kind=OutboundMessage.Kind.SCHEDULED_CONTACT,
                scheduled_contact_attempts__plan__mode=ContactCommunicationPlan.Mode.AUTOMATIC,
            )
        )
        .exclude(pk=message.pk)
    )
    workspace_used = (
        workspace_messages.filter(
            Q(
                state=OutboundMessage.State.SENT,
                sent_at__gte=local_start,
                sent_at__lt=local_end,
            )
            | Q(state__in=in_flight_states)
        )
        .values("pk")
        .distinct()
        .count()
    )
    if workspace_used + 1 > int(settings.AUTOMATIC_REPLY_WORKSPACE_DAILY_LIMIT):
        return "Se alcanzó el máximo diario de respuestas automáticas del espacio de trabajo."
    return ""


def _reply_thread_fields(decision: ReplyDecision) -> tuple[str, tuple[str, ...], str]:
    inbound = decision.inbound
    root = inbound.related_outbound
    in_reply_to = normalize_message_id(inbound.message_id)
    references = normalize_references(
        (root.message_id, *(str(value) for value in inbound.references), inbound.message_id)
    )
    if not inbound.gmail_thread_id or not in_reply_to:
        raise ValidationError(
            "La conversación no conserva los identificadores necesarios para responder."
        )
    return in_reply_to, references, inbound.gmail_thread_id


def _base_outbound_values(
    decision: ReplyDecision,
    *,
    kind: str,
    email: EmailAddress,
    subject: str,
    body_text: str,
    semantic_key: str,
) -> dict[str, Any]:
    root = decision.inbound.related_outbound
    return {
        "kind": kind,
        "campaign": root.campaign,
        "organization": decision.contact.organization,
        "campaign_enrollment": root.campaign_enrollment,
        "contact": decision.contact,
        "prospect": root.prospect,
        "prospect_email": root.prospect_email,
        "email_address": email,
        "analysis": None,
        "parent_inbound": decision.inbound,
        "recipient": email.original_email,
        "recipient_normalized": email.normalized_email,
        "subject": subject,
        "body_text": body_text,
        "state": OutboundMessage.State.QUEUED,
        "delivery_mode": Campaign.DeliveryMode.LIVE,
        "idempotency_key": semantic_key,
        "semantic_action_key": semantic_key,
        "message_id": deterministic_message_id(semantic_key),
        "next_attempt_at": timezone.now(),
    }


def _create_semantic_outbound(values: dict[str, Any]) -> tuple[OutboundMessage, bool]:
    try:
        with transaction.atomic():
            return OutboundMessage.objects.create(**values), True
    except IntegrityError:
        semantic_key = str(values["semantic_action_key"])
        return OutboundMessage.objects.get(semantic_action_key=semantic_key), False


@transaction.atomic
def authorize_reply_decision(decision_id: uuid.UUID | str) -> OutboundMessage | str:
    decision = (
        ReplyDecision.objects.select_for_update()
        .select_related(
            "workspace",
            "contact__organization",
            "conversation",
            "inbound__connection",
            "inbound__related_outbound__campaign",
            "inbound__related_outbound__campaign_enrollment",
            "inbound__related_outbound__prospect",
            "inbound__related_outbound__prospect_email",
        )
        .prefetch_related("selected_facts__fact")
        .get(pk=decision_id)
    )
    InboundMessage.objects.select_for_update().get(pk=decision.inbound_id)
    if decision.state in {
        ReplyDecision.State.COMPLETED,
        ReplyDecision.State.HUMAN_REQUIRED,
        ReplyDecision.State.REJECTED_POLICY,
        ReplyDecision.State.FAILED,
        ReplyDecision.State.NO_ACTION,
        ReplyDecision.State.SHADOW_RECORDED,
    }:
        return decision.state
    existing = (
        OutboundMessage.objects.filter(
            parent_inbound_id=decision.inbound_id,
            kind__in=AUTOMATIC_KINDS,
        )
        .order_by("created_at")
        .first()
    )
    if existing is not None:
        return existing
    if decision.state != ReplyDecision.State.AUTO_ELIGIBLE:
        return decision.state
    if error := _manual_reply_conflict_error(decision):
        return _reject_decision_locked(
            decision,
            reason="MANUAL_REPLY_ALREADY_AUTHORIZED",
            summary=error,
            failed=True,
        )
    if error := _conversation_error(decision):
        return _reject_decision_locked(
            decision,
            reason="HUMAN_TASK_OPEN",
            summary=error,
        )
    if error := _decision_policy_error(decision, check_current_context=True):
        return _reject_decision_locked(
            decision,
            reason="POLICY_RECHECK_FAILED",
            summary=error,
        )
    root = decision.inbound.related_outbound
    campaign = root.campaign
    if root.delivery_mode != Campaign.DeliveryMode.LIVE or (
        campaign is not None and campaign.delivery_mode != Campaign.DeliveryMode.LIVE
    ):
        return _reject_decision_locked(
            decision,
            reason="NOT_LIVE_ORIGIN",
            summary="La conversación no se originó en un envío en vivo.",
        )
    connection = (
        GmailConnection.objects.select_for_update()
        .filter(
            pk=decision.inbound.connection_id,
            workspace_id=decision.workspace_id,
        )
        .first()
    )
    if error := _gmail_connection_error(connection):
        return _reject_decision_locked(
            decision,
            reason="GMAIL_NOT_READY",
            summary=error,
        )
    now = timezone.now()
    slots = 2 if decision.action == "REDIRECT_PROPOSAL" else 1
    if error := _reserve_rate_capacity(decision, slots=slots, now=now):
        return _reject_decision_locked(
            decision,
            reason="AUTOMATIC_RATE_LIMIT",
            summary=error,
        )

    if decision.action == "REPLY":
        email, normalized = _reply_email(decision)
        if email is None:
            return _reject_decision_locked(
                decision,
                reason="INVALID_REPLY_ADDRESS",
                summary="No se pudo confirmar el email desde el que respondió el contacto.",
            )
        lock_email_eligibility(normalized)
        if error := _contact_or_channel_error(
            contact=decision.contact,
            email=email,
            normalized_email=normalized,
        ):
            return _reject_decision_locked(
                decision,
                reason="CONTACT_RESTRICTED",
                summary=error,
            )
        try:
            in_reply_to, references, thread_id = _reply_thread_fields(decision)
        except ValidationError as exc:
            return _reject_decision_locked(
                decision,
                reason="MISSING_THREAD_CONTEXT",
                summary=str(exc.message),
            )
        semantic_key = f"automatic-reply:{decision.inbound.gmail_message_id}"
        values = _base_outbound_values(
            decision,
            kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
            email=email,
            subject=root.subject,
            body_text=_grounded_reply_body(decision),
            semantic_key=semantic_key,
        )
        values.update(
            {
                "conversation": decision.conversation,
                "in_reply_to": in_reply_to,
                "references": list(references),
                "gmail_thread_id": thread_id,
            }
        )
        message, _ = _create_semantic_outbound(values)
    else:
        redirect_message = _authorize_redirect_proposal_locked(decision)
        if isinstance(redirect_message, str):
            return redirect_message
        message = redirect_message

    decision.state = ReplyDecision.State.AUTHORIZED
    decision.error = ""
    decision.save(update_fields=("state", "error", "updated_at"))
    record_event(
        action="automation.reply_authorized",
        entity=decision,
        actor=None,
        after={"action": decision.action, "message_id": str(message.pk)},
    )
    return message


def _candidate_validation_error(decision: ReplyDecision, candidate: EmailCandidate) -> str:
    if (
        candidate.inbound_id != decision.inbound_id
        or candidate.region != EmailCandidate.Region.NEW_CONTENT
    ):
        return "El email elegido no proviene del texto nuevo de este mensaje."
    candidate_count = EmailCandidate.objects.filter(
        inbound_id=decision.inbound_id,
        region=EmailCandidate.Region.NEW_CONTENT,
    ).count()
    if candidate_count != 1:
        return "El mensaje contiene más de un email posible y necesita revisión."
    try:
        normalized = normalize_email(candidate.normalized_email)
    except ValidationError:
        return "El email indicado ya no tiene una sintaxis válida."
    if normalized != candidate.normalized_email:
        return "El email indicado no coincide con su versión validada."
    inbound = decision.inbound
    current_literals = extract_literal_candidates(
        inbound.body_text,
        mailto_literals=extract_mailto_literals(inbound.body_html_sanitized),
    )
    current = next(
        (
            item
            for item in current_literals
            if item.original == candidate.original_literal
            and item.normalized == candidate.normalized_email
            and item.region == candidate.region
            and item.source == candidate.source
            and item.start_offset == candidate.start_offset
            and item.end_offset == candidate.end_offset
        ),
        None,
    )
    if current is None:
        return "El email indicado ya no coincide literalmente con el mensaje recibido."
    evidence = "|".join(
        (
            str(inbound.pk),
            current.original,
            current.normalized,
            current.region,
            current.source,
            str(current.start_offset),
            str(current.end_offset),
        )
    )
    if sha256(evidence.encode()).hexdigest() != candidate.evidence_hash:
        return "La evidencia guardada del email indicado ya no coincide."
    if any(
        state != EmailCandidate.CheckState.VALID
        for state in (
            candidate.syntax_state,
            candidate.mx_state,
            candidate.restriction_state,
            candidate.ownership_state,
        )
    ):
        return "No se pudo confirmar de forma inequívoca el email indicado."
    return ""


def _refresh_redirect_candidate_mx(
    decision_id: uuid.UUID | str,
    *,
    resolver: MXResolver | None,
) -> None:
    decision = ReplyDecision.objects.select_related("inbound").filter(pk=decision_id).first()
    if (
        decision is None
        or decision.action != "REDIRECT_PROPOSAL"
        or decision.candidate_id is None
        or decision.state
        not in {
            ReplyDecision.State.AUTO_ELIGIBLE,
            ReplyDecision.State.AUTHORIZED,
            ReplyDecision.State.EXECUTING,
        }
    ):
        return
    proposal = OutboundMessage.objects.filter(
        parent_inbound_id=decision.inbound_id,
        kind=OutboundMessage.Kind.REFERRED_PROPOSAL,
    ).first()
    if proposal is not None and proposal.state != OutboundMessage.State.QUEUED:
        return
    candidate = EmailCandidate.objects.get(pk=decision.candidate_id)
    try:
        normalized = normalize_email(candidate.normalized_email)
    except ValidationError:
        mx_status = MXStatus.INVALID
    else:
        mx_status = (resolver or DNSMXResolver()).resolve(normalized.rsplit("@", 1)[-1])
    next_state = {
        MXStatus.VALID: EmailCandidate.CheckState.VALID,
        MXStatus.INVALID: EmailCandidate.CheckState.INVALID,
        MXStatus.TRANSIENT: EmailCandidate.CheckState.TRANSIENT,
    }[mx_status]
    with transaction.atomic():
        locked_decision = ReplyDecision.objects.select_for_update().get(pk=decision.pk)
        InboundMessage.objects.select_for_update().get(pk=locked_decision.inbound_id)
        locked = EmailCandidate.objects.select_for_update().get(pk=candidate.pk)
        if (
            locked_decision.candidate_id == locked.pk
            and locked.normalized_email == candidate.normalized_email
        ):
            locked.mx_state = next_state
            locked.save(update_fields=("mx_state", "updated_at"))


def _redirect_email_locked(decision: ReplyDecision) -> EmailAddress | str:
    if decision.candidate_id is None:
        return "La redirección no conserva el email que fue seleccionado."
    candidate = EmailCandidate.objects.select_for_update().get(pk=decision.candidate_id)
    if error := _candidate_validation_error(decision, candidate):
        return error
    normalized = candidate.normalized_email
    lock_email_eligibility(normalized)
    existing = (
        EmailAddress.objects.select_for_update()
        .filter(
            workspace_id=decision.workspace_id,
            normalized_email=normalized,
        )
        .first()
    )
    if existing is not None and existing.organization_id != decision.contact.organization_id:
        return "El email indicado ya pertenece a otra organización."
    if existing is not None:
        if error := _contact_or_channel_error(
            contact=decision.contact,
            email=existing,
            normalized_email=normalized,
        ):
            return error
        changes: list[str] = []
        if not existing.label:
            existing.label = "Dirección indicada para propuestas"
            changes.append("label")
        if not existing.provenance:
            existing.provenance = "INBOUND_REDIRECT"
            changes.append("provenance")
        if changes:
            existing.save(update_fields=(*changes, "updated_at"))
        email = existing
    else:
        if SuppressionEntry.objects.filter(normalized_email=normalized).exists():
            return "El email indicado está en la lista de direcciones que no se deben contactar."
        provider_order = (
            EmailAddress.objects.filter(organization=decision.contact.organization).aggregate(
                maximum=Max("provider_order")
            )["maximum"]
            or 0
        ) + 1
        try:
            with transaction.atomic():
                email = EmailAddress.objects.create(
                    workspace=decision.workspace,
                    organization=decision.contact.organization,
                    original_email=normalized,
                    normalized_email=normalized,
                    domain=normalized.rsplit("@", 1)[-1],
                    label="Dirección indicada para propuestas",
                    provenance="INBOUND_REDIRECT",
                    source_content_hash=candidate.evidence_hash,
                    provider_order=provider_order,
                    validity=EmailAddress.Validity.VALID,
                    validated_at=timezone.now(),
                )
        except IntegrityError:
            email = EmailAddress.objects.select_for_update().get(
                workspace_id=decision.workspace_id,
                normalized_email=normalized,
            )
            if email.organization_id != decision.contact.organization_id:
                return "El email indicado quedó asociado a otra organización."
    candidate.resolved_email_address = email
    candidate.save(update_fields=("resolved_email_address", "updated_at"))
    return email


def _origin_attachments(decision: ReplyDecision) -> tuple[OutboundAttachment, ...]:
    root = decision.inbound.related_outbound
    source = root
    if root.campaign_enrollment_id is not None:
        original = (
            OutboundMessage.objects.filter(
                campaign_enrollment_id=root.campaign_enrollment_id,
                kind__in=(OutboundMessage.Kind.FIRST_CONTACT, OutboundMessage.Kind.INITIAL),
                state=OutboundMessage.State.SENT,
            )
            .order_by("sent_at", "created_at")
            .first()
        )
        if original is not None:
            source = original
    return tuple(
        source.attachments.select_for_update()
        .select_related("catalog")
        .order_by("position", "created_at")
    )


def _attachment_signature(attachment: OutboundAttachment) -> tuple[Any, ...]:
    return (
        attachment.catalog_id,
        attachment.position,
        attachment.catalog_version,
        attachment.storage_key,
        attachment.filename,
        attachment.byte_size,
        attachment.sha256,
    )


def _automatic_outbound_integrity_error(
    message: OutboundMessage,
    decision: ReplyDecision,
) -> str:
    root = decision.inbound.related_outbound
    if root.state != OutboundMessage.State.SENT or root.sent_at is None:
        return "El mensaje que originó la conversación ya no figura como enviado."
    if (
        message.parent_inbound_id != decision.inbound_id
        or message.contact_id != decision.contact_id
        or message.organization_id != decision.contact.organization_id
        or message.campaign_id != root.campaign_id
        or message.campaign_enrollment_id != root.campaign_enrollment_id
        or message.delivery_mode != Campaign.DeliveryMode.LIVE
    ):
        return "El mensaje automático ya no coincide con la conversación que lo autorizó."
    expected_kind = {
        "REPLY": (OutboundMessage.Kind.AUTOMATIC_REPLY,),
        "REDIRECT_PROPOSAL": (
            OutboundMessage.Kind.REFERRED_PROPOSAL,
            OutboundMessage.Kind.REDIRECT_ACK,
        ),
    }.get(decision.action, ())
    if message.kind not in expected_kind:
        return "El tipo de mensaje automático no coincide con la acción autorizada."
    semantic_prefixes: dict[str, str] = {
        OutboundMessage.Kind.AUTOMATIC_REPLY: "automatic-reply",
        OutboundMessage.Kind.REFERRED_PROPOSAL: "redirect-proposal",
        OutboundMessage.Kind.REDIRECT_ACK: "redirect-ack",
    }
    semantic_prefix = semantic_prefixes[message.kind]
    semantic_key = f"{semantic_prefix}:{decision.inbound.gmail_message_id}"
    if (
        message.semantic_action_key != semantic_key
        or message.idempotency_key != semantic_key
        or message.message_id != deterministic_message_id(semantic_key)
    ):
        return "Cambió la identificación segura del envío automático."
    try:
        actual_recipient = normalize_email(message.recipient)
    except ValidationError:
        return "El destinatario del mensaje automático ya no es válido."
    if actual_recipient != message.recipient_normalized:
        return "El destinatario del mensaje automático cambió después de autorizarse."

    if message.kind in REPLY_KINDS:
        try:
            in_reply_to, references, thread_id = _reply_thread_fields(decision)
        except ValidationError:
            return "La conversación perdió los identificadores necesarios para responder."
        if (
            message.conversation_id != decision.conversation_id
            or message.gmail_thread_id != thread_id
            or message.in_reply_to != in_reply_to
            or tuple(str(value) for value in message.references) != references
            or message.attachments.exists()
        ):
            return "Cambió el hilo o los adjuntos del mensaje automático autorizado."

    if message.kind == OutboundMessage.Kind.AUTOMATIC_REPLY:
        if (
            message.subject != root.subject
            or message.body_text != _grounded_reply_body(decision)
            or message.signature_snapshot
            or message.content_hash
        ):
            return "Cambió el contenido de la respuesta automática autorizada."
    elif message.kind == OutboundMessage.Kind.REDIRECT_ACK:
        proposal_sent = OutboundMessage.objects.filter(
            parent_inbound_id=decision.inbound_id,
            kind=OutboundMessage.Kind.REFERRED_PROPOSAL,
            state=OutboundMessage.State.SENT,
            sent_at__isnull=False,
        ).exists()
        if not proposal_sent:
            return "La propuesta todavía no fue confirmada; no se puede agradecer el reenvío."
        if (
            message.subject != root.subject
            or message.body_text != REDIRECT_ACK_BODY
            or message.signature_snapshot
            or message.content_hash
        ):
            return "Cambió el texto fijo de confirmación del reenvío."
    else:
        campaign = root.campaign
        if campaign is None or campaign.approved_at is None or campaign.approved_by_id is None:
            return "La propuesta ya no conserva una campaña aprobada."
        try:
            frozen = freeze_message_content(
                subject=campaign.referred_subject_snapshot,
                body=campaign.referred_body_snapshot,
                signature=campaign.signature_snapshot,
            )
        except ValueError:
            return "El texto fijo de la propuesta reenviada ya no es válido."
        if (
            message.conversation_id is not None
            or message.gmail_thread_id
            or message.in_reply_to
            or message.references
            or message.subject != frozen.subject
            or message.body_text != frozen.rendered_body
            or message.signature_snapshot != frozen.signature
            or message.content_hash != frozen.content_hash
        ):
            return "Cambió el contenido fijo de la propuesta reenviada."
        expected_attachments = tuple(
            _attachment_signature(item) for item in _origin_attachments(decision)
        )
        actual_attachments = tuple(
            _attachment_signature(item)
            for item in message.attachments.select_for_update().order_by("position", "created_at")
        )
        if not expected_attachments or actual_attachments != expected_attachments:
            return "Cambió el conjunto completo de PDFs de la propuesta reenviada."
    return ""


def _authorize_redirect_proposal_locked(decision: ReplyDecision) -> OutboundMessage | str:
    root = decision.inbound.related_outbound
    campaign = root.campaign
    if campaign is None or campaign.approved_at is None or campaign.approved_by_id is None:
        return _reject_decision_locked(
            decision,
            reason="MISSING_APPROVED_CAMPAIGN",
            summary="La propuesta original no conserva una campaña aprobada.",
        )
    email_or_error = _redirect_email_locked(decision)
    if isinstance(email_or_error, str):
        return _reject_decision_locked(
            decision,
            reason="REDIRECT_ADDRESS_UNSAFE",
            summary=email_or_error,
        )
    source_attachments = _origin_attachments(decision)
    if not source_attachments:
        return _reject_decision_locked(
            decision,
            reason="MISSING_APPROVED_ATTACHMENTS",
            summary="La propuesta original no conserva todos sus PDFs aprobados.",
        )
    frozen = freeze_message_content(
        subject=campaign.referred_subject_snapshot,
        body=campaign.referred_body_snapshot,
        signature=campaign.signature_snapshot,
    )
    semantic_key = f"redirect-proposal:{decision.inbound.gmail_message_id}"
    values = _base_outbound_values(
        decision,
        kind=OutboundMessage.Kind.REFERRED_PROPOSAL,
        email=email_or_error,
        subject=frozen.subject,
        body_text=frozen.rendered_body,
        semantic_key=semantic_key,
    )
    values.update(
        {
            "conversation": None,
            "signature_snapshot": frozen.signature,
            "content_hash": frozen.content_hash,
            "catalog": source_attachments[0].catalog,
            "catalog_version": source_attachments[0].catalog_version,
        }
    )
    message, created = _create_semantic_outbound(values)
    if created:
        OutboundAttachment.objects.bulk_create(
            OutboundAttachment(
                message=message,
                catalog=item.catalog,
                position=item.position,
                catalog_version=item.catalog_version,
                storage_key=item.storage_key,
                filename=item.filename,
                byte_size=item.byte_size,
                sha256=item.sha256,
            )
            for item in source_attachments
        )
    return message


@transaction.atomic
def authorize_redirect_ack(decision_id: uuid.UUID | str) -> OutboundMessage | str:
    decision = (
        ReplyDecision.objects.select_for_update()
        .select_related(
            "workspace",
            "contact__organization",
            "conversation",
            "inbound__connection",
            "inbound__related_outbound__campaign",
            "inbound__related_outbound__campaign_enrollment",
            "inbound__related_outbound__prospect",
            "inbound__related_outbound__prospect_email",
        )
        .prefetch_related("selected_facts__fact")
        .get(pk=decision_id)
    )
    existing = OutboundMessage.objects.filter(
        parent_inbound_id=decision.inbound_id,
        kind=OutboundMessage.Kind.REDIRECT_ACK,
    ).first()
    if existing is not None:
        return existing
    proposal = (
        OutboundMessage.objects.select_for_update()
        .filter(
            parent_inbound_id=decision.inbound_id,
            kind=OutboundMessage.Kind.REFERRED_PROPOSAL,
        )
        .first()
    )
    if proposal is None or proposal.state != OutboundMessage.State.SENT:
        return decision.state
    if decision.state not in {ReplyDecision.State.AUTHORIZED, ReplyDecision.State.EXECUTING}:
        return decision.state
    if error := _conversation_error(decision):
        return _reject_decision_locked(
            decision,
            reason="HUMAN_TASK_OPEN",
            summary=error,
            failed=True,
        )
    if error := _decision_policy_error(
        decision,
        check_current_context=False,
        allowed_outbound_ids=(proposal.pk,),
    ):
        return _reject_decision_locked(
            decision,
            reason="POLICY_RECHECK_FAILED",
            summary=error,
            failed=True,
        )
    email, normalized = _reply_email(decision)
    if email is None:
        return _reject_decision_locked(
            decision,
            reason="INVALID_REPLY_ADDRESS",
            summary="La propuesta se envió, pero no se pudo confirmar dónde responder el aviso.",
            failed=True,
        )
    lock_email_eligibility(normalized)
    if error := _contact_or_channel_error(
        contact=decision.contact,
        email=email,
        normalized_email=normalized,
    ):
        return _reject_decision_locked(
            decision,
            reason="CONTACT_RESTRICTED",
            summary=error,
            failed=True,
        )
    try:
        in_reply_to, references, thread_id = _reply_thread_fields(decision)
    except ValidationError as exc:
        return _reject_decision_locked(
            decision,
            reason="MISSING_THREAD_CONTEXT",
            summary=str(exc.message),
            failed=True,
        )
    root = decision.inbound.related_outbound
    semantic_key = f"redirect-ack:{decision.inbound.gmail_message_id}"
    values = _base_outbound_values(
        decision,
        kind=OutboundMessage.Kind.REDIRECT_ACK,
        email=email,
        subject=root.subject,
        body_text=REDIRECT_ACK_BODY,
        semantic_key=semantic_key,
    )
    values.update(
        {
            "conversation": decision.conversation,
            "in_reply_to": in_reply_to,
            "references": list(references),
            "gmail_thread_id": thread_id,
        }
    )
    message, _ = _create_semantic_outbound(values)
    return message


def _attachment_payloads(message: OutboundMessage) -> tuple[PdfAttachment, ...]:
    payloads: list[PdfAttachment] = []
    for attachment in message.attachments.select_related("catalog").order_by(
        "position", "created_at"
    ):
        catalog = attachment.catalog
        if (
            catalog.version != attachment.catalog_version
            or catalog.storage_key != attachment.storage_key
            or catalog.original_filename != attachment.filename
            or catalog.byte_size != attachment.byte_size
            or catalog.sha256 != attachment.sha256
        ):
            raise ValidationError("Uno de los PDFs ya no coincide con la propuesta aprobada.")
        verify_catalog(catalog)
        with catalog.file.open("rb") as handle:
            content = handle.read()
        if len(content) != attachment.byte_size or sha256(content).hexdigest() != attachment.sha256:
            raise ValidationError("Uno de los PDFs perdió integridad.")
        payloads.append(PdfAttachment(filename=attachment.filename, content=content))
    if message.kind == OutboundMessage.Kind.REFERRED_PROPOSAL and not payloads:
        raise ValidationError("La propuesta reenviada necesita todos los PDFs aprobados.")
    return tuple(payloads)


def _scheduled_message_error(message: OutboundMessage) -> str:
    if settings.SEND_MODE != "live" or settings.SEND_KILL_SWITCH:
        return "El envío en vivo está desactivado por la configuración general."
    if settings.RELATIONSHIP_KILL_SWITCH:
        return "El bloqueo independiente de contactos programados está activado."
    if message.campaign_id is not None or message.parent_inbound_id is not None:
        return "El contacto programado no es un mensaje nuevo independiente."
    contact = message.contact
    email = message.email_address
    if contact is None or email is None:
        return "El contacto programado no conserva un contacto y email válidos."
    if (
        contact.automation_suspended
        or HumanTask.objects.filter(
            contact=contact,
            status=HumanTask.Status.OPEN,
        ).exists()
    ):
        return "El contacto tiene una revisión humana pendiente."
    attempts = (
        ScheduledContactAttempt.objects.select_for_update()
        .select_related(
            "plan__contact__workspace",
            "plan__preferred_email",
        )
        .filter(outbound_message=message)
    )
    if attempts.count() != 1:
        return "El mensaje no tiene una única programación que lo autorice."
    attempt = attempts.get()
    plan = attempt.plan
    if attempt.state != ScheduledContactAttempt.State.AUTHORIZED:
        return "La programación ya no está autorizada para enviar."
    if (
        plan.state != ContactCommunicationPlan.State.ACTIVE
        or plan.contact_id != contact.pk
        or plan.preferred_email_id != email.pk
        or plan.next_due_at != attempt.due_at
        or message.scheduled_for != attempt.due_at
    ):
        return "La programación o el email preferido cambiaron antes del envío."
    from apps.automation.scheduled import PURPOSE_GOALS, scheduled_contact_eligibility

    eligibility = scheduled_contact_eligibility(plan, for_send=True)
    if not eligibility.eligible:
        return eligibility.message
    if plan.mode == ContactCommunicationPlan.Mode.AUTOMATIC:
        if settings.AUTO_REPLY_KILL_SWITCH:
            return "El bloqueo independiente de automatización está activado."
        configuration = ReplyAutomationConfiguration.objects.filter(
            workspace_id=contact.workspace_id
        ).first()
        if (
            configuration is None
            or configuration.mode != ReplyAutomationConfiguration.Mode.LIVE
            or configuration.live_enabled_at is None
            or configuration.live_enabled_by_id is None
        ):
            return "La automatización ya no está habilitada en modo activo."
        from apps.automation.services import qualification_snapshot

        if not qualification_snapshot(contact.workspace).qualified:
            return "El modo activo ya no cumple la evaluación mínima de seguridad."
        reservation = AutomaticActionReservation.objects.filter(
            scheduled_attempt=attempt,
            workspace_id=contact.workspace_id,
            slots=1,
        ).first()
        if reservation is None:
            return "La capacidad diaria del envío automático no quedó reservada."
        try:
            from apps.automation.context import build_bounded_scheduled_contact_context

            current_context = build_bounded_scheduled_contact_context(
                plan,
                goal=PURPOSE_GOALS.get(plan.purpose, plan.goal_text.strip()),
            )
        except ValidationError:
            return "El contexto actual ya no cabe en el límite seguro."
        if current_context.context_hash != attempt.context_hash:
            return "El contexto del contacto cambió antes del envío automático."
        manifest_fact_ids = {
            str(item.get("revision_id"))
            for item in attempt.context_manifest.get("facts", [])
            if isinstance(item, dict)
        }
        if not set(str(item) for item in attempt.fact_revision_ids).issubset(manifest_fact_ids):
            return "El borrador cita información fuera de su contexto autorizado."
    elif plan.mode == ContactCommunicationPlan.Mode.REVIEW_BEFORE_SEND:
        if message.approved_at is None or message.approved_by_id is None:
            return "El borrador todavía no fue aprobado por un administrador."
    else:
        return "El modo de la programación no es válido."
    try:
        normalized = normalize_email(message.recipient)
    except ValidationError:
        return "El destinatario del contacto programado no es válido."
    return _contact_or_channel_error(
        contact=contact,
        email=email,
        normalized_email=normalized,
    )


def _automatic_message_error(
    message: OutboundMessage,
    decision: ReplyDecision,
) -> str:
    if error := _conversation_error(decision):
        return error
    if error := _manual_reply_conflict_error(decision):
        return error
    allowed = tuple(
        OutboundMessage.objects.filter(
            parent_inbound_id=decision.inbound_id,
            kind__in=AUTOMATIC_KINDS,
        ).values_list("pk", flat=True)
    )
    if error := _decision_policy_error(
        decision,
        check_current_context=False,
        allowed_outbound_ids=allowed,
    ):
        return error
    reservation = AutomaticActionReservation.objects.filter(decision=decision).first()
    expected_slots = 2 if decision.action == "REDIRECT_PROPOSAL" else 1
    if reservation is None or reservation.slots != expected_slots:
        return "La capacidad de envío automático no quedó reservada correctamente."
    if error := _automatic_outbound_integrity_error(message, decision):
        return error
    if message.kind == OutboundMessage.Kind.REFERRED_PROPOSAL:
        if decision.candidate_id is None:
            return "La redirección no conserva el email que fue seleccionado."
        candidate = EmailCandidate.objects.select_for_update().get(pk=decision.candidate_id)
        if error := _candidate_validation_error(decision, candidate):
            return error
        email = message.email_address
        if email is None or email.pk != candidate.resolved_email_address_id:
            return "El destinatario ya no coincide con el email elegido en la respuesta."
    else:
        email, normalized = _reply_email(decision)
        if email is None or message.email_address_id != email.pk:
            return "El destinatario de la respuesta ya no coincide con el remitente validado."
        if message.recipient_normalized != normalized:
            return "El destinatario de la respuesta cambió antes del envío."
    assert message.email_address is not None
    return _contact_or_channel_error(
        contact=decision.contact,
        email=message.email_address,
        normalized_email=message.recipient_normalized,
    )


def _build_effect(message: OutboundMessage, connection: GmailConnection) -> OutboundEffect:
    body_text = message.body_text
    if message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
        canonical = json.dumps(
            {
                "subject": message.subject,
                "body": message.body_text,
                "signature": message.signature_snapshot,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if sha256(canonical.encode()).hexdigest() != message.content_hash:
            raise ValidationError("El contenido programado cambió después de su autorización.")
        if message.signature_snapshot.strip():
            body_text = f"{message.body_text.strip()}\n\n{message.signature_snapshot.strip()}"
    if message.kind in REPLY_KINDS:
        built = build_reply_message(
            sender=connection.email,
            recipient=message.recipient,
            subject=message.subject,
            body_text=body_text,
            message_id=message.message_id,
            sent_at=message.created_at,
            thread_references=tuple(str(value) for value in message.references),
            in_reply_to=message.in_reply_to,
            campaign_header=str(message.campaign_id or "conversation"),
            message_header=str(message.pk),
        )
        reply = True
    else:
        built = build_message(
            sender=connection.email,
            recipient=message.recipient,
            subject=message.subject,
            body_text=body_text,
            message_id=message.message_id,
            sent_at=message.created_at,
            campaign_header=str(message.campaign_id or "relationship"),
            message_header=str(message.pk),
            pdf_attachments=_attachment_payloads(message),
        )
        reply = False
    message.mime_sha256 = built.sha256
    message.mime_size = built.size
    return OutboundEffect(
        message_id=message.pk,
        connection_id=connection.pk,
        raw_message=built.raw,
        rfc_message_id=message.message_id,
        recipient=message.recipient,
        gmail_thread_id=message.gmail_thread_id,
        idempotency_key=message.idempotency_key,
        reply=reply,
    )


@transaction.atomic
def _prepare_outbound_effect(message_id: uuid.UUID | str) -> OutboundEffect | str:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related(
            "campaign",
            "contact__organization",
            "email_address",
            "parent_inbound__connection",
        )
        .get(pk=message_id, kind__in=(*AUTOMATIC_KINDS, OutboundMessage.Kind.SCHEDULED_CONTACT))
    )
    if message.state in {
        OutboundMessage.State.SENT,
        OutboundMessage.State.SEND_FAILED,
        OutboundMessage.State.CANCELLED,
        OutboundMessage.State.INELIGIBLE,
        OutboundMessage.State.RECONCILING,
        OutboundMessage.State.SENDING,
    }:
        return message.state
    if message.state != OutboundMessage.State.QUEUED:
        message.state = OutboundMessage.State.SEND_FAILED
        message.error = "El mensaje automático no estaba autorizado y en cola."
        message.save(update_fields=("state", "error", "updated_at"))
        return message.state

    decision: ReplyDecision | None = None
    if message.kind in AUTOMATIC_KINDS:
        if message.parent_inbound_id is None:
            message.state = OutboundMessage.State.SEND_FAILED
            message.error = "El mensaje perdió la respuesta recibida que lo autorizaba."
            message.save(update_fields=("state", "error", "updated_at"))
            return message.state
        decision = (
            ReplyDecision.objects.select_for_update()
            .select_related(
                "workspace",
                "contact__organization",
                "conversation",
                "inbound__connection",
                "inbound__related_outbound__campaign",
            )
            .prefetch_related("selected_facts__fact")
            .get(inbound_id=message.parent_inbound_id)
        )
        InboundMessage.objects.select_for_update().get(pk=decision.inbound_id)
        if error := _automatic_message_error(message, decision):
            return _reject_decision_locked(
                decision,
                reason="FINAL_SEND_RECHECK_FAILED",
                summary=error,
                message=message,
                failed=True,
            )
        connection = (
            GmailConnection.objects.select_for_update()
            .filter(
                pk=decision.inbound.connection_id,
                workspace_id=decision.workspace_id,
            )
            .first()
        )
    else:
        if error := _scheduled_message_error(message):
            message.state = OutboundMessage.State.INELIGIBLE
            message.error = error
            message.next_attempt_at = None
            message.save(update_fields=("state", "error", "next_attempt_at", "updated_at"))
            return message.state
        assert message.contact is not None
        connection = (
            GmailConnection.objects.select_for_update()
            .filter(workspace_id=message.contact.workspace_id)
            .first()
        )
    if error := _gmail_connection_error(connection):
        if decision is not None:
            return _reject_decision_locked(
                decision,
                reason="GMAIL_NOT_READY",
                summary=error,
                message=message,
                failed=True,
            )
        message.state = OutboundMessage.State.SEND_FAILED
        message.error = error
        message.next_attempt_at = None
        message.save(update_fields=("state", "error", "next_attempt_at", "updated_at"))
        return message.state
    assert connection is not None
    lock_email_eligibility(message.recipient_normalized)
    try:
        effect = _build_effect(message, connection)
    except (OSError, ValidationError) as exc:
        summary = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else "No se pudo leer uno de los PDFs aprobados desde el almacenamiento privado."
        )
        if decision is not None:
            return _reject_decision_locked(
                decision,
                reason="MIME_OR_ATTACHMENT_FAILURE",
                summary=summary,
                message=message,
                failed=True,
            )
        message.state = OutboundMessage.State.SEND_FAILED
        message.error = summary[:500]
        message.next_attempt_at = None
        message.save(update_fields=("state", "error", "next_attempt_at", "updated_at"))
        return message.state
    now = timezone.now()
    scheduled_automatic = (
        decision is None
        and message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT
        and ScheduledContactAttempt.objects.filter(
            outbound_message=message,
            plan__mode=ContactCommunicationPlan.Mode.AUTOMATIC,
        ).exists()
    )
    if decision is not None or scheduled_automatic:
        if error := _final_delivery_rate_error(message, decision=decision, now=now):
            if decision is not None:
                return _reject_decision_locked(
                    decision,
                    reason="FINAL_AUTOMATIC_RATE_LIMIT",
                    summary=error,
                    message=message,
                    failed=True,
                )
            message.state = OutboundMessage.State.INELIGIBLE
            message.error = error
            message.next_attempt_at = None
            message.save(update_fields=("state", "error", "next_attempt_at", "updated_at"))
            return message.state
    message.state = OutboundMessage.State.SENDING
    message.attempts += 1
    message.sending_started_at = now
    message.last_attempt_at = now
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
            "mime_size",
            "error",
            "updated_at",
        )
    )
    if decision is not None and decision.state != ReplyDecision.State.EXECUTING:
        decision.state = ReplyDecision.State.EXECUTING
        decision.save(update_fields=("state", "updated_at"))
    return effect


def _send_effect(effect: OutboundEffect, provider: GmailProvider) -> GmailSendResult:
    if effect.reply:
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


def _new_conversation_for_result(
    message: OutboundMessage,
    connection: GmailConnection,
    result: GmailSendResult,
    now: datetime,
) -> Conversation | None:
    contact = message.contact
    if contact is None:
        return None
    existing = (
        Conversation.objects.select_for_update()
        .filter(
            connection=connection,
            gmail_thread_id=result.thread_id,
        )
        .first()
    )
    if existing is not None:
        return existing if existing.contact_id == contact.pk else None
    return Conversation.objects.create(
        workspace=contact.workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id=result.thread_id,
        subject=message.subject,
        last_message_at=now,
    )


def _connection_for_outbound(message: OutboundMessage) -> GmailConnection | None:
    if message.parent_inbound_id is not None:
        parent = message.parent_inbound
        return parent.connection if parent is not None else None
    contact = message.contact
    if contact is None:
        return None
    return GmailConnection.objects.filter(workspace_id=contact.workspace_id).first()


@transaction.atomic
def _confirm_outbound_sent(
    message_id: uuid.UUID,
    result: GmailSendResult,
    *,
    now: datetime,
) -> str:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related("contact__workspace", "conversation", "parent_inbound__connection")
        .get(pk=message_id)
    )
    if message.state == OutboundMessage.State.SENT:
        return message.state
    if message.state not in {OutboundMessage.State.SENDING, OutboundMessage.State.RECONCILING}:
        return message.state
    connection = _connection_for_outbound(message)
    reply_thread_conflict = (
        message.kind in REPLY_KINDS and result.thread_id != message.gmail_thread_id
    )
    proposal_thread_conflict = (
        message.kind == OutboundMessage.Kind.REFERRED_PROPOSAL
        and message.parent_inbound is not None
        and (not result.thread_id or result.thread_id == message.parent_inbound.gmail_thread_id)
    )
    new_conversation: Conversation | None = None
    if message.kind in NEW_THREAD_KINDS and connection is not None and not proposal_thread_conflict:
        new_conversation = _new_conversation_for_result(message, connection, result, now)
    message.state = OutboundMessage.State.SENT
    message.gmail_message_id = result.message_id
    message.gmail_thread_id = result.thread_id
    message.sent_at = now
    message.next_attempt_at = None
    message.error = ""
    if new_conversation is not None:
        message.conversation = new_conversation
    message.save(
        update_fields=(
            "state",
            "gmail_message_id",
            "gmail_thread_id",
            "sent_at",
            "next_attempt_at",
            "error",
            "conversation",
            "updated_at",
        )
    )
    if message.contact_id is not None:
        Contact.objects.filter(pk=message.contact_id).filter(
            Q(last_interaction_at__isnull=True) | Q(last_interaction_at__lt=now)
        ).update(last_interaction_at=now, updated_at=now)
    if message.conversation_id is not None:
        Conversation.objects.filter(pk=message.conversation_id).update(
            last_message_at=now,
            updated_at=now,
        )

    decision = None
    if message.parent_inbound_id is not None and message.kind in AUTOMATIC_KINDS:
        decision = (
            ReplyDecision.objects.select_for_update()
            .filter(inbound_id=message.parent_inbound_id)
            .first()
        )
    if decision is not None and message.kind in {
        OutboundMessage.Kind.AUTOMATIC_REPLY,
        OutboundMessage.Kind.REDIRECT_ACK,
    }:
        if reply_thread_conflict:
            _reject_decision_locked(
                decision,
                reason="GMAIL_REPLY_THREAD_MISMATCH",
                summary=(
                    "Gmail confirmó el envío, pero lo ubicó en un hilo distinto. "
                    "Revisá la conversación."
                ),
                message=message,
                failed=True,
            )
        else:
            decision.state = ReplyDecision.State.COMPLETED
            decision.error = ""
            decision.save(update_fields=("state", "error", "updated_at"))
    elif decision is not None:
        decision.state = ReplyDecision.State.EXECUTING
        decision.save(update_fields=("state", "updated_at"))
        if new_conversation is None:
            _reject_decision_locked(
                decision,
                reason="GMAIL_THREAD_OWNERSHIP_CONFLICT",
                summary=(
                    "La propuesta se envió, pero el nuevo hilo no pudo vincularse con seguridad."
                ),
                failed=True,
            )
    elif (
        message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT
        and message.contact is not None
        and new_conversation is None
    ):
        from apps.automation.services import open_human_task

        open_human_task(
            workspace=message.contact.workspace,
            contact_id=message.contact.pk,
            conversation_id=None,
            kind="SCHEDULED_CONTACT",
            reason="GMAIL_THREAD_OWNERSHIP_CONFLICT",
            friendly_summary=(
                "El mensaje se envió, pero no pudimos ubicarlo con seguridad en el historial. "
                "Revisá el hilo en Gmail."
            ),
        )
    record_event(
        action="gmail.automatic_message_sent"
        if message.kind in AUTOMATIC_KINDS
        else "gmail.scheduled_contact_sent",
        entity=message,
        actor=None,
        after={"kind": message.kind, "state": message.state},
    )
    if message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
        transaction.on_commit(lambda: _complete_scheduled_attempt(message.pk))
    return message.state


def _complete_scheduled_attempt(message_id: uuid.UUID) -> None:
    try:
        from apps.automation.scheduled import complete_scheduled_contact_attempt
    except ImportError:
        return
    complete_scheduled_contact_attempt(message_id)


@transaction.atomic
def _record_outbound_error(
    message_id: uuid.UUID,
    error: Exception,
    *,
    ambiguous: bool,
) -> str:
    message = (
        OutboundMessage.objects.select_for_update()
        .select_related(
            "parent_inbound__connection",
            "contact",
        )
        .get(pk=message_id)
    )
    connection = _connection_for_outbound(message)
    owner_id = connection.owner_id if connection is not None else None
    summary = redact_provider_error(error, owner_id=owner_id)
    if ambiguous:
        message.state = OutboundMessage.State.RECONCILING
        message.next_attempt_at = timezone.now() + RECONCILE_AFTER
        message.error = summary[:500]
        message.save(update_fields=("state", "next_attempt_at", "error", "updated_at"))
        return message.state
    message.state = OutboundMessage.State.SEND_FAILED
    message.next_attempt_at = None
    message.error = summary[:500]
    message.save(update_fields=("state", "next_attempt_at", "error", "updated_at"))
    if message.kind in AUTOMATIC_KINDS and message.parent_inbound_id is not None:
        decision = (
            ReplyDecision.objects.select_for_update()
            .select_related("workspace", "contact", "conversation")
            .get(inbound_id=message.parent_inbound_id)
        )
        _reject_decision_locked(
            decision,
            reason="GMAIL_DELIVERY_FAILED",
            summary="Gmail no pudo completar el envío automático. Revisá la conversación.",
            message=message,
            failed=True,
        )
    return message.state


def deliver_authorized_outbound(
    message_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> str:
    kind = OutboundMessage.objects.filter(pk=message_id).values_list("kind", flat=True).first()
    try:
        prepared = _prepare_outbound_effect(message_id)
    except (OSError, ValidationError) as exc:
        summary = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else "No se pudo acceder al almacenamiento privado del mensaje."
        )
        state = _record_outbound_error(
            uuid.UUID(str(message_id)),
            ValidationProviderError(summary),
            ambiguous=False,
        )
        if kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
            _complete_scheduled_attempt(uuid.UUID(str(message_id)))
        return state
    if isinstance(prepared, str):
        if kind == OutboundMessage.Kind.SCHEDULED_CONTACT and prepared in {
            OutboundMessage.State.SENT,
            OutboundMessage.State.SEND_FAILED,
            OutboundMessage.State.INELIGIBLE,
            OutboundMessage.State.CANCELLED,
        }:
            _complete_scheduled_attempt(uuid.UUID(str(message_id)))
        return prepared
    connection = GmailConnection.objects.get(pk=prepared.connection_id)
    try:
        active_provider = provider or provider_for_connection(connection, persist_fake=True)
        result = _send_effect(prepared, active_provider)
    except (ImproperlyConfigured, ValidationError) as exc:
        state = _record_outbound_error(prepared.message_id, exc, ambiguous=False)
        if kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
            _complete_scheduled_attempt(prepared.message_id)
        return state
    except (AmbiguousProviderError, RetryableProviderError) as exc:
        return _record_outbound_error(prepared.message_id, exc, ambiguous=True)
    except (AuthenticationError, PermanentProviderError, ValidationProviderError) as exc:
        state = _record_outbound_error(prepared.message_id, exc, ambiguous=False)
        if kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
            _complete_scheduled_attempt(prepared.message_id)
        return state
    except ProviderError as exc:
        return _record_outbound_error(prepared.message_id, exc, ambiguous=True)
    return _confirm_outbound_sent(prepared.message_id, result, now=timezone.now())


def execute_reply_decision(
    decision_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
    mx_resolver: MXResolver | None = None,
) -> str:
    _refresh_redirect_candidate_mx(decision_id, resolver=mx_resolver)
    authorized = authorize_reply_decision(decision_id)
    if isinstance(authorized, str):
        decision = ReplyDecision.objects.get(pk=decision_id)
        if decision.action != "REDIRECT_PROPOSAL" or decision.state not in {
            ReplyDecision.State.AUTHORIZED,
            ReplyDecision.State.EXECUTING,
        }:
            return authorized
    decision = ReplyDecision.objects.get(pk=decision_id)
    if decision.action == "REPLY":
        message = OutboundMessage.objects.get(
            parent_inbound_id=decision.inbound_id,
            kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
        )
        state = deliver_authorized_outbound(message.pk, provider=provider)
        return ReplyDecision.objects.get(pk=decision.pk).state if state else state

    proposal = OutboundMessage.objects.get(
        parent_inbound_id=decision.inbound_id,
        kind=OutboundMessage.Kind.REFERRED_PROPOSAL,
    )
    if proposal.state != OutboundMessage.State.SENT:
        proposal_state = deliver_authorized_outbound(proposal.pk, provider=provider)
        if proposal_state != OutboundMessage.State.SENT:
            return ReplyDecision.objects.get(pk=decision.pk).state
    ack = authorize_redirect_ack(decision.pk)
    if isinstance(ack, str):
        return ack
    deliver_authorized_outbound(ack.pk, provider=provider)
    return ReplyDecision.objects.get(pk=decision.pk).state


def reconcile_authorized_outbound(
    message_id: uuid.UUID | str,
    *,
    provider: GmailProvider | None = None,
) -> str:
    message = OutboundMessage.objects.select_related(
        "parent_inbound__connection",
        "contact",
    ).get(pk=message_id)
    if message.state not in {OutboundMessage.State.SENDING, OutboundMessage.State.RECONCILING}:
        return message.state
    connection = _connection_for_outbound(message)
    if connection is None:
        state = _record_outbound_error(
            message.pk,
            ValidationProviderError("No se encontró la conexión Gmail."),
            ambiguous=False,
        )
        if message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
            _complete_scheduled_attempt(message.pk)
        return state
    try:
        active_provider = provider or provider_for_connection(connection, persist_fake=True)
        result = active_provider.find_by_message_id(message.message_id)
    except (ImproperlyConfigured, ValidationError) as exc:
        state = _record_outbound_error(message.pk, exc, ambiguous=False)
        if message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
            _complete_scheduled_attempt(message.pk)
        return state
    except RetryableProviderError as exc:
        return _record_outbound_error(message.pk, exc, ambiguous=True)
    except ProviderError as exc:
        state = _record_outbound_error(message.pk, exc, ambiguous=False)
        if message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
            _complete_scheduled_attempt(message.pk)
        return state
    if result is None:
        state = _record_outbound_error(
            message.pk,
            ValidationProviderError(
                "Gmail confirmó que el identificador del mensaje automático no existe."
            ),
            ambiguous=False,
        )
        if message.kind == OutboundMessage.Kind.SCHEDULED_CONTACT:
            _complete_scheduled_attempt(message.pk)
        return state
    state = _confirm_outbound_sent(message.pk, result, now=timezone.now())
    if message.kind == OutboundMessage.Kind.REFERRED_PROPOSAL:
        execute_reply_decision(
            ReplyDecision.objects.get(inbound_id=message.parent_inbound_id).pk,
            provider=active_provider,
        )
    return state


def pending_reply_decision_ids() -> tuple[uuid.UUID, ...]:
    return tuple(
        ReplyDecision.objects.filter(
            mode=ReplyAutomationConfiguration.Mode.LIVE,
            state__in=(
                ReplyDecision.State.AUTO_ELIGIBLE,
                ReplyDecision.State.AUTHORIZED,
                ReplyDecision.State.EXECUTING,
            ),
        ).values_list("pk", flat=True)
    )


def recoverable_outbound_ids(now: datetime | None = None) -> tuple[uuid.UUID, ...]:
    moment = now or timezone.now()
    queryset = OutboundMessage.objects.filter(
        kind__in=(*AUTOMATIC_KINDS, OutboundMessage.Kind.SCHEDULED_CONTACT),
        delivery_mode=Campaign.DeliveryMode.LIVE,
    )
    pending = queryset.filter(state=OutboundMessage.State.QUEUED).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=moment)
    )
    stale = queryset.filter(
        state=OutboundMessage.State.SENDING,
        sending_started_at__lte=moment - STALE_SENDING_AFTER,
    )
    reconciling = queryset.filter(
        state=OutboundMessage.State.RECONCILING,
        next_attempt_at__lte=moment,
    )
    return (
        tuple(pending.values_list("pk", flat=True))
        + tuple(stale.values_list("pk", flat=True))
        + tuple(reconciling.values_list("pk", flat=True))
    )
