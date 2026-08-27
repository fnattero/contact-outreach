from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.automation.models import EmailCandidate, HumanTask, ReplyDecision
from apps.campaigns.delivery import deliver_message, pending_message_ids
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email
from apps.contacts.models import (
    CommunicationRestriction,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
)
from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
    GmailReplyRequest,
    GmailSendRequest,
    GmailSendResult,
    LLMProvider,
    ReplyDecisionResult,
)
from apps.integrations.fakes import FakeGmailProvider
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox import manual as manual_services
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.manual import (
    authorize_manual_reply,
    deliver_manual_reply,
    pending_manual_reply_ids,
    reconcile_manual_reply,
    recoverable_manual_reply_ids,
)
from apps.mailbox.models import FakeGmailMessage, GmailConnection, InboundMessage
from apps.mailbox.sync import process_inbound_reply, sync_gmail_connection
from apps.mailbox.tasks import deliver_manual_reply_task
from apps.prospects.email_validation import MockMXResolver
from apps.prospects.models import AIAnalysis, Prospect, ProspectEmail

NOW = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)


def _thread_fixture(owner: User, *, suffix: str = "one") -> tuple[Campaign, OutboundMessage]:
    catalog = create_catalog(
        name=f"Mailbox {suffix}",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            f"%PDF-1.4\n% {suffix}\n1 0 obj\n<<>>\nendobj\n%%EOF".encode(),
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        name=f"Campaña {suffix}",
        state=Campaign.State.RUNNING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        window_start="00:01",
        window_end="23:59",
        catalog=catalog,
        created_by=owner,
    )
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Taller",
        zone_snapshot="CABA",
        location_snapshot="CABA",
        query_text="fixture",
        normalized_query=f"fixture-{suffix}",
    )
    run = SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="fake",
        idempotency_key=f"mailbox-run:{suffix}",
        requested_limit=10,
    )
    prospect = Prospect.objects.create(
        campaign=campaign,
        source_run=run,
        name=f"Taller {suffix}",
        normalized_name=f"taller {suffix}",
        pipeline_state=Prospect.PipelineState.QUEUED,
    )
    recipient = f"ventas-{suffix}@example.com"
    prospect_email = ProspectEmail.objects.create(
        prospect=prospect,
        original_email=recipient,
        normalized_email=recipient,
        domain="example.com",
        local_part=f"ventas-{suffix}",
        source="fixture",
        mx_status=ProspectEmail.MXStatus.VALID,
        mx_checked_at=timezone.now(),
        is_primary=True,
    )
    analysis = AIAnalysis.objects.create(
        prospect=prospect,
        input_hash=(suffix.encode().hex() + "0" * 64)[:64],
        prompt_version="v1",
        schema_version="v1",
        provider="fake",
        model="fake",
        analyzed_at=timezone.now(),
        status=AIAnalysis.Status.VALID,
        relevance_score=90,
        confidence="0.9",
        prompt_text="fixture",
    )
    root = OutboundMessage.objects.create(
        campaign=campaign,
        prospect=prospect,
        prospect_email=prospect_email,
        analysis=analysis,
        recipient=recipient,
        recipient_normalized=recipient,
        subject="PUBLICIDAD - Consulta técnica",
        body_text="Mensaje inicial con catálogo.",
        catalog=catalog,
        catalog_version=catalog.version,
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key=f"mailbox-message:{suffix}",
        message_id=f"<root-{suffix}@contact-outreach.local>",
        gmail_message_id=f"gmail-root-{suffix}",
        gmail_thread_id=f"thread-{suffix}",
        sent_at=timezone.now() - timedelta(hours=1),
    )
    return campaign, root


def _connection(owner: User, *, history_id: str = "0") -> GmailConnection:
    return GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("fake-refresh-token"),
        history_id=history_id,
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )


def _direct_contact_fixture(
    owner: User,
) -> tuple[GmailConnection, Organization, EmailAddress, Contact]:
    workspace = owner.membership.workspace
    connection = _connection(owner)
    organization = Organization.objects.create(
        workspace=workspace,
        name="Cliente preexistente",
        normalized_name="cliente preexistente",
    )
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="cliente-directo@example.com",
        normalized_email="cliente-directo@example.com",
        domain="example.com",
        is_preferred=True,
        validity=EmailAddress.Validity.VALID,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        name="Cliente preexistente",
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
        created_by=owner,
    )
    return connection, organization, email, contact


@pytest.mark.django_db
def test_incremental_sync_imports_only_campaign_threads_and_is_idempotent(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner)
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Tenemos interés. ¿Podrían llamarnos?",
        body_html=(
            '<script>alert(1)</script><p onclick="bad()">Tenemos <strong>interés</strong>.</p>'
        ),
        in_reply_to=root.message_id,
        references=(root.message_id,),
    )
    fake.inject_inbound(
        thread_id="different-gmail-thread",
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="También me interesa.",
        references=(root.message_id,),
    )
    fake.inject_inbound(
        thread_id="unrelated-thread",
        sender="stranger@example.net",
        recipient=connection.email,
        subject="Mensaje ajeno",
        body_text="No pertenece a ninguna campaña.",
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 2
    assert sync_gmail_connection(connection.pk, provider=fake) == 0
    assert InboundMessage.objects.count() == 2
    first = InboundMessage.objects.get(gmail_thread_id=root.gmail_thread_id)
    assert first.related_outbound == root
    assert first.related_outbound.prospect == root.prospect
    # Non-deterministic analysis is deliberately outside the transaction that holds the
    # Gmail synchronization lock. The durable reply and Contact are available first.
    assert first.classification == InboundMessage.Classification.OTHER
    assert first.contact_id is not None
    assert first.conversation_id is not None
    assert process_inbound_reply(first.pk, resolver=MockMXResolver()) == (
        InboundMessage.Classification.OTHER
    )
    first.refresh_from_db()
    assert first.classification == InboundMessage.Classification.OTHER
    assert first.reply_decision.state == ReplyDecision.State.SHADOW_RECORDED
    assert "script" not in first.body_html_sanitized
    assert "onclick" not in first.body_html_sanitized
    assert "Tenemos interés" in first.body_text

    client.force_login(owner)
    dashboard = client.get(reverse("dashboard"))
    responses = client.get(reverse("responses"))
    assert "2 mensajes recibidos" in dashboard.content.decode()
    assert root.prospect.name in responses.content.decode()
    assert "Mensaje ajeno" not in responses.content.decode()
    rfc_linked = InboundMessage.objects.get(gmail_thread_id="different-gmail-thread")
    rfc_thread = client.get(reverse("response-thread", args=(rfc_linked.pk,)))
    assert "Mensaje inicial con catálogo" in rfc_thread.content.decode()


@pytest.mark.django_db
def test_incremental_sync_imports_direct_email_from_existing_contact(
    client: Client,
    owner: User,
) -> None:
    connection, organization, email, contact = _direct_contact_fixture(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id="direct-contact-thread",
        sender=f"Cliente <{email.original_email}>",
        recipient=connection.email,
        subject="Consulta directa",
        body_text="Hola, queria consultar por stock.",
        rfc_message_id="<direct-contact@example.com>",
    )
    fake.inject_inbound(
        thread_id="unknown-direct-thread",
        sender="desconocido@example.net",
        recipient=connection.email,
        subject="Mensaje externo",
        body_text="No soy un contacto cargado.",
        rfc_message_id="<unknown-direct@example.net>",
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    assert sync_gmail_connection(connection.pk, provider=fake) == 0

    inbound = InboundMessage.objects.get(gmail_thread_id="direct-contact-thread")
    assert inbound.related_outbound is None
    assert inbound.organization == organization
    assert inbound.contact == contact
    assert inbound.conversation is not None
    assert inbound.conversation.contact == contact
    assert inbound.classification == InboundMessage.Classification.OTHER
    assert InboundMessage.objects.filter(subject="Mensaje externo").exists() is False

    assert process_inbound_reply(inbound.pk, resolver=MockMXResolver()) == (
        InboundMessage.Classification.OTHER
    )
    inbound.refresh_from_db()
    assert inbound.reply_decision.state == ReplyDecision.State.SHADOW_RECORDED

    client.force_login(owner)
    responses = client.get(reverse("responses"))
    thread = client.get(reverse("response-thread", args=(inbound.pk,)))
    export = client.get(reverse("responses-export"))
    assert responses.status_code == thread.status_code == export.status_code == 200
    assert "Cliente preexistente" in responses.content.decode()
    assert "Sin campaña" in responses.content.decode()
    assert "Contacto directo" not in responses.content.decode()
    assert "Consulta directa" in thread.content.decode()
    assert "Sin campaña" in thread.content.decode()
    assert "Sin campaña" in export.content.decode()


@pytest.mark.django_db
def test_sync_imports_reply_to_campaignless_contact_message(
    owner: User,
) -> None:
    workspace = owner.membership.workspace
    connection = _connection(owner)
    organization = Organization.objects.create(
        workspace=workspace,
        name="Cliente con seguimiento",
        normalized_name="cliente con seguimiento",
    )
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="cliente-seguimiento@example.com",
        normalized_email="cliente-seguimiento@example.com",
        domain="example.com",
        is_preferred=True,
        validity=EmailAddress.Validity.VALID,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        name="Cliente con seguimiento",
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
        created_by=owner,
    )
    root = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
        organization=organization,
        contact=contact,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject="Localidades",
        body_text="Llegamos a todo el país.",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="automatic-contact-follow-up",
        message_id="<automatic-contact-follow-up@example.invalid>",
        gmail_message_id="automatic-contact-follow-up",
        gmail_thread_id="contact-follow-up-thread",
        sent_at=timezone.now() - timedelta(minutes=5),
    )
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=email.original_email,
        recipient=connection.email,
        subject=root.subject,
        body_text="Gracias, quería consultar otra cosa.",
        in_reply_to=root.message_id,
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1

    inbound = InboundMessage.objects.get(related_outbound=root)
    assert inbound.contact == contact
    assert inbound.conversation is not None


@pytest.mark.django_db
def test_sync_preserves_the_direct_parent_inside_a_multi_message_thread(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign, root = _thread_fixture(owner, suffix="direct-parent")
    direct_parent = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
        campaign=campaign,
        prospect=root.prospect,
        prospect_email=root.prospect_email,
        recipient=root.recipient,
        recipient_normalized=root.recipient_normalized,
        subject=root.subject,
        body_text="Respuesta intermedia con información aprobada.",
        catalog=root.catalog,
        catalog_version=root.catalog_version,
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="direct-parent-automatic",
        message_id="<direct-parent-automatic@contact-outreach.local>",
        gmail_message_id="gmail-direct-parent-automatic",
        gmail_thread_id=root.gmail_thread_id,
        sent_at=timezone.now(),
    )
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Gracias, tengo otra pregunta.",
        in_reply_to=direct_parent.message_id,
        references=(root.message_id, direct_parent.message_id),
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1

    inbound = InboundMessage.objects.get(gmail_thread_id=root.gmail_thread_id)
    assert inbound.related_outbound == direct_parent


@pytest.mark.django_db
def test_sync_persists_literal_mailto_candidate_before_sanitizing_html(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="mailto")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Envíenla a esta dirección.",
        body_html=(
            '<p>Envíenla a <a href="mailto:propuestas%40example.org">esta dirección</a>.</p>'
        ),
        in_reply_to=root.message_id,
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1

    inbound = InboundMessage.objects.get(gmail_thread_id=root.gmail_thread_id)
    candidate = EmailCandidate.objects.get(inbound=inbound)
    assert candidate.normalized_email == "propuestas@example.org"
    assert candidate.region == EmailCandidate.Region.NEW_CONTENT
    assert candidate.source == EmailCandidate.Source.MAILTO
    assert "mailto" not in inbound.body_html_sanitized


@pytest.mark.django_db
def test_expired_history_uses_bounded_fake_fallback_and_advances_cursor(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="fallback")
    connection = _connection(owner, history_id="expired")
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Hola, me interesa.",
        in_reply_to=root.message_id,
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    connection.refresh_from_db()
    assert connection.history_id == "1"
    assert connection.last_sync_at is not None


@pytest.mark.django_db
def test_unsubscribe_suppresses_future_campaigns_and_bounce_invalidates_email(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign, root = _thread_fixture(owner, suffix="unsubscribe")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="BAJA, no me contacten nuevamente.",
        in_reply_to=root.message_id,
    )
    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    suppression = SuppressionEntry.objects.get(normalized_email=root.recipient_normalized)
    assert suppression.reason == SuppressionEntry.Reason.UNSUBSCRIBE
    root.refresh_from_db()
    contact = Contact.objects.get(organization_id=root.organization_id)
    assert contact.status == Contact.Status.UNSUBSCRIBED
    assert CommunicationRestriction.objects.filter(
        email_address__normalized_email=root.recipient_normalized,
        kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
        revoked_at__isnull=True,
    ).exists()

    later_analysis = AIAnalysis.objects.create(
        prospect=root.prospect,
        input_hash="f" * 64,
        prompt_version="v2",
        schema_version="v1",
        provider="fake",
        model="fake",
        analyzed_at=timezone.now(),
        status=AIAnalysis.Status.VALID,
        relevance_score=90,
        confidence="0.9",
        prompt_text="fixture",
    )
    later = OutboundMessage.objects.create(
        campaign=campaign,
        prospect=root.prospect,
        prospect_email=root.prospect_email,
        analysis=later_analysis,
        recipient=root.recipient,
        recipient_normalized=root.recipient_normalized,
        subject=root.subject,
        body_text="No debe salir.",
        catalog=root.catalog,
        catalog_version=root.catalog_version,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        state=OutboundMessage.State.QUEUED,
        approved_at=timezone.now(),
        approved_by=owner,
        idempotency_key="future-after-unsubscribe",
    )
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        assert deliver_message(later.pk, now=NOW) == OutboundMessage.State.INELIGIBLE
    assert "ya es un contacto" in OutboundMessage.objects.get(pk=later.pk).error

    # Bounce has deterministic precedence even when the quoted footer contains BAJA.
    _, bounce_root = _thread_fixture(owner, suffix="bounce")
    fake.inject_inbound(
        thread_id=bounce_root.gmail_thread_id,
        sender="mailer-daemon@example.net",
        recipient=connection.email,
        subject="Delivery Status Notification (Failure)",
        body_text="User unknown\n\n> Si no querés recibir mensajes, respondé BAJA.",
        in_reply_to=bounce_root.message_id,
    )
    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    bounce_root.prospect_email.refresh_from_db()
    bounce_root.refresh_from_db()
    assert bounce_root.prospect_email.is_invalid is True
    assert not Contact.objects.filter(organization_id=bounce_root.organization_id).exists()
    assert (
        EmailAddress.objects.get(
            organization_id=bounce_root.organization_id,
            normalized_email=bounce_root.recipient_normalized,
        ).validity
        == EmailAddress.Validity.INVALID
    )
    assert InboundMessage.objects.get(related_outbound=bounce_root).classification == "BOUNCE"


@pytest.mark.django_db
def test_auto_reply_is_persisted_without_promoting_a_contact(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="automatic")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject="Respuesta automática: fuera de la oficina",
        body_text="Estoy fuera de la oficina.",
        in_reply_to=root.message_id,
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    inbound = InboundMessage.objects.get(related_outbound=root)
    root.refresh_from_db()
    assert inbound.classification == InboundMessage.Classification.AUTO_REPLY
    assert inbound.contact_id is None
    assert not Contact.objects.filter(organization_id=root.organization_id).exists()


@pytest.mark.django_db
def test_human_reply_analysis_is_enqueued_only_after_commit(
    owner: User,
    private_catalog_dir: Path,
    django_capture_on_commit_callbacks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="post-commit")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Quisiera conocer más sobre el producto.",
        in_reply_to=root.message_id,
    )
    decision_processor = Mock(side_effect=AssertionError("LLM ejecutado bajo el lock de sync"))
    queued = Mock()
    monkeypatch.setattr(
        "apps.automation.services.process_inbound_decision",
        decision_processor,
    )
    monkeypatch.setattr("apps.mailbox.tasks.process_inbound_reply_task.delay", queued)

    with django_capture_on_commit_callbacks(execute=True):
        assert sync_gmail_connection(connection.pk, provider=fake) == 1

    inbound = InboundMessage.objects.get(related_outbound=root)
    decision_processor.assert_not_called()
    queued.assert_called_once_with(str(inbound.pk))
    assert inbound.contact_id is not None
    assert inbound.conversation_id is not None


@pytest.mark.django_db
def test_sync_imports_manual_gmail_reply_into_contact_and_closes_attention_task(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="manual-gmail-import")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Quisiera coordinar una llamada.",
        in_reply_to=root.message_id,
        rfc_message_id="<manual-gmail-inbound@example.com>",
    )
    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    inbound = InboundMessage.objects.get(gmail_message_id__startswith="fake-inbound-")
    assert inbound.contact_id is not None
    assert inbound.conversation_id is not None
    task = HumanTask.objects.create(
        workspace=connection.workspace,
        contact=inbound.contact,
        conversation=inbound.conversation,
        inbound=inbound,
        kind="REPLY_REVIEW",
        reason="MEETING_OR_DATE",
        status=HumanTask.Status.OPEN,
        friendly_summary="La respuesta necesita una revisión.",
        opened_at=timezone.now(),
    )
    inbound.conversation.automation_suspended = True
    inbound.conversation.save(update_fields=("automation_suspended", "updated_at"))
    decision = ReplyDecision.objects.create(
        workspace=connection.workspace,
        inbound=inbound,
        contact=inbound.contact,
        conversation=inbound.conversation,
        mode="LIVE",
        provider="fake",
        model="fake",
        policy_version="reply-policy-v1",
        classification="INTERESTED",
        intent="APPROVED_PRODUCT_INFORMATION",
        action="REPLY",
        confidence="0.990",
        proposed_body="Respuesta preparada.",
        context_manifest={},
        context_hash="a" * 64,
        state=ReplyDecision.State.AUTO_ELIGIBLE,
    )
    automatic = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.AUTOMATIC_REPLY,
        campaign=root.campaign,
        organization=inbound.organization,
        campaign_enrollment=inbound.campaign_enrollment,
        contact=inbound.contact,
        conversation=inbound.conversation,
        email_address=inbound.contact.preferred_email,
        parent_inbound=inbound,
        recipient=root.recipient,
        recipient_normalized=root.recipient_normalized,
        subject=root.subject,
        body_text="Respuesta automática que no debe salir.",
        state=OutboundMessage.State.QUEUED,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="automatic-before-manual-gmail",
        semantic_action_key="automatic-before-manual-gmail",
        message_id="<automatic-before-manual-gmail@example.invalid>",
        gmail_thread_id=root.gmail_thread_id,
    )
    fake.inject_sent(
        thread_id=root.gmail_thread_id,
        recipient=root.recipient,
        subject=root.subject,
        body_text="Perfecto, te llamo mañana.",
        in_reply_to=inbound.message_id,
        references=(root.message_id, inbound.message_id),
        rfc_message_id="<manual-gmail-reply@example.com>",
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1

    manual = OutboundMessage.objects.get(
        kind=OutboundMessage.Kind.MANUAL_REPLY,
        parent_inbound=inbound,
    )
    assert manual.state == OutboundMessage.State.SENT
    assert manual.body_text == "Perfecto, te llamo mañana."
    assert manual.gmail_thread_id == root.gmail_thread_id
    assert manual.contact_id == inbound.contact_id
    decision.refresh_from_db()
    automatic.refresh_from_db()
    assert decision.state == ReplyDecision.State.MANUAL_REPLY_RECORDED
    assert automatic.state == OutboundMessage.State.CANCELLED
    assert "respuesta manual" in automatic.error
    task.refresh_from_db()
    inbound.conversation.refresh_from_db()
    assert task.status == HumanTask.Status.RESOLVED
    assert task.resolution_note == "Respondida manualmente."
    assert task.resolved_by == owner
    assert not inbound.conversation.automation_suspended

    from apps.contacts.queries import conversation_timelines

    timeline = conversation_timelines(inbound.contact, include_simulations=False)
    assert any(
        item.body == "Perfecto, te llamo mañana."
        for conversation in timeline
        for item in conversation.items
    )
    assert any(
        conversation.automation_label == "Respondido manualmente" for conversation in timeline
    )
    provider = Mock(spec=LLMProvider)
    assert process_inbound_reply(inbound.pk, provider=provider) == inbound.classification
    provider.decide_reply.assert_not_called()


@pytest.mark.django_db
def test_sync_does_not_reimport_an_outbound_message_already_owned_by_the_app(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="manual-gmail-dedupe")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.send(
        GmailSendRequest(
            recipient=root.recipient,
            raw_message=b"app-owned-message",
            message_id=root.message_id,
            correlation_id="test",
            idempotency_key="app-owned-message",
        )
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 0
    assert not InboundMessage.objects.exists()


@pytest.mark.django_db
def test_shadow_reply_decision_is_idempotent_and_never_sends_gmail(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="shadow-idempotent")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Mandá la propuesta a propuestas@example.org.",
        in_reply_to=root.message_id,
    )
    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    inbound = InboundMessage.objects.get(related_outbound=root)
    provider = Mock(spec=LLMProvider)
    provider.decide_reply.return_value = ReplyDecisionResult(
        classification="INTERESTED",
        intent="MEETING_OR_DATE",
        action="HUMAN",
        confidence=0.99,
        candidate_id=None,
        fact_revision_ids=(),
        proposed_body=None,
        human_reason="MEETING_OR_DATE",
    )
    resolver = MockMXResolver()

    assert process_inbound_reply(inbound.pk, provider=provider, resolver=resolver) == (
        InboundMessage.Classification.INTERESTED
    )
    assert process_inbound_reply(inbound.pk, provider=provider, resolver=resolver) == (
        InboundMessage.Classification.INTERESTED
    )

    provider.decide_reply.assert_called_once()
    decision = ReplyDecision.objects.get(inbound=inbound)
    assert decision.state == ReplyDecision.State.SHADOW_RECORDED
    assert decision.mode == "SHADOW"
    assert EmailCandidate.objects.filter(
        inbound=inbound,
        normalized_email="propuestas@example.org",
        region=EmailCandidate.Region.NEW_CONTENT,
    ).exists()
    assert not FakeGmailMessage.objects.filter(
        direction=FakeGmailMessage.Direction.OUTBOUND
    ).exists()


@pytest.mark.django_db
def test_deterministic_unsubscribe_runs_when_llm_provider_cannot_be_constructed(
    owner: User,
    private_catalog_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="deterministic-first")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="BAJA inmediata.",
        in_reply_to=root.message_id,
    )
    decision_processor = Mock(side_effect=AuthenticationError("API key faltante"))
    monkeypatch.setattr(
        "apps.automation.services.process_inbound_decision",
        decision_processor,
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    decision_processor.assert_not_called()
    assert InboundMessage.objects.get().classification == "UNSUBSCRIBE"
    assert SuppressionEntry.objects.filter(
        normalized_email=root.recipient_normalized,
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
    ).exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_manual_reply_is_explicit_idempotent_and_stays_in_thread(
    client: Client,
    owner: User,
    private_catalog_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="manual")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Me interesa, llamame mañana.",
        in_reply_to=root.message_id,
        references=(root.message_id,),
        rfc_message_id="<reply-from-prospect@example.com>",
    )
    sync_gmail_connection(connection.pk, provider=fake)
    inbound = InboundMessage.objects.get()
    assert inbound.contact is not None
    assert inbound.conversation is not None
    inbound.conversation.automation_suspended = True
    inbound.conversation.save(update_fields=("automation_suspended", "updated_at"))
    review_task = HumanTask.objects.create(
        workspace=root.campaign.workspace,
        contact=inbound.contact,
        conversation=inbound.conversation,
        inbound=inbound,
        kind="REPLY_REVIEW",
        reason="MEETING_OR_DATE",
        status=HumanTask.Status.OPEN,
        friendly_summary="Quiere coordinar una llamada.",
        opened_at=timezone.now(),
    )
    request_key = uuid.uuid4()
    client.force_login(owner)

    assert client.get(reverse("manual-reply", args=(inbound.pk,))).status_code == 405
    payload = {"body_text": "Perfecto, te llamo mañana.", "idempotency_key": request_key}
    anonymous = Client()
    assert anonymous.post(reverse("manual-reply", args=(inbound.pk,)), payload).status_code == 302
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert csrf_client.post(reverse("manual-reply", args=(inbound.pk,)), payload).status_code == 403
    delay = Mock()
    monkeypatch.setattr(deliver_manual_reply_task, "delay", delay)
    first = client.post(reverse("manual-reply", args=(inbound.pk,)), payload)
    second = client.post(reverse("manual-reply", args=(inbound.pk,)), payload)
    assert first.status_code == second.status_code == 302

    manual = OutboundMessage.objects.get(kind=OutboundMessage.Kind.MANUAL_REPLY)
    assert manual.state == OutboundMessage.State.QUEUED
    delay.assert_called_once_with(str(manual.pk))
    assert not FakeGmailMessage.objects.filter(
        direction=FakeGmailMessage.Direction.OUTBOUND,
        rfc_message_id=manual.message_id,
    ).exists()
    with django_capture_on_commit_callbacks(execute=True):
        assert deliver_manual_reply_task(str(manual.pk)) == OutboundMessage.State.SENT
    manual.refresh_from_db()
    review_task.refresh_from_db()
    inbound.conversation.refresh_from_db()
    assert manual.state == OutboundMessage.State.SENT
    assert review_task.status == HumanTask.Status.RESOLVED
    assert review_task.resolved_by == owner
    assert review_task.resolution_note == "Respondida manualmente."
    assert not inbound.conversation.automation_suspended
    assert manual.gmail_thread_id == root.gmail_thread_id
    assert manual.parent_inbound == inbound
    assert manual.sent_by == owner
    assert manual.subject == root.subject
    assert manual.in_reply_to == inbound.message_id
    assert root.message_id in manual.references
    assert inbound.message_id in manual.references
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.MANUAL_REPLY).count() == 1
    assert manual.pk not in pending_message_ids()
    with pytest.raises(ValidationError, match="confirmación explícita"):
        deliver_message(manual.pk)

    fake_reply = FakeGmailMessage.objects.get(
        direction=FakeGmailMessage.Direction.OUTBOUND,
        rfc_message_id=manual.message_id,
    )
    parsed = BytesParser(policy=policy.default).parsebytes(bytes(fake_reply.raw_message))
    assert parsed["Subject"] == root.subject
    assert parsed["In-Reply-To"] == inbound.message_id
    assert root.message_id in str(parsed["References"])
    assert parsed.get_content().strip() == "Perfecto, te llamo mañana."
    assert parsed.is_multipart() is False

    thread = client.get(reverse("response-thread", args=(inbound.pk,)))
    page = thread.content.decode()
    assert page.index("Mensaje inicial con catálogo") < page.index("Me interesa")
    assert page.index("Me interesa") < page.index("Perfecto, te llamo")


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_manual_reply_rechecks_late_suppression_before_gmail_effect(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="late-suppression")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Me interesa.",
        in_reply_to=root.message_id,
    )
    sync_gmail_connection(connection.pk, provider=fake)
    inbound = InboundMessage.objects.get()
    assert inbound.contact is not None
    assert inbound.conversation is not None
    inbound.conversation.automation_suspended = True
    inbound.conversation.save(update_fields=("automation_suspended", "updated_at"))
    review_task = HumanTask.objects.create(
        workspace=root.campaign.workspace,
        contact=inbound.contact,
        conversation=inbound.conversation,
        inbound=inbound,
        kind="REPLY_REVIEW",
        reason="MEETING_OR_DATE",
        status=HumanTask.Status.OPEN,
        friendly_summary="Necesita revisión antes de responder.",
        opened_at=timezone.now(),
    )
    manual, created = authorize_manual_reply(
        actor=owner,
        inbound_id=inbound.pk,
        body_text="Te llamo mañana.",
        request_key=uuid.uuid4(),
    )
    assert created is True
    suppress_email(
        email=root.recipient,
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        actor=None,
        source="test",
    )

    assert deliver_manual_reply(manual.pk, provider=fake) == OutboundMessage.State.SEND_FAILED
    manual.refresh_from_db()
    review_task.refresh_from_db()
    inbound.conversation.refresh_from_db()
    assert "suprimido" in manual.error
    assert review_task.status == HumanTask.Status.OPEN
    assert inbound.conversation.automation_suspended
    assert not FakeGmailMessage.objects.filter(
        direction=FakeGmailMessage.Direction.OUTBOUND,
        rfc_message_id=manual.message_id,
    ).exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_contact_only_thread_can_authorize_and_deliver_a_manual_reply(
    client: Client,
    owner: User,
) -> None:
    workspace = owner.membership.workspace
    connection = _connection(owner)
    organization = Organization.objects.create(
        workspace=workspace,
        name="Cliente actual",
        normalized_name="cliente actual",
    )
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="cliente@example.com",
        normalized_email="cliente@example.com",
        domain="example.com",
        is_preferred=True,
        validity=EmailAddress.Validity.VALID,
    )
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        preferred_email=email,
        name="Cliente actual",
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
        created_by=owner,
    )
    conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="contact-only-thread",
        subject="Seguimiento",
    )
    root = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.SCHEDULED_CONTACT,
        organization=organization,
        contact=contact,
        conversation=conversation,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject="Seguimiento",
        body_text="¿Cómo resultó el producto?",
        state=OutboundMessage.State.SENT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key="scheduled:contact-only",
        message_id="<contact-only-root@example.invalid>",
        gmail_message_id="contact-only-root",
        gmail_thread_id=conversation.gmail_thread_id,
        sent_at=timezone.now() - timedelta(hours=1),
    )
    inbound = InboundMessage.objects.create(
        connection=connection,
        organization=organization,
        contact=contact,
        conversation=conversation,
        related_outbound=root,
        gmail_message_id="contact-only-inbound",
        gmail_thread_id=conversation.gmail_thread_id,
        message_id="<contact-only-inbound@example.invalid>",
        in_reply_to=root.message_id,
        references=[root.message_id],
        sender=email.original_email,
        recipients=[connection.email],
        subject="Re: Seguimiento",
        external_at=timezone.now(),
        received_at=timezone.now(),
        body_text="Funcionó muy bien.",
        is_human=True,
    )

    client.force_login(owner)
    thread_page = client.get(reverse("response-thread", args=(inbound.pk,)))
    outbound_page = client.get(reverse("outbound-detail", args=(root.pk,)))
    outbound_export = client.get(reverse("outbound-export"))
    response_export = client.get(reverse("responses-export"))
    assert thread_page.status_code == outbound_page.status_code == 200
    assert "¿Cómo resultó el producto?" in thread_page.content.decode()
    assert "Cliente actual" in outbound_page.content.decode()
    assert "Sin campaña" in outbound_export.content.decode()
    assert "Sin campaña" in response_export.content.decode()

    manual, created = authorize_manual_reply(
        actor=owner,
        inbound_id=inbound.pk,
        body_text="Muchas gracias por contarnos.",
        request_key=uuid.uuid4(),
    )

    assert created
    assert manual.campaign is None
    assert manual.prospect is None
    assert manual.catalog is None
    assert manual.contact == contact
    assert manual.email_address == email
    assert manual.pk in pending_manual_reply_ids()
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    assert deliver_manual_reply(manual.pk, provider=fake) == OutboundMessage.State.SENT


@pytest.mark.django_db
def test_response_thread_explains_why_automatic_reply_needs_review(
    client: Client,
    owner: User,
) -> None:
    workspace = owner.membership.workspace
    connection, organization, email, contact = _direct_contact_fixture(owner)
    conversation = Conversation.objects.create(
        workspace=workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="review-reason-thread",
        subject="Consulta por envíos",
    )
    inbound = InboundMessage.objects.create(
        connection=connection,
        organization=organization,
        contact=contact,
        conversation=conversation,
        gmail_message_id="review-reason-inbound",
        gmail_thread_id=conversation.gmail_thread_id,
        message_id="<review-reason-inbound@example.invalid>",
        sender=email.original_email,
        recipients=[connection.email],
        subject="Envíos a Córdoba",
        external_at=timezone.now(),
        received_at=timezone.now(),
        body_text="Hola, ¿hacen envíos a Córdoba?",
        classification=InboundMessage.Classification.INTERESTED,
        classification_confidence="0.900",
        is_human=True,
    )
    decision = ReplyDecision.objects.create(
        workspace=workspace,
        inbound=inbound,
        contact=contact,
        conversation=conversation,
        mode="LIVE",
        provider="fake",
        model="fake",
        policy_version="reply-policy-v1",
        classification="INTERESTED",
        intent="APPROVED_PRODUCT_INFORMATION",
        action="REPLY",
        confidence="0.900",
        human_reason="HUMAN_TASK_OPEN",
        context_manifest={},
        context_hash="a" * 64,
        state=ReplyDecision.State.REJECTED_POLICY,
        error="Hay una revisión humana pendiente para este contacto.",
    )
    HumanTask.objects.create(
        workspace=workspace,
        contact=contact,
        conversation=conversation,
        inbound=inbound,
        decision=decision,
        kind="REPLY_REVIEW",
        reason="HUMAN_TASK_OPEN",
        status=HumanTask.Status.OPEN,
        friendly_summary="Hay una revisión humana pendiente para este contacto.",
        opened_at=timezone.now(),
    )

    client.force_login(owner)
    thread = client.get(reverse("response-thread", args=(inbound.pk,)))

    page = thread.content.decode()
    assert thread.status_code == 200
    assert "Ya hay una revisión abierta para este contacto" in page
    assert "Hay una revisión humana pendiente para este contacto." in page
    assert "Resolvé la tarea pendiente" in page


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_direct_contact_thread_can_authorize_and_deliver_manual_reply(
    owner: User,
) -> None:
    connection, organization, email, contact = _direct_contact_fixture(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id="direct-manual-thread",
        sender=email.original_email,
        recipient=connection.email,
        subject="Pedido directo",
        body_text="Me pasas precio actualizado?",
        rfc_message_id="<direct-manual@example.com>",
    )
    sync_gmail_connection(connection.pk, provider=fake)
    inbound = InboundMessage.objects.get(gmail_thread_id="direct-manual-thread")

    manual, created = authorize_manual_reply(
        actor=owner,
        inbound_id=inbound.pk,
        body_text="Si, te paso la lista actualizada.",
        request_key=uuid.uuid4(),
    )

    assert created
    assert manual.campaign is None
    assert manual.organization == organization
    assert manual.contact == contact
    assert manual.conversation == inbound.conversation
    assert manual.prospect is None
    assert manual.email_address == email
    assert manual.recipient_normalized == email.normalized_email
    assert manual.subject == inbound.subject
    assert manual.in_reply_to == inbound.message_id
    assert inbound.message_id in manual.references
    assert manual.pk in pending_manual_reply_ids()
    assert deliver_manual_reply(manual.pk, provider=fake) == OutboundMessage.State.SENT
    manual.refresh_from_db()
    assert manual.gmail_thread_id == inbound.gmail_thread_id


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_manual_reply_mode_mismatch_never_reaches_gmail_or_recovery(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign, root = _thread_fixture(owner, suffix="review-mismatch")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Me interesa.",
        in_reply_to=root.message_id,
    )
    sync_gmail_connection(connection.pk, provider=fake)
    manual, _ = authorize_manual_reply(
        actor=owner,
        inbound_id=InboundMessage.objects.get().pk,
        body_text="Te llamo mañana.",
        request_key=uuid.uuid4(),
    )
    Campaign.objects.filter(pk=campaign.pk).update(delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY)
    provider = Mock()

    assert manual.pk not in pending_manual_reply_ids()
    assert deliver_manual_reply(manual.pk, provider=provider) == OutboundMessage.State.SEND_FAILED
    provider.reply.assert_not_called()

    OutboundMessage.objects.filter(pk=manual.pk).update(
        state=OutboundMessage.State.RECONCILING,
        next_attempt_at=NOW - timedelta(minutes=1),
    )
    assert manual.pk not in recoverable_manual_reply_ids(NOW)
    assert reconcile_manual_reply(manual.pk, provider=provider) == OutboundMessage.State.SEND_FAILED
    provider.find_by_message_id.assert_not_called()
    assert not FakeGmailMessage.objects.filter(
        direction=FakeGmailMessage.Direction.OUTBOUND,
        rfc_message_id=manual.message_id,
    ).exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_manual_reply_persists_sending_before_starting_gmail_effect(
    owner: User,
    private_catalog_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="durable-sending")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Me interesa.",
        in_reply_to=root.message_id,
    )
    sync_gmail_connection(connection.pk, provider=fake)
    manual, _ = authorize_manual_reply(
        actor=owner,
        inbound_id=InboundMessage.objects.get().pk,
        body_text="Te llamo mañana.",
        request_key=uuid.uuid4(),
    )

    def assert_durable_state(
        effect: manual_services.ManualReplyEffect,
        provider: object,
    ) -> str:
        del provider
        persisted = OutboundMessage.objects.get(pk=effect.message_id)
        assert persisted.state == OutboundMessage.State.SENDING
        assert persisted.sending_started_at is not None
        assert persisted.mime_sha256
        return persisted.state

    monkeypatch.setattr(manual_services, "_execute_manual_effect", assert_durable_state)
    assert deliver_manual_reply(manual.pk, provider=fake) == OutboundMessage.State.SENDING


class AmbiguousReplyProvider:
    def __init__(self) -> None:
        self.reply_calls = 0

    def reply(self, request: GmailReplyRequest) -> GmailSendResult:
        self.reply_calls += 1
        raise AmbiguousProviderError("timeout después de aceptar")

    def find_by_message_id(self, message_id: str) -> GmailSendResult | None:
        assert message_id
        return GmailSendResult(message_id="reconciled-reply", thread_id="thread-ambiguous")


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_ambiguous_manual_reply_reconciles_and_new_form_key_cannot_duplicate(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="ambiguous")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Me interesa.",
        in_reply_to=root.message_id,
    )
    sync_gmail_connection(connection.pk, provider=fake)
    inbound = InboundMessage.objects.get()
    manual, created = authorize_manual_reply(
        actor=owner,
        inbound_id=inbound.pk,
        body_text="Respuesta autorizada.",
        request_key=uuid.uuid4(),
    )
    provider = AmbiguousReplyProvider()

    assert deliver_manual_reply(manual.pk, provider=provider) == OutboundMessage.State.RECONCILING
    duplicate, duplicate_created = authorize_manual_reply(
        actor=owner,
        inbound_id=inbound.pk,
        body_text="Otro texto que no debe crear otro envío.",
        request_key=uuid.uuid4(),
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate.pk == manual.pk
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.MANUAL_REPLY).count() == 1
    assert provider.reply_calls == 1
    manual.refresh_from_db()
    manual.next_attempt_at = timezone.now() - timedelta(seconds=1)
    manual.save(update_fields=("next_attempt_at", "updated_at"))
    assert manual.pk in recoverable_manual_reply_ids()

    assert reconcile_manual_reply(manual.pk, provider=provider) == OutboundMessage.State.SENT
    manual.refresh_from_db()
    assert manual.gmail_message_id == "reconciled-reply"
    assert provider.reply_calls == 1


class AuthenticationFailureSyncProvider:
    def sync(self, cursor: object) -> object:
        del cursor
        raise AuthenticationError("refresh token revocado")


@pytest.mark.django_db
def test_non_retryable_sync_failure_marks_connection_error(
    owner: User,
) -> None:
    connection = _connection(owner, history_id="123")
    with pytest.raises(AuthenticationError, match="revocado"):
        sync_gmail_connection(connection.pk, provider=AuthenticationFailureSyncProvider())  # type: ignore[arg-type]
    connection.refresh_from_db()
    assert connection.status == GmailConnection.Status.ERROR
    assert "revocado" in connection.error


@pytest.mark.django_db
def test_blank_legacy_cursor_bootstraps_without_importing_history(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="legacy")
    connection = _connection(owner, history_id="")
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Mensaje histórico que no debe importarse.",
        in_reply_to=root.message_id,
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 0
    connection.refresh_from_db()
    assert connection.history_id == "1"
    assert not InboundMessage.objects.exists()

    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Mensaje nuevo.",
        in_reply_to=root.message_id,
    )
    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    assert InboundMessage.objects.get().body_text == "Mensaje nuevo."


@pytest.mark.django_db
def test_oversized_rfc_headers_do_not_poison_incremental_cursor(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, root = _thread_fixture(owner, suffix="oversized-rfc")
    connection = _connection(owner)
    fake = FakeGmailProvider(account_email=connection.email, persist=True)
    oversized = f"<{'x' * 500}@example.com>"
    fake.inject_inbound(
        thread_id=root.gmail_thread_id,
        sender=root.recipient,
        recipient=connection.email,
        subject=root.subject,
        body_text="Me interesa.",
        in_reply_to=oversized,
        references=(oversized,),
        rfc_message_id=oversized,
    )

    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    inbound = InboundMessage.objects.get()
    assert inbound.message_id == ""
    assert inbound.in_reply_to == ""
    assert inbound.references == []
    connection.refresh_from_db()
    assert connection.history_id == "1"
    assert sync_gmail_connection(connection.pk, provider=fake) == 0
