from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.automation.execution import deliver_authorized_outbound
from apps.automation.models import (
    AutomaticActionReservation,
    ContactCommunicationPlan,
    FollowUpTopic,
    HumanTask,
    ReplyAutomationConfiguration,
    ScheduledContactAttempt,
)
from apps.automation.scheduled import (
    approve_contact_follow_up_topic,
    authorize_scheduled_contact_attempt,
    create_due_scheduled_attempts,
    edit_scheduled_contact_draft,
    process_scheduled_contact_attempt,
    record_genuine_contact_interaction,
    set_contact_communication_plan_state,
    snooze_contact_communication_plan,
)
from apps.automation.services import close_human_task
from apps.automation.tasks import dispatch_scheduled_contacts, recover_automation_actions
from apps.campaigns.models import OutboundAttachment, OutboundMessage
from apps.contacts.models import (
    CampaignEnrollment,
    CommunicationRestriction,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
)
from apps.contacts.services import apply_inbound_contact_effect
from apps.integrations.contracts import (
    ScheduledContactDraftRequest,
    ScheduledContactDraftResult,
    ValidationProviderError,
)
from apps.integrations.fakes import FakeGmailProvider
from apps.integrations.gmail import GMAIL_SCOPES
from apps.integrations.llm import MockLLMProvider
from apps.integrations.llm_inputs import scheduled_contact_input_character_count
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import FakeGmailMessage, GmailConnection, InboundMessage


def _contact(owner: User) -> tuple[Contact, EmailAddress]:
    workspace = owner.membership.workspace
    organization = Organization.objects.create(
        workspace=workspace,
        name="Taller del Centro",
    )
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="ventas@taller.example",
        normalized_email="ventas@taller.example",
        domain="taller.example",
        is_preferred=True,
        validity=EmailAddress.Validity.VALID,
        validated_at=timezone.now(),
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        name="María",
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    return contact, email


def _ready_gmail(owner: User) -> GmailConnection:
    return GmailConnection.objects.create(
        workspace=owner.membership.workspace,
        owner=owner,
        email="equipo@example.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh-token"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )


def _plan(
    owner: User,
    contact: Contact,
    email: EmailAddress,
    *,
    due_at: datetime | None = None,
    mode: str = FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
    topic_name: str = "Preguntar cómo está",
) -> ContactCommunicationPlan:
    topic = FollowUpTopic.objects.create(
        workspace=owner.membership.workspace,
        name=f"{topic_name} {str(contact.pk)[:8]}",
        objective="Preguntar de manera cordial cómo están.",
        cadence_days=30,
        mode=mode,
        next_due_at=due_at or timezone.now() - timedelta(minutes=1),
        active=True,
        created_by=owner,
        updated_by=owner,
    )
    return approve_contact_follow_up_topic(
        actor=owner,
        contact_id=contact.pk,
        topic_id=topic.pk,
    )


class FailingScheduledProvider(MockLLMProvider):
    def draft_scheduled_contact(
        self,
        request: ScheduledContactDraftRequest,
    ) -> ScheduledContactDraftResult:
        del request
        raise ValidationProviderError("provider failed")


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
    RELATIONSHIP_KILL_SWITCH=False,
)
def test_due_review_plan_creates_one_editable_draft_without_gmail_or_pdfs(
    owner: User,
) -> None:
    contact, email = _contact(owner)
    _ready_gmail(owner)
    plan = _plan(owner, contact, email)
    provider = MockLLMProvider()

    first = create_due_scheduled_attempts()
    second = create_due_scheduled_attempts()
    assert first == second
    assert ScheduledContactAttempt.objects.filter(plan=plan).count() == 1

    attempt = process_scheduled_contact_attempt(first[0], provider=provider)

    assert attempt.state == ScheduledContactAttempt.State.DRAFT_REVIEW
    assert len(provider.scheduled_contact_requests) == 1
    request = provider.scheduled_contact_requests[0]
    assert request.purpose.startswith("Preguntar cómo está")
    assert scheduled_contact_input_character_count(request) <= 24_000
    assert attempt.context_manifest["request"]["characters"] == (
        scheduled_contact_input_character_count(request)
    )
    assert "Taller del Centro" not in str(attempt.context_manifest)
    outbound = attempt.outbound_message
    assert outbound is not None
    assert outbound.kind == OutboundMessage.Kind.SCHEDULED_CONTACT
    assert outbound.state == OutboundMessage.State.REVIEW_READY
    assert outbound.campaign_id is None
    assert outbound.conversation_id is None
    assert not OutboundAttachment.objects.filter(message=outbound).exists()

    edit_scheduled_contact_draft(
        actor=owner,
        attempt_id=attempt.pk,
        subject="Un saludo",
        body_text="Buen día. Queríamos saber cómo están.",
    )
    authorize_scheduled_contact_attempt(actor=owner, attempt_id=attempt.pk)
    outbound.refresh_from_db()
    attempt.refresh_from_db()
    assert outbound.subject == "Un saludo"
    assert outbound.state == OutboundMessage.State.QUEUED
    assert outbound.approved_by == owner
    assert attempt.state == ScheduledContactAttempt.State.AUTHORIZED


@pytest.mark.django_db
def test_due_topics_create_one_attempt_per_contact(owner: User) -> None:
    contact, email = _contact(owner)
    now = timezone.now()
    later_plan = _plan(
        owner,
        contact,
        email,
        due_at=now - timedelta(hours=1),
        topic_name="Tema menos urgente",
    )
    urgent_plan = _plan(
        owner,
        contact,
        email,
        due_at=now - timedelta(days=2),
        topic_name="Tema urgente",
    )

    attempt_ids = create_due_scheduled_attempts(at=now)
    repeated = create_due_scheduled_attempts(at=now)

    assert repeated == attempt_ids
    assert len(attempt_ids) == 1
    attempt = ScheduledContactAttempt.objects.get(pk=attempt_ids[0])
    assert attempt.plan == urgent_plan
    assert ScheduledContactAttempt.objects.filter(plan=later_plan).count() == 0


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
    RELATIONSHIP_KILL_SWITCH=False,
)
def test_automatic_plan_requires_live_mode_and_queues_new_thread(
    owner: User,
) -> None:
    contact, email = _contact(owner)
    _ready_gmail(owner)
    ReplyAutomationConfiguration.objects.create(
        workspace=owner.membership.workspace,
        mode=ReplyAutomationConfiguration.Mode.LIVE,
        live_enabled_at=timezone.now(),
        live_enabled_by=owner,
    )
    _plan(owner, contact, email, mode=FollowUpTopic.Mode.AUTOMATIC)
    attempt_id = create_due_scheduled_attempts()[0]

    attempt = process_scheduled_contact_attempt(attempt_id, provider=MockLLMProvider())

    assert attempt.state == ScheduledContactAttempt.State.AUTHORIZED
    assert attempt.authorized_at is not None
    assert attempt.outbound_message is not None
    assert attempt.outbound_message.state == OutboundMessage.State.QUEUED
    assert attempt.outbound_message.conversation_id is None
    assert AutomaticActionReservation.objects.filter(scheduled_attempt=attempt).count() == 1

    repeated = process_scheduled_contact_attempt(attempt.pk, provider=MockLLMProvider())
    assert repeated.pk == attempt.pk
    assert AutomaticActionReservation.objects.filter(scheduled_attempt=attempt).count() == 1


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    AUTO_REPLY_KILL_SWITCH=False,
    RELATIONSHIP_KILL_SWITCH=True,
)
def test_relationship_kill_switch_blocks_automatic_attempt_before_llm(
    owner: User,
) -> None:
    contact, email = _contact(owner)
    _ready_gmail(owner)
    ReplyAutomationConfiguration.objects.create(
        workspace=owner.membership.workspace,
        mode=ReplyAutomationConfiguration.Mode.LIVE,
        live_enabled_at=timezone.now(),
        live_enabled_by=owner,
    )
    _plan(owner, contact, email, mode=FollowUpTopic.Mode.AUTOMATIC)
    provider = MockLLMProvider()

    attempt = process_scheduled_contact_attempt(
        create_due_scheduled_attempts()[0],
        provider=provider,
    )

    assert attempt.state == ScheduledContactAttempt.State.INELIGIBLE
    assert "bloqueo de seguridad" in attempt.reason
    assert provider.scheduled_contact_requests == []
    assert attempt.outbound_message_id is None
    assert create_due_scheduled_attempts() == ()


@pytest.mark.django_db
def test_restriction_blocks_due_attempt_and_never_calls_llm(owner: User) -> None:
    contact, email = _contact(owner)
    _plan(owner, contact, email)
    CommunicationRestriction.objects.create(
        workspace=contact.workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=CommunicationRestriction.Kind.MANUAL,
        email_address=email,
        source="test",
        evidence="El contacto pidió una pausa.",
        created_by=owner,
    )
    provider = MockLLMProvider()

    attempt = process_scheduled_contact_attempt(
        create_due_scheduled_attempts()[0],
        provider=provider,
    )

    assert attempt.state == ScheduledContactAttempt.State.INELIGIBLE
    assert "restricción activa" in attempt.reason
    assert provider.scheduled_contact_requests == []
    assert create_due_scheduled_attempts() == ()
    assert process_scheduled_contact_attempt(attempt.pk, provider=provider).state == (
        ScheduledContactAttempt.State.INELIGIBLE
    )
    assert provider.scheduled_contact_requests == []


@pytest.mark.django_db
def test_pause_snooze_and_genuine_interaction_control_the_next_due_date(
    owner: User,
) -> None:
    contact, email = _contact(owner)
    now = timezone.now()
    plan = _plan(owner, contact, email, due_at=now - timedelta(minutes=1))
    original_attempt = ScheduledContactAttempt.objects.get(
        pk=create_due_scheduled_attempts(at=now)[0]
    )
    snoozed_until = now + timedelta(days=10)

    snooze_contact_communication_plan(
        actor=owner,
        plan_id=plan.pk,
        until=snoozed_until,
    )

    original_attempt.refresh_from_db()
    plan.refresh_from_db()
    assert original_attempt.state == ScheduledContactAttempt.State.CANCELLED
    assert plan.next_due_at == snoozed_until
    assert create_due_scheduled_attempts(at=now + timedelta(days=1)) == ()

    set_contact_communication_plan_state(
        actor=owner,
        plan_id=plan.pk,
        state=ContactCommunicationPlan.State.PAUSED,
    )
    assert create_due_scheduled_attempts(at=now + timedelta(days=20)) == ()

    interacted_at = now + timedelta(days=2)
    enrollments_before = CampaignEnrollment.objects.count()
    record_genuine_contact_interaction(contact.pk, interacted_at=interacted_at)
    plan.refresh_from_db()
    assert plan.last_interaction_at == interacted_at
    assert plan.next_due_at >= interacted_at + timedelta(days=30)
    assert CampaignEnrollment.objects.count() == enrollments_before


@pytest.mark.django_db
def test_contact_plan_ui_is_plain_language_and_vendedor_is_read_only(
    client: Client,
    owner: User,
) -> None:
    contact, _email = _contact(owner)
    topic = FollowUpTopic.objects.create(
        workspace=owner.membership.workspace,
        name="Pedir feedback",
        objective="Pedir una opinión general sobre el producto.",
        cadence_days=30,
        mode=FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
        next_due_at=timezone.now() + timedelta(days=7),
        active=True,
        created_by=owner,
        updated_by=owner,
    )
    client.force_login(owner)
    response = client.post(
        reverse("contact-follow-up-topic-approve", args=(contact.pk, topic.pk)),
    )
    assert response.status_code == 302
    page = client.get(reverse("contact-detail", args=(contact.pk,))).content.decode()
    assert "Temas de seguimiento" in page
    assert "Pedir feedback" in page
    assert "Revisar antes de enviar" in page
    assert "Posponer" in page
    assert "Próxima fecha" not in page
    assert "No incluye PDFs" not in page

    seller = User.objects.create_user(username="seller-scheduled", password="password")
    client.force_login(seller)
    seller_page = client.get(reverse("contact-detail", args=(contact.pk,))).content.decode()
    assert "Temas de seguimiento" in seller_page
    assert "Aprobar tema" not in seller_page
    assert "Detalles técnicos" not in seller_page
    assert (
        client.post(
            reverse("contact-follow-up-topic-approve", args=(contact.pk, topic.pk)),
            {"enabled": "on"},
        ).status_code
        == 403
    )


@pytest.mark.django_db
def test_reply_to_scheduled_new_thread_uses_existing_contact_without_enrollment(
    owner: User,
) -> None:
    contact, email = _contact(owner)
    connection = _ready_gmail(owner)
    conversation = Conversation.objects.create(
        workspace=contact.workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="scheduled-thread",
        subject="¿Cómo están?",
        last_message_at=timezone.now() - timedelta(days=1),
    )
    outbound = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.SCHEDULED_CONTACT,
        campaign=None,
        organization=contact.organization,
        contact=contact,
        conversation=conversation,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject="¿Cómo están?",
        body_text="Buen día. Queríamos saber cómo están.",
        state=OutboundMessage.State.SENT,
        delivery_mode="LIVE",
        idempotency_key="scheduled-existing-contact",
        message_id="<scheduled-existing-contact@example.invalid>",
        gmail_message_id="gmail-scheduled-existing-contact",
        gmail_thread_id=conversation.gmail_thread_id,
        sent_at=timezone.now() - timedelta(days=1),
    )
    plan = _plan(
        owner,
        contact,
        email,
        due_at=timezone.now() + timedelta(days=2),
    )
    received_at = timezone.now()
    inbound = InboundMessage.objects.create(
        connection=connection,
        related_outbound=outbound,
        gmail_message_id="gmail-scheduled-reply",
        gmail_thread_id=conversation.gmail_thread_id,
        message_id="<scheduled-reply@example.invalid>",
        in_reply_to=outbound.message_id,
        sender=f"María <{email.original_email}>",
        recipients=[connection.email],
        subject="Re: ¿Cómo están?",
        external_at=received_at,
        received_at=received_at,
        body_text="Estamos bien, gracias por escribir.",
        classification=InboundMessage.Classification.OTHER,
        classification_confidence="0.900",
        is_human=True,
    )

    effect = apply_inbound_contact_effect(inbound)

    inbound.refresh_from_db()
    plan.refresh_from_db()
    assert effect.contact == contact
    assert inbound.contact == contact
    assert inbound.conversation == conversation
    assert CampaignEnrollment.objects.count() == 0
    assert plan.last_interaction_at == received_at
    assert plan.next_due_at is not None
    assert plan.next_due_at >= received_at + timedelta(days=30)


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    RELATIONSHIP_KILL_SWITCH=False,
)
def test_reviewed_scheduled_contact_sends_once_in_a_new_thread_and_recalculates_cadence(
    owner: User,
    django_capture_on_commit_callbacks: Any,
) -> None:
    contact, email = _contact(owner)
    connection = _ready_gmail(owner)
    plan = _plan(owner, contact, email)
    attempt = process_scheduled_contact_attempt(
        create_due_scheduled_attempts()[0],
        provider=MockLLMProvider(),
    )
    authorize_scheduled_contact_attempt(actor=owner, attempt_id=attempt.pk)
    assert attempt.outbound_message is not None
    gmail = FakeGmailProvider(account_email=connection.email, persist=True)

    with django_capture_on_commit_callbacks(execute=True):
        state = deliver_authorized_outbound(attempt.outbound_message.pk, provider=gmail)

    attempt.refresh_from_db()
    attempt.outbound_message.refresh_from_db()
    plan.refresh_from_db()
    assert state == OutboundMessage.State.SENT
    assert attempt.state == ScheduledContactAttempt.State.SENT
    assert attempt.outbound_message.conversation is not None
    assert attempt.outbound_message.conversation.contact == contact
    assert FakeGmailMessage.objects.count() == 1
    assert plan.last_sent_at == attempt.outbound_message.sent_at
    assert plan.next_due_at is not None
    assert plan.last_sent_at is not None
    assert plan.next_due_at == plan.last_sent_at + timedelta(days=30)

    with django_capture_on_commit_callbacks(execute=True):
        repeated = deliver_authorized_outbound(attempt.outbound_message.pk, provider=gmail)
    assert repeated == OutboundMessage.State.SENT
    assert FakeGmailMessage.objects.count() == 1


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    RELATIONSHIP_KILL_SWITCH=False,
)
def test_confirmed_scheduled_send_with_foreign_thread_opens_attention_task(
    owner: User,
    django_capture_on_commit_callbacks: Any,
) -> None:
    contact, email = _contact(owner)
    connection = _ready_gmail(owner)
    _plan(owner, contact, email)
    attempt = process_scheduled_contact_attempt(
        create_due_scheduled_attempts()[0],
        provider=MockLLMProvider(),
    )
    authorize_scheduled_contact_attempt(actor=owner, attempt_id=attempt.pk)
    outbound = attempt.outbound_message
    assert outbound is not None

    other_organization = Organization.objects.create(
        workspace=contact.workspace,
        name="Otro cliente",
    )
    other_email = EmailAddress.objects.create(
        workspace=contact.workspace,
        organization=other_organization,
        original_email="otro@cliente.example",
        normalized_email="otro@cliente.example",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    other_contact = Contact.objects.create(
        workspace=contact.workspace,
        organization=other_organization,
        preferred_email=other_email,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    foreign_conversation = Conversation.objects.create(
        workspace=contact.workspace,
        contact=other_contact,
        connection=connection,
        gmail_thread_id="foreign-thread",
        subject="Otro hilo",
    )
    FakeGmailMessage.objects.create(
        rfc_message_id=outbound.message_id,
        gmail_message_id="gmail-already-sent",
        gmail_thread_id=foreign_conversation.gmail_thread_id,
        recipient=outbound.recipient,
        raw_message=b"already sent",
        idempotency_key=outbound.idempotency_key,
    )

    gmail = FakeGmailProvider(account_email=connection.email, persist=True)
    with django_capture_on_commit_callbacks(execute=True):
        state = deliver_authorized_outbound(outbound.pk, provider=gmail)

    outbound.refresh_from_db()
    attempt.refresh_from_db()
    contact.refresh_from_db()
    assert state == OutboundMessage.State.SENT
    assert outbound.conversation_id is None
    assert attempt.state == ScheduledContactAttempt.State.SENT
    task = HumanTask.objects.get(
        contact=contact,
        kind="SCHEDULED_CONTACT",
        reason="GMAIL_THREAD_OWNERSHIP_CONFLICT",
    )
    assert task.conversation_id is None
    assert task.status == HumanTask.Status.OPEN
    assert contact.automation_suspended
    foreign_conversation.refresh_from_db()
    assert foreign_conversation.contact == other_contact


@pytest.mark.django_db
def test_scheduled_failure_opens_one_contact_level_task_without_prior_conversation(
    owner: User,
) -> None:
    contact, email = _contact(owner)
    plan = _plan(owner, contact, email)
    attempt_id = create_due_scheduled_attempts()[0]

    attempt = process_scheduled_contact_attempt(
        attempt_id,
        provider=FailingScheduledProvider(),
    )

    assert attempt.state == ScheduledContactAttempt.State.HUMAN_REQUIRED
    task = HumanTask.objects.get(contact=contact, kind="SCHEDULED_CONTACT")
    assert task.conversation_id is None
    contact.refresh_from_db()
    assert contact.automation_suspended

    duplicate = process_scheduled_contact_attempt(
        attempt_id,
        provider=FailingScheduledProvider(),
    )
    assert duplicate.pk == attempt.pk
    assert HumanTask.objects.filter(contact=contact, kind="SCHEDULED_CONTACT").count() == 1
    assert create_due_scheduled_attempts() == ()

    close_human_task(
        task,
        actor=owner,
        dismiss=True,
        note="Se revisará el objetivo antes de volver a activarlo.",
    )
    contact.refresh_from_db()
    assert not contact.automation_suspended

    plan.topic.next_due_at = timezone.now() + timedelta(days=30)
    plan.topic.save(update_fields=("next_due_at", "updated_at"))
    approve_contact_follow_up_topic(
        actor=owner,
        contact_id=contact.pk,
        topic_id=plan.topic_id,
    )
    attempt.refresh_from_db()
    assert attempt.state == ScheduledContactAttempt.State.CANCELLED


@pytest.mark.django_db
@override_settings(
    SEND_MODE="live",
    SEND_KILL_SWITCH=False,
    RELATIONSHIP_KILL_SWITCH=False,
)
def test_celery_dispatch_creates_processes_and_recovers_scheduled_work(
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact, email = _contact(owner)
    _ready_gmail(owner)
    _plan(owner, contact, email)
    processed: list[str] = []
    monkeypatch.setattr(
        "apps.automation.tasks.process_scheduled_contact_attempt_task.delay",
        lambda attempt_id: processed.append(attempt_id),
    )

    assert dispatch_scheduled_contacts() == 1
    assert processed == [str(ScheduledContactAttempt.objects.get().pk)]

    attempt = process_scheduled_contact_attempt(
        ScheduledContactAttempt.objects.get().pk,
        provider=MockLLMProvider(),
    )
    authorize_scheduled_contact_attempt(actor=owner, attempt_id=attempt.pk)
    assert attempt.outbound_message is not None
    delivered: list[str] = []
    reconciled: list[str] = []
    monkeypatch.setattr(
        "apps.automation.tasks.deliver_authorized_outbound_task.delay",
        lambda message_id: delivered.append(message_id),
    )
    monkeypatch.setattr(
        "apps.automation.tasks.reconcile_authorized_outbound_task.delay",
        lambda message_id: reconciled.append(message_id),
    )

    assert recover_automation_actions() == 1
    assert delivered == [str(attempt.outbound_message.pk)]
    assert reconciled == []
    assert settings.CELERY_BEAT_SCHEDULE["dispatch-scheduled-contacts"]["schedule"] == 60.0

    attempt.outbound_message.state = OutboundMessage.State.RECONCILING
    attempt.outbound_message.next_attempt_at = timezone.now() - timedelta(minutes=1)
    attempt.outbound_message.save(update_fields=("state", "next_attempt_at", "updated_at"))
    delivered.clear()
    assert recover_automation_actions() == 1
    assert delivered == []
    assert reconciled == [str(attempt.outbound_message.pk)]
