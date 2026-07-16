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

from apps.campaigns.delivery import deliver_message, pending_message_ids
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email
from apps.integrations.contracts import (
    AmbiguousProviderError,
    AuthenticationError,
    GmailReplyRequest,
    GmailSendResult,
)
from apps.integrations.fakes import FakeGmailProvider
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox import manual as manual_services
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.manual import (
    authorize_manual_reply,
    deliver_manual_reply,
    reconcile_manual_reply,
    recoverable_manual_reply_ids,
)
from apps.mailbox.models import FakeGmailMessage, GmailConnection, InboundMessage
from apps.mailbox.sync import sync_gmail_connection
from apps.mailbox.tasks import deliver_manual_reply_task
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
    assert first.classification == InboundMessage.Classification.INTERESTED
    assert "script" not in first.body_html_sanitized
    assert "onclick" not in first.body_html_sanitized
    assert "Tenemos interés" in first.body_text

    client.force_login(owner)
    dashboard = client.get(reverse("dashboard"))
    responses = client.get(reverse("responses"))
    assert "2 respuestas de campañas" in dashboard.content.decode()
    assert root.prospect.name in responses.content.decode()
    assert "Mensaje ajeno" not in responses.content.decode()
    rfc_linked = InboundMessage.objects.get(gmail_thread_id="different-gmail-thread")
    rfc_thread = client.get(reverse("response-thread", args=(rfc_linked.pk,)))
    assert "Mensaje inicial con catálogo" in rfc_thread.content.decode()


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
        idempotency_key="future-after-unsubscribe",
    )
    with override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False):
        assert deliver_message(later.pk, now=NOW) == OutboundMessage.State.SEND_FAILED
    assert "suprimido" in OutboundMessage.objects.get(pk=later.pk).error

    # Bounce has deterministic precedence even when the quoted footer contains BAJA.
    second_owner = User.objects.create_user(username="bounce-owner", password="password")
    _, bounce_root = _thread_fixture(second_owner, suffix="bounce")
    bounce_connection = _connection(second_owner)
    bounce_fake = FakeGmailProvider(account_email=bounce_connection.email, persist=True)
    bounce_fake.inject_inbound(
        thread_id=bounce_root.gmail_thread_id,
        sender="mailer-daemon@example.net",
        recipient=bounce_connection.email,
        subject="Delivery Status Notification (Failure)",
        body_text="User unknown\n\n> Si no querés recibir mensajes, respondé BAJA.",
        in_reply_to=bounce_root.message_id,
    )
    assert sync_gmail_connection(bounce_connection.pk, provider=bounce_fake) == 1
    bounce_root.prospect_email.refresh_from_db()
    assert bounce_root.prospect_email.is_invalid is True
    assert InboundMessage.objects.get(related_outbound=bounce_root).classification == "BOUNCE"


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
    provider_factory = Mock(side_effect=AuthenticationError("API key faltante"))
    monkeypatch.setattr("apps.mailbox.sync.get_llm_provider", provider_factory)

    assert sync_gmail_connection(connection.pk, provider=fake) == 1
    provider_factory.assert_not_called()
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
    assert deliver_manual_reply_task(str(manual.pk)) == OutboundMessage.State.SENT
    manual.refresh_from_db()
    assert manual.state == OutboundMessage.State.SENT
    assert manual.gmail_thread_id == root.gmail_thread_id
    assert manual.parent_inbound == inbound
    assert manual.sent_by == owner
    assert manual.subject == root.subject
    assert manual.in_reply_to == inbound.message_id
    assert root.message_id in manual.references
    assert inbound.message_id in manual.references
    assert OutboundMessage.objects.filter(kind=OutboundMessage.Kind.MANUAL_REPLY).count() == 1
    assert manual.pk not in pending_message_ids()
    with pytest.raises(ValidationError, match="POST explícito"):
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
    assert "suprimido" in manual.error
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
