from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from django.core.exceptions import ValidationError

from apps.automation.candidates import split_inbound_regions
from apps.automation.memory import verified_conversation_memory_text
from apps.automation.models import (
    ContactCommunicationPlan,
    ConversationMemory,
    EmailCandidate,
)
from apps.automation.retrieval import (
    MAX_RAG_FACTS,
    current_global_context_revision,
    retrieve_relevant_fact_revisions,
)
from apps.campaigns.models import OutboundMessage
from apps.integrations.contracts import (
    EmailCandidateRef,
    EmbeddingProvider,
    FactRevisionRef,
    ReplyContextBlock,
    ReplyDecisionRequest,
    ScheduledContactDraftRequest,
)
from apps.integrations.llm_inputs import (
    MAX_LLM_INPUT_CHARACTERS,
    manifest_request_metadata,
    reply_decision_input_character_count,
    scheduled_contact_input_character_count,
)
from apps.mailbox.models import InboundMessage

MAX_REPLY_CONTEXT_CHARS = MAX_LLM_INPUT_CHARACTERS
MAX_RECENT_MESSAGES = 6
MAX_FACTS = MAX_RAG_FACTS
_WORDS = re.compile(r"[\wáéíóúüñ]{3,}", re.IGNORECASE)


class MandatoryContextOverflow(ValidationError):
    pass


@dataclass(frozen=True, slots=True)
class BoundedReplyContext:
    blocks: tuple[ReplyContextBlock, ...]
    candidates: tuple[EmailCandidateRef, ...]
    facts: tuple[FactRevisionRef, ...]
    manifest: dict[str, Any]
    context_hash: str
    character_count: int


@dataclass(frozen=True, slots=True)
class BoundedScheduledContactContext:
    blocks: tuple[ReplyContextBlock, ...]
    facts: tuple[FactRevisionRef, ...]
    manifest: dict[str, Any]
    context_hash: str
    character_count: int


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _WORDS.findall(value)}


def _message_block(
    *,
    source_id: object,
    role: str,
    provenance: str,
    text: str,
    mandatory: bool,
) -> ReplyContextBlock:
    return ReplyContextBlock(
        source_id=str(source_id),
        role=role,
        provenance=provenance,
        text=text,
        mandatory=mandatory,
    )


def _original_outbound(inbound: InboundMessage) -> OutboundMessage:
    if inbound.contact_id is None or inbound.conversation_id is None:
        raise ValidationError("La respuesta todavía no está vinculada a un Contacto.")
    parent = inbound.related_outbound
    if parent.state != OutboundMessage.State.SENT:
        raise ValidationError(
            "No se pudo verificar el mensaje enviado al que responde este correo."
        )
    original_kinds = {
        OutboundMessage.Kind.FIRST_CONTACT,
        OutboundMessage.Kind.INITIAL,
        OutboundMessage.Kind.REFERRED_PROPOSAL,
    }
    if parent.kind in original_kinds:
        return parent
    queryset = OutboundMessage.objects.filter(
        contact_id=inbound.contact_id,
        conversation_id=inbound.conversation_id,
        kind__in=original_kinds,
        state=OutboundMessage.State.SENT,
    )
    original = queryset.order_by("sent_at", "created_at").first()
    return original or parent


def _mandatory_blocks(inbound: InboundMessage) -> list[ReplyContextBlock]:
    authored = split_inbound_regions(inbound.body_text).new_content
    authored = authored or inbound.body_text
    blocks = [
        _message_block(
            source_id=inbound.pk,
            role="CLIENT",
            provenance="NEW_INBOUND",
            text=authored,
            mandatory=True,
        )
    ]
    parent = inbound.related_outbound
    original = _original_outbound(inbound)
    if original.pk == parent.pk:
        blocks.append(
            _message_block(
                source_id=original.pk,
                role="WORKSPACE",
                provenance="ORIGINAL_AND_DIRECT_PARENT",
                text=original.body_text,
                mandatory=True,
            )
        )
    else:
        blocks.extend(
            (
                _message_block(
                    source_id=original.pk,
                    role="WORKSPACE",
                    provenance="ORIGINAL_OUTBOUND",
                    text=original.body_text,
                    mandatory=True,
                ),
                _message_block(
                    source_id=parent.pk,
                    role="WORKSPACE",
                    provenance="DIRECT_PARENT",
                    text=parent.body_text,
                    mandatory=True,
                ),
            )
        )
    return blocks


def _recent_blocks(
    inbound: InboundMessage,
    excluded_ids: set[str],
) -> list[ReplyContextBlock]:
    if inbound.contact_id is None:
        return []
    recent: list[tuple[datetime, ReplyContextBlock]] = []
    for inbound_message in (
        InboundMessage.objects.filter(contact_id=inbound.contact_id, is_human=True)
        .exclude(
            classification__in=(
                InboundMessage.Classification.AUTO_REPLY,
                InboundMessage.Classification.BOUNCE,
            )
        )
        .exclude(pk=inbound.pk)
        .order_by("-external_at")[: MAX_RECENT_MESSAGES * 2]
    ):
        source_id = str(inbound_message.pk)
        if source_id in excluded_ids:
            continue
        recent.append(
            (
                inbound_message.external_at,
                _message_block(
                    source_id=inbound_message.pk,
                    role="CLIENT",
                    provenance="RECENT_CONTACT_MESSAGE",
                    text=split_inbound_regions(inbound_message.body_text).new_content
                    or inbound_message.body_text,
                    mandatory=False,
                ),
            )
        )
    for outbound_message in (
        OutboundMessage.objects.filter(
            contact_id=inbound.contact_id,
            state=OutboundMessage.State.SENT,
        )
        .exclude(pk__in=excluded_ids)
        .order_by("-sent_at", "-created_at")[: MAX_RECENT_MESSAGES * 2]
    ):
        source_id = str(outbound_message.pk)
        if source_id in excluded_ids:
            continue
        recent.append(
            (
                outbound_message.sent_at or outbound_message.created_at,
                _message_block(
                    source_id=outbound_message.pk,
                    role="WORKSPACE",
                    provenance="RECENT_CONTACT_MESSAGE",
                    text=outbound_message.body_text,
                    mandatory=False,
                ),
            )
        )
    recent.sort(key=lambda item: item[0], reverse=True)
    return [block for _, block in recent[:MAX_RECENT_MESSAGES]]


def _memory_blocks(inbound: InboundMessage) -> list[ReplyContextBlock]:
    if inbound.contact_id is None:
        return []
    memory = (
        ConversationMemory.objects.filter(contact_id=inbound.contact_id, superseded_at__isnull=True)
        .order_by("-version")
        .first()
    )
    if memory is None:
        return []
    text = verified_conversation_memory_text(memory)
    if text is None:
        return []
    return [
        _message_block(
            source_id=memory.pk,
            role="MEMORY",
            provenance="SOURCE_LINKED_MEMORY",
            text=text,
            mandatory=False,
        )
    ]


def _global_context_blocks(workspace_id: uuid.UUID | str) -> list[ReplyContextBlock]:
    revision = current_global_context_revision(workspace_id)
    if revision is None:
        return []
    return [
        _message_block(
            source_id=revision.pk,
            role="WORKSPACE",
            provenance="GLOBAL_APPROVED_CONTEXT",
            text=revision.context_text,
            mandatory=True,
        )
    ]


def reply_candidate_refs(inbound: InboundMessage) -> tuple[EmailCandidateRef, ...]:
    return tuple(
        EmailCandidateRef(
            candidate_id=str(candidate.pk),
            normalized_email=candidate.normalized_email,
            region=candidate.region,
            validation_state=(
                candidate.mx_state
                if candidate.mx_state != EmailCandidate.CheckState.PENDING
                else candidate.syntax_state
            ),
        )
        for candidate in inbound.email_candidates.order_by("created_at")
    )


def _manifest_for(
    blocks: list[ReplyContextBlock],
    facts: list[FactRevisionRef],
    *,
    knowledge_retrieval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "blocks": [
            {
                "source_id": block.source_id,
                "role": block.role,
                "provenance": block.provenance,
                "mandatory": block.mandatory,
                "characters": len(block.text),
                "sha256": sha256(block.text.encode()).hexdigest(),
            }
            for block in blocks
        ],
        "facts": [
            {
                "revision_id": fact.revision_id,
                "version": fact.version,
                "characters": len(fact.text),
                "sha256": sha256(fact.text.encode()).hexdigest(),
            }
            for fact in facts
        ],
    }
    if knowledge_retrieval is not None:
        manifest["knowledge_retrieval"] = knowledge_retrieval
    return manifest


def build_bounded_reply_context(
    inbound: InboundMessage,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    policy_version: str = "2026-07",
    schema_version: str = "1",
    max_characters: int = MAX_REPLY_CONTEXT_CHARS,
) -> BoundedReplyContext:
    if inbound.contact_id is None or inbound.conversation_id is None:
        raise ValidationError("La respuesta todavía no está vinculada a un Contacto.")
    contact = inbound.contact
    if contact is None:
        raise ValidationError("La respuesta todavía no está vinculada a un Contacto.")
    mandatory = _mandatory_blocks(inbound) + _global_context_blocks(contact.workspace_id)
    candidates = reply_candidate_refs(inbound)

    def input_count(blocks: list[ReplyContextBlock], facts: list[FactRevisionRef]) -> int:
        return reply_decision_input_character_count(
            ReplyDecisionRequest(
                context=tuple(blocks),
                candidates=candidates,
                facts=tuple(facts),
                correlation_id="",
                idempotency_key="",
                policy_version=policy_version,
                schema_version=schema_version,
            )
        )

    mandatory_count = input_count(mandatory, [])
    if mandatory_count > max_characters:
        raise MandatoryContextOverflow(
            "El mensaje y sus antecedentes directos son demasiado extensos para responder "
            "automáticamente."
        )

    blocks = list(mandatory)
    excluded_ids = {block.source_id for block in mandatory}
    optional = _recent_blocks(inbound, excluded_ids) + _memory_blocks(inbound)
    for block in optional:
        candidate_blocks = [*blocks, block]
        if input_count(candidate_blocks, []) > max_characters:
            continue
        blocks = candidate_blocks

    facts: list[FactRevisionRef] = []
    authored_block = next(block for block in mandatory if block.provenance == "NEW_INBOUND")
    retrieval = retrieve_relevant_fact_revisions(
        workspace_id=contact.workspace_id,
        query_text=authored_block.text,
        provider=embedding_provider,
    )
    for revision in retrieval.revisions:
        fact = FactRevisionRef(
            revision_id=str(revision.pk),
            version=revision.version,
            text=revision.text,
        )
        candidate_facts = [*facts, fact]
        if input_count(blocks, candidate_facts) > max_characters:
            continue
        facts = candidate_facts

    used = input_count(blocks, facts)
    manifest = _manifest_for(blocks, facts, knowledge_retrieval=retrieval.manifest)
    manifest["request"] = manifest_request_metadata(
        character_count=used,
        candidates=tuple(
            {
                "candidate_id": candidate.candidate_id,
                "region": candidate.region,
                "validation_state": candidate.validation_state,
            }
            for candidate in candidates
        ),
        policy_version=policy_version,
        schema_version=schema_version,
    )
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return BoundedReplyContext(
        blocks=tuple(blocks),
        candidates=candidates,
        facts=tuple(facts),
        manifest=manifest,
        context_hash=sha256(canonical.encode()).hexdigest(),
        character_count=used,
    )


_SCHEDULED_PURPOSE_TEXT: dict[str, str] = {
    "CHECK_IN": (
        "Retomar el contacto de manera cordial y preguntar cómo están, sin asumir una compra "
        "ni una experiencia concreta."
    ),
    "PRODUCT_FEEDBACK": (
        "Pedir una opinión general sobre el producto o la atención, sin asumir qué compró "
        "ni cómo fue su experiencia."
    ),
}


def _scheduled_mandatory_blocks(
    plan: ContactCommunicationPlan,
) -> list[ReplyContextBlock]:
    contact = plan.contact
    purpose_text = _SCHEDULED_PURPOSE_TEXT.get(plan.purpose, plan.goal_text.strip())
    if not purpose_text:
        raise ValidationError("El seguimiento no tiene un objetivo claro.")
    profile_parts = []
    if contact.name:
        profile_parts.append(f"Nombre del contacto: {contact.name}")
    if contact.organization.name:
        profile_parts.append(f"Empresa: {contact.organization.name}")
    profile_parts.append(f"Email elegido: {plan.preferred_email.normalized_email}")
    return [
        _message_block(
            source_id=plan.pk,
            role="WORKSPACE",
            provenance="SCHEDULED_PURPOSE",
            text=purpose_text,
            mandatory=True,
        ),
        _message_block(
            source_id=contact.pk,
            role="CONTACT_PROFILE",
            provenance="CONTACT_RECORD",
            text="\n".join(profile_parts),
            mandatory=True,
        ),
    ]


def _scheduled_recent_blocks(
    plan: ContactCommunicationPlan,
    *,
    query_text: str,
) -> list[ReplyContextBlock]:
    contact = plan.contact
    query_tokens = _tokens(query_text)
    recent: list[tuple[int, datetime, ReplyContextBlock]] = []
    inbound_messages = (
        InboundMessage.objects.filter(contact=contact, is_human=True)
        .exclude(
            classification__in=(
                InboundMessage.Classification.AUTO_REPLY,
                InboundMessage.Classification.BOUNCE,
            )
        )
        .order_by("-external_at")[: MAX_RECENT_MESSAGES * 3]
    )
    for inbound in inbound_messages:
        text = split_inbound_regions(inbound.body_text).new_content or inbound.body_text
        recent.append(
            (
                len(query_tokens & _tokens(text)),
                inbound.external_at,
                _message_block(
                    source_id=inbound.pk,
                    role="CLIENT",
                    provenance="RECENT_CONTACT_MESSAGE",
                    text=text,
                    mandatory=False,
                ),
            )
        )
    outbound_messages = OutboundMessage.objects.filter(
        contact=contact,
        state=OutboundMessage.State.SENT,
    ).order_by("-sent_at")[: MAX_RECENT_MESSAGES * 3]
    for outbound in outbound_messages:
        recent.append(
            (
                len(query_tokens & _tokens(outbound.body_text)),
                outbound.sent_at or outbound.created_at,
                _message_block(
                    source_id=outbound.pk,
                    role="WORKSPACE",
                    provenance="RECENT_CONTACT_MESSAGE",
                    text=outbound.body_text,
                    mandatory=False,
                ),
            )
        )
    recent.sort(key=lambda item: (item[0] > 0, item[0], item[1]), reverse=True)
    return [block for _, _, block in recent[:MAX_RECENT_MESSAGES]]


def _scheduled_memory_blocks(plan: ContactCommunicationPlan) -> list[ReplyContextBlock]:
    memory = (
        ConversationMemory.objects.filter(contact=plan.contact, superseded_at__isnull=True)
        .order_by("-version")
        .first()
    )
    if memory is None:
        return []
    text = verified_conversation_memory_text(memory)
    if text is None:
        return []
    return [
        _message_block(
            source_id=memory.pk,
            role="MEMORY",
            provenance="SOURCE_LINKED_MEMORY",
            text=text,
            mandatory=False,
        )
    ]


def build_bounded_scheduled_contact_context(
    plan: ContactCommunicationPlan,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    goal: str | None = None,
    schema_version: str = "1",
    max_characters: int = MAX_REPLY_CONTEXT_CHARS,
) -> BoundedScheduledContactContext:
    """Build cross-thread relationship context without PDFs, HTML, or giant prompts."""

    if plan.contact_id is None or plan.preferred_email_id is None:
        raise ValidationError("El seguimiento necesita un contacto y un email preferido.")
    scheduled_mandatory = _scheduled_mandatory_blocks(plan)
    mandatory = scheduled_mandatory + _global_context_blocks(plan.contact.workspace_id)
    request_goal = (
        goal
        if goal is not None
        else _SCHEDULED_PURPOSE_TEXT.get(plan.purpose, plan.goal_text.strip())
    )

    def input_count(blocks: list[ReplyContextBlock], facts: list[FactRevisionRef]) -> int:
        return scheduled_contact_input_character_count(
            ScheduledContactDraftRequest(
                context=tuple(blocks),
                facts=tuple(facts),
                purpose=plan.purpose,
                goal=request_goal,
                correlation_id="",
                idempotency_key="",
                schema_version=schema_version,
            )
        )

    mandatory_count = input_count(mandatory, [])
    if mandatory_count > max_characters:
        raise MandatoryContextOverflow(
            "El objetivo obligatorio es demasiado extenso para generar un mensaje seguro."
        )
    query_text = " ".join(block.text for block in scheduled_mandatory)
    blocks = list(mandatory)
    for block in _scheduled_recent_blocks(plan, query_text=query_text) + _scheduled_memory_blocks(
        plan
    ):
        candidate_blocks = [*blocks, block]
        if input_count(candidate_blocks, []) > max_characters:
            continue
        blocks = candidate_blocks

    facts: list[FactRevisionRef] = []
    retrieval = retrieve_relevant_fact_revisions(
        workspace_id=plan.contact.workspace_id,
        query_text=query_text,
        provider=embedding_provider,
    )
    for revision in retrieval.revisions:
        fact = FactRevisionRef(
            revision_id=str(revision.pk),
            version=revision.version,
            text=revision.text,
        )
        candidate_facts = [*facts, fact]
        if input_count(blocks, candidate_facts) > max_characters:
            continue
        facts = candidate_facts

    used = input_count(blocks, facts)
    manifest = _manifest_for(blocks, facts, knowledge_retrieval=retrieval.manifest)
    manifest["request"] = manifest_request_metadata(
        character_count=used,
        schema_version=schema_version,
        purpose=plan.purpose,
        goal_hash=sha256(request_goal.encode()).hexdigest(),
    )
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return BoundedScheduledContactContext(
        blocks=tuple(blocks),
        facts=tuple(facts),
        manifest=manifest,
        context_hash=sha256(canonical.encode()).hexdigest(),
        character_count=used,
    )
