from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import BackgroundJob
from apps.campaigns.delivery import (
    _backoff,
    _error_text,
    _execute_external_effect,
    _failure_threshold_reason,
    _prepare_live_effect,
    _recheck_before_external_effect,
    complete_drained_campaigns,
    deliver_message,
    reconcile_message,
)
from apps.campaigns.models import Campaign, OutboundMessage, SearchQuery, SearchRun
from apps.campaigns.services import finish_discovery, transition_campaign
from apps.catalogs.services import create_catalog
from apps.compliance.models import ContactLedger, SuppressionEntry
from apps.compliance.services import suppress_email
from apps.integrations.contracts import (
    AmbiguousProviderError,
    GmailSendRequest,
    GmailSendResult,
)
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import FakeGmailMessage, GmailConnection
from apps.mailbox.services import provider_for_connection
from apps.prospects.models import AIAnalysis, Prospect, ProspectEmail

DELIVERY_NOW = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)


def test_delivery_backoff_is_bounded_and_empty_errors_are_named() -> None:
    assert _backoff(1, retry_after=10_000) == timedelta(seconds=900)
    assert _error_text(Exception()) == "Exception"


def _campaign(
    owner: User,
    *,
    mode: str,
    daily_limit: int = 30,
    interval: int = 5,
    window_start: str = "00:01",
    window_end: str = "23:59",
) -> Campaign:
    fixture_index = Campaign.objects.count()
    catalog = create_catalog(
        name=f"Entrega {mode} {daily_limit} {fixture_index}",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            f"%PDF-1.4\n% fixture {fixture_index}\n1 0 obj\n<<>>\nendobj\n%%EOF".encode(),
            content_type="application/pdf",
        ),
        actor=owner,
    )
    return Campaign.objects.create(
        name="Entrega",
        state=Campaign.State.RUNNING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
        delivery_mode=mode,
        daily_limit=daily_limit,
        message_interval_minutes=interval,
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        window_start=window_start,
        window_end=window_end,
        catalog=catalog,
        created_by=owner,
    )


def _message(campaign: Campaign, *, suffix: str = "one") -> OutboundMessage:
    query, _ = SearchQuery.objects.get_or_create(
        campaign=campaign,
        normalized_query="fixture",
        defaults={
            "category_snapshot": "Taller",
            "zone_snapshot": "CABA",
            "location_snapshot": "CABA",
            "query_text": "fixture",
        },
    )
    run, _ = SearchRun.objects.get_or_create(
        campaign=campaign,
        query=query,
        defaults={
            "provider": "fake",
            "idempotency_key": f"run:{campaign.pk}",
            "requested_limit": 10,
        },
    )
    prospect = Prospect.objects.create(
        campaign=campaign,
        source_run=run,
        name=f"Taller {suffix}",
        normalized_name=f"taller {suffix}",
        pipeline_state=Prospect.PipelineState.QUEUED,
    )
    recipient = f"ventas-{suffix}@example.com"
    email = ProspectEmail.objects.create(
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
    return OutboundMessage.objects.create(
        campaign=campaign,
        prospect=prospect,
        prospect_email=email,
        analysis=analysis,
        recipient=recipient,
        recipient_normalized=recipient,
        subject="PUBLICIDAD - Consulta",
        body_text="Texto plano de prueba.\n\nSi no querés recibir más mensajes, respondé BAJA.",
        catalog=campaign.catalog,
        catalog_version=campaign.catalog.version,
        delivery_mode=campaign.delivery_mode,
        idempotency_key=f"message:{campaign.pk}:{suffix}",
    )


def _connect(owner: User) -> GmailConnection:
    return GmailConnection.objects.create(
        owner=owner,
        email="owner@example.invalid",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("fake-refresh-token"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )


@pytest.mark.django_db
def test_dry_run_completes_without_gmail_or_contact_ledger(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    message = _message(_campaign(owner, mode=Campaign.DeliveryMode.DRY_RUN))

    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.DRY_RUN_COMPLETED
    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.DRY_RUN_COMPLETED

    message.refresh_from_db()
    assert message.mime_sha256
    assert message.message_id
    assert message.simulated_at == DELIVERY_NOW
    assert not FakeGmailMessage.objects.exists()
    assert not ContactLedger.objects.exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_live_fake_records_complete_mime_and_repeating_task_does_not_duplicate(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    _connect(owner)

    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT

    message.refresh_from_db()
    fake = FakeGmailMessage.objects.get()
    parsed = BytesParser(policy=policy.default).parsebytes(bytes(fake.raw_message))
    assert FakeGmailMessage.objects.count() == 1
    assert parsed["To"] == message.recipient
    assert parsed["Cc"] is None and parsed["Bcc"] is None
    assert parsed.get_body(preferencelist=("plain",)) is not None
    attachment = next(iter(parsed.iter_attachments()))
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True).startswith(b"%PDF-")
    assert message.gmail_message_id == fake.gmail_message_id
    assert message.gmail_thread_id == fake.gmail_thread_id
    assert message.contact_sequence == 1
    assert BackgroundJob.objects.get().state == BackgroundJob.State.SUCCEEDED
    assert ContactLedger.objects.get().last_sent_message == message


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_daily_limit_and_pause_prevent_new_sends(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE, daily_limit=1)
    first = _message(campaign, suffix="first")
    second = _message(campaign, suffix="second")
    _connect(owner)

    assert deliver_message(first.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    assert deliver_message(second.pk, now=DELIVERY_NOW) == OutboundMessage.State.QUEUED
    assert FakeGmailMessage.objects.count() == 1

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.PAUSED,
        actor=owner,
    )
    assert deliver_message(second.pk, now=DELIVERY_NOW.replace(day=16)) == (
        OutboundMessage.State.QUEUED
    )
    assert FakeGmailMessage.objects.count() == 1


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_interval_and_business_window_are_enforced(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE, interval=5)
    first = _message(campaign, suffix="first")
    second = _message(campaign, suffix="second")
    _connect(owner)
    assert deliver_message(first.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    assert deliver_message(second.pk, now=DELIVERY_NOW) == OutboundMessage.State.QUEUED
    assert deliver_message(second.pk, now=DELIVERY_NOW + timedelta(minutes=5)) == (
        OutboundMessage.State.SENT
    )

    outside = _campaign(
        owner,
        mode=Campaign.DeliveryMode.LIVE,
        window_start="16:00",
        window_end="17:00",
    )
    outside_message = _message(outside, suffix="outside")
    assert deliver_message(outside_message.pk, now=DELIVERY_NOW) == OutboundMessage.State.QUEUED
    assert FakeGmailMessage.objects.count() == 2


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=True)
def test_kill_switch_pauses_live_campaign(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    _connect(owner)

    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.QUEUED
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.PAUSED
    assert "Kill switch" in campaign.status_reason
    assert not FakeGmailMessage.objects.exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_late_invalid_email_is_not_sent_and_error_is_visible(
    client: Client, owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    _connect(owner)
    message.prospect_email.is_primary = False
    message.prospect_email.save(update_fields=("is_primary", "updated_at"))

    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.SEND_FAILED
    assert not FakeGmailMessage.objects.exists()
    client.force_login(owner)
    response = client.get(reverse("campaign-detail", args=(campaign.pk,)))
    assert response.status_code == 200
    assert "email primario validado" in response.content.decode()


class AmbiguousThenFoundProvider:
    def __init__(self) -> None:
        self.send_calls = 0

    def send(self, request: GmailSendRequest) -> GmailSendResult:
        self.send_calls += 1
        raise AmbiguousProviderError("timeout after accept")

    def find_by_message_id(self, message_id: str) -> GmailSendResult | None:
        assert message_id
        return GmailSendResult(message_id="reconciled-message", thread_id="reconciled-thread")


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_ambiguous_send_is_reconciled_without_second_send(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    _connect(owner)
    provider = AmbiguousThenFoundProvider()

    assert deliver_message(message.pk, now=DELIVERY_NOW, provider=provider) == (
        OutboundMessage.State.RECONCILING
    )
    assert reconcile_message(message.pk, now=DELIVERY_NOW, provider=provider) == (
        OutboundMessage.State.SENT
    )
    assert provider.send_calls == 1
    message.refresh_from_db()
    assert message.gmail_message_id == "reconciled-message"


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_restart_reconciles_durable_fake_record_without_resending(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    connection = _connect(owner)
    effect = _prepare_live_effect(message.pk, DELIVERY_NOW)
    assert effect is not None
    provider_for_connection(connection, persist_fake=True).send(
        GmailSendRequest(
            recipient=effect.recipient,
            raw_message=effect.raw_message,
            message_id=effect.rfc_message_id,
            correlation_id=str(effect.message_id),
            idempotency_key=effect.idempotency_key,
        )
    )

    # A fresh provider instance models a restarted worker and reads the durable fake outbox.
    assert reconcile_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    assert FakeGmailMessage.objects.count() == 1


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_pause_between_reservation_and_external_effect_aborts_send(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    _connect(owner)
    effect = _prepare_live_effect(message.pk, DELIVERY_NOW)
    assert effect is not None

    transition_campaign(
        campaign_id=campaign.pk,
        target_state=Campaign.State.PAUSED,
        actor=owner,
    )
    assert _recheck_before_external_effect(effect) == OutboundMessage.State.QUEUED

    message.refresh_from_db()
    assert message.delivery_reserved_at is None
    assert message.sending_started_at is None
    assert not FakeGmailMessage.objects.exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_suppression_after_reservation_blocks_final_gmail_effect(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    connection = _connect(owner)
    effect = _prepare_live_effect(message.pk, DELIVERY_NOW)
    assert effect is not None

    suppress_email(
        email=message.recipient,
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        actor=owner,
    )
    result = _execute_external_effect(
        effect,
        provider_for_connection(connection, persist_fake=True),
    )

    assert result == OutboundMessage.State.SEND_FAILED
    message.refresh_from_db()
    assert "suprimido" in message.error
    assert not FakeGmailMessage.objects.exists()
    assert ContactLedger.objects.get().reserved_message_id is None


class MessageNotFoundProvider:
    def find_by_message_id(self, message_id: str) -> GmailSendResult | None:
        assert message_id
        return None


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_five_exhausted_ambiguous_sends_pause_campaign(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    _connect(owner)
    provider = MessageNotFoundProvider()

    for index in range(5):
        message = _message(campaign, suffix=f"failed-{index}")
        effect = _prepare_live_effect(message.pk, DELIVERY_NOW)
        assert effect is not None
        OutboundMessage.objects.filter(pk=message.pk).update(
            state=OutboundMessage.State.RECONCILING,
            attempts=3,
            next_attempt_at=DELIVERY_NOW,
        )
        assert reconcile_message(message.pk, now=DELIVERY_NOW, provider=provider) == (
            OutboundMessage.State.SEND_FAILED
        )
        campaign.refresh_from_db()
        if index < 4:
            assert campaign.state == Campaign.State.RUNNING

    assert campaign.state == Campaign.State.PAUSED
    assert "cinco fallos" in campaign.status_reason


@pytest.mark.django_db
def test_thirty_percent_failure_rate_in_twenty_results_triggers_safety_reason(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    for index in range(20):
        message = _message(campaign, suffix=f"window-{index}")
        failed = index % 3 == 0
        state = OutboundMessage.State.SEND_FAILED if failed else OutboundMessage.State.SENT
        OutboundMessage.objects.filter(pk=message.pk).update(state=state)
        BackgroundJob.objects.create(
            task_name="mailbox.deliver_message",
            idempotency_key=f"deliver:{message.pk}",
            entity_type="OutboundMessage",
            entity_id=str(message.pk),
            state=BackgroundJob.State.FAILED if failed else BackgroundJob.State.SUCCEEDED,
            finished_at=timezone.now(),
        )

    assert "30%" in _failure_threshold_reason(campaign.pk)


@pytest.mark.django_db
def test_campaign_completion_waits_for_pipeline_reservations_and_deferred_analysis(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.DRY_RUN)
    message = _message(campaign)
    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.DRY_RUN_COMPLETED
    finish_discovery(
        campaign_id=campaign.pk,
        target_state=Campaign.DiscoveryState.EXHAUSTED_QUERIES,
        reason="fixture exhausted",
    )
    extra = Prospect.objects.create(
        campaign=campaign,
        source_run=message.prospect.source_run,
        name="Pipeline pendiente",
        normalized_name="pipeline pendiente",
        pipeline_state=Prospect.PipelineState.EMAIL_FOUND,
    )

    assert complete_drained_campaigns() == 0
    extra.pipeline_state = Prospect.PipelineState.QUEUED
    extra.pipeline_reservation_key = "active-reservation"
    extra.pipeline_reserved_at = timezone.now()
    extra.save(
        update_fields=(
            "pipeline_state",
            "pipeline_reservation_key",
            "pipeline_reserved_at",
            "updated_at",
        )
    )
    assert complete_drained_campaigns() == 0

    extra.pipeline_state = Prospect.PipelineState.ERROR
    extra.pipeline_reservation_key = ""
    extra.pipeline_reserved_at = None
    extra.save(
        update_fields=(
            "pipeline_state",
            "pipeline_reservation_key",
            "pipeline_reserved_at",
            "updated_at",
        )
    )
    deferred = AIAnalysis.objects.create(
        prospect=extra,
        input_hash="f" * 64,
        prompt_version="v1",
        schema_version="v1",
        provider="fake",
        model="fake",
        analyzed_at=timezone.now(),
        status=AIAnalysis.Status.RETRY_WAIT,
        next_retry_at=timezone.now() + timedelta(minutes=1),
        prompt_text="fixture",
    )
    assert complete_drained_campaigns() == 0

    deferred.status = AIAnalysis.Status.ERROR
    deferred.next_retry_at = None
    deferred.save(update_fields=("status", "next_retry_at", "updated_at"))
    assert complete_drained_campaigns() == 1
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.COMPLETED
