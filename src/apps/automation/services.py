from __future__ import annotations

import secrets
import uuid
from functools import partial
from hashlib import sha256
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import Workspace
from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.automation.candidates import persist_email_candidates
from apps.automation.context import MandatoryContextOverflow, build_bounded_reply_context
from apps.automation.memory import refresh_contact_memory
from apps.automation.models import (
    EmailCandidate,
    HumanTask,
    KnowledgeFact,
    KnowledgeFactRevision,
    ReplyAutomationConfiguration,
    ReplyDecision,
    WorkspaceKnowledgeContextRevision,
)
from apps.compliance.models import SuppressionEntry
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import IntegrationConfiguration
from apps.configuration.services import runtime_prompt_configuration
from apps.contacts.models import EmailAddress
from apps.integrations.contracts import (
    LLMProvider,
    ProviderError,
    ReplyDecisionRequest,
    ReplyDecisionResult,
)
from apps.integrations.factory import get_llm_provider
from apps.prospects.email_validation import DNSMXResolver, MXResolver, MXStatus

AUTO_INTENTS = frozenset(
    {
        "APPROVED_PRODUCT_INFORMATION",
        "APPROVED_COMPANY_FACT",
        "GROUNDED_SIMPLE_CLARIFICATION",
        "EXPLICIT_PROPOSAL_REDIRECTION",
    }
)
AUTO_ACTIONS = frozenset({"REPLY", "REDIRECT_PROPOSAL"})
NO_ACTION_INTENTS = frozenset({"POLITE_ACKNOWLEDGEMENT", "NOT_INTERESTED"})
HUMAN_REQUIRED_INTENT_REASONS = {
    "MEETING_OR_DATE": "MEETING_OR_DATE",
    "PRICING_OR_QUOTE": "PRICING_OR_QUOTE",
    "NEGOTIATION": "NEGOTIATION",
    "COMPLAINT": "COMPLAINT",
    "LEGAL_OR_PRIVACY": "LEGAL_OR_PRIVACY",
    "UNSUPPORTED_TECHNICAL_ADVICE": "UNSUPPORTED_TECHNICAL_ADVICE",
    "MULTIPLE_OR_AMBIGUOUS": "MULTIPLE_INTENTS",
    "INSUFFICIENT_CONTEXT": "INSUFFICIENT_CONTEXT",
}
MIN_AUTOMATIC_CONFIDENCE = 0.90


@transaction.atomic
def set_non_live_mode(
    *,
    workspace: Workspace,
    actor: User,
    mode: str,
) -> ReplyAutomationConfiguration:
    require_user_capability(actor, Capability.MANAGE_AUTOMATION, workspace_id=workspace.pk)
    if mode not in {
        ReplyAutomationConfiguration.Mode.OFF,
        ReplyAutomationConfiguration.Mode.SHADOW,
    }:
        raise ValidationError("El modo solicitado no está disponible desde esta acción.")
    configuration, _ = ReplyAutomationConfiguration.objects.select_for_update().get_or_create(
        workspace=workspace
    )
    configuration.mode = mode
    configuration.live_enabled_at = None
    configuration.live_enabled_by = None
    configuration.save(update_fields=("mode", "live_enabled_at", "live_enabled_by", "updated_at"))
    record_event(
        action="automation.mode_changed",
        entity=configuration,
        actor=actor,
        after={"mode": mode},
    )
    return configuration


@transaction.atomic
def set_live_mode(
    *,
    workspace: Workspace,
    actor: User,
    reauthenticated: bool,
) -> ReplyAutomationConfiguration:
    require_user_capability(actor, Capability.MANAGE_AUTOMATION, workspace_id=workspace.pk)
    if not reauthenticated:
        raise PermissionDenied("Volvé a ingresar tu contraseña antes de activar respuestas.")
    configuration, _ = ReplyAutomationConfiguration.objects.select_for_update().get_or_create(
        workspace=workspace
    )
    configuration.mode = ReplyAutomationConfiguration.Mode.LIVE
    configuration.live_enabled_at = timezone.now()
    configuration.live_enabled_by = actor
    configuration.save(update_fields=("mode", "live_enabled_at", "live_enabled_by", "updated_at"))
    record_event(
        action="automation.live_enabled",
        entity=configuration,
        actor=actor,
        after={"mode": ReplyAutomationConfiguration.Mode.LIVE},
    )
    return configuration


@transaction.atomic
def save_global_knowledge_context(
    *,
    workspace: Workspace,
    actor: User,
    context_text: str,
) -> WorkspaceKnowledgeContextRevision:
    revision = create_global_knowledge_context_revision(
        workspace=workspace,
        actor=actor,
        context_text=context_text,
        source_notes="",
    )
    return approve_global_knowledge_context_revision(revision, actor=actor)


@transaction.atomic
def create_knowledge_revision(
    *,
    workspace: Workspace,
    actor: User,
    title: str,
    category: str,
    text: str,
    source_notes: str = "",
) -> KnowledgeFactRevision:
    require_user_capability(actor, Capability.MANAGE_KNOWLEDGE, workspace_id=workspace.pk)
    clean_title = title.strip()
    clean_text = text.strip()
    if not clean_title or not clean_text:
        raise ValidationError("Completá el título y la información que se puede usar.")
    fact, _ = KnowledgeFact.objects.select_for_update().get_or_create(
        workspace=workspace,
        title=clean_title,
        defaults={"category": category.strip(), "created_by": actor},
    )
    latest = KnowledgeFactRevision.objects.filter(fact=fact).order_by("-version").first()
    version = (latest.version if latest else 0) + 1
    revision = KnowledgeFactRevision.objects.create(
        fact=fact,
        version=version,
        text=clean_text,
        source_notes=source_notes.strip(),
        content_hash=sha256(clean_text.encode()).hexdigest(),
    )
    record_event(
        action="knowledge.revision_created",
        entity=revision,
        actor=actor,
        after={"fact_id": str(fact.pk), "version": version},
    )
    return revision


@transaction.atomic
def save_knowledge_revision(
    *,
    workspace: Workspace,
    actor: User,
    title: str,
    category: str,
    text: str,
    source_notes: str = "",
) -> KnowledgeFactRevision:
    revision = create_knowledge_revision(
        workspace=workspace,
        actor=actor,
        title=title,
        category=category,
        text=text,
        source_notes=source_notes,
    )
    return approve_knowledge_revision(revision, actor=actor)


@transaction.atomic
def approve_knowledge_revision(
    revision: KnowledgeFactRevision,
    *,
    actor: User,
) -> KnowledgeFactRevision:
    locked = (
        KnowledgeFactRevision.objects.select_for_update().select_related("fact").get(pk=revision.pk)
    )
    require_user_capability(
        actor,
        Capability.MANAGE_KNOWLEDGE,
        workspace_id=locked.fact.workspace_id,
    )
    if locked.approved_at is not None:
        return locked
    now = timezone.now()
    KnowledgeFactRevision.objects.filter(
        fact=locked.fact,
        approved_at__isnull=False,
        superseded_at__isnull=True,
    ).update(superseded_at=now)
    locked.approved_at = now
    locked.approved_by = actor
    locked.save(update_fields=("approved_at", "approved_by", "updated_at"))
    record_event(
        action="knowledge.revision_approved",
        entity=locked,
        actor=actor,
        after={"version": locked.version},
    )
    from apps.automation.tasks import refresh_knowledge_revision_embedding_task

    transaction.on_commit(partial(refresh_knowledge_revision_embedding_task.delay, str(locked.pk)))
    return locked


@transaction.atomic
def create_global_knowledge_context_revision(
    *,
    workspace: Workspace,
    actor: User,
    context_text: str,
    source_notes: str = "",
) -> WorkspaceKnowledgeContextRevision:
    require_user_capability(actor, Capability.MANAGE_KNOWLEDGE, workspace_id=workspace.pk)
    clean_context = context_text.strip()
    if not clean_context:
        raise ValidationError("Escribí el contexto general antes de guardarlo.")
    latest = (
        WorkspaceKnowledgeContextRevision.objects.select_for_update()
        .filter(workspace=workspace)
        .order_by("-version")
        .first()
    )
    version = (latest.version if latest else 0) + 1
    revision = WorkspaceKnowledgeContextRevision.objects.create(
        workspace=workspace,
        version=version,
        context_text=clean_context,
        source_notes=source_notes.strip(),
        content_hash=sha256(clean_context.encode()).hexdigest(),
    )
    record_event(
        action="knowledge.global_context_revision_created",
        entity=revision,
        actor=actor,
        after={"version": version},
    )
    return revision


@transaction.atomic
def approve_global_knowledge_context_revision(
    revision: WorkspaceKnowledgeContextRevision,
    *,
    actor: User,
) -> WorkspaceKnowledgeContextRevision:
    locked = WorkspaceKnowledgeContextRevision.objects.select_for_update().get(pk=revision.pk)
    require_user_capability(
        actor,
        Capability.MANAGE_KNOWLEDGE,
        workspace_id=locked.workspace_id,
    )
    if locked.approved_at is not None:
        return locked
    now = timezone.now()
    WorkspaceKnowledgeContextRevision.objects.filter(
        workspace=locked.workspace,
        approved_at__isnull=False,
        superseded_at__isnull=True,
    ).update(superseded_at=now)
    locked.approved_at = now
    locked.approved_by = actor
    locked.save(update_fields=("approved_at", "approved_by", "updated_at"))
    record_event(
        action="knowledge.global_context_revision_approved",
        entity=locked,
        actor=actor,
        after={"version": locked.version},
    )
    return locked


@transaction.atomic
def open_human_task(
    *,
    workspace: Workspace,
    contact_id: uuid.UUID | str,
    conversation_id: uuid.UUID | str | None,
    kind: str,
    reason: str,
    friendly_summary: str,
    inbound_id: uuid.UUID | str | None = None,
    decision_id: uuid.UUID | str | None = None,
) -> HumanTask:
    from apps.contacts.models import Contact, Conversation

    contact = Contact.objects.select_for_update().get(pk=contact_id, workspace=workspace)
    conversation = None
    if conversation_id is not None:
        conversation = Conversation.objects.select_for_update().get(
            pk=conversation_id,
            contact=contact,
            workspace=workspace,
        )
    lookup: dict[str, Any] = {
        "workspace": workspace,
        "contact": contact,
        "conversation": conversation,
        "kind": kind,
        "reason": reason,
        "status": HumanTask.Status.OPEN,
    }
    if inbound_id is not None:
        lookup["inbound_id"] = uuid.UUID(str(inbound_id))
    try:
        task, created = HumanTask.objects.get_or_create(
            **lookup,
            defaults={
                "decision_id": decision_id,
                "friendly_summary": friendly_summary,
                "opened_at": timezone.now(),
            },
        )
    except IntegrityError:
        fallback = HumanTask.objects.filter(
            contact=contact,
            conversation=conversation,
            kind=kind,
            reason=reason,
            status=HumanTask.Status.OPEN,
        )
        if inbound_id is not None:
            fallback = fallback.filter(inbound_id=uuid.UUID(str(inbound_id)))
        task = fallback.get()
        created = False
    if conversation is not None and not conversation.automation_suspended:
        conversation.automation_suspended = True
        conversation.save(update_fields=("automation_suspended", "updated_at"))
    elif conversation is None and not contact.automation_suspended:
        contact.automation_suspended = True
        contact.save(update_fields=("automation_suspended", "updated_at"))
    if created:
        record_event(
            action="human_task.opened",
            entity=task,
            actor=None,
            after={"reason": reason},
        )
        from apps.automation.notifications import ensure_notification_deliveries

        transaction.on_commit(partial(ensure_notification_deliveries, task.pk))
    return task


def _human_task_lock_queryset() -> QuerySet[HumanTask]:
    # Conversation can be NULL; only lock the task row to avoid FOR UPDATE on an outer join.
    return HumanTask.objects.select_for_update(of=("self",)).select_related(
        "contact",
        "conversation",
    )


def _resume_automation_after_task_close(task: HumanTask) -> None:
    if task.conversation is not None:
        still_open = HumanTask.objects.filter(
            conversation=task.conversation,
            status=HumanTask.Status.OPEN,
        ).exists()
        if not still_open:
            task.conversation.automation_suspended = False
            task.conversation.save(update_fields=("automation_suspended", "updated_at"))
    else:
        still_open = HumanTask.objects.filter(
            contact=task.contact,
            conversation__isnull=True,
            status=HumanTask.Status.OPEN,
        ).exists()
        if not still_open:
            task.contact.automation_suspended = False
            task.contact.save(update_fields=("automation_suspended", "updated_at"))


@transaction.atomic
def close_human_task(
    task: HumanTask,
    *,
    actor: User,
    dismiss: bool,
    note: str,
) -> HumanTask:
    locked = _human_task_lock_queryset().get(pk=task.pk)
    require_user_capability(
        actor,
        Capability.MANAGE_AUTOMATION,
        workspace_id=locked.workspace.pk,
    )
    if locked.status != HumanTask.Status.OPEN:
        return locked
    if not note.strip():
        raise ValidationError("Contanos brevemente cómo se resolvió.")
    locked.status = HumanTask.Status.DISMISSED if dismiss else HumanTask.Status.RESOLVED
    locked.resolved_at = timezone.now()
    locked.resolved_by = actor
    locked.resolution_note = note.strip()
    locked.save(
        update_fields=(
            "status",
            "resolved_at",
            "resolved_by",
            "resolution_note",
            "updated_at",
        )
    )
    _resume_automation_after_task_close(locked)
    record_event(
        action="human_task.dismissed" if dismiss else "human_task.resolved",
        entity=locked,
        actor=actor,
        after={"status": locked.status},
    )
    return locked


@transaction.atomic
def resolve_reply_review_tasks_for_manual_reply(
    *,
    inbound_id: uuid.UUID | str,
    workspace_id: uuid.UUID | str,
    actor: User,
) -> int:
    tasks = list(
        _human_task_lock_queryset().filter(
            workspace_id=workspace_id,
            inbound_id=uuid.UUID(str(inbound_id)),
            kind="REPLY_REVIEW",
            status=HumanTask.Status.OPEN,
        )
    )
    resolved = 0
    for task in tasks:
        task.status = HumanTask.Status.RESOLVED
        task.resolved_at = timezone.now()
        task.resolved_by = actor
        task.resolution_note = "Respondida manualmente."
        task.save(
            update_fields=(
                "status",
                "resolved_at",
                "resolved_by",
                "resolution_note",
                "updated_at",
            )
        )
        _resume_automation_after_task_close(task)
        record_event(
            action="human_task.resolved",
            entity=task,
            actor=actor,
            after={"status": task.status, "source": "manual_reply"},
        )
        resolved += 1
    return resolved


@transaction.atomic
def assess_email_candidates(
    candidates: tuple[EmailCandidate, ...],
    *,
    workspace: Workspace,
    contact_organization_id: uuid.UUID | str,
    resolver: MXResolver,
) -> tuple[EmailCandidate, ...]:
    assessed: list[EmailCandidate] = []
    for candidate in candidates:
        locked = EmailCandidate.objects.select_for_update().get(pk=candidate.pk)
        domain = locked.normalized_email.rsplit("@", 1)[-1]
        mx_status = resolver.resolve(domain)
        locked.mx_state = {
            MXStatus.VALID: EmailCandidate.CheckState.VALID,
            MXStatus.INVALID: EmailCandidate.CheckState.INVALID,
            MXStatus.TRANSIENT: EmailCandidate.CheckState.TRANSIENT,
        }[mx_status]
        legacy_restricted = SuppressionEntry.objects.filter(
            normalized_email=locked.normalized_email
        ).exists()
        email = EmailAddress.objects.filter(
            workspace=workspace,
            normalized_email=locked.normalized_email,
        ).first()
        restricted = legacy_restricted
        if email is not None:
            restricted = restricted or email.restrictions.filter(revoked_at__isnull=True).exists()
            locked.resolved_email_address = email
            locked.ownership_state = (
                EmailCandidate.CheckState.VALID
                if str(email.organization_id) == str(contact_organization_id)
                else EmailCandidate.CheckState.CONFLICT
            )
        else:
            locked.ownership_state = EmailCandidate.CheckState.VALID
        locked.restriction_state = (
            EmailCandidate.CheckState.RESTRICTED if restricted else EmailCandidate.CheckState.VALID
        )
        locked.save(
            update_fields=(
                "mx_state",
                "restriction_state",
                "ownership_state",
                "resolved_email_address",
                "updated_at",
            )
        )
        assessed.append(locked)
    return tuple(assessed)


def _policy_result(
    result: ReplyDecisionResult,
    *,
    candidates: tuple[EmailCandidate, ...],
) -> tuple[str, str]:
    if result.intent in HUMAN_REQUIRED_INTENT_REASONS:
        return (
            ReplyDecision.State.HUMAN_REQUIRED,
            HUMAN_REQUIRED_INTENT_REASONS[result.intent],
        )
    if result.intent in NO_ACTION_INTENTS:
        return ReplyDecision.State.NO_ACTION, ""
    if result.action == "HUMAN":
        return ReplyDecision.State.HUMAN_REQUIRED, result.human_reason or "INSUFFICIENT_CONTEXT"
    if result.action == "NO_ACTION":
        return ReplyDecision.State.HUMAN_REQUIRED, "INSUFFICIENT_CONTEXT"
    if (
        result.intent not in AUTO_INTENTS
        or result.action not in AUTO_ACTIONS
        or result.confidence < MIN_AUTOMATIC_CONFIDENCE
    ):
        return ReplyDecision.State.HUMAN_REQUIRED, "INSUFFICIENT_CONTEXT"
    if result.action == "REDIRECT_PROPOSAL":
        new_candidates = [
            item for item in candidates if item.region == EmailCandidate.Region.NEW_CONTENT
        ]
        selected = next(
            (item for item in candidates if str(item.pk) == result.candidate_id),
            None,
        )
        if len(new_candidates) != 1 or selected is None:
            return ReplyDecision.State.HUMAN_REQUIRED, "AMBIGUOUS_CANDIDATE"
        if selected.ownership_state == EmailCandidate.CheckState.CONFLICT:
            return ReplyDecision.State.HUMAN_REQUIRED, "OWNERSHIP_CONFLICT"
        if any(
            state != EmailCandidate.CheckState.VALID
            for state in (
                selected.syntax_state,
                selected.mx_state,
                selected.restriction_state,
                selected.ownership_state,
            )
        ):
            return ReplyDecision.State.HUMAN_REQUIRED, "AMBIGUOUS_CANDIDATE"
    return ReplyDecision.State.AUTO_ELIGIBLE, ""


def _validate_result_references(
    result: ReplyDecisionResult,
    *,
    candidates: tuple[EmailCandidate, ...],
    fact_ids: frozenset[str],
) -> None:
    candidate_ids = {str(candidate.pk) for candidate in candidates}
    if result.candidate_id is not None and result.candidate_id not in candidate_ids:
        raise ValueError("La decisión devolvió un email que no estaba en la solicitud.")
    if result.action == "REDIRECT_PROPOSAL" and result.candidate_id is None:
        raise ValueError("La redirección no eligió un email de la solicitud.")
    if result.action != "REDIRECT_PROPOSAL" and result.candidate_id is not None:
        raise ValueError("La decisión eligió un email para una acción que no lo admite.")
    selected_fact_ids = tuple(result.fact_revision_ids)
    if len(selected_fact_ids) != len(set(selected_fact_ids)):
        raise ValueError("La decisión repitió una referencia de información.")
    if not set(selected_fact_ids).issubset(fact_ids):
        raise ValueError("La decisión citó información que no estaba en la solicitud.")
    if result.action == "REPLY" and (
        not selected_fact_ids or not (result.proposed_body or "").strip()
    ):
        raise ValueError("La respuesta automática no quedó fundamentada en información aprobada.")
    if result.action == "REDIRECT_PROPOSAL" and selected_fact_ids:
        raise ValueError("Una redirección explícita no debe agregar información no solicitada.")


def _failure_decision(
    *,
    inbound: Any,
    configuration: ReplyAutomationConfiguration,
    provider_name: str,
    model_name: str,
    error: Exception,
) -> ReplyDecision:
    with transaction.atomic():
        decision, _ = ReplyDecision.objects.get_or_create(
            inbound=inbound,
            defaults={
                "workspace": inbound.contact.workspace,
                "contact": inbound.contact,
                "conversation": inbound.conversation,
                "mode": configuration.mode,
                "provider": provider_name,
                "model": model_name,
                "policy_version": configuration.policy_version,
                "classification": "OTHER",
                "intent": "INSUFFICIENT_CONTEXT",
                "action": "HUMAN",
                "confidence": 0,
                "human_reason": "PROVIDER_OR_SCHEMA_FAILURE",
                "context_manifest": {},
                "context_hash": sha256(str(inbound.pk).encode()).hexdigest(),
                "state": ReplyDecision.State.FAILED,
                "error": error.__class__.__name__,
            },
        )
        open_human_task(
            workspace=inbound.contact.workspace,
            contact_id=inbound.contact_id,
            conversation_id=inbound.conversation_id,
            inbound_id=inbound.pk,
            decision_id=decision.pk,
            kind="REPLY_REVIEW",
            reason="PROVIDER_OR_SCHEMA_FAILURE",
            friendly_summary="No pudimos analizar esta respuesta. Revisala antes de continuar.",
        )
        return decision


def process_inbound_decision(
    inbound_id: uuid.UUID | str,
    *,
    provider: LLMProvider | None = None,
    resolver: MXResolver | None = None,
) -> ReplyDecision | None:
    from apps.mailbox.models import InboundMessage

    inbound = InboundMessage.objects.select_related(
        "contact__workspace",
        "contact__organization",
        "conversation",
        "related_outbound__campaign",
    ).get(pk=inbound_id)
    contact = inbound.contact
    conversation = inbound.conversation
    if inbound.contact_id is None or inbound.conversation_id is None or contact is None:
        return None
    if conversation is None:
        return None
    configuration, _ = ReplyAutomationConfiguration.objects.get_or_create(
        workspace=contact.workspace
    )
    if configuration.mode == ReplyAutomationConfiguration.Mode.OFF:
        return None
    existing = ReplyDecision.objects.filter(inbound=inbound).first()
    if existing is not None:
        return existing
    candidates = persist_email_candidates(inbound)
    candidates = assess_email_candidates(
        candidates,
        workspace=contact.workspace,
        contact_organization_id=contact.organization_id,
        resolver=resolver or DNSMXResolver(),
    )
    root = inbound.related_outbound
    campaign = root.campaign if root is not None else None
    owner_id: int | None
    if campaign is not None:
        provider_name = campaign.llm_provider
        model_name = campaign.llm_model
        base_url = campaign.llm_base_url
        owner_id = campaign.created_by_id
    else:
        integration = IntegrationConfiguration.objects.filter(workspace=contact.workspace).first()
        owner_id = integration.owner_id if integration is not None else None
        runtime = runtime_integration_configuration(owner_id)
        provider_name = runtime.llm_provider
        model_name = runtime.llm_model
        base_url = runtime.llm_base_url()
    prompt_runtime = runtime_prompt_configuration(owner_id)
    try:
        refresh_contact_memory(contact.pk)
        context = build_bounded_reply_context(
            inbound,
            policy_version=configuration.policy_version,
            writing_instructions=prompt_runtime.automatic_reply_prompt,
        )
        selected_provider = provider or get_llm_provider(
            provider_name,
            base_url=base_url,
            model=model_name,
            owner_id=owner_id,
        )
        request = ReplyDecisionRequest(
            context=context.blocks,
            candidates=context.candidates,
            facts=context.facts,
            correlation_id=secrets.token_hex(16),
            idempotency_key=f"reply-decision:{inbound.gmail_message_id}",
            policy_version=configuration.policy_version,
            writing_instructions=prompt_runtime.automatic_reply_prompt,
        )
        result = selected_provider.decide_reply(request)
        _validate_result_references(
            result,
            candidates=candidates,
            fact_ids=frozenset(fact.revision_id for fact in context.facts),
        )
    except (MandatoryContextOverflow, ProviderError, ValueError, ValidationError) as exc:
        return _failure_decision(
            inbound=inbound,
            configuration=configuration,
            provider_name=provider_name,
            model_name=model_name,
            error=exc,
        )

    policy_state, human_reason = _policy_result(result, candidates=candidates)
    state = (
        ReplyDecision.State.SHADOW_RECORDED
        if configuration.mode == ReplyAutomationConfiguration.Mode.SHADOW
        else policy_state
    )
    with transaction.atomic():
        try:
            decision = ReplyDecision.objects.create(
                workspace=contact.workspace,
                inbound=inbound,
                contact=contact,
                conversation=conversation,
                mode=configuration.mode,
                provider=provider_name,
                model=model_name,
                policy_version=configuration.policy_version,
                classification=result.classification,
                intent=result.intent,
                action=result.action,
                confidence=result.confidence,
                candidate_id=result.candidate_id,
                proposed_body=result.proposed_body or "",
                human_reason=human_reason or result.human_reason or "",
                context_manifest=context.manifest,
                context_hash=context.context_hash,
                state=state,
            )
        except IntegrityError:
            return ReplyDecision.objects.get(inbound=inbound)
        selected_fact_ids = set(result.fact_revision_ids)
        decision.selected_facts.set(KnowledgeFactRevision.objects.filter(pk__in=selected_fact_ids))
        if configuration.mode == ReplyAutomationConfiguration.Mode.LIVE and policy_state == (
            ReplyDecision.State.HUMAN_REQUIRED
        ):
            open_human_task(
                workspace=contact.workspace,
                contact_id=inbound.contact_id,
                conversation_id=inbound.conversation_id,
                inbound_id=inbound.pk,
                decision_id=decision.pk,
                kind="REPLY_REVIEW",
                reason=human_reason,
                friendly_summary="Esta conversación necesita que la revise una persona.",
            )
        elif (
            configuration.mode == ReplyAutomationConfiguration.Mode.LIVE
            and policy_state == ReplyDecision.State.AUTO_ELIGIBLE
        ):
            from apps.automation.tasks import execute_reply_decision_task

            transaction.on_commit(partial(execute_reply_decision_task.delay, str(decision.pk)))
        record_event(
            action="reply_decision.recorded",
            entity=decision,
            actor=None,
            after={
                "mode": decision.mode,
                "state": decision.state,
                "intent": decision.intent,
                "action": decision.action,
            },
        )
    return decision
