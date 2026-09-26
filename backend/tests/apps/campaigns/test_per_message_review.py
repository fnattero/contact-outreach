from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.campaigns.content import INITIAL_BODY, INITIAL_SUBJECT
from apps.campaigns.models import (
    Campaign,
    CampaignAttachment,
    OutboundAttachment,
    OutboundMessage,
)
from apps.campaigns.review import (
    approve_message_for_delivery,
    can_approve_message,
    can_edit_message,
    edit_message_draft,
)
from apps.catalogs.services import create_catalog
from apps.contacts.models import CampaignEnrollment, EmailAddress, Organization


@pytest.mark.django_db
def test_per_message_initial_is_approved_without_starting_delivery(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    catalog = create_catalog(
        name="Aprobación individual",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    workspace = owner.membership.workspace
    campaign = Campaign.objects.create(
        workspace=workspace,
        name="Revisión individual",
        delivery_mode=Campaign.DeliveryMode.LIVE,
        approval_mode=Campaign.ApprovalMode.PER_MESSAGE,
        catalog=catalog,
        created_by=owner,
    )
    CampaignAttachment.objects.create(campaign=campaign, catalog=catalog, position=0)
    campaign.state = Campaign.State.AWAITING_APPROVAL
    campaign.save(update_fields=("state", "updated_at"))
    organization = Organization.objects.create(
        workspace=workspace,
        name="Taller seguro",
        normalized_name="taller seguro",
    )
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="ventas@example.com",
        normalized_email="ventas@example.com",
        domain="example.com",
        is_preferred=True,
        validity=EmailAddress.Validity.VALID,
    )
    enrollment = CampaignEnrollment.objects.create(
        workspace=workspace,
        campaign=campaign,
        organization=organization,
        selected_email=email,
        state=CampaignEnrollment.State.PREPARED,
    )
    message = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        campaign=campaign,
        organization=organization,
        campaign_enrollment=enrollment,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject=INITIAL_SUBJECT,
        body_text=INITIAL_BODY,
        catalog=catalog,
        catalog_version=catalog.version,
        state=OutboundMessage.State.REVIEW_READY,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key=f"initial:{campaign.pk}:{enrollment.pk}",
    )
    OutboundAttachment.objects.create(
        message=message,
        catalog=catalog,
        position=0,
        catalog_version=catalog.version,
        storage_key=catalog.storage_key,
        filename=catalog.original_filename,
        byte_size=catalog.byte_size,
        sha256=catalog.sha256,
    )

    assert can_edit_message(message)
    assert can_approve_message(message)
    edited = edit_message_draft(
        message.pk,
        actor=owner,
        subject=INITIAL_SUBJECT,
        body_text=f"{INITIAL_BODY}\n\nQuedamos a disposición.",
    )
    assert edited.state == OutboundMessage.State.REVIEW_READY

    approved = approve_message_for_delivery(message.pk, actor=owner)

    assert approved.state == OutboundMessage.State.PREPARED
    assert approved.approved_by == owner
    assert approved.approved_at is not None
    assert approved.next_attempt_at is None
    campaign.refresh_from_db()
    assert campaign.state == Campaign.State.AWAITING_APPROVAL
