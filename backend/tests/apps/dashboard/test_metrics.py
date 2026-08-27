from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.automation.models import HumanTask, ReplyDecision
from apps.campaigns.models import Campaign, OutboundMessage
from apps.catalogs.models import Catalog
from apps.contacts.models import (
    CampaignEnrollment,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
)
from apps.dashboard.metrics import duration_label, workspace_summary_metrics
from apps.mailbox.models import GmailConnection, InboundMessage


def _catalog(owner) -> Catalog:
    return Catalog.objects.create(
        workspace=owner.membership.workspace,
        name="Métricas",
        version=1,
        file="catalogs/metrics.pdf",
        original_filename="metricas.pdf",
        detected_mime="application/pdf",
        byte_size=1,
        sha256="a" * 64,
        uploaded_by=owner,
    )


def _enrollment(campaign: Campaign, index: int) -> tuple[CampaignEnrollment, EmailAddress]:
    workspace = campaign.workspace
    organization = Organization.objects.create(workspace=workspace, name=f"Empresa {index}")
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email=f"persona{index}@example.com",
        normalized_email=f"persona{index}@example.com",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    enrollment = CampaignEnrollment.objects.create(
        workspace=workspace,
        campaign=campaign,
        organization=organization,
        selected_email=email,
        state=CampaignEnrollment.State.INITIAL_SENT,
    )
    return enrollment, email


def _outbound(
    *,
    kind: str,
    recipient: str,
    state: str = OutboundMessage.State.SENT,
    campaign: Campaign | None = None,
    enrollment: CampaignEnrollment | None = None,
    contact: Contact | None = None,
    conversation: Conversation | None = None,
    sent_at=None,
) -> OutboundMessage:
    return OutboundMessage.objects.create(
        kind=kind,
        campaign=campaign,
        organization=enrollment.organization if enrollment is not None else None,
        campaign_enrollment=enrollment,
        contact=contact,
        conversation=conversation,
        email_address=enrollment.selected_email if enrollment is not None else None,
        recipient=recipient,
        recipient_normalized=recipient,
        subject="Propuesta comercial",
        body_text="Mensaje persistido",
        state=state,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key=f"metric:{kind}:{recipient}:{OutboundMessage.objects.count()}",
        message_id=f"<metric-{OutboundMessage.objects.count()}@example.invalid>",
        sent_at=sent_at,
    )


def _contact_conversation(
    enrollment: CampaignEnrollment,
    connection: GmailConnection,
    index: int,
) -> tuple[Contact, Conversation]:
    contact = Contact.objects.create(
        workspace=enrollment.workspace,
        organization=enrollment.organization,
        preferred_email=enrollment.selected_email,
        name=f"Persona {index}",
        created_reason=Contact.CreatedReason.HUMAN_REPLY,
    )
    conversation = Conversation.objects.create(
        workspace=enrollment.workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id=f"metric-thread-{index}",
    )
    return contact, conversation


def _inbound(
    *,
    connection: GmailConnection,
    outbound: OutboundMessage,
    enrollment: CampaignEnrollment,
    index: int,
    external_at,
    classification: str,
    is_human: bool,
    contact: Contact | None = None,
    conversation: Conversation | None = None,
) -> InboundMessage:
    return InboundMessage.objects.create(
        connection=connection,
        organization=enrollment.organization,
        campaign_enrollment=enrollment,
        contact=contact,
        conversation=conversation,
        related_outbound=outbound,
        gmail_message_id=f"metric-inbound-{index}",
        gmail_thread_id=(conversation.gmail_thread_id if conversation else f"system-{index}"),
        sender=enrollment.selected_email.normalized_email,
        recipients=[connection.email],
        subject="Re: Propuesta comercial",
        external_at=external_at,
        received_at=external_at,
        body_text="Respuesta",
        classification=classification,
        classification_confidence=Decimal("1"),
        is_human=is_human,
    )


@pytest.mark.django_db
def test_summary_metrics_are_derived_without_double_counting_replies(owner) -> None:
    workspace = owner.membership.workspace
    now = timezone.now()
    catalog = _catalog(owner)
    campaign = Campaign.objects.create(
        workspace=workspace,
        name="Campaña de métricas",
        catalog=catalog,
        created_by=owner,
        state=Campaign.State.RUNNING,
        delivery_mode=Campaign.DeliveryMode.LIVE,
    )
    enrollments = [_enrollment(campaign, index) for index in range(1, 5)]
    initials: list[OutboundMessage] = []
    for enrollment, email in enrollments:
        sent_at = now - timedelta(days=4)
        enrollment.initial_sent_at = sent_at
        enrollment.save(update_fields=("initial_sent_at", "updated_at"))
        initials.append(
            _outbound(
                kind=OutboundMessage.Kind.INITIAL,
                recipient=email.normalized_email,
                campaign=campaign,
                enrollment=enrollment,
                sent_at=sent_at,
            )
        )

    reminder = _outbound(
        kind=OutboundMessage.Kind.CAMPAIGN_REMINDER,
        recipient=enrollments[0][1].normalized_email,
        campaign=campaign,
        enrollment=enrollments[0][0],
        sent_at=now - timedelta(days=2),
    )
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="equipo@example.com",
        status=GmailConnection.Status.CONNECTED,
    )
    first_contact, first_conversation = _contact_conversation(enrollments[0][0], connection, 1)
    second_contact, second_conversation = _contact_conversation(enrollments[1][0], connection, 2)
    unsubscribe_contact, unsubscribe_conversation = _contact_conversation(
        enrollments[3][0], connection, 4
    )
    first_reply = _inbound(
        connection=connection,
        outbound=reminder,
        enrollment=enrollments[0][0],
        index=1,
        external_at=now - timedelta(days=1),
        classification=InboundMessage.Classification.INTERESTED,
        is_human=True,
        contact=first_contact,
        conversation=first_conversation,
    )
    # A second reply from the same Contact must not inflate recipient or responder rates.
    _inbound(
        connection=connection,
        outbound=reminder,
        enrollment=enrollments[0][0],
        index=2,
        external_at=now - timedelta(hours=12),
        classification=InboundMessage.Classification.OTHER,
        is_human=True,
        contact=first_contact,
        conversation=first_conversation,
    )
    second_reply = _inbound(
        connection=connection,
        outbound=initials[1],
        enrollment=enrollments[1][0],
        index=3,
        external_at=now - timedelta(days=3),
        classification=InboundMessage.Classification.INTERESTED,
        is_human=True,
        contact=second_contact,
        conversation=second_conversation,
    )
    _inbound(
        connection=connection,
        outbound=initials[2],
        enrollment=enrollments[2][0],
        index=4,
        external_at=now - timedelta(days=3),
        classification=InboundMessage.Classification.BOUNCE,
        is_human=False,
    )
    unsubscribe_reply = _inbound(
        connection=connection,
        outbound=initials[3],
        enrollment=enrollments[3][0],
        index=5,
        external_at=now - timedelta(days=3),
        classification=InboundMessage.Classification.UNSUBSCRIBE,
        is_human=True,
        contact=unsubscribe_contact,
        conversation=unsubscribe_conversation,
    )
    Contact.objects.filter(pk=first_contact.pk).update(source_inbound_message_id=first_reply.pk)
    Contact.objects.filter(pk=second_contact.pk).update(source_inbound_message_id=second_reply.pk)
    Contact.objects.filter(pk=unsubscribe_contact.pk).update(
        source_inbound_message_id=unsubscribe_reply.pk
    )

    completed = ReplyDecision.objects.create(
        workspace=workspace,
        inbound=first_reply,
        contact=first_contact,
        conversation=first_conversation,
        mode="LIVE",
        provider="fake",
        model="fake",
        policy_version="test",
        classification="INTERESTED",
        intent="PRODUCT_INFORMATION",
        action="REPLY",
        confidence=Decimal("0.95"),
        context_hash="b" * 64,
        state=ReplyDecision.State.COMPLETED,
    )
    del completed
    required = ReplyDecision.objects.create(
        workspace=workspace,
        inbound=second_reply,
        contact=second_contact,
        conversation=second_conversation,
        mode="LIVE",
        provider="fake",
        model="fake",
        policy_version="test",
        classification="INTERESTED",
        intent="MEETING_REQUEST",
        action="HUMAN",
        confidence=Decimal("0.99"),
        human_reason="MEETING_OR_DATE",
        context_hash="c" * 64,
        state=ReplyDecision.State.HUMAN_REQUIRED,
    )
    shadow_inbound = InboundMessage.objects.get(gmail_message_id="metric-inbound-2")
    ReplyDecision.objects.create(
        workspace=workspace,
        inbound=shadow_inbound,
        contact=first_contact,
        conversation=first_conversation,
        mode="SHADOW",
        provider="fake",
        model="fake",
        policy_version="test",
        classification="INTERESTED",
        intent="PRICING_OR_QUOTE",
        action="HUMAN",
        confidence=Decimal("0.99"),
        human_reason="PRICING_OR_QUOTE",
        context_hash="d" * 64,
        state=ReplyDecision.State.SHADOW_RECORDED,
    )
    HumanTask.objects.create(
        workspace=workspace,
        contact=second_contact,
        conversation=second_conversation,
        inbound=second_reply,
        decision=required,
        kind="REPLY_REVIEW",
        reason="MEETING_OR_DATE",
        friendly_summary="Revisar reunión.",
        opened_at=now - timedelta(hours=3),
        resolved_at=now - timedelta(hours=1),
        resolved_by=owner,
        status=HumanTask.Status.RESOLVED,
    )
    HumanTask.objects.create(
        workspace=workspace,
        contact=first_contact,
        conversation=first_conversation,
        kind="REPLY_REVIEW",
        reason="OTHER",
        friendly_summary="Revisar conversación.",
        opened_at=now,
    )
    _outbound(
        kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
        recipient=enrollments[0][1].normalized_email,
        contact=first_contact,
        conversation=first_conversation,
        sent_at=now,
    )
    _outbound(
        kind=OutboundMessage.Kind.SCHEDULED_CONTACT,
        recipient=enrollments[1][1].normalized_email,
        contact=second_contact,
        conversation=second_conversation,
        sent_at=now,
    )

    all_time = workspace_summary_metrics(workspace)
    assert all_time.unique_initial_recipients == 4
    assert all_time.initial_messages_sent == 4
    assert all_time.reminders_sent == 1
    assert all_time.automatic_replies_sent == 1
    assert all_time.scheduled_contacts_sent == 1
    assert all_time.unique_human_responders == 3
    assert all_time.response_rate == pytest.approx(0.75)
    assert all_time.positive_response_rate == pytest.approx(0.5)
    assert all_time.contacts_created == 3
    assert all_time.bounce_rate == pytest.approx(0.25)
    assert all_time.unsubscribe_rate == pytest.approx(0.25)
    assert all_time.automatically_resolved == 1
    assert all_time.human_required == 3
    assert all_time.open_human_tasks == 1
    assert all_time.median_human_intervention == timedelta(hours=2)
    assert all_time.median_first_response == timedelta(days=1)
    assert all_time.responses_after_initial == 2
    assert all_time.responses_after_reminder == 1

    filtered = workspace_summary_metrics(workspace, campaign_id=campaign.pk)
    assert filtered.automatic_replies_sent == 0
    assert filtered.scheduled_contacts_sent == 0
    assert filtered.response_rate == pytest.approx(0.75)


@pytest.mark.django_db
def test_metrics_dedupe_recipient_and_include_legacy_reply_after_reminder(owner) -> None:
    workspace = owner.membership.workspace
    now = timezone.now()
    catalog = _catalog(owner)
    first_campaign = Campaign.objects.create(
        workspace=workspace,
        name="Histórica uno",
        catalog=catalog,
        created_by=owner,
        state=Campaign.State.COMPLETED,
        delivery_mode=Campaign.DeliveryMode.LIVE,
    )
    second_campaign = Campaign.objects.create(
        workspace=workspace,
        name="Histórica dos",
        catalog=catalog,
        created_by=owner,
        state=Campaign.State.COMPLETED,
        delivery_mode=Campaign.DeliveryMode.LIVE,
    )
    first_initial = _outbound(
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        recipient="legacy@example.com",
        campaign=first_campaign,
        sent_at=now - timedelta(days=5),
    )
    second_initial = _outbound(
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        recipient="legacy@example.com",
        campaign=second_campaign,
        sent_at=now - timedelta(days=4),
    )
    del first_initial, second_initial
    reminder = _outbound(
        kind=OutboundMessage.Kind.CAMPAIGN_REMINDER,
        recipient="legacy@example.com",
        campaign=first_campaign,
        sent_at=now - timedelta(days=2),
    )
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="equipo@example.com",
        status=GmailConnection.Status.CONNECTED,
    )
    for index, sender in enumerate(
        ("Ana <LEGACY@example.com>", "legacy@example.com"),
        start=1,
    ):
        InboundMessage.objects.create(
            connection=connection,
            related_outbound=reminder,
            gmail_message_id=f"legacy-metric-{index}",
            gmail_thread_id="legacy-thread",
            sender=sender,
            recipients=[connection.email],
            subject="Re: Propuesta comercial",
            external_at=now - timedelta(days=1, hours=2 - index),
            received_at=now - timedelta(days=1, hours=2 - index),
            body_text="Respuesta histórica",
            classification=InboundMessage.Classification.INTERESTED,
            classification_confidence=Decimal("1"),
            is_human=True,
        )

    metrics = workspace_summary_metrics(workspace)

    assert metrics.initial_messages_sent == 2
    assert metrics.unique_initial_recipients == 1
    assert metrics.response_rate == pytest.approx(0.5)
    assert metrics.positive_response_rate == pytest.approx(0.5)
    assert metrics.unique_human_responders == 1
    assert metrics.responses_after_initial == 0
    assert metrics.responses_after_reminder == 1
    assert metrics.median_first_response is not None


def test_metric_helpers_explain_empty_values() -> None:
    assert duration_label(None) == "—"
    assert duration_label(timedelta(minutes=45)) == "45 min"
    assert duration_label(timedelta(hours=3, minutes=15)) == "3 h 15 min"
    assert duration_label(timedelta(days=2, hours=2)) == "2 días 2 h"
