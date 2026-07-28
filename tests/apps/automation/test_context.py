from __future__ import annotations

import json
from datetime import timedelta
from hashlib import sha256

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.automation.context import (
    MAX_FACTS,
    MAX_RECENT_MESSAGES,
    MandatoryContextOverflow,
    build_bounded_reply_context,
)
from apps.automation.memory import (
    MAX_MEMORY_SERIALIZED_CHARACTERS,
    refresh_contact_memory,
    verified_conversation_memory_text,
)
from apps.automation.models import EmailCandidate, HumanTask, KnowledgeFactEmbedding, ReplyDecision
from apps.automation.services import (
    approve_global_knowledge_context_revision,
    approve_knowledge_revision,
    create_global_knowledge_context_revision,
    create_knowledge_revision,
    process_inbound_decision,
)
from apps.campaigns.models import Campaign, OutboundMessage
from apps.contacts.models import Contact, Conversation, EmailAddress, Organization
from apps.integrations.contracts import EmbeddingRequest, EmbeddingResult, ReplyDecisionRequest
from apps.integrations.llm import MockLLMProvider
from apps.integrations.llm_inputs import reply_decision_input_character_count
from apps.mailbox.models import GmailConnection, InboundMessage


class RankingEmbeddingProvider:
    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple(
                self._vector_for(text, dimensions=request.dimensions) for text in request.texts
            ),
            model=request.model,
            dimensions=request.dimensions,
        )

    @staticmethod
    def _vector_for(text: str, *, dimensions: int) -> tuple[float, ...]:
        lowered = text.casefold()
        score = 0.0
        if "texto nuevo completo" in lowered:
            score = 1.0
        elif "número 0" in lowered:
            score = 0.98
        elif "número 1" in lowered:
            score = 0.82
        elif "número 2" in lowered:
            score = 0.70
        elif "número 3" in lowered:
            score = 0.54
        vector = [0.0] * dimensions
        vector[0] = score
        if dimensions > 1:
            vector[1] = (max(0.0, 1.0 - score * score)) ** 0.5
        return tuple(vector)


class LowSimilarityEmbeddingProvider(RankingEmbeddingProvider):
    @staticmethod
    def _vector_for(text: str, *, dimensions: int) -> tuple[float, ...]:
        lowered = text.casefold()
        vector = [0.0] * dimensions
        if "texto nuevo completo" in lowered:
            vector[0] = 1.0
        else:
            vector[1] = 1.0
        return tuple(vector)


def _reply_context_fixture(
    owner: User,
) -> tuple[InboundMessage, OutboundMessage, OutboundMessage]:
    workspace = owner.membership.workspace
    organization = Organization.objects.create(workspace=workspace, name="Taller Contexto")
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="contexto@example.com",
        normalized_email="contexto@example.com",
        domain="example.com",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="equipo@example.com",
        status=GmailConnection.Status.CONNECTED,
    )
    conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="thread-contexto",
    )
    original = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        organization=organization,
        contact=contact,
        conversation=conversation,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject="Propuesta comercial",
        body_text="PROPUESTA ORIGINAL COMPLETA",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="context-original",
        message_id="<context-original@example.invalid>",
        gmail_message_id="gmail-context-original",
        gmail_thread_id=conversation.gmail_thread_id,
        sent_at=timezone.now() - timedelta(days=2),
    )
    parent = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
        organization=organization,
        contact=contact,
        conversation=conversation,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject="Re: Propuesta comercial",
        body_text="RESPUESTA DIRECTA COMPLETA",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="context-parent",
        message_id="<context-parent@example.invalid>",
        gmail_message_id="gmail-context-parent",
        gmail_thread_id=conversation.gmail_thread_id,
        sent_at=timezone.now() - timedelta(hours=2),
    )
    inbound = InboundMessage.objects.create(
        connection=connection,
        organization=organization,
        contact=contact,
        conversation=conversation,
        related_outbound=parent,
        gmail_message_id="gmail-context-inbound",
        gmail_thread_id=conversation.gmail_thread_id,
        message_id="<context-inbound@example.invalid>",
        in_reply_to=parent.message_id,
        references=[original.message_id, parent.message_id],
        sender=email.original_email,
        recipients=[connection.email],
        subject="Re: Propuesta comercial",
        external_at=timezone.now(),
        received_at=timezone.now(),
        body_text=(
            "TEXTO NUEVO COMPLETO sobre carbones.\n\n"
            "On Friday alguien wrote:\n> HISTORIAL CITADO QUE NO DEBE REPETIRSE"
        ),
        is_human=True,
    )
    return inbound, original, parent


@pytest.mark.django_db
def test_reply_context_keeps_mandatory_messages_and_bounds_optional_history(owner: User) -> None:
    inbound, original, parent = _reply_context_fixture(owner)
    assert inbound.contact is not None
    contact = inbound.contact

    for index in range(8):
        OutboundMessage.objects.create(
            kind=OutboundMessage.Kind.MANUAL_REPLY,
            organization=contact.organization,
            contact=contact,
            conversation=inbound.conversation,
            email_address=contact.preferred_email,
            recipient="contexto@example.com",
            recipient_normalized="contexto@example.com",
            subject=f"Historial {index}",
            body_text=f"MENSAJE RECIENTE {index}",
            state=OutboundMessage.State.SENT,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            idempotency_key=f"context-recent-{index}",
            sent_at=timezone.now() - timedelta(minutes=index + 10),
        )
    memory = refresh_contact_memory(contact.pk)
    assert memory is not None
    for index in range(10):
        revision = create_knowledge_revision(
            workspace=contact.workspace,
            actor=owner,
            title=f"Carbones {index:02d}",
            category="Producto",
            text=f"Información aprobada sobre carbones número {index}.",
        )
        approve_knowledge_revision(revision, actor=owner)
    KnowledgeFactEmbedding.objects.all().delete()

    context = build_bounded_reply_context(inbound, embedding_provider=RankingEmbeddingProvider())

    mandatory = [block for block in context.blocks if block.mandatory]
    assert [(block.provenance, block.text) for block in mandatory] == [
        ("NEW_INBOUND", "TEXTO NUEVO COMPLETO sobre carbones."),
        ("ORIGINAL_OUTBOUND", original.body_text),
        ("DIRECT_PARENT", parent.body_text),
    ]
    recent = [block for block in context.blocks if block.provenance == "RECENT_CONTACT_MESSAGE"]
    assert len(recent) == MAX_RECENT_MESSAGES
    assert any(block.source_id == str(memory.pk) for block in context.blocks)
    assert len(context.facts) == MAX_FACTS
    assert context.manifest["knowledge_retrieval"]["status"] == "SELECTED"
    assert context.character_count <= 24_000
    request = ReplyDecisionRequest(
        context=context.blocks,
        candidates=context.candidates,
        facts=context.facts,
        correlation_id="ignored-by-model-input",
        idempotency_key="ignored-by-model-input",
        policy_version="2026-07",
    )
    assert context.character_count == reply_decision_input_character_count(request)
    assert context.manifest["request"]["characters"] == context.character_count

    serialized_manifest = str(context.manifest)
    assert "TEXTO NUEVO COMPLETO" not in serialized_manifest
    assert "PROPUESTA ORIGINAL COMPLETA" not in serialized_manifest
    assert "MENSAJE RECIENTE" not in serialized_manifest
    assert "Información aprobada" not in serialized_manifest
    canonical = json.dumps(context.manifest, sort_keys=True, separators=(",", ":"))
    assert context.context_hash == sha256(canonical.encode()).hexdigest()


@pytest.mark.django_db
def test_reply_context_includes_approved_global_context(owner: User) -> None:
    inbound, _, _ = _reply_context_fixture(owner)
    assert inbound.contact is not None
    revision = create_global_knowledge_context_revision(
        workspace=inbound.contact.workspace,
        actor=owner,
        context_text=(
            "Somos una empresa de carbones para motores. Si falta información técnica, "
            "pedimos modelo, medida o aplicación."
        ),
        source_notes="Revisado por dirección.",
    )
    approve_global_knowledge_context_revision(revision, actor=owner)

    context = build_bounded_reply_context(
        inbound,
        embedding_provider=LowSimilarityEmbeddingProvider(),
    )

    global_blocks = [
        block for block in context.blocks if block.provenance == "GLOBAL_APPROVED_CONTEXT"
    ]
    assert len(global_blocks) == 1
    assert global_blocks[0].mandatory is True
    assert "empresa de carbones" not in str(context.manifest)


@pytest.mark.django_db
def test_reply_context_with_low_rag_similarity_passes_no_facts(owner: User) -> None:
    inbound, _, _ = _reply_context_fixture(owner)
    assert inbound.contact is not None
    revision = create_knowledge_revision(
        workspace=inbound.contact.workspace,
        actor=owner,
        title="Entrega",
        category="Logística",
        text="Hacemos entregas coordinadas en zonas definidas.",
    )
    approve_knowledge_revision(revision, actor=owner)
    KnowledgeFactEmbedding.objects.all().delete()

    context = build_bounded_reply_context(
        inbound,
        embedding_provider=LowSimilarityEmbeddingProvider(),
    )

    assert context.facts == ()
    assert context.manifest["knowledge_retrieval"]["status"] == "LOW_SIMILARITY"


@pytest.mark.django_db
def test_reply_context_trims_optional_blocks_using_serialized_budget(owner: User) -> None:
    inbound, _, _ = _reply_context_fixture(owner)
    assert inbound.contact is not None
    candidate_email = "compras@example.com"
    candidate = EmailCandidate.objects.create(
        inbound=inbound,
        original_literal=candidate_email,
        normalized_email=candidate_email,
        region=EmailCandidate.Region.NEW_CONTENT,
        source=EmailCandidate.Source.TEXT,
        syntax_state=EmailCandidate.CheckState.VALID,
        mx_state=EmailCandidate.CheckState.VALID,
        restriction_state=EmailCandidate.CheckState.VALID,
        ownership_state=EmailCandidate.CheckState.VALID,
        evidence_hash=sha256(candidate_email.encode()).hexdigest(),
    )
    OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        organization=inbound.contact.organization,
        contact=inbound.contact,
        conversation=inbound.conversation,
        email_address=inbound.contact.preferred_email,
        recipient="contexto@example.com",
        recipient_normalized="contexto@example.com",
        subject="Historia opcional",
        body_text="CONTEXTO OPCIONAL " + "x" * 500,
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="context-optional-budget",
        sent_at=timezone.now() - timedelta(minutes=1),
    )
    full = build_bounded_reply_context(inbound)
    mandatory = tuple(block for block in full.blocks if block.mandatory)
    mandatory_request = ReplyDecisionRequest(
        context=mandatory,
        candidates=full.candidates,
        facts=(),
        correlation_id="",
        idempotency_key="",
        policy_version="2026-07",
    )
    exact_mandatory_size = reply_decision_input_character_count(mandatory_request)

    bounded = build_bounded_reply_context(
        inbound,
        max_characters=exact_mandatory_size,
    )

    assert bounded.blocks == mandatory
    assert bounded.facts == ()
    assert bounded.character_count == exact_mandatory_size
    assert bounded.manifest["request"]["candidate_refs"] == [
        {
            "candidate_id": str(candidate.pk),
            "region": EmailCandidate.Region.NEW_CONTENT,
            "validation_state": EmailCandidate.CheckState.VALID,
        }
    ]


@pytest.mark.django_db
def test_reply_context_opens_human_path_when_mandatory_content_cannot_fit(owner: User) -> None:
    inbound, original, parent = _reply_context_fixture(owner)
    mandatory_length = (
        len("TEXTO NUEVO COMPLETO sobre carbones.")
        + len(original.body_text)
        + len(parent.body_text)
    )

    # The raw bodies fit, but their mandatory labels, source IDs, candidates and instructions do
    # not. This guards against budgeting only the visible body strings.
    with pytest.raises(MandatoryContextOverflow, match="demasiado extensos"):
        build_bounded_reply_context(inbound, max_characters=mandatory_length + 100)


@pytest.mark.django_db
def test_mandatory_overflow_opens_human_task_without_calling_provider(owner: User) -> None:
    inbound, _, _ = _reply_context_fixture(owner)
    InboundMessage.objects.filter(pk=inbound.pk).update(body_text="x" * 24_000)
    provider = MockLLMProvider()

    decision = process_inbound_decision(inbound.pk, provider=provider)

    assert decision is not None
    assert decision.state == ReplyDecision.State.FAILED
    assert provider.decision_requests == []
    assert HumanTask.objects.filter(
        inbound=inbound,
        status=HumanTask.Status.OPEN,
    ).exists()


@pytest.mark.django_db
def test_recent_context_excludes_non_human_inbound_and_unsent_outbound(owner: User) -> None:
    inbound, _, parent = _reply_context_fixture(owner)
    assert inbound.contact is not None
    now = timezone.now()
    human = InboundMessage.objects.create(
        connection=inbound.connection,
        organization=inbound.organization,
        contact=inbound.contact,
        conversation=inbound.conversation,
        related_outbound=parent,
        gmail_message_id="gmail-human-history",
        gmail_thread_id=inbound.gmail_thread_id,
        message_id="<human-history@example.invalid>",
        sender=inbound.sender,
        recipients=inbound.recipients,
        external_at=now - timedelta(minutes=5),
        received_at=now - timedelta(minutes=5),
        body_text="HISTORIA HUMANA RELEVANTE",
        is_human=True,
    )
    automatic = InboundMessage.objects.create(
        connection=inbound.connection,
        organization=inbound.organization,
        contact=inbound.contact,
        conversation=inbound.conversation,
        related_outbound=parent,
        gmail_message_id="gmail-automatic-history",
        gmail_thread_id=inbound.gmail_thread_id,
        message_id="<automatic-history@example.invalid>",
        sender=inbound.sender,
        recipients=inbound.recipients,
        external_at=now - timedelta(minutes=1),
        received_at=now - timedelta(minutes=1),
        body_text="RESPUESTA AUTOMÁTICA IRRELEVANTE",
        classification=InboundMessage.Classification.AUTO_REPLY,
        is_human=False,
    )
    draft = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        organization=inbound.organization,
        contact=inbound.contact,
        conversation=inbound.conversation,
        email_address=inbound.contact.preferred_email,
        recipient="contexto@example.com",
        recipient_normalized="contexto@example.com",
        subject="Borrador",
        body_text="BORRADOR NO ENVIADO",
        state=OutboundMessage.State.REVIEW_READY,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="context-unsent-draft",
    )

    context = build_bounded_reply_context(inbound)
    source_ids = {block.source_id for block in context.blocks}

    assert str(human.pk) in source_ids
    assert str(automatic.pk) not in source_ids
    assert str(draft.pk) not in source_ids


@pytest.mark.django_db
def test_original_lookup_ignores_unsent_messages_in_the_same_conversation(owner: User) -> None:
    inbound, original, _ = _reply_context_fixture(owner)
    draft = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        organization=inbound.organization,
        contact=inbound.contact,
        conversation=inbound.conversation,
        email_address=inbound.contact.preferred_email if inbound.contact else None,
        recipient="contexto@example.com",
        recipient_normalized="contexto@example.com",
        subject="No enviado",
        body_text="ESTE BORRADOR NO ES EL ORIGINAL",
        state=OutboundMessage.State.PREPARED,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="context-unrelated-unsent",
    )

    context = build_bounded_reply_context(inbound)
    mandatory_ids = {block.source_id for block in context.blocks if block.mandatory}

    assert str(original.pk) in mandatory_ids
    assert str(draft.pk) not in mandatory_ids


@pytest.mark.django_db
def test_contact_memory_is_versioned_bounded_source_linked_and_verified(owner: User) -> None:
    inbound, _, _ = _reply_context_fixture(owner)
    assert inbound.contact is not None
    contact = inbound.contact
    for index in range(10):
        OutboundMessage.objects.create(
            kind=OutboundMessage.Kind.MANUAL_REPLY,
            organization=contact.organization,
            contact=contact,
            conversation=inbound.conversation,
            email_address=contact.preferred_email,
            recipient="contexto@example.com",
            recipient_normalized="contexto@example.com",
            subject=f"Historia {index}",
            body_text=(f"Detalle histórico {index}. " + "x" * 500),
            state=OutboundMessage.State.SENT,
            delivery_mode=Campaign.DeliveryMode.LIVE,
            idempotency_key=f"memory-history-{index}",
            sent_at=timezone.now() - timedelta(days=index + 1),
        )

    memory = refresh_contact_memory(contact.pk)

    assert memory is not None
    assert memory.source_message_ids
    assert all(item.startswith(("inbound:", "outbound:")) for item in memory.source_message_ids)
    verified = verified_conversation_memory_text(memory)
    assert verified is not None
    assert len(verified) <= MAX_MEMORY_SERIALIZED_CHARACTERS
    assert refresh_contact_memory(contact.pk).pk == memory.pk  # type: ignore[union-attr]

    source_id = next(item for item in memory.source_message_ids if item.startswith("outbound:"))
    OutboundMessage.objects.filter(pk=source_id.partition(":")[2]).update(
        body_text="EL ORIGEN CAMBIÓ"
    )
    assert verified_conversation_memory_text(memory) is None
    context = build_bounded_reply_context(inbound)
    assert all(block.source_id != str(memory.pk) for block in context.blocks)

    refreshed = refresh_contact_memory(contact.pk)
    memory.refresh_from_db()
    assert refreshed is not None
    assert refreshed.version == memory.version + 1
    assert memory.superseded_at is not None
    assert verified_conversation_memory_text(refreshed) is not None
