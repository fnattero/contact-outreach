from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from hashlib import sha256

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone

from apps.automation.candidates import persist_email_candidates
from apps.automation.context import build_bounded_reply_context
from apps.automation.execution import (
    authorize_reply_decision,
    deliver_authorized_outbound,
    execute_reply_decision,
    reconcile_authorized_outbound,
)
from apps.automation.models import (
    AutomaticActionReservation,
    EmailCandidate,
    HumanTask,
    KnowledgeFact,
    KnowledgeFactRevision,
    NotificationDelivery,
    ReplyAutomationConfiguration,
    ReplyDecision,
)
from apps.automation.notifications import (
    deliver_notification,
    ensure_notification_deliveries,
    reconcile_notification,
)
from apps.automation.services import (
    _policy_result,
    approve_global_knowledge_context_revision,
    create_global_knowledge_context_revision,
    process_inbound_decision,
)
from apps.automation.tasks import recover_automation_actions
from apps.campaigns.content import REDIRECT_ACK_BODY
from apps.campaigns.models import (
    Campaign,
    CampaignAttachment,
    OutboundAttachment,
    OutboundMessage,
)
from apps.catalogs.models import Catalog
from apps.configuration.services import save_automatic_reply_prompt
from apps.contacts.models import (
    CampaignEnrollment,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
)
from apps.integrations.contracts import (
    AmbiguousProviderError,
    GmailSendResult,
    PermanentProviderError,
    ReplyDecisionRequest,
    ReplyDecisionResult,
)
from apps.integrations.fakes import FakeGmailProvider
from apps.integrations.gmail import GMAIL_SCOPES
from apps.integrations.llm import MockLLMProvider
from apps.mailbox.models import FakeGmailMessage, GmailConnection, InboundMessage
from apps.prospects.email_validation import MockMXResolver


@dataclass(frozen=True)
class ReplyScenario:
    owner: object
    campaign: Campaign
    contact: Contact
    conversation: Conversation
    inbound: InboundMessage
    decision: ReplyDecision


class _RiskyNoActionProvider:
    def decide_reply(self, request: ReplyDecisionRequest) -> ReplyDecisionResult:
        del request
        return ReplyDecisionResult(
            classification="INTERESTED",
            intent="MEETING_OR_DATE",
            action="NO_ACTION",
            confidence=0.99,
            candidate_id=None,
            fact_revision_ids=(),
            proposed_body=None,
            human_reason=None,
        )


class _MisclassifiedSchedulingProvider:
    def decide_reply(self, request: ReplyDecisionRequest) -> ReplyDecisionResult:
        fact = request.facts[0]
        return ReplyDecisionResult(
            classification="INTERESTED",
            intent="APPROVED_PRODUCT_INFORMATION",
            action="REPLY",
            confidence=0.99,
            candidate_id=None,
            fact_revision_ids=(fact.revision_id,),
            proposed_body="Podemos coordinar una llamada y ajustar nuestra agenda.",
            human_reason=None,
        )


def _pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _scenario(
    owner,
    *,
    private_catalog_dir,
    inbound_body: str = "¿Cuántos años de experiencia tienen?",
    action: str = "REPLY",
    intent: str = "APPROVED_COMPANY_FACT",
    proposed_body: str = "Tenemos veinte años de experiencia.",
    headers: dict[str, str] | None = None,
    redirect_email: str = "",
) -> ReplyScenario:
    del private_catalog_dir
    now = timezone.now()
    workspace = owner.membership.workspace
    owner.email = "admin@example.com"
    owner.save(update_fields=("email",))
    content = _pdf()
    catalog = Catalog.objects.create(
        workspace=workspace,
        name="Catálogo",
        version=1,
        file=ContentFile(content, name="catalogo.pdf"),
        original_filename="catalogo.pdf",
        detected_mime="application/pdf",
        byte_size=len(content),
        sha256=sha256(content).hexdigest(),
        uploaded_by=owner,
    )
    campaign = Campaign.objects.create(
        workspace=workspace,
        name="Campaña",
        catalog=catalog,
        created_by=owner,
        delivery_mode=Campaign.DeliveryMode.LIVE,
    )
    CampaignAttachment.objects.create(campaign=campaign, catalog=catalog, position=0)
    campaign.state = Campaign.State.RUNNING
    campaign.approved_at = now
    campaign.approved_by = owner
    campaign.save(update_fields=("state", "approved_at", "approved_by", "updated_at"))

    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="cliente@example.com",
        normalized_email="cliente@example.com",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        created_reason=Contact.CreatedReason.HUMAN_REPLY,
        last_interaction_at=now,
    )
    enrollment = CampaignEnrollment.objects.create(
        workspace=workspace,
        campaign=campaign,
        organization=organization,
        selected_email=email,
        state=CampaignEnrollment.State.RESPONDED,
    )
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="sender@example.com",
        scopes=sorted(GMAIL_SCOPES),
        refresh_token_encrypted="test-token",
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=now,
    )
    initial = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        campaign=campaign,
        organization=organization,
        campaign_enrollment=enrollment,
        contact=contact,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject="Propuesta comercial",
        body_text="Propuesta original",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="initial:scenario",
        message_id="<initial@contact-outreach.local>",
        gmail_message_id="gmail-initial",
        gmail_thread_id="thread-original",
        sent_at=now,
    )
    OutboundAttachment.objects.create(
        message=initial,
        catalog=catalog,
        position=0,
        catalog_version=catalog.version,
        storage_key=catalog.storage_key,
        filename=catalog.original_filename,
        byte_size=catalog.byte_size,
        sha256=catalog.sha256,
    )
    conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="thread-original",
        subject=initial.subject,
        last_message_at=now,
    )
    initial.conversation = conversation
    initial.save(update_fields=("conversation", "updated_at"))
    inbound = InboundMessage.objects.create(
        connection=connection,
        organization=organization,
        campaign_enrollment=enrollment,
        contact=contact,
        conversation=conversation,
        related_outbound=initial,
        gmail_message_id="gmail-inbound",
        gmail_thread_id="thread-original",
        message_id="<inbound@example.com>",
        in_reply_to=initial.message_id,
        references=[initial.message_id],
        sender="Cliente <cliente@example.com>",
        recipients=[connection.email],
        subject=initial.subject,
        external_at=now,
        received_at=now,
        body_text=inbound_body,
        headers=headers or {},
        classification=InboundMessage.Classification.INTERESTED,
        classification_confidence=Decimal("0.990"),
        is_human=True,
    )
    fact = KnowledgeFact.objects.create(
        workspace=workspace,
        title="Experiencia",
        category="Empresa",
        created_by=owner,
    )
    fact_text = "Tenemos veinte años de experiencia."
    revision = KnowledgeFactRevision.objects.create(
        fact=fact,
        version=1,
        text=fact_text,
        content_hash=sha256(fact_text.encode()).hexdigest(),
        approved_at=now,
        approved_by=owner,
    )
    candidate = None
    if redirect_email:
        (candidate,) = persist_email_candidates(inbound)
        candidate.mx_state = EmailCandidate.CheckState.VALID
        candidate.restriction_state = EmailCandidate.CheckState.VALID
        candidate.ownership_state = EmailCandidate.CheckState.VALID
        candidate.save(
            update_fields=("mx_state", "restriction_state", "ownership_state", "updated_at")
        )
    context = build_bounded_reply_context(inbound)
    ReplyAutomationConfiguration.objects.create(
        workspace=workspace,
        mode=ReplyAutomationConfiguration.Mode.LIVE,
        live_enabled_at=now,
        live_enabled_by=owner,
    )
    decision = ReplyDecision.objects.create(
        workspace=workspace,
        inbound=inbound,
        contact=contact,
        conversation=conversation,
        mode=ReplyAutomationConfiguration.Mode.LIVE,
        provider="fake",
        model="fake-deterministic",
        policy_version="2026-07",
        classification="INTERESTED",
        intent=intent,
        action=action,
        confidence=Decimal("0.990"),
        candidate=candidate,
        proposed_body=proposed_body,
        context_manifest=context.manifest,
        context_hash=context.context_hash,
        state=ReplyDecision.State.AUTO_ELIGIBLE,
    )
    if action == "REPLY":
        decision.selected_facts.add(revision)
    return ReplyScenario(owner, campaign, contact, conversation, inbound, decision)


def _allow_test_live_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.automation.execution._live_mode_error",
        lambda decision: "",
    )


@pytest.mark.django_db
def test_live_decision_is_enqueued_only_after_commit(
    owner,
    private_catalog_dir,
    monkeypatch,
    django_capture_on_commit_callbacks,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    scenario.decision.delete()
    queued: list[str] = []
    monkeypatch.setattr(
        "apps.automation.tasks.execute_reply_decision_task.delay",
        lambda value: queued.append(value),
    )

    with django_capture_on_commit_callbacks(execute=True):
        decision = process_inbound_decision(
            scenario.inbound.pk,
            provider=MockLLMProvider(),
            resolver=MockMXResolver(),
        )

    assert decision is not None
    assert decision.state == ReplyDecision.State.AUTO_ELIGIBLE
    assert queued == [str(decision.pk)]
    assert not OutboundMessage.objects.filter(
        kind__in=(
            OutboundMessage.Kind.AUTOMATIC_REPLY,
            OutboundMessage.Kind.REFERRED_PROPOSAL,
            OutboundMessage.Kind.REDIRECT_ACK,
        )
    ).exists()


@pytest.mark.django_db
def test_reply_decision_uses_configured_writing_instructions(
    owner,
    private_catalog_dir,
    django_capture_on_commit_callbacks,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    scenario.decision.delete()
    save_automatic_reply_prompt(
        owner=owner,
        automatic_reply_prompt="Respondé primero la pregunta y evitá listar tarjetas.",
    )
    provider = MockLLMProvider()

    with django_capture_on_commit_callbacks(execute=True):
        decision = process_inbound_decision(
            scenario.inbound.pk,
            provider=provider,
            resolver=MockMXResolver(),
        )

    assert decision is not None
    assert provider.decision_requests[0].writing_instructions == (
        "Respondé primero la pregunta y evitá listar tarjetas."
    )
    request_metadata = decision.context_manifest["request"]
    assert (
        request_metadata["writing_instructions_sha256"]
        == sha256("Respondé primero la pregunta y evitá listar tarjetas.".encode()).hexdigest()
    )
    assert "evitá listar tarjetas" not in str(decision.context_manifest)


@pytest.mark.parametrize(
    ("body", "requires_human"),
    (
        (
            "Me gustaría coordinar una llamada. ¿Qué día y horario tienen disponibles?",
            True,
        ),
        ("¿Podemos agendar una reunión para conversar sobre la compra?", True),
        ("¿Cuál es el horario de atención para retirar mercadería?", False),
        ("¿Tienen un teléfono para llamar?", False),
    ),
)
def test_scheduling_guardrail_only_matches_explicit_human_coordination(
    body: str,
    requires_human: bool,
) -> None:
    state, reason = _policy_result(
        ReplyDecisionResult(
            classification="INTERESTED",
            intent="APPROVED_PRODUCT_INFORMATION",
            action="REPLY",
            confidence=0.99,
            candidate_id=None,
            fact_revision_ids=("fact-1",),
            proposed_body="Respuesta basada en un dato aprobado.",
            human_reason=None,
        ),
        candidates=(),
        inbound_text=body,
    )

    assert (state == ReplyDecision.State.HUMAN_REQUIRED) is requires_human
    assert reason == ("MEETING_OR_DATE" if requires_human else "")


@pytest.mark.django_db
def test_clear_scheduling_request_overrides_misclassified_llm_action(
    owner,
    private_catalog_dir,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        inbound_body=(
            "Me gustaría coordinar una llamada para conversar sobre su experiencia. "
            "¿Qué día y horario tienen disponibles?"
        ),
    )
    scenario.decision.delete()
    queued: list[str] = []
    monkeypatch.setattr(
        "apps.automation.tasks.execute_reply_decision_task.delay",
        lambda value: queued.append(value),
    )

    with django_capture_on_commit_callbacks(execute=True):
        decision = process_inbound_decision(
            scenario.inbound.pk,
            provider=_MisclassifiedSchedulingProvider(),
            resolver=MockMXResolver(),
        )

    assert decision is not None
    assert decision.action == "REPLY"
    assert decision.state == ReplyDecision.State.HUMAN_REQUIRED
    assert decision.human_reason == "MEETING_OR_DATE"
    assert queued == []
    assert HumanTask.objects.filter(
        decision=decision,
        reason="MEETING_OR_DATE",
        status=HumanTask.Status.OPEN,
    ).exists()


@pytest.mark.django_db
def test_shadow_decision_never_enqueues_a_gmail_effect(
    owner,
    private_catalog_dir,
    monkeypatch,
    django_capture_on_commit_callbacks,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    scenario.decision.delete()
    ReplyAutomationConfiguration.objects.filter(workspace=scenario.contact.workspace).update(
        mode=ReplyAutomationConfiguration.Mode.SHADOW,
        live_enabled_at=None,
        live_enabled_by=None,
    )
    queued: list[str] = []
    monkeypatch.setattr(
        "apps.automation.tasks.execute_reply_decision_task.delay",
        lambda value: queued.append(value),
    )

    with django_capture_on_commit_callbacks(execute=True):
        decision = process_inbound_decision(
            scenario.inbound.pk,
            provider=MockLLMProvider(),
            resolver=MockMXResolver(),
        )

    assert decision is not None
    assert decision.state == ReplyDecision.State.SHADOW_RECORDED
    assert queued == []
    assert not FakeGmailMessage.objects.exists()


@pytest.mark.django_db
def test_risky_intent_with_no_action_still_opens_human_task_in_live_mode(
    owner,
    private_catalog_dir,
    django_capture_on_commit_callbacks,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    scenario.decision.delete()

    with django_capture_on_commit_callbacks(execute=True):
        decision = process_inbound_decision(
            scenario.inbound.pk,
            provider=_RiskyNoActionProvider(),
            resolver=MockMXResolver(),
        )

    assert decision is not None
    assert decision.state == ReplyDecision.State.HUMAN_REQUIRED
    assert decision.human_reason == "MEETING_OR_DATE"
    assert HumanTask.objects.filter(
        decision=decision,
        reason="MEETING_OR_DATE",
        status=HumanTask.Status.OPEN,
    ).exists()
    assert not FakeGmailMessage.objects.exists()


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_safe_reply_is_durable_idempotent_and_stays_in_original_thread(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    _allow_test_live_gate(monkeypatch)
    provider = FakeGmailProvider(persist=True)

    assert execute_reply_decision(scenario.decision.pk, provider=provider) == (
        ReplyDecision.State.COMPLETED
    )
    assert execute_reply_decision(scenario.decision.pk, provider=provider) == (
        ReplyDecision.State.COMPLETED
    )

    message = OutboundMessage.objects.get(kind=OutboundMessage.Kind.AUTOMATIC_REPLY)
    assert message.state == OutboundMessage.State.SENT
    assert message.gmail_thread_id == scenario.inbound.gmail_thread_id
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.AUTOMATIC_REPLY).count() == 1
    assert AutomaticActionReservation.objects.get(decision=scenario.decision).slots == 1
    fake = FakeGmailMessage.objects.get(rfc_message_id=message.message_id)
    parsed = BytesParser(policy=policy.default).parsebytes(bytes(fake.raw_message))
    assert parsed["In-Reply-To"] == scenario.inbound.message_id
    assert parsed.get_content().strip() == scenario.decision.proposed_body


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_cross_thread_automatic_reply_after_decision_does_not_stale_policy_recheck(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    assert scenario.contact.preferred_email is not None
    cutoff = scenario.decision.created_at
    other_conversation = Conversation.objects.create(
        workspace=scenario.contact.workspace,
        contact=scenario.contact,
        connection=scenario.inbound.connection,
        gmail_thread_id="thread-other-automatic",
        subject="Consulta repetida",
        last_message_at=cutoff - timedelta(seconds=1),
    )
    other_inbound = InboundMessage.objects.create(
        connection=scenario.inbound.connection,
        organization=scenario.contact.organization,
        campaign_enrollment=scenario.inbound.campaign_enrollment,
        contact=scenario.contact,
        conversation=other_conversation,
        related_outbound=None,
        gmail_message_id="gmail-other-automatic-inbound",
        gmail_thread_id=other_conversation.gmail_thread_id,
        message_id="<other-automatic-inbound@example.com>",
        sender="Cliente <cliente@example.com>",
        recipients=[scenario.inbound.connection.email],
        subject="Consulta repetida",
        external_at=cutoff - timedelta(minutes=1),
        received_at=cutoff - timedelta(minutes=1),
        body_text=scenario.inbound.body_text,
        classification=InboundMessage.Classification.INTERESTED,
        classification_confidence=Decimal("0.990"),
        is_human=True,
    )
    InboundMessage.objects.filter(pk=other_inbound.pk).update(
        created_at=cutoff - timedelta(seconds=1),
        updated_at=cutoff - timedelta(seconds=1),
    )
    context_with_other_inbound = build_bounded_reply_context(scenario.inbound)
    scenario.decision.context_manifest = context_with_other_inbound.manifest
    scenario.decision.context_hash = context_with_other_inbound.context_hash
    scenario.decision.save(update_fields=("context_manifest", "context_hash", "updated_at"))

    later_reply = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
        campaign=scenario.campaign,
        organization=scenario.contact.organization,
        campaign_enrollment=scenario.inbound.campaign_enrollment,
        contact=scenario.contact,
        conversation=other_conversation,
        email_address=scenario.contact.preferred_email,
        parent_inbound=other_inbound,
        recipient=scenario.contact.preferred_email.original_email,
        recipient_normalized=scenario.contact.preferred_email.normalized_email,
        subject="Re: Consulta repetida",
        body_text="Respuesta automática posterior en otro hilo.",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="other-thread-automatic-reply",
        semantic_action_key="other-thread-automatic-reply",
        message_id="<other-thread-automatic-reply@example.invalid>",
        gmail_message_id="gmail-other-thread-automatic-reply",
        gmail_thread_id=other_conversation.gmail_thread_id,
        sent_at=cutoff + timedelta(seconds=2),
    )
    OutboundMessage.objects.filter(pk=later_reply.pk).update(
        created_at=cutoff + timedelta(seconds=1),
        updated_at=cutoff + timedelta(seconds=1),
    )
    assert build_bounded_reply_context(scenario.inbound).context_hash != (
        scenario.decision.context_hash
    )
    _allow_test_live_gate(monkeypatch)

    assert (
        execute_reply_decision(
            scenario.decision.pk,
            provider=FakeGmailProvider(persist=True),
        )
        == ReplyDecision.State.COMPLETED
    )


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_live_reply_sends_llm_proposed_body_after_selected_fact_validation(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        proposed_body=(
            "Tenemos veinte años de experiencia. Quedamos a disposición para ampliar la "
            "información."
        ),
    )
    _allow_test_live_gate(monkeypatch)
    provider = FakeGmailProvider(persist=True)

    assert execute_reply_decision(scenario.decision.pk, provider=provider) == (
        ReplyDecision.State.COMPLETED
    )

    message = OutboundMessage.objects.get(kind=OutboundMessage.Kind.AUTOMATIC_REPLY)
    fake = FakeGmailMessage.objects.get(rfc_message_id=message.message_id)
    parsed = BytesParser(policy=policy.default).parsebytes(bytes(fake.raw_message))
    assert parsed.get_content().strip() == scenario.decision.proposed_body


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_confirmed_reply_in_an_unexpected_thread_requires_human_review(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    _allow_test_live_gate(monkeypatch)

    assert (
        execute_reply_decision(
            scenario.decision.pk,
            provider=_WrongThreadReply(persist=True),
        )
        == ReplyDecision.State.HUMAN_REQUIRED
    )

    message = OutboundMessage.objects.get(kind=OutboundMessage.Kind.AUTOMATIC_REPLY)
    assert message.state == OutboundMessage.State.SENT
    assert message.gmail_thread_id == "unexpected-thread"
    assert HumanTask.objects.filter(
        decision=scenario.decision,
        reason="GMAIL_REPLY_THREAD_MISMATCH",
        status=HumanTask.Status.OPEN,
    ).exists()


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_redirect_sends_proposal_with_pdfs_then_exact_ack_and_creates_second_thread(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    redirect = "compras@nueva.example"
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        inbound_body=f"Por favor mandá la propuesta a {redirect}",
        action="REDIRECT_PROPOSAL",
        intent="EXPLICIT_PROPOSAL_REDIRECTION",
        proposed_body="",
        redirect_email=redirect,
    )
    _allow_test_live_gate(monkeypatch)
    provider = FakeGmailProvider(persist=True)

    assert execute_reply_decision(
        scenario.decision.pk, provider=provider, mx_resolver=MockMXResolver()
    ) == (ReplyDecision.State.COMPLETED)
    assert execute_reply_decision(
        scenario.decision.pk, provider=provider, mx_resolver=MockMXResolver()
    ) == (ReplyDecision.State.COMPLETED)

    proposal = OutboundMessage.objects.get(kind=OutboundMessage.Kind.REFERRED_PROPOSAL)
    ack = OutboundMessage.objects.get(kind=OutboundMessage.Kind.REDIRECT_ACK)
    assert proposal.state == ack.state == OutboundMessage.State.SENT
    assert proposal.gmail_thread_id != scenario.conversation.gmail_thread_id
    assert ack.gmail_thread_id == scenario.conversation.gmail_thread_id
    assert ack.body_text == REDIRECT_ACK_BODY
    assert proposal.attachments.count() == 1
    redirected = EmailAddress.objects.get(normalized_email=redirect)
    assert redirected.organization_id == scenario.contact.organization_id
    assert redirected.label == "Dirección indicada para propuestas"
    assert scenario.contact.conversations.count() == 2
    assert proposal.conversation is not None
    assert proposal.conversation.contact == scenario.contact
    assert proposal.conversation_id != scenario.conversation.pk
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REFERRED_PROPOSAL).count() == 1
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REDIRECT_ACK).count() == 1
    assert AutomaticActionReservation.objects.get(decision=scenario.decision).slots == 2

    proposal_raw = FakeGmailMessage.objects.get(rfc_message_id=proposal.message_id).raw_message
    parsed_proposal = BytesParser(policy=policy.default).parsebytes(bytes(proposal_raw))
    assert len(tuple(parsed_proposal.iter_attachments())) == 1
    ack_raw = FakeGmailMessage.objects.get(rfc_message_id=ack.message_id).raw_message
    parsed_ack = BytesParser(policy=policy.default).parsebytes(bytes(ack_raw))
    assert parsed_ack.get_content().strip() == REDIRECT_ACK_BODY


class _PermanentProposalFailure(FakeGmailProvider):
    def send(self, request):
        del request
        raise PermanentProviderError("recipient rejected")


class _WrongThreadReply(FakeGmailProvider):
    def reply(self, request):
        result = super().reply(request)
        return GmailSendResult(message_id=result.message_id, thread_id="unexpected-thread")


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_redirect_failure_never_creates_false_ack_and_opens_human_task(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    redirect = "compras@nueva.example"
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        inbound_body=f"Mandalo a {redirect}",
        action="REDIRECT_PROPOSAL",
        intent="EXPLICIT_PROPOSAL_REDIRECTION",
        proposed_body="",
        redirect_email=redirect,
    )
    _allow_test_live_gate(monkeypatch)

    assert (
        execute_reply_decision(
            scenario.decision.pk,
            provider=_PermanentProposalFailure(),
            mx_resolver=MockMXResolver(),
        )
        == ReplyDecision.State.HUMAN_REQUIRED
    )

    proposal = OutboundMessage.objects.get(kind=OutboundMessage.Kind.REFERRED_PROPOSAL)
    assert proposal.state == OutboundMessage.State.SEND_FAILED
    assert not OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REDIRECT_ACK).exists()
    assert HumanTask.objects.filter(
        decision=scenario.decision,
        status=HumanTask.Status.OPEN,
    ).exists()
    assert (
        execute_reply_decision(
            scenario.decision.pk,
            provider=_PermanentProposalFailure(),
            mx_resolver=MockMXResolver(),
        )
        == ReplyDecision.State.HUMAN_REQUIRED
    )
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REFERRED_PROPOSAL).count() == 1
    assert not OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REDIRECT_ACK).exists()
    assert (
        HumanTask.objects.filter(
            decision=scenario.decision,
            status=HumanTask.Status.OPEN,
        ).count()
        == 1
    )


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_redirect_never_sends_a_partial_approved_attachment_set(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    redirect = "compras@nueva.example"
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        inbound_body=f"Mandalo a {redirect}",
        action="REDIRECT_PROPOSAL",
        intent="EXPLICIT_PROPOSAL_REDIRECTION",
        proposed_body="",
        redirect_email=redirect,
    )
    second_content = b"%PDF-1.4\n2 0 obj\n<<>>\nendobj\n%%EOF\n"
    second = Catalog.objects.create(
        workspace=scenario.contact.workspace,
        name="Catálogo adicional",
        version=1,
        file=ContentFile(second_content, name="catalogo-adicional.pdf"),
        original_filename="catalogo-adicional.pdf",
        detected_mime="application/pdf",
        byte_size=len(second_content),
        sha256=sha256(second_content).hexdigest(),
        uploaded_by=owner,
    )
    initial = scenario.inbound.related_outbound
    OutboundAttachment.objects.create(
        message=initial,
        catalog=second,
        position=1,
        catalog_version=second.version,
        storage_key=second.storage_key,
        filename=second.original_filename,
        byte_size=second.byte_size,
        sha256=second.sha256,
    )
    _allow_test_live_gate(monkeypatch)
    authorized = authorize_reply_decision(scenario.decision.pk)
    assert isinstance(authorized, OutboundMessage)
    assert authorized.attachments.count() == 2
    authorized.attachments.filter(position=1).delete()

    assert (
        deliver_authorized_outbound(
            authorized.pk,
            provider=FakeGmailProvider(persist=True),
        )
        == ReplyDecision.State.HUMAN_REQUIRED
    )
    assert not FakeGmailMessage.objects.exists()
    assert not OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REDIRECT_ACK).exists()


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_ambiguous_redirect_is_reconciled_before_ack_is_authorized(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    redirect = "compras@nueva.example"
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        inbound_body=f"Mandalo a {redirect}",
        action="REDIRECT_PROPOSAL",
        intent="EXPLICIT_PROPOSAL_REDIRECTION",
        proposed_body="",
        redirect_email=redirect,
    )
    _allow_test_live_gate(monkeypatch)
    provider = _AcceptedButAmbiguous(persist=True)

    assert execute_reply_decision(
        scenario.decision.pk, provider=provider, mx_resolver=MockMXResolver()
    ) == (ReplyDecision.State.EXECUTING)
    proposal = OutboundMessage.objects.get(kind=OutboundMessage.Kind.REFERRED_PROPOSAL)
    assert proposal.state == OutboundMessage.State.RECONCILING
    assert not OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REDIRECT_ACK).exists()

    assert reconcile_authorized_outbound(proposal.pk, provider=provider) == (
        OutboundMessage.State.SENT
    )
    scenario.decision.refresh_from_db()
    assert scenario.decision.state == ReplyDecision.State.COMPLETED
    assert OutboundMessage.objects.get(kind=OutboundMessage.Kind.REDIRECT_ACK).state == (
        OutboundMessage.State.SENT
    )


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_ambiguous_redirect_ack_is_reconciled_without_a_duplicate(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    redirect = "compras@nueva.example"
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        inbound_body=f"Mandalo a {redirect}",
        action="REDIRECT_PROPOSAL",
        intent="EXPLICIT_PROPOSAL_REDIRECTION",
        proposed_body="",
        redirect_email=redirect,
    )
    _allow_test_live_gate(monkeypatch)
    provider = _AckAcceptedButAmbiguous(persist=True)

    assert execute_reply_decision(
        scenario.decision.pk, provider=provider, mx_resolver=MockMXResolver()
    ) == (ReplyDecision.State.EXECUTING)
    ack = OutboundMessage.objects.get(kind=OutboundMessage.Kind.REDIRECT_ACK)
    assert ack.state == OutboundMessage.State.RECONCILING
    assert OutboundMessage.objects.get(kind=OutboundMessage.Kind.REFERRED_PROPOSAL).state == (
        OutboundMessage.State.SENT
    )

    assert reconcile_authorized_outbound(ack.pk, provider=provider) == OutboundMessage.State.SENT
    scenario.decision.refresh_from_db()
    assert scenario.decision.state == ReplyDecision.State.COMPLETED
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.REDIRECT_ACK).count() == 1
    assert FakeGmailMessage.objects.count() == 2


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_direct_contact_reply_profile_and_global_context_survive_policy_recheck(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    scenario.inbound.related_outbound = None
    scenario.inbound.campaign_enrollment = None
    scenario.inbound.subject = "Consulta directa"
    scenario.inbound.save(
        update_fields=("related_outbound", "campaign_enrollment", "subject", "updated_at")
    )
    revision = create_global_knowledge_context_revision(
        workspace=scenario.contact.workspace,
        actor=owner,
        context_text=(
            "Somos fabricantes de componentes industriales y atendemos consultas técnicas."
        ),
        source_notes="",
    )
    approve_global_knowledge_context_revision(revision, actor=owner)
    context = build_bounded_reply_context(scenario.inbound)
    scenario.decision.context_manifest = context.manifest
    scenario.decision.context_hash = context.context_hash
    scenario.decision.save(update_fields=("context_manifest", "context_hash", "updated_at"))
    assert {"CONTACT_RECORD", "GLOBAL_APPROVED_CONTEXT"}.issubset(
        {block["provenance"] for block in context.manifest["blocks"]}
    )
    _allow_test_live_gate(monkeypatch)

    authorized = authorize_reply_decision(scenario.decision.pk)

    assert isinstance(authorized, OutboundMessage)
    assert authorized.kind == OutboundMessage.Kind.AUTOMATIC_REPLY
    assert not HumanTask.objects.filter(
        inbound=scenario.inbound, status=HumanTask.Status.OPEN
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setting_overrides", "header", "mutate_context"),
    (
        ({"AUTO_REPLY_KILL_SWITCH": True}, None, False),
        ({"SEND_KILL_SWITCH": True}, None, False),
        ({"SEND_MODE": "dry-run"}, None, False),
        ({}, {"Auto-Submitted": "auto-replied"}, False),
        ({}, None, True),
    ),
)
def test_final_policy_rechecks_block_kill_headers_and_stale_context(
    owner,
    private_catalog_dir,
    monkeypatch,
    setting_overrides,
    header,
    mutate_context,
) -> None:
    scenario = _scenario(
        owner,
        private_catalog_dir=private_catalog_dir,
        headers=header,
    )
    _allow_test_live_gate(monkeypatch)
    if mutate_context:
        scenario.inbound.body_text += " Cambió después de analizarse."
        scenario.inbound.save(update_fields=("body_text", "updated_at"))

    effective_settings = {
        "SEND_MODE": "live",
        "SEND_KILL_SWITCH": False,
        "AUTO_REPLY_KILL_SWITCH": False,
    }
    effective_settings.update(setting_overrides)
    with override_settings(**effective_settings):
        state = execute_reply_decision(
            scenario.decision.pk,
            provider=FakeGmailProvider(persist=True),
        )

    assert state == ReplyDecision.State.REJECTED_POLICY
    assert not FakeGmailMessage.objects.exists()
    assert not OutboundMessage.objects.filter(kind=OutboundMessage.Kind.AUTOMATIC_REPLY).exists()


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_final_send_recheck_rejects_a_changed_recipient(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    _allow_test_live_gate(monkeypatch)
    authorized = authorize_reply_decision(scenario.decision.pk)
    assert isinstance(authorized, OutboundMessage)
    OutboundMessage.objects.filter(pk=authorized.pk).update(recipient="attacker@example.com")

    assert (
        deliver_authorized_outbound(
            authorized.pk,
            provider=FakeGmailProvider(persist=True),
        )
        == ReplyDecision.State.HUMAN_REQUIRED
    )
    scenario.decision.refresh_from_db()
    assert scenario.decision.state == ReplyDecision.State.HUMAN_REQUIRED
    assert not FakeGmailMessage.objects.exists()


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
def test_gmail_provider_configuration_failure_opens_human_review(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    _allow_test_live_gate(monkeypatch)
    authorized = authorize_reply_decision(scenario.decision.pk)
    assert isinstance(authorized, OutboundMessage)

    def invalid_provider(*args, **kwargs):
        del args, kwargs
        raise ValidationError("No se pudo descifrar la credencial de Gmail.")

    monkeypatch.setattr("apps.automation.execution.provider_for_connection", invalid_provider)
    assert deliver_authorized_outbound(authorized.pk) == OutboundMessage.State.SEND_FAILED
    authorized.refresh_from_db()
    scenario.decision.refresh_from_db()
    assert authorized.state == OutboundMessage.State.SEND_FAILED
    assert scenario.decision.state == ReplyDecision.State.HUMAN_REQUIRED
    assert HumanTask.objects.filter(
        decision=scenario.decision,
        reason="GMAIL_DELIVERY_FAILED",
        status=HumanTask.Status.OPEN,
    ).exists()


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
)
@pytest.mark.parametrize(
    "limit_setting",
    (
        "AUTOMATIC_REPLY_CONVERSATION_DAILY_LIMIT",
        "AUTOMATIC_REPLY_WORKSPACE_DAILY_LIMIT",
    ),
)
def test_rate_capacity_is_reserved_transactionally_before_any_gmail_effect(
    owner,
    private_catalog_dir,
    monkeypatch,
    limit_setting,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    _allow_test_live_gate(monkeypatch)

    with override_settings(**{limit_setting: 0}):
        assert (
            execute_reply_decision(
                scenario.decision.pk,
                provider=FakeGmailProvider(persist=True),
            )
            == ReplyDecision.State.REJECTED_POLICY
        )
    assert not AutomaticActionReservation.objects.filter(decision=scenario.decision).exists()
    assert not FakeGmailMessage.objects.exists()


class _AcceptedButAmbiguous(FakeGmailProvider):
    def send(self, request):
        super().send(request)
        raise AmbiguousProviderError("timeout after acceptance")


class _AckAcceptedButAmbiguous(FakeGmailProvider):
    def reply(self, request):
        super().reply(request)
        raise AmbiguousProviderError("timeout after acknowledgement acceptance")


@pytest.mark.django_db
@override_settings(
    PUBLIC_BASE_URL="https://outreach.example",
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
)
def test_generic_admin_notification_contains_no_conversation_data_and_reconciles(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    task = HumanTask.objects.create(
        workspace=scenario.contact.workspace,
        contact=scenario.contact,
        conversation=scenario.conversation,
        inbound=scenario.inbound,
        decision=scenario.decision,
        kind="REPLY_REVIEW",
        reason="MEETING_OR_DATE",
        status=HumanTask.Status.OPEN,
        friendly_summary="Contenido privado del cliente",
        opened_at=timezone.now(),
    )
    monkeypatch.setattr(
        "apps.automation.tasks.deliver_notification_task.delay",
        lambda delivery_id: None,
    )
    delivery_ids = ensure_notification_deliveries(task.pk)
    assert len(delivery_ids) == 1
    assert ensure_notification_deliveries(task.pk) == delivery_ids
    assert NotificationDelivery.objects.filter(task=task, recipient=owner).count() == 1
    delivery = NotificationDelivery.objects.get(pk=delivery_ids[0])
    provider = _AcceptedButAmbiguous(persist=True)

    assert deliver_notification(delivery.pk, provider=provider) == (
        NotificationDelivery.State.RECONCILING
    )
    assert reconcile_notification(delivery.pk, provider=provider) == (
        NotificationDelivery.State.SENT
    )
    assert deliver_notification(delivery.pk, provider=provider) == NotificationDelivery.State.SENT
    assert FakeGmailMessage.objects.filter(rfc_message_id=delivery.message_id).count() == 1

    task.refresh_from_db()
    assert task.status == HumanTask.Status.OPEN
    raw = FakeGmailMessage.objects.get(rfc_message_id=delivery.message_id).raw_message
    parsed = BytesParser(policy=policy.default).parsebytes(bytes(raw))
    assert parsed["Subject"] == "Hay una conversación que necesita revisión"
    body = parsed.get_content()
    assert delivery.secure_url in body
    assert scenario.inbound.body_text not in body
    assert task.friendly_summary not in body
    assert scenario.inbound.subject not in body


@pytest.mark.django_db
@override_settings(PUBLIC_BASE_URL="")
def test_missing_public_base_url_keeps_task_open_and_records_failed_alert(
    owner,
    private_catalog_dir,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    task = HumanTask.objects.create(
        workspace=scenario.contact.workspace,
        contact=scenario.contact,
        conversation=scenario.conversation,
        kind="REPLY_REVIEW",
        reason="OTHER",
        status=HumanTask.Status.OPEN,
        friendly_summary="Revisar.",
        opened_at=timezone.now(),
    )

    assert ensure_notification_deliveries(task.pk) == ()
    delivery = NotificationDelivery.objects.get(task=task, recipient=owner)
    assert delivery.state == NotificationDelivery.State.FAILED
    assert "PUBLIC_BASE_URL" in delivery.error
    task.refresh_from_db()
    assert task.status == HumanTask.Status.OPEN


@pytest.mark.django_db
@override_settings(
    PUBLIC_BASE_URL="https://outreach.example",
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
)
def test_notification_provider_failure_keeps_dashboard_task_open(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    task = HumanTask.objects.create(
        workspace=scenario.contact.workspace,
        contact=scenario.contact,
        conversation=scenario.conversation,
        kind="REPLY_REVIEW",
        reason="PROVIDER_FAILURE",
        status=HumanTask.Status.OPEN,
        friendly_summary="Revisar.",
        opened_at=timezone.now(),
    )
    monkeypatch.setattr(
        "apps.automation.tasks.deliver_notification_task.delay",
        lambda delivery_id: None,
    )
    delivery_id = ensure_notification_deliveries(task.pk)[0]

    def invalid_provider(*args, **kwargs):
        del args, kwargs
        raise ValidationError("No se pudo descifrar la credencial de Gmail.")

    monkeypatch.setattr("apps.automation.notifications.provider_for_connection", invalid_provider)
    assert deliver_notification(delivery_id) == NotificationDelivery.State.FAILED
    task.refresh_from_db()
    assert task.status == HumanTask.Status.OPEN


@pytest.mark.django_db
@override_settings(PUBLIC_BASE_URL="https://outreach.example")
def test_recovery_dispatches_live_decisions_and_pending_notifications(
    owner,
    private_catalog_dir,
    monkeypatch,
) -> None:
    scenario = _scenario(owner, private_catalog_dir=private_catalog_dir)
    task = HumanTask.objects.create(
        workspace=scenario.contact.workspace,
        contact=scenario.contact,
        conversation=scenario.conversation,
        kind="REPLY_REVIEW",
        reason="RECOVERY_TEST",
        status=HumanTask.Status.OPEN,
        friendly_summary="Revisar.",
        opened_at=timezone.now(),
    )
    key = f"recovery-alert:{task.pk}"
    delivery = NotificationDelivery.objects.create(
        task=task,
        recipient=owner,
        secure_url=f"https://outreach.example/contactos/{scenario.contact.pk}/#tarea-{task.pk}",
        idempotency_key=key,
        message_id=f"<{sha256(key.encode()).hexdigest()}@contact-outreach.local>",
    )
    decisions: list[str] = []
    notifications: list[str] = []
    monkeypatch.setattr(
        "apps.automation.tasks.execute_reply_decision_task.delay",
        lambda value: decisions.append(value),
    )
    monkeypatch.setattr(
        "apps.automation.tasks.deliver_notification_task.delay",
        lambda value: notifications.append(value),
    )

    assert recover_automation_actions() == 2
    assert decisions == [str(scenario.decision.pk)]
    assert notifications == [str(delivery.pk)]
