from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.campaigns.models import Campaign, SearchQuery, SearchRun
from apps.catalogs.services import create_catalog
from apps.compliance.models import ContactLedger, ContactOverride, SuppressionEntry
from apps.compliance.services import suppress_email
from apps.contacts.models import (
    CommunicationRestriction,
    Contact,
    EmailAddress,
    Organization,
    OrganizationIdentity,
)
from apps.contacts.services import (
    IdentityClaim,
    OrganizationResolutionConflict,
    create_manual_contact,
    create_manual_restriction,
    enrollment_eligibility,
    ensure_prospect_enrollment,
    resolve_organization,
    revoke_manual_restriction,
    validate_contact_email_address,
)
from apps.prospects.email_validation import MockMXResolver
from apps.prospects.models import Prospect, ProspectEmail


def _campaign_prospect(owner: User, *, suffix: str = "one") -> tuple[Campaign, Prospect]:
    catalog = create_catalog(
        name=f"Contactos {suffix}",
        upload=SimpleUploadedFile(
            f"contactos-{suffix}.pdf",
            f"%PDF-1.4\n% {suffix}\n%%EOF".encode(),
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        workspace=owner.membership.workspace,
        name=f"Campaña {suffix}",
        catalog=catalog,
        created_by=owner,
    )
    query = SearchQuery.objects.create(
        campaign=campaign,
        category_snapshot="Taller",
        zone_snapshot="CABA",
        location_snapshot="CABA",
        query_text=f"taller-{suffix}",
        normalized_query=f"taller-{suffix}",
    )
    run = SearchRun.objects.create(
        campaign=campaign,
        query=query,
        provider="fake",
        idempotency_key=f"contact-service:{suffix}",
        requested_limit=10,
    )
    prospect = Prospect.objects.create(
        campaign=campaign,
        source_run=run,
        name=f"Taller {suffix}",
        normalized_name=f"taller {suffix}",
        address="Av. Siempre Viva 123",
        normalized_address="av. siempre viva 123",
        pipeline_state=Prospect.PipelineState.EMAIL_FOUND,
    )
    ProspectEmail.objects.create(
        prospect=prospect,
        original_email=f"ventas-{suffix}@example.com",
        normalized_email=f"ventas-{suffix}@example.com",
        domain="example.com",
        local_part=f"ventas-{suffix}",
        source="fixture",
        mx_status=ProspectEmail.MXStatus.VALID,
        mx_checked_at=timezone.now(),
        is_primary=True,
    )
    return campaign, prospect


@pytest.mark.django_db
def test_resolution_converges_and_refuses_cross_organization_merge(owner: User) -> None:
    workspace = owner.membership.workspace
    gers = IdentityClaim(kind=OrganizationIdentity.Kind.GERS_ID, value="GERS-123")
    first = resolve_organization(
        workspace=workspace,
        identities=(gers,),
        email="Ventas@Ejemplo.com",
        name="Taller Uno",
        email_validity=EmailAddress.Validity.VALID,
        prefer_email=True,
    )
    second = resolve_organization(
        workspace=workspace,
        identities=(gers,),
        email="ventas@ejemplo.com",
        name="Nombre posterior",
        email_validity=EmailAddress.Validity.VALID,
    )

    assert first.organization == second.organization
    assert first.email_address == second.email_address
    assert Organization.objects.count() == 1
    assert EmailAddress.objects.count() == 1

    other = resolve_organization(
        workspace=workspace,
        identities=(IdentityClaim(kind=OrganizationIdentity.Kind.PROVIDER_ID, value="other:456"),),
        email="otra@example.net",
    )
    assert other.organization != first.organization
    with pytest.raises(OrganizationResolutionConflict, match="organizaciones distintas"):
        resolve_organization(
            workspace=workspace,
            identities=(
                gers,
                IdentityClaim(
                    kind=OrganizationIdentity.Kind.PROVIDER_ID,
                    value="other:456",
                ),
            ),
        )


@pytest.mark.django_db
def test_contact_excludes_enrollment_even_when_legacy_override_exists(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    campaign, prospect = _campaign_prospect(owner, suffix="override")
    enrollment = ensure_prospect_enrollment(prospect)
    assert enrollment_eligibility(enrollment).eligible
    selected = enrollment.selected_email
    assert selected is not None
    Contact.objects.create(
        workspace=owner.membership.workspace,
        organization=enrollment.organization,
        preferred_email=selected,
        created_reason=Contact.CreatedReason.MANUAL_ENTRY,
    )
    ledger = ContactLedger.objects.create(normalized_email=selected.normalized_email)
    ContactOverride.objects.create(
        ledger=ledger,
        campaign=campaign,
        reason="Registro histórico que ya no autoriza envíos",
        created_by=owner,
    )

    result = enrollment_eligibility(enrollment)
    assert not result.eligible
    assert result.code == "EXISTING_CONTACT"


@pytest.mark.django_db
def test_manual_contact_and_restriction_require_admin_and_keep_audit_history(
    owner: User,
) -> None:
    vendedor = User.objects.create_user(username="vendedor", password="password")
    with pytest.raises(PermissionDenied):
        create_manual_contact(actor=vendedor, email="cliente@example.com")

    contact = create_manual_contact(
        actor=owner,
        email="Cliente@Example.com",
        organization_name="Cliente actual",
        contact_name="Ana",
    )
    same_contact = create_manual_contact(actor=owner, email="cliente@example.com")
    assert same_contact == contact
    assert contact.organization.email_addresses.count() == 1

    email_address = contact.preferred_email
    assert email_address is not None
    restriction = create_manual_restriction(
        actor=owner,
        scope=CommunicationRestriction.Scope.EMAIL,
        email_address_id=email_address.pk,
        reason="El cliente pidió usar otro canal.",
    )
    assert restriction.is_active
    assert SuppressionEntry.objects.filter(
        normalized_email=email_address.normalized_email,
        reason=SuppressionEntry.Reason.MANUAL,
    ).exists()

    revoked = revoke_manual_restriction(
        actor=owner,
        restriction_id=restriction.pk,
        reason="El administrador confirmó que el canal vuelve a estar habilitado.",
    )
    assert not revoked.is_active
    assert revoked.revoked_by == owner
    assert not SuppressionEntry.objects.filter(
        normalized_email=email_address.normalized_email
    ).exists()


@pytest.mark.django_db
def test_manual_contact_email_can_be_validated_for_scheduled_communication(owner: User) -> None:
    contact = create_manual_contact(actor=owner, email="cliente-validar@example.com")
    email_address = contact.preferred_email
    assert email_address is not None

    state = validate_contact_email_address(email_address.pk, resolver=MockMXResolver())

    email_address.refresh_from_db()
    assert state == EmailAddress.Validity.VALID
    assert email_address.validity == EmailAddress.Validity.VALID
    assert email_address.validated_at is not None


@pytest.mark.django_db
def test_revoking_contact_restriction_keeps_an_unrelated_legacy_suppression(owner: User) -> None:
    contact = create_manual_contact(actor=owner, email="legacy-block@example.com")
    email_address = contact.preferred_email
    assert email_address is not None
    suppress_email(
        email=email_address.original_email,
        reason=SuppressionEntry.Reason.MANUAL,
        actor=owner,
        source="legacy_dashboard",
        evidence="Bloqueo histórico independiente",
    )
    restriction = create_manual_restriction(
        actor=owner,
        scope=CommunicationRestriction.Scope.EMAIL,
        email_address_id=email_address.pk,
        reason="Pausa temporal adicional.",
    )

    revoke_manual_restriction(
        actor=owner,
        restriction_id=restriction.pk,
        reason="Terminó la pausa temporal.",
    )

    assert SuppressionEntry.objects.filter(
        normalized_email=email_address.normalized_email,
        source="legacy_dashboard",
    ).exists()


@pytest.mark.django_db
def test_unsubscribe_restriction_cannot_use_manual_reversal(owner: User) -> None:
    workspace = owner.membership.workspace
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    email_address = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="baja@example.com",
        normalized_email="baja@example.com",
    )
    unsubscribe = CommunicationRestriction.objects.create(
        workspace=workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
        email_address=email_address,
        source="gmail_reply",
        source_inbound_message_id=None,
    )

    with pytest.raises(ValidationError, match="Sólo las restricciones manuales"):
        revoke_manual_restriction(
            actor=owner,
            restriction_id=unsubscribe.pk,
            reason="No debería poder hacerse",
        )


@pytest.mark.django_db
def test_email_restriction_without_contact_blocks_only_that_enrollment(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    del private_catalog_dir
    _, prospect = _campaign_prospect(owner, suffix="restriction")
    enrollment = ensure_prospect_enrollment(prospect)
    selected = enrollment.selected_email
    assert selected is not None
    CommunicationRestriction.objects.create(
        workspace=owner.membership.workspace,
        scope=CommunicationRestriction.Scope.EMAIL,
        kind=CommunicationRestriction.Kind.BOUNCE,
        email_address=selected,
        source="fixture",
        evidence="Rebote confirmado",
    )

    result = enrollment_eligibility(enrollment)
    assert not result.eligible
    assert result.code == "RESTRICTED"
