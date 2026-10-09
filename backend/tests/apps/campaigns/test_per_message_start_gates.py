"""Starting delivery of a per-message campaign: only what a person approved, and still eligible."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from tests.apps.campaigns.test_campaign_review_extra import (
    _campaign,
    _catalog,
    _enrollment,
    _message,
    _organization_and_email,
)

from apps.audit.models import AuditEvent
from apps.campaigns.approval import approve_campaign, start_per_message_campaign
from apps.campaigns.models import Campaign, OutboundAttachment, OutboundMessage
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email
from apps.contacts.models import CampaignEnrollment, EmailAddress


class World:
    """A campaign waiting for approval with three recipients in different situations."""

    def __init__(self, owner: User) -> None:
        self.owner = owner
        self.workspace = owner.membership.workspace
        self.catalog = _catalog(owner)
        self.campaign = _campaign(owner, self.catalog)

    def recipient(self, suffix: str, *, approved: bool, state: str | None = None):
        organization, email = _organization_and_email(self.workspace, suffix)
        enrollment = _enrollment(self.workspace, self.campaign, organization, email)
        message = _message(
            self.campaign,
            organization,
            enrollment,
            email,
            self.catalog,
            state=state or (OutboundMessage.State.PREPARED if approved else OutboundMessage.State.REVIEW_READY),
            approved_at=timezone.now() if approved else None,
            approved_by=self.owner if approved else None,
        )
        return message, enrollment, email


@pytest.fixture
def world(owner: User, private_catalog_dir: Path) -> World:
    del private_catalog_dir
    return World(owner)


def _reload(*objects):
    for item in objects:
        item.refresh_from_db()


@pytest.mark.django_db
def test_only_individually_approved_messages_are_queued_and_the_rest_are_dropped(world: World) -> None:
    approved, approved_enrollment, _ = world.recipient("a", approved=True)
    skipped, skipped_enrollment, _ = world.recipient("b", approved=False)

    campaign = start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(approved, skipped, approved_enrollment, skipped_enrollment)
    assert campaign.state == Campaign.State.RUNNING
    assert campaign.approved_by == world.owner and campaign.approved_at is not None
    assert approved.state == OutboundMessage.State.QUEUED
    assert approved.next_attempt_at is not None
    assert skipped.state == OutboundMessage.State.CANCELLED
    assert skipped_enrollment.state == CampaignEnrollment.State.CANCELLED
    assert "No se aprobó" in skipped_enrollment.exclusion_reason
    event = AuditEvent.objects.get(action="campaign.per_message_delivery_started")
    assert event.after["approved_messages"] == 1
    assert event.after["content_hash"] == campaign.content_hash


@pytest.mark.django_db
def test_starting_with_nothing_approved_changes_nothing(world: World) -> None:
    pending, enrollment, _ = world.recipient("a", approved=False)

    with pytest.raises(ValidationError, match="Aprobá al menos un mensaje"):
        start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(pending, enrollment, world.campaign)
    assert pending.state == OutboundMessage.State.REVIEW_READY
    assert enrollment.state == CampaignEnrollment.State.PREPARED
    assert world.campaign.state == Campaign.State.AWAITING_APPROVAL


@pytest.mark.django_db
def test_a_recipient_suppressed_after_approval_is_dropped_but_the_others_still_go(world: World) -> None:
    safe, safe_enrollment, _ = world.recipient("safe", approved=True)
    blocked, blocked_enrollment, blocked_email = world.recipient("blocked", approved=True)
    suppress_email(
        email=blocked_email.original_email, reason=SuppressionEntry.Reason.UNSUBSCRIBE, actor=None
    )

    start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(safe, blocked, safe_enrollment, blocked_enrollment)
    assert safe.state == OutboundMessage.State.QUEUED
    assert blocked.state == OutboundMessage.State.CANCELLED
    assert blocked.error
    assert blocked_enrollment.state == CampaignEnrollment.State.INELIGIBLE
    assert blocked_enrollment.exclusion_reason == blocked.error


@pytest.mark.django_db
def test_an_invalidated_address_is_dropped_at_start(world: World) -> None:
    message, enrollment, email = world.recipient("a", approved=True)
    other, _, _ = world.recipient("b", approved=True)
    EmailAddress.objects.filter(pk=email.pk).update(validity=EmailAddress.Validity.INVALID)

    start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(message, other, enrollment)
    assert message.state == OutboundMessage.State.CANCELLED
    assert other.state == OutboundMessage.State.QUEUED
    assert enrollment.state == CampaignEnrollment.State.INELIGIBLE


@pytest.mark.django_db
def test_a_recipient_whose_selected_email_changed_after_approval_is_dropped(world: World) -> None:
    message, enrollment, _ = world.recipient("a", approved=True)
    other, _, _ = world.recipient("b", approved=True)
    _, replacement = _organization_and_email(world.workspace, "replacement")
    CampaignEnrollment.objects.filter(pk=enrollment.pk).update(selected_email=replacement)

    start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(message, other)
    assert message.state == OutboundMessage.State.CANCELLED
    assert "email" in message.error.lower()
    assert other.state == OutboundMessage.State.QUEUED


@pytest.mark.django_db
def test_a_message_without_an_audience_entry_is_never_queued(world: World) -> None:
    message, _, _ = world.recipient("a", approved=True)
    other, _, _ = world.recipient("b", approved=True)
    OutboundMessage.objects.filter(pk=message.pk).update(campaign_enrollment=None)

    start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(message, other)
    assert message.state == OutboundMessage.State.CANCELLED
    assert "audiencia aprobada" in message.error
    assert other.state == OutboundMessage.State.QUEUED


@pytest.mark.django_db
def test_tampered_pdfs_stop_the_whole_start_without_queuing_anything(world: World) -> None:
    message, _, _ = world.recipient("a", approved=True)
    other, _, _ = world.recipient("b", approved=True)
    start_state = Campaign.State.AWAITING_APPROVAL
    # Snapshot the attachments once, then corrupt one so the prepared set no longer matches.
    from apps.campaigns.approval import _recheck_prepared_message

    _recheck_prepared_message(message)
    OutboundAttachment.objects.filter(message=message).update(sha256="0" * 64)

    with pytest.raises(ValidationError, match="PDFs preparados"):
        start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(message, other, world.campaign)
    assert world.campaign.state == start_state
    assert message.state == OutboundMessage.State.PREPARED
    assert other.state == OutboundMessage.State.PREPARED


@pytest.mark.django_db
def test_a_seller_cannot_start_delivery(world: World) -> None:
    message, _, _ = world.recipient("a", approved=True)
    seller = User.objects.create_user(username="seller", password="seller-password-1")

    with pytest.raises(PermissionDenied):
        start_per_message_campaign(world.campaign.pk, actor=seller)
    with pytest.raises(PermissionDenied):
        approve_campaign(world.campaign.pk, actor=seller)

    _reload(message, world.campaign)
    assert message.state == OutboundMessage.State.PREPARED
    assert world.campaign.state == Campaign.State.AWAITING_APPROVAL


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [Campaign.State.DRAFT, Campaign.State.RUNNING, Campaign.State.PAUSED, Campaign.State.CANCELLED],
)
def test_delivery_only_starts_from_awaiting_approval(world: World, state: str) -> None:
    message, _, _ = world.recipient("a", approved=True)
    Campaign.objects.filter(pk=world.campaign.pk).update(state=state)

    with pytest.raises(ValidationError, match="todavía no está lista"):
        start_per_message_campaign(world.campaign.pk, actor=world.owner)
    with pytest.raises(ValidationError, match="todavía no está lista"):
        approve_campaign(world.campaign.pk, actor=world.owner)

    _reload(message)
    assert message.state == OutboundMessage.State.PREPARED


@pytest.mark.django_db
def test_each_approval_mode_refuses_the_other_flow(world: World) -> None:
    world.recipient("a", approved=True)

    with pytest.raises(ValidationError, match="una sola confirmación"):
        Campaign.objects.filter(pk=world.campaign.pk).update(approval_mode=Campaign.ApprovalMode.CAMPAIGN)
        start_per_message_campaign(world.campaign.pk, actor=world.owner)

    Campaign.objects.filter(pk=world.campaign.pk).update(approval_mode=Campaign.ApprovalMode.PER_MESSAGE)
    with pytest.raises(ValidationError, match="mensaje por mensaje"):
        approve_campaign(world.campaign.pk, actor=world.owner)


@pytest.mark.django_db
def test_starting_twice_cannot_queue_the_same_messages_again(world: World) -> None:
    message, _, _ = world.recipient("a", approved=True)
    start_per_message_campaign(world.campaign.pk, actor=world.owner)

    with pytest.raises(ValidationError, match="todavía no está lista"):
        start_per_message_campaign(world.campaign.pk, actor=world.owner)

    _reload(message)
    assert message.state == OutboundMessage.State.QUEUED
    assert AuditEvent.objects.filter(action="campaign.per_message_delivery_started").count() == 1
