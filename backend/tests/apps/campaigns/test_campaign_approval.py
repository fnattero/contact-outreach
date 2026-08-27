from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.campaigns.approval import (
    approve_campaign,
    freeze_campaign_content,
    prepare_fixed_initial_messages,
    refresh_campaign_hashes,
)
from apps.campaigns.models import Campaign, CampaignAttachment, OutboundMessage
from apps.catalogs.services import create_catalog
from apps.contacts.models import CampaignEnrollment, Contact, EmailAddress, Organization


def _ready_campaign(
    owner: User,
    *,
    recipient_count: int = 2,
) -> tuple[Campaign, tuple[CampaignEnrollment, ...]]:
    catalog = create_catalog(
        name="Aprobación segura",
        upload=SimpleUploadedFile(
            "catalogo-aprobacion.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    workspace = owner.membership.workspace
    campaign = Campaign.objects.create(
        workspace=workspace,
        name="Campaña lista para aprobar",
        catalog=catalog,
        created_by=owner,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        approval_mode=Campaign.ApprovalMode.CAMPAIGN,
    )
    CampaignAttachment.objects.create(campaign=campaign, catalog=catalog, position=0)
    freeze_campaign_content(campaign)
    campaign.save(
        update_fields=(
            "initial_subject_snapshot",
            "initial_body_snapshot",
            "reminder_body_snapshot",
            "referred_subject_snapshot",
            "referred_body_snapshot",
            "signature_snapshot",
            "template_revision_snapshot",
            "content_hash",
            "updated_at",
        )
    )
    enrollments: list[CampaignEnrollment] = []
    for index in range(recipient_count):
        organization = Organization.objects.create(
            workspace=workspace,
            name=f"Empresa aprobación {index}",
        )
        email = EmailAddress.objects.create(
            workspace=workspace,
            organization=organization,
            original_email=f"aprobacion-{index}@example.com",
            normalized_email=f"aprobacion-{index}@example.com",
            domain="example.com",
            validity=EmailAddress.Validity.VALID,
            is_preferred=True,
        )
        enrollments.append(
            CampaignEnrollment.objects.create(
                workspace=workspace,
                campaign=campaign,
                organization=organization,
                selected_email=email,
                state=CampaignEnrollment.State.ELIGIBLE,
            )
        )
    prepare_fixed_initial_messages(campaign)
    refresh_campaign_hashes(campaign)
    campaign.state = Campaign.State.AWAITING_APPROVAL
    campaign.save(
        update_fields=(
            "state",
            "audience_hash",
            "attachment_hash",
            "schedule_hash",
            "updated_at",
        )
    )
    return campaign, tuple(enrollments)


@pytest.mark.django_db
def test_campaign_approval_records_only_the_final_eligible_audience(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign, enrollments = _ready_campaign(owner)
    preview_hash = campaign.audience_hash
    excluded = enrollments[0]
    Contact.objects.create(
        workspace=campaign.workspace,
        organization=excluded.organization,
        preferred_email=excluded.selected_email,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
        created_by=owner,
    )

    approved = approve_campaign(campaign.pk, actor=owner)

    excluded.refresh_from_db()
    assert approved.state == Campaign.State.RUNNING
    assert approved.audience_hash != preview_hash
    assert excluded.state == CampaignEnrollment.State.INELIGIBLE
    assert campaign.messages.filter(
        campaign_enrollment=excluded,
        state=OutboundMessage.State.CANCELLED,
    ).exists()
    queued = campaign.messages.filter(state=OutboundMessage.State.QUEUED)
    assert queued.count() == 1
    assert queued.get().campaign_enrollment_id == enrollments[1].pk


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("daily_limit", 999, "horario o el recordatorio"),
        ("initial_body_snapshot", "Contenido alterado", "mensaje o la firma"),
    ),
)
def test_campaign_approval_rejects_changed_schedule_or_content_snapshot(
    owner: User,
    private_catalog_dir: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    del private_catalog_dir
    campaign, _ = _ready_campaign(owner, recipient_count=1)
    Campaign.objects.filter(pk=campaign.pk).update(**{field: value})

    with pytest.raises(ValidationError, match=message):
        approve_campaign(campaign.pk, actor=owner)

    assert campaign.messages.get().state == OutboundMessage.State.PREPARED


@pytest.mark.django_db
def test_campaign_approval_rejects_a_tampered_pdf_snapshot_without_partial_queue(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign, enrollments = _ready_campaign(owner)
    compromised = campaign.messages.get(campaign_enrollment=enrollments[0])
    compromised.attachments.update(sha256="f" * 64)

    with pytest.raises(ValidationError, match="PDFs preparados"):
        approve_campaign(campaign.pk, actor=owner)

    compromised.refresh_from_db()
    enrollments[0].refresh_from_db()
    assert compromised.state == OutboundMessage.State.PREPARED
    assert enrollments[0].state == CampaignEnrollment.State.PREPARED
    assert not campaign.messages.filter(state=OutboundMessage.State.QUEUED).exists()
