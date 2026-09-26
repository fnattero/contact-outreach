from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Workspace
from apps.campaigns.models import Campaign
from apps.catalogs.services import create_catalog
from apps.contacts.models import (
    CampaignEnrollment,
    CommunicationRestriction,
    Contact,
    Conversation,
    EmailAddress,
    Organization,
    OrganizationIdentity,
)
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection


@pytest.mark.django_db
def test_email_is_unique_in_workspace_and_only_one_is_preferred(owner) -> None:
    workspace = owner.membership.workspace
    first = Organization.objects.create(workspace=workspace, name="Primera")
    second = Organization.objects.create(workspace=workspace, name="Segunda")
    EmailAddress.objects.create(
        workspace=workspace,
        organization=first,
        original_email="ventas@example.com",
        normalized_email="ventas@example.com",
        validity=EmailAddress.Validity.VALID,
        is_preferred=True,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        EmailAddress.objects.create(
            workspace=workspace,
            organization=second,
            original_email="VENTAS@example.com",
            normalized_email="ventas@example.com",
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        EmailAddress.objects.create(
            workspace=workspace,
            organization=first,
            original_email="info@example.com",
            normalized_email="info@example.com",
            is_preferred=True,
        )


@pytest.mark.django_db
def test_contact_rejects_a_preferred_email_owned_by_another_organization(owner) -> None:
    workspace = owner.membership.workspace
    first = Organization.objects.create(workspace=workspace, name="Primera")
    second = Organization.objects.create(workspace=workspace, name="Segunda")
    wrong_email = EmailAddress.objects.create(
        workspace=workspace,
        organization=second,
        original_email="contacto@example.com",
        normalized_email="contacto@example.com",
    )
    contact = Contact(
        workspace=workspace,
        organization=first,
        preferred_email=wrong_email,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )

    with pytest.raises(ValidationError, match="email preferido"):
        contact.full_clean()


@pytest.mark.django_db
def test_unsubscribe_is_irreversible_but_manual_restriction_has_audited_reversal(owner) -> None:
    workspace = owner.membership.workspace
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    email_address = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="cliente@example.com",
        normalized_email="cliente@example.com",
    )
    unsubscribe = CommunicationRestriction.objects.create(
        workspace=workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
        email_address=email_address,
        source="respuesta",
    )
    unsubscribe.revoked_at = timezone.now()
    unsubscribe.revoked_by = owner
    unsubscribe.revocation_reason = "No corresponde"

    with pytest.raises(ValidationError, match="baja"):
        unsubscribe.save()

    manual = CommunicationRestriction.objects.create(
        workspace=workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=CommunicationRestriction.Kind.MANUAL,
        email_address=email_address,
        source="dashboard",
    )
    manual.revoked_at = timezone.now() + timedelta(seconds=1)
    manual.revoked_by = owner
    manual.revocation_reason = "El administrador confirmó el cambio"
    manual.save()
    assert not manual.is_active


@pytest.mark.django_db
def test_bounce_must_target_one_email_and_does_not_require_a_contact(owner) -> None:
    workspace = owner.membership.workspace
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    invalid = CommunicationRestriction(
        workspace=workspace,
        scope=CommunicationRestriction.Scope.CONTACT,
        kind=CommunicationRestriction.Kind.BOUNCE,
        contact=contact,
        source="gmail",
    )

    with pytest.raises(ValidationError, match="rebote"):
        invalid.save()


# This is a single-company installation: Workspace has a DB CheckConstraint that
# forces singleton_key=1, so a second *persisted* Workspace row cannot exist. The
# workspace-mismatch branches in the clean() methods below are still real,
# reachable defensive checks (they compare in-memory workspace_id values), so
# they are exercised here with an unsaved Workspace instance that is never
# written to the database.


@pytest.mark.django_db
def test_organization_identity_rejects_an_organization_from_another_workspace(owner) -> None:
    workspace = owner.membership.workspace
    other_workspace = Workspace(name="Otro espacio (no persistido)")
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    identity = OrganizationIdentity(
        workspace=other_workspace,
        organization=organization,
        kind=OrganizationIdentity.Kind.EMAIL,
        value="alguien@example.com",
        value_hash="a" * 64,
    )

    with pytest.raises(ValidationError, match="espacio de la organización"):
        identity.full_clean()


@pytest.mark.django_db
def test_email_address_rejects_an_organization_from_another_workspace(owner) -> None:
    workspace = owner.membership.workspace
    other_workspace = Workspace(name="Otro espacio (no persistido)")
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    email_address = EmailAddress(
        workspace=other_workspace,
        organization=organization,
        original_email="cliente@example.com",
        normalized_email="cliente@example.com",
    )

    with pytest.raises(ValidationError, match="espacio de la organización"):
        email_address.full_clean()


@pytest.mark.django_db
def test_contact_rejects_an_organization_from_another_workspace(owner) -> None:
    workspace = owner.membership.workspace
    other_workspace = Workspace(name="Otro espacio (no persistido)")
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    contact = Contact(
        workspace=other_workspace,
        organization=organization,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )

    with pytest.raises(ValidationError, match="mismo espacio"):
        contact.full_clean()


@pytest.mark.django_db
def test_communication_restriction_validates_scope_target_and_reversal_consistency(
    owner,
) -> None:
    workspace = owner.membership.workspace
    other_workspace = Workspace(name="Otro espacio (no persistido)")
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    email_address = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="canal@example.com",
        normalized_email="canal@example.com",
    )

    # scope=CONTACT without a contact (or with an email_address too) is invalid.
    with pytest.raises(ValidationError, match="contacto completo"):
        CommunicationRestriction(
            workspace=workspace,
            scope=CommunicationRestriction.Scope.CONTACT,
            kind=CommunicationRestriction.Kind.MANUAL,
            source="dashboard",
        ).full_clean()

    # scope=CONTACT with a contact from another workspace is invalid.
    with pytest.raises(ValidationError, match="mismo espacio"):
        CommunicationRestriction(
            workspace=other_workspace,
            scope=CommunicationRestriction.Scope.CONTACT,
            kind=CommunicationRestriction.Kind.MANUAL,
            contact=contact,
            source="dashboard",
        ).full_clean()

    # scope=EMAIL without an email_address (or with a contact too) is invalid.
    with pytest.raises(ValidationError, match="único email"):
        CommunicationRestriction(
            workspace=workspace,
            scope=CommunicationRestriction.Scope.EMAIL,
            kind=CommunicationRestriction.Kind.MANUAL,
            source="dashboard",
        ).full_clean()

    # scope=EMAIL with an email_address from another workspace is invalid.
    with pytest.raises(ValidationError, match="mismo espacio"):
        CommunicationRestriction(
            workspace=other_workspace,
            scope=CommunicationRestriction.Scope.EMAIL,
            kind=CommunicationRestriction.Kind.MANUAL,
            email_address=email_address,
            source="dashboard",
        ).full_clean()

    # An unsubscribe restriction can never carry a revoked_at value.
    with pytest.raises(ValidationError, match="baja"):
        CommunicationRestriction(
            workspace=workspace,
            scope=CommunicationRestriction.Scope.EMAIL,
            kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
            email_address=email_address,
            source="dashboard",
            revoked_at=timezone.now(),
            revoked_by=owner,
            revocation_reason="No debería poder",
        ).full_clean()

    # A revocation cannot set only some of revoked_at/revoked_by/revocation_reason.
    with pytest.raises(ValidationError, match="reversión está incompleta"):
        CommunicationRestriction(
            workspace=workspace,
            scope=CommunicationRestriction.Scope.EMAIL,
            kind=CommunicationRestriction.Kind.MANUAL,
            email_address=email_address,
            source="dashboard",
            revoked_by=owner,
        ).full_clean()

    with pytest.raises(ValidationError, match="quién revierte"):
        CommunicationRestriction(
            workspace=workspace,
            scope=CommunicationRestriction.Scope.EMAIL,
            kind=CommunicationRestriction.Kind.MANUAL,
            email_address=email_address,
            source="dashboard",
            revoked_at=timezone.now(),
        ).full_clean()


@pytest.mark.django_db
def test_conversation_rejects_a_contact_from_another_workspace(owner) -> None:
    workspace = owner.membership.workspace
    other_workspace = Workspace(name="Otro espacio (no persistido)")
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    contact = Contact.objects.create(
        workspace=workspace,
        organization=organization,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    connection = GmailConnection.objects.create(
        workspace=workspace,
        owner=owner,
        email="equipo@example.com",
        refresh_token_encrypted=encrypt_token("token"),
    )
    conversation = Conversation(
        workspace=other_workspace,
        contact=contact,
        connection=connection,
        gmail_thread_id="thread-1",
    )

    with pytest.raises(ValidationError, match="mismo espacio"):
        conversation.full_clean()


@pytest.mark.django_db
def test_campaign_enrollment_rejects_mismatched_organization_and_selected_email(
    owner,
    private_catalog_dir,
) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    workspace = owner.membership.workspace
    other_workspace = Workspace(name="Otro espacio (no persistido)")
    catalog = create_catalog(
        name="Catálogo enrollment",
        upload=SimpleUploadedFile(
            "catalogo-enrollment.pdf",
            b"%PDF-1.4\n% enrollment\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        workspace=workspace,
        name="Campaña enrollment",
        catalog=catalog,
        created_by=owner,
    )
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    other_organization = Organization.objects.create(workspace=workspace, name="Otro cliente")
    unrelated_email = EmailAddress.objects.create(
        workspace=workspace,
        organization=other_organization,
        original_email="otro@example.com",
        normalized_email="otro@example.com",
    )

    with pytest.raises(ValidationError, match="pertenecer al mismo espacio"):
        CampaignEnrollment(
            workspace=other_workspace,
            campaign=campaign,
            organization=organization,
        ).full_clean()

    with pytest.raises(ValidationError, match="pertenecer a la organización"):
        CampaignEnrollment(
            workspace=workspace,
            campaign=campaign,
            organization=organization,
            selected_email=unrelated_email,
        ).full_clean()
