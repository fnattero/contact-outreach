"""Additional coverage for apps.campaigns.review: error paths and edge branches
not already exercised by test_per_message_review.py."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.campaigns import review as campaign_review
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

LEGACY_PROFILE = {
    "signature": "Equipo Ventas",
    "company_name": "Fran",
    "address": "Buenos Aires",
}
LEGACY_OPERATOR_BODY = (
    "Hola, somos proveedores de componentes industriales y herramientas electricas. "
    "Queremos acercar una propuesta clara para reposicion y mantenimiento, con catalogos "
    "actualizados, medidas variadas y atencion directa para talleres, ferreterias y servicios "
    "electromecanicos. Si la informacion resulta util, podemos coordinar una visita comercial "
    "para revisar necesidades, aplicaciones frecuentes y disponibilidad de productos. "
    "¿Qué día conviene que pase el vendedor?\n\n"
    "Equipo Ventas\n"
    "Fran · Buenos Aires"
)


def _catalog(owner: User, name: str = "Revisión") -> object:
    return create_catalog(
        name=name,
        upload=SimpleUploadedFile(
            f"{name}.pdf",
            f"%PDF-1.4\n% {name}\n1 0 obj\n<<>>\nendobj\n%%EOF".encode(),
            content_type="application/pdf",
        ),
        actor=owner,
    )


def _campaign(owner: User, catalog: object, **overrides: object) -> Campaign:
    workspace = owner.membership.workspace
    final_state = overrides.pop("state", Campaign.State.AWAITING_APPROVAL)
    defaults: dict[str, object] = dict(
        workspace=workspace,
        name="Campaña revisión",
        catalog=catalog,
        created_by=owner,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        approval_mode=Campaign.ApprovalMode.PER_MESSAGE,
        profile_snapshot=LEGACY_PROFILE,
        state=Campaign.State.DRAFT,
    )
    defaults.update(overrides)
    campaign = Campaign.objects.create(**defaults)
    CampaignAttachment.objects.create(campaign=campaign, catalog=catalog, position=0)
    if final_state != Campaign.State.DRAFT:
        campaign.state = final_state
        campaign.save(update_fields=("state", "updated_at"))
    return campaign


def _organization_and_email(
    workspace: object, suffix: str = "0"
) -> tuple[Organization, EmailAddress]:
    organization = Organization.objects.create(
        workspace=workspace,
        name=f"Empresa revisión {suffix}",
        normalized_name=f"empresa revision {suffix}",
    )
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email=f"contacto-{suffix}@example.com",
        normalized_email=f"contacto-{suffix}@example.com",
        domain="example.com",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )
    return organization, email


def _enrollment(
    workspace: object, campaign: Campaign, organization: Organization, email: EmailAddress
) -> CampaignEnrollment:
    return CampaignEnrollment.objects.create(
        workspace=workspace,
        campaign=campaign,
        organization=organization,
        selected_email=email,
        state=CampaignEnrollment.State.PREPARED,
    )


def _message(
    campaign: Campaign,
    organization: Organization,
    enrollment: CampaignEnrollment,
    email: EmailAddress,
    base_catalog: object,
    **overrides: object,
) -> OutboundMessage:
    defaults: dict[str, object] = dict(
        kind=OutboundMessage.Kind.INITIAL,
        campaign=campaign,
        organization=organization,
        campaign_enrollment=enrollment,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject=INITIAL_SUBJECT,
        body_text=INITIAL_BODY,
        catalog=base_catalog,
        catalog_version=base_catalog.version,
        state=OutboundMessage.State.REVIEW_READY,
        delivery_mode=campaign.delivery_mode,
        idempotency_key=f"msg:{uuid.uuid4()}",
    )
    defaults.update(overrides)
    if defaults.get("kind") != OutboundMessage.Kind.INITIAL and "body_text" not in overrides:
        defaults["subject"] = "Consulta comercial"
        defaults["body_text"] = LEGACY_OPERATOR_BODY
    return OutboundMessage.objects.create(**defaults)


@pytest.mark.django_db
def test_edit_and_approve_reject_a_message_without_a_campaign(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    message = OutboundMessage.objects.create(
        kind=OutboundMessage.Kind.INITIAL,
        organization=organization,
        email_address=email,
        recipient=email.original_email,
        recipient_normalized=email.normalized_email,
        subject=INITIAL_SUBJECT,
        body_text=INITIAL_BODY,
        catalog=catalog,
        catalog_version=catalog.version,
        state=OutboundMessage.State.REVIEW_READY,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        idempotency_key=f"orphan:{uuid.uuid4()}",
    )

    with pytest.raises(ValidationError, match="campaña de origen"):
        edit_message_draft(message.pk, actor=owner, subject=INITIAL_SUBJECT, body_text=INITIAL_BODY)
    with pytest.raises(ValidationError, match="campaña de origen"):
        approve_message_for_delivery(message.pk, actor=owner)


@pytest.mark.django_db
def test_edit_message_draft_rejects_manual_reply_kind(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        kind=OutboundMessage.Kind.MANUAL_REPLY,
    )

    with pytest.raises(ValidationError, match=r"respuesta.*conversación"):
        edit_message_draft(message.pk, actor=owner, subject="x", body_text="y")


@pytest.mark.django_db
def test_validate_copy_rejects_a_message_with_no_campaign_when_called_directly() -> None:
    message = OutboundMessage(
        kind=OutboundMessage.Kind.INITIAL,
        subject=INITIAL_SUBJECT,
        body_text=INITIAL_BODY,
    )
    with pytest.raises(ValidationError, match="campaña de origen"):
        campaign_review._validate_copy(message, subject=INITIAL_SUBJECT, body_text=INITIAL_BODY)


@pytest.mark.django_db
def test_edit_message_draft_rejects_invalid_subject_and_body(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(campaign, organization, enrollment, email, catalog)

    with pytest.raises(ValidationError, match="asunto no es válido"):
        edit_message_draft(
            message.pk,
            actor=owner,
            subject="Línea uno\nLínea dos",
            body_text=INITIAL_BODY,
        )
    with pytest.raises(ValidationError, match="texto plano válido"):
        edit_message_draft(
            message.pk,
            actor=owner,
            subject=INITIAL_SUBJECT,
            body_text="<b>hola</b>",
        )


@pytest.mark.django_db
def test_edit_message_draft_requires_the_approved_signature(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog, signature_snapshot="Equipo Ventas")
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        body_text=f"{INITIAL_BODY}\n\nEquipo Ventas",
    )

    with pytest.raises(ValidationError, match="firma aprobada"):
        edit_message_draft(
            message.pk,
            actor=owner,
            subject=INITIAL_SUBJECT,
            body_text="Contenido sin firma",
        )


@pytest.mark.django_db
def test_edit_message_draft_rejects_placeholder_markers_via_freeze_content(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog, signature_snapshot="Equipo Ventas")
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        body_text=f"{INITIAL_BODY}\n\nEquipo Ventas",
    )

    with pytest.raises(ValidationError, match="datos variables"):
        edit_message_draft(
            message.pk,
            actor=owner,
            subject=INITIAL_SUBJECT,
            body_text="Hola {{ nombre }}\n\nEquipo Ventas",
        )


@pytest.mark.django_db
def test_edit_message_draft_is_a_noop_when_content_is_unchanged(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(campaign, organization, enrollment, email, catalog)
    original_revision = message.content_revision

    result = edit_message_draft(
        message.pk,
        actor=owner,
        subject=INITIAL_SUBJECT,
        body_text=INITIAL_BODY,
    )

    assert result.pk == message.pk
    assert result.content_revision == original_revision


@pytest.mark.django_db
def test_edit_message_draft_uses_operator_validation_for_first_contact_kind(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(
        owner,
        catalog,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        state=Campaign.State.RUNNING,
    )
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        kind=OutboundMessage.Kind.FIRST_CONTACT,
    )
    assert can_edit_message(message)

    with pytest.raises(ValidationError, match="asunto no es válido"):
        edit_message_draft(
            message.pk,
            actor=owner,
            subject="Línea uno\nLínea dos",
            body_text=INITIAL_BODY,
        )


@pytest.mark.django_db
def test_can_edit_message_false_for_unsupported_kind_state_or_missing_campaign() -> None:
    reminder = OutboundMessage(
        kind=OutboundMessage.Kind.CAMPAIGN_REMINDER,
        state=OutboundMessage.State.REVIEW_READY,
    )
    assert can_edit_message(reminder) is False

    not_ready = OutboundMessage(
        kind=OutboundMessage.Kind.INITIAL,
        state=OutboundMessage.State.PREPARED,
    )
    assert can_edit_message(not_ready) is False

    orphan = OutboundMessage(
        kind=OutboundMessage.Kind.INITIAL,
        state=OutboundMessage.State.REVIEW_READY,
    )
    assert orphan.campaign is None
    assert can_edit_message(orphan) is False


@pytest.mark.django_db
def test_can_edit_message_false_when_delivery_mode_mismatches(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog, delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        delivery_mode=Campaign.DeliveryMode.LIVE,
    )
    assert can_edit_message(message) is False


@pytest.mark.django_db
def test_can_edit_message_review_only_first_contact_depends_on_campaign_state(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(
        owner,
        catalog,
        delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY,
        state=Campaign.State.CANCELLED,
    )
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        kind=OutboundMessage.Kind.FIRST_CONTACT,
        delivery_mode=Campaign.DeliveryMode.REVIEW_ONLY,
    )
    assert can_edit_message(message) is False

    Campaign.objects.filter(pk=campaign.pk).update(state=Campaign.State.RUNNING)
    message.campaign.refresh_from_db()
    assert can_edit_message(message) is True


@pytest.mark.django_db
def test_approve_message_rejects_when_not_approvable(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog, approval_mode=Campaign.ApprovalMode.CAMPAIGN)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(campaign, organization, enrollment, email, catalog)
    assert can_approve_message(message) is False

    with pytest.raises(ValidationError, match="no está disponible para aprobación"):
        approve_message_for_delivery(message.pk, actor=owner)


@pytest.mark.django_db
def test_approve_message_rejects_missing_mismatched_or_stale_catalog(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner, name="Original")
    other_catalog = _catalog(owner, name="Otro")
    campaign = _campaign(owner, catalog)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)

    missing_catalog_message = _message(
        campaign, organization, enrollment, email, catalog, catalog=None, catalog_version=None
    )
    with pytest.raises(ValidationError, match="PDF asociado"):
        approve_message_for_delivery(missing_catalog_message.pk, actor=owner)

    mismatched_catalog_message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        catalog=other_catalog,
        catalog_version=other_catalog.version,
        idempotency_key=f"msg:{uuid.uuid4()}",
    )
    with pytest.raises(ValidationError, match="no coincide con la campaña"):
        approve_message_for_delivery(mismatched_catalog_message.pk, actor=owner)

    stale_version_message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        catalog_version=catalog.version + 1,
        idempotency_key=f"msg:{uuid.uuid4()}",
    )
    with pytest.raises(ValidationError, match="inconsistente"):
        approve_message_for_delivery(stale_version_message.pk, actor=owner)


@pytest.mark.django_db
def test_approve_message_initial_kind_requires_attachments(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(campaign, organization, enrollment, email, catalog)

    with pytest.raises(ValidationError, match="PDFs aprobados de la campaña"):
        approve_message_for_delivery(message.pk, actor=owner)


@pytest.mark.django_db
def test_approve_message_initial_kind_rejects_a_tampered_attachment(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(owner, catalog)
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(campaign, organization, enrollment, email, catalog)
    OutboundAttachment.objects.create(
        message=message,
        catalog=catalog,
        position=0,
        catalog_version=catalog.version,
        storage_key=catalog.storage_key,
        filename=catalog.original_filename,
        byte_size=catalog.byte_size,
        sha256="f" * 64,
    )

    with pytest.raises(ValidationError, match="cambió desde que se preparó"):
        approve_message_for_delivery(message.pk, actor=owner)


@pytest.mark.django_db
def test_approve_message_first_contact_kind_skips_attachment_checks_and_succeeds(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(
        owner,
        catalog,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        state=Campaign.State.RUNNING,
    )
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        kind=OutboundMessage.Kind.FIRST_CONTACT,
    )
    assert can_approve_message(message) is True

    approved = approve_message_for_delivery(message.pk, actor=owner)

    assert approved.state == OutboundMessage.State.QUEUED
    assert approved.approved_by == owner
    assert approved.next_attempt_at is not None
    assert not approved.attachments.exists()


@pytest.mark.django_db
def test_approve_message_rejects_an_ineligible_recipient(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    catalog = _catalog(owner)
    campaign = _campaign(
        owner,
        catalog,
        delivery_mode=Campaign.DeliveryMode.LIVE,
        state=Campaign.State.RUNNING,
    )
    workspace = owner.membership.workspace
    organization, email = _organization_and_email(workspace)
    email.validity = EmailAddress.Validity.UNKNOWN
    email.save(update_fields=("validity", "updated_at"))
    enrollment = _enrollment(workspace, campaign, organization, email)
    message = _message(
        campaign,
        organization,
        enrollment,
        email,
        catalog,
        kind=OutboundMessage.Kind.FIRST_CONTACT,
    )

    with pytest.raises(ValidationError, match="no está validado para enviar"):
        approve_message_for_delivery(message.pk, actor=owner)
