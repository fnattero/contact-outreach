from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
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
    pending_message_ids,
    reconcile_message,
    recoverable_message_ids,
    retry_failed_message,
)
from apps.campaigns.models import (
    Campaign,
    CampaignDeliveryReservation,
    OutboundAttachment,
    OutboundMessage,
    SearchQuery,
    SearchRun,
)
from apps.campaigns.services import finish_discovery, transition_campaign
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog
from apps.compliance.models import ContactLedger, SuppressionEntry
from apps.compliance.services import suppress_email
from apps.contacts.models import CampaignEnrollment
from apps.contacts.services import (
    apply_inbound_contact_effect,
    create_manual_contact,
    ensure_outbound_contact_links,
)
from apps.integrations.contracts import (
    AmbiguousProviderError,
    GmailSendRequest,
    GmailSendResult,
)
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import FakeGmailMessage, GmailConnection, InboundMessage
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
    is_live = campaign.delivery_mode == Campaign.DeliveryMode.LIVE
    return OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        campaign=campaign,
        prospect=prospect,
        prospect_email=email,
        analysis=analysis,
        recipient=recipient,
        recipient_normalized=recipient,
        subject="Consulta",
        body_text="Texto plano de prueba.",
        catalog=campaign.catalog,
        catalog_version=campaign.catalog.version,
        delivery_mode=campaign.delivery_mode,
        approved_at=timezone.now() if is_live else None,
        approved_by=campaign.created_by if is_live else None,
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


def _as_initial(
    message: OutboundMessage,
    *,
    catalogs: tuple[Catalog, ...] = (),
) -> OutboundMessage:
    enrollment = ensure_outbound_contact_links(message)
    assert enrollment.selected_email is not None
    message.kind = OutboundMessage.Kind.INITIAL
    message.organization = enrollment.organization
    message.campaign_enrollment = enrollment
    message.email_address = enrollment.selected_email
    message.contact_sequence = None
    message.save(
        update_fields=(
            "kind",
            "organization",
            "campaign_enrollment",
            "email_address",
            "contact_sequence",
            "updated_at",
        )
    )
    selected_catalogs = catalogs or (message.catalog,)
    for position, catalog in enumerate(selected_catalogs):
        assert catalog is not None
        OutboundAttachment.objects.create(
            message=message,
            catalog=catalog,
            position=position,
            catalog_version=catalog.version,
            storage_key=catalog.storage_key,
            filename=catalog.original_filename,
            byte_size=catalog.byte_size,
            sha256=catalog.sha256,
        )
    return message


def _same_recipient_initial(
    campaign: Campaign,
    *,
    original: OutboundMessage,
) -> OutboundMessage:
    assert original.organization is not None
    assert original.email_address is not None
    enrollment = CampaignEnrollment.objects.create(
        workspace=campaign.workspace,
        campaign=campaign,
        organization=original.organization,
        selected_email=original.email_address,
        state=CampaignEnrollment.State.PREPARED,
        source="TEST",
    )
    message = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        campaign=campaign,
        organization=original.organization,
        campaign_enrollment=enrollment,
        email_address=original.email_address,
        recipient=original.recipient,
        recipient_normalized=original.recipient_normalized,
        subject="Propuesta comercial",
        body_text="Texto fijo.",
        catalog=campaign.catalog,
        catalog_version=campaign.catalog.version,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        approved_at=timezone.now(),
        approved_by=campaign.created_by,
        idempotency_key=f"initial:{campaign.pk}:{enrollment.pk}",
    )
    return _as_initial(message)


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
def test_review_only_is_never_scheduled_or_sent(owner: User, private_catalog_dir: Path) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.REVIEW_ONLY)
    message = _message(campaign)
    _connect(owner)

    assert message.pk not in pending_message_ids(DELIVERY_NOW)
    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.REVIEW_READY
    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.REVIEW_READY

    message.refresh_from_db()
    assert message.message_id == ""
    assert message.mime_sha256 == ""
    assert message.sent_at is None
    assert message.simulated_at is None
    assert not FakeGmailMessage.objects.exists()
    assert GmailConnection.objects.filter(owner=owner).exists()
    assert not ContactLedger.objects.exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_live_message_without_manual_approval_is_never_scheduled_or_sent(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    OutboundMessage.objects.filter(pk=message.pk).update(
        state=OutboundMessage.State.REVIEW_READY,
        approved_at=None,
        approved_by=None,
    )
    _connect(owner)

    assert message.pk not in pending_message_ids(DELIVERY_NOW)
    assert deliver_message(message.pk, now=DELIVERY_NOW) == OutboundMessage.State.REVIEW_READY

    message.refresh_from_db()
    assert message.message_id == ""
    assert message.mime_sha256 == ""
    assert not FakeGmailMessage.objects.exists()
    assert not ContactLedger.objects.exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_manual_approval_is_rechecked_at_the_external_gmail_boundary(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    connection = _connect(owner)
    effect = _prepare_live_effect(message.pk, DELIVERY_NOW)
    assert effect is not None
    OutboundMessage.objects.filter(pk=message.pk).update(approved_at=None, approved_by=None)

    result = _execute_external_effect(
        effect,
        provider_for_connection(connection, persist_fake=True),
    )

    assert result == OutboundMessage.State.REVIEW_READY
    message.refresh_from_db()
    assert message.state == OutboundMessage.State.REVIEW_READY
    assert not FakeGmailMessage.objects.exists()
    assert ContactLedger.objects.get().reserved_message is None


@pytest.mark.django_db
def test_failed_approved_live_message_keeps_approval_when_retried(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    message = _message(_campaign(owner, mode=Campaign.DeliveryMode.LIVE))
    OutboundMessage.objects.filter(pk=message.pk).update(
        state=OutboundMessage.State.SEND_FAILED,
        error="Fallo corregible",
    )

    retried = retry_failed_message(
        message.pk,
        actor=owner,
        reason="Se corrigió la conexión con Gmail",
    )

    assert retried.state == OutboundMessage.State.QUEUED
    assert retried.approved_at is not None
    assert retried.approved_by == owner
    assert retried.next_attempt_at is not None


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_unknown_delivery_mode_fails_closed_before_gmail(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    _connect(owner)
    OutboundMessage.objects.filter(pk=message.pk).update(delivery_mode="UNKNOWN")

    with pytest.raises(ValidationError, match="no es válido"):
        deliver_message(message.pk, now=DELIVERY_NOW)

    assert not FakeGmailMessage.objects.exists()
    assert not ContactLedger.objects.exists()


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_campaign_message_mode_mismatch_is_not_scheduled_recovered_or_sent(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    _connect(owner)
    Campaign.objects.filter(pk=campaign.pk).update(delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY)

    assert message.pk not in pending_message_ids(DELIVERY_NOW)
    with pytest.raises(ValidationError, match="campaña con envío en vivo"):
        deliver_message(message.pk, now=DELIVERY_NOW)

    OutboundMessage.objects.filter(pk=message.pk).update(
        state=OutboundMessage.State.SENDING,
        sending_started_at=DELIVERY_NOW - timedelta(minutes=3),
    )
    assert message.pk not in recoverable_message_ids(DELIVERY_NOW)
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
    assert "bloqueo general" in campaign.status_reason
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
    assert "Elegí un email válido antes de continuar" in response.content.decode()


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
def test_mode_change_after_reservation_is_blocked_at_final_gmail_boundary(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    message = _message(campaign)
    connection = _connect(owner)
    effect = _prepare_live_effect(message.pk, DELIVERY_NOW)
    assert effect is not None
    Campaign.objects.filter(pk=campaign.pk).update(delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY)

    result = _execute_external_effect(
        effect,
        provider_for_connection(connection, persist_fake=True),
    )

    assert result == OutboundMessage.State.QUEUED
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
    assert "restricción registrada" in message.error
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


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_same_day_campaign_conflict_moves_to_next_window_without_cross_campaign_cooldown(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    first_campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    first = _as_initial(_message(first_campaign, suffix="same-day"))
    second_campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    second = _same_recipient_initial(second_campaign, original=first)
    _connect(owner)
    near_midnight_utc = datetime(2026, 7, 16, 2, 30, tzinfo=UTC)

    assert deliver_message(first.pk, now=near_midnight_utc) == OutboundMessage.State.SENT
    assert deliver_message(second.pk, now=near_midnight_utc) == OutboundMessage.State.QUEUED

    second.refresh_from_db()
    assert second.error == ("Se pasó al próximo día permitido para evitar correos duplicados.")
    assert second.scheduled_for is not None
    assert (
        second.scheduled_for.astimezone(timezone.get_fixed_timezone(-180)).date()
        == datetime(2026, 7, 16).date()
    )
    first_reservation = CampaignDeliveryReservation.objects.get(message=first)
    assert first_reservation.local_date == datetime(2026, 7, 15).date()
    assert first_reservation.status == CampaignDeliveryReservation.Status.CONSUMED
    assert not CampaignDeliveryReservation.objects.filter(message=second).exists()

    assert deliver_message(second.pk, now=second.scheduled_for) == OutboundMessage.State.SENT
    second_reservation = CampaignDeliveryReservation.objects.get(message=second)
    assert second_reservation.local_date == datetime(2026, 7, 16).date()
    assert second_reservation.status == CampaignDeliveryReservation.Status.CONSUMED


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_confirmed_initial_creates_one_threaded_attachment_free_reminder(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign = _campaign(
        owner,
        mode=Campaign.DeliveryMode.LIVE,
        window_start="09:00",
        window_end="17:00",
    )
    Campaign.objects.filter(pk=campaign.pk).update(
        reminder_enabled=True,
        reminder_delay_days=3,
        weekdays=[0, 1, 2, 3, 4],
        signature_snapshot="Componentes Delta SA",
    )
    campaign.refresh_from_db()
    initial = _as_initial(_message(campaign, suffix="reminder"))
    _connect(owner)

    assert deliver_message(initial.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    assert deliver_message(initial.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    initial.refresh_from_db()
    reminder = OutboundMessage.objects.get(reminder_for=initial)
    assert OutboundMessage.objects.filter(reminder_for=initial).count() == 1
    assert reminder.kind == OutboundMessage.Kind.CAMPAIGN_REMINDER
    assert reminder.scheduled_for == datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    assert reminder.next_attempt_at == reminder.scheduled_for
    assert reminder.in_reply_to == initial.message_id
    assert initial.message_id in reminder.references
    assert reminder.gmail_thread_id == initial.gmail_thread_id
    assert not reminder.attachments.exists()

    assert deliver_message(reminder.pk, now=reminder.scheduled_for) == OutboundMessage.State.SENT
    reminder.refresh_from_db()
    initial.refresh_from_db()
    assert reminder.gmail_thread_id == initial.gmail_thread_id
    assert reminder.subject == initial.subject
    parsed = BytesParser(policy=policy.default).parsebytes(
        bytes(FakeGmailMessage.objects.get(idempotency_key=reminder.idempotency_key).raw_message)
    )
    assert parsed["In-Reply-To"] == initial.message_id
    assert initial.message_id in parsed["References"]
    assert list(parsed.iter_attachments()) == []
    reservation = CampaignDeliveryReservation.objects.get(message=reminder)
    assert reservation.status == CampaignDeliveryReservation.Status.CONSUMED


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_auto_reply_keeps_reminder_but_manual_contact_cancels_it_and_unblocks_completion(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    Campaign.objects.filter(pk=campaign.pk).update(reminder_enabled=True, reminder_delay_days=3)
    campaign.refresh_from_db()
    initial = _as_initial(_message(campaign, suffix="contact-cancel"))
    connection = _connect(owner)
    assert deliver_message(initial.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    reminder = OutboundMessage.objects.get(reminder_for=initial)

    inbound = InboundMessage.objects.create(
        connection=connection,
        related_outbound=initial,
        gmail_message_id="auto-reply-reminder-fixture",
        gmail_thread_id=initial.gmail_thread_id,
        sender="ventas-contact-cancel@example.com",
        recipients=[connection.email],
        subject=initial.subject,
        external_at=DELIVERY_NOW + timedelta(hours=1),
        received_at=DELIVERY_NOW + timedelta(hours=1),
        body_text="Respuesta automática",
        classification=InboundMessage.Classification.AUTO_REPLY,
        is_human=False,
    )
    effect = apply_inbound_contact_effect(inbound)
    assert effect.cancelled_messages == 0
    reminder.refresh_from_db()
    assert reminder.state == OutboundMessage.State.QUEUED

    finish_discovery(
        campaign_id=campaign.pk,
        target_state=Campaign.DiscoveryState.EXHAUSTED_QUERIES,
        reason="fixture exhausted",
    )
    assert complete_drained_campaigns() == 0
    create_manual_contact(actor=owner, email=initial.recipient)
    reminder.refresh_from_db()
    assert reminder.state == OutboundMessage.State.CANCELLED
    assert complete_drained_campaigns() == 1


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_tampered_second_pdf_pauses_campaign_and_sends_no_partial_set(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    second_catalog = create_catalog(
        name="Segundo catálogo",
        upload=SimpleUploadedFile(
            "segundo.pdf",
            b"%PDF-1.4\nsegundo\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    initial = _as_initial(
        _message(campaign, suffix="tampered"),
        catalogs=(campaign.catalog, second_catalog),
    )
    _connect(owner)
    Path(second_catalog.file.path).write_bytes(b"%PDF-1.4\ncontenido alterado\n%%EOF")

    assert deliver_message(initial.pk, now=DELIVERY_NOW) == OutboundMessage.State.QUEUED
    campaign.refresh_from_db()
    initial.refresh_from_db()
    assert campaign.state == Campaign.State.PAUSED
    assert "integridad" in campaign.status_reason
    assert "integridad" in initial.error
    assert not FakeGmailMessage.objects.exists()
    assert not CampaignDeliveryReservation.objects.filter(message=initial).exists()


@pytest.mark.django_db
def test_queue_recheck_marks_new_initial_ineligible_after_restriction(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    initial = _as_initial(_message(campaign, suffix="queue-restriction"))
    suppress_email(
        email=initial.recipient,
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        actor=owner,
    )

    assert initial.pk not in pending_message_ids(DELIVERY_NOW)
    initial.refresh_from_db()
    assert initial.state == OutboundMessage.State.INELIGIBLE
    assert "restricción" in initial.error


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_final_eligibility_recheck_releases_initial_reservation_before_gmail(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    initial = _as_initial(_message(campaign, suffix="final-restriction"))
    connection = _connect(owner)
    effect = _prepare_live_effect(initial.pk, DELIVERY_NOW)
    assert effect is not None
    reservation = CampaignDeliveryReservation.objects.get(message=initial)
    assert reservation.status == CampaignDeliveryReservation.Status.RESERVED

    suppress_email(
        email=initial.recipient,
        reason=SuppressionEntry.Reason.UNSUBSCRIBE,
        actor=owner,
    )
    result = _execute_external_effect(
        effect,
        provider_for_connection(connection, persist_fake=True),
    )

    assert result == OutboundMessage.State.INELIGIBLE
    reservation.refresh_from_db()
    assert reservation.status == CampaignDeliveryReservation.Status.RELEASED
    assert reservation.released_at is not None
    assert not FakeGmailMessage.objects.exists()


class AmbiguousReminderProvider:
    def __init__(self, *, thread_id: str) -> None:
        self.thread_id = thread_id
        self.reply_calls = 0

    def reply(self, request: object) -> GmailSendResult:
        del request
        self.reply_calls += 1
        raise AmbiguousProviderError("timeout after reminder accept")

    def find_by_message_id(self, message_id: str) -> GmailSendResult:
        assert message_id
        return GmailSendResult(message_id="reconciled-reminder", thread_id=self.thread_id)


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_ambiguous_reminder_reconciles_without_second_reply(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    Campaign.objects.filter(pk=campaign.pk).update(reminder_enabled=True, reminder_delay_days=3)
    campaign.refresh_from_db()
    initial = _as_initial(_message(campaign, suffix="ambiguous-reminder"))
    _connect(owner)
    assert deliver_message(initial.pk, now=DELIVERY_NOW) == OutboundMessage.State.SENT
    initial.refresh_from_db()
    reminder = OutboundMessage.objects.get(reminder_for=initial)
    assert reminder.scheduled_for is not None
    provider = AmbiguousReminderProvider(thread_id=initial.gmail_thread_id)

    assert deliver_message(reminder.pk, now=reminder.scheduled_for, provider=provider) == (
        OutboundMessage.State.RECONCILING
    )
    reservation = CampaignDeliveryReservation.objects.get(message=reminder)
    assert reservation.status == CampaignDeliveryReservation.Status.RESERVED
    assert (
        reconcile_message(
            reminder.pk,
            now=reminder.scheduled_for,
            provider=provider,
        )
        == OutboundMessage.State.SENT
    )
    assert provider.reply_calls == 1
    reservation.refresh_from_db()
    assert reservation.status == CampaignDeliveryReservation.Status.CONSUMED


@pytest.mark.django_db
@override_settings(SEND_MODE="live", SEND_KILL_SWITCH=False)
def test_oversized_serialized_mime_pauses_before_reservation_or_gmail(
    monkeypatch: pytest.MonkeyPatch,
    owner: User,
    private_catalog_dir: Path,
) -> None:
    from apps.mailbox import mime as mime_module

    del private_catalog_dir
    campaign = _campaign(owner, mode=Campaign.DeliveryMode.LIVE)
    initial = _as_initial(_message(campaign, suffix="raw-size"))
    _connect(owner)
    monkeypatch.setattr(mime_module, "MAX_SERIALIZED_MIME_BYTES", 100)

    assert deliver_message(initial.pk, now=DELIVERY_NOW) == OutboundMessage.State.QUEUED
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.PAUSED
    assert "24 MiB" in campaign.status_reason
    assert not CampaignDeliveryReservation.objects.filter(message=initial).exists()
    assert not FakeGmailMessage.objects.exists()
