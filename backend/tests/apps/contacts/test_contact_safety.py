"""Do-not-contact, restrictions and manual channels: who may never be contacted again."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError

from apps.audit.models import AuditEvent
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email
from apps.contacts.models import CommunicationRestriction, Contact, EmailAddress
from apps.contacts.services import (
    OrganizationResolutionConflict,
    add_contact_email_address,
    communication_is_restricted,
    create_manual_contact,
    create_manual_restriction,
    queue_contact_email_validation,
    revoke_manual_restriction,
    set_contact_no_contact,
    validate_contact_email_address,
)
from apps.prospects.email_validation import MXStatus


class _Resolver:
    def __init__(self, status: MXStatus) -> None:
        self.status = status
        self.asked: list[str] = []

    def resolve(self, domain: str) -> MXStatus:
        self.asked.append(domain)
        return self.status


@pytest.fixture
def seller(owner: User) -> User:
    return User.objects.create_user(username="seller", password="seller-password-1")


@pytest.fixture
def contact(owner: User) -> Contact:
    return create_manual_contact(actor=owner, email="cliente@example.com", organization_name="Cliente")


def _unsubscribe(contact: Contact) -> CommunicationRestriction:
    return CommunicationRestriction.objects.create(
        workspace=contact.workspace,
        scope=CommunicationRestriction.Scope.CONTACT,
        kind=CommunicationRestriction.Kind.UNSUBSCRIBE,
        contact=contact,
        source="inbound",
    )


# --- do not contact ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_marking_do_not_contact_restricts_the_contact_and_suppresses_every_address(
    owner: User, contact: Contact
) -> None:
    add_contact_email_address(actor=owner, contact_id=contact.pk, email="ventas@example.com")

    blocked = set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=True)

    assert blocked.status == Contact.Status.DO_NOT_CONTACT
    assert CommunicationRestriction.objects.filter(
        contact=contact, kind=CommunicationRestriction.Kind.MANUAL, revoked_at__isnull=True
    ).count() == 1
    assert set(SuppressionEntry.objects.values_list("normalized_email", flat=True)) == {
        "cliente@example.com",
        "ventas@example.com",
    }
    assert communication_is_restricted(contact=blocked) is True
    assert AuditEvent.objects.filter(action="contact.restriction_created").exists()


@pytest.mark.django_db
def test_marking_do_not_contact_twice_does_not_stack_restrictions(owner: User, contact: Contact) -> None:
    set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=True)
    set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=True)

    assert CommunicationRestriction.objects.filter(contact=contact).count() == 1


@pytest.mark.django_db
def test_lifting_a_manual_block_restores_the_contact_and_removes_only_its_own_suppressions(
    owner: User, contact: Contact
) -> None:
    suppress_email(email="otro@legacy.example", reason=SuppressionEntry.Reason.MANUAL, actor=owner)
    set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=True)

    restored = set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=False)

    assert restored.status == Contact.Status.ACTIVE
    assert communication_is_restricted(contact=restored) is False
    assert list(SuppressionEntry.objects.values_list("normalized_email", flat=True)) == [
        "otro@legacy.example"
    ]
    assert AuditEvent.objects.filter(action="contact.restriction_revoked").exists()


@pytest.mark.django_db
def test_an_unsubscribe_can_never_be_lifted_from_the_contacts_screen(owner: User, contact: Contact) -> None:
    _unsubscribe(contact)

    with pytest.raises(ValidationError, match="no se puede quitar"):
        set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=False)

    assert communication_is_restricted(contact=contact) is True


@pytest.mark.django_db
def test_a_block_added_next_to_an_unsubscribe_still_cannot_be_lifted(owner: User, contact: Contact) -> None:
    _unsubscribe(contact)
    set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=True)

    with pytest.raises(ValidationError):
        set_contact_no_contact(actor=owner, contact_id=contact.pk, blocked=False)

    assert communication_is_restricted(contact=contact) is True


@pytest.mark.django_db
def test_only_a_contact_manager_can_block_or_unblock(seller: User, contact: Contact) -> None:
    with pytest.raises(PermissionDenied):
        set_contact_no_contact(actor=seller, contact_id=contact.pk, blocked=True)
    with pytest.raises(PermissionDenied):
        create_manual_restriction(
            actor=seller, scope="CONTACT", contact_id=contact.pk, reason="Quiero bloquearlo"
        )

    assert not CommunicationRestriction.objects.exists()
    assert not SuppressionEntry.objects.exists()


@pytest.mark.django_db
def test_revoking_requires_a_reason_and_a_manual_restriction(owner: User, contact: Contact) -> None:
    manual = create_manual_restriction(
        actor=owner, scope="CONTACT", contact_id=contact.pk, reason="Pidió no recibir más"
    )

    with pytest.raises(ValidationError, match="por qué se quita"):
        revoke_manual_restriction(actor=owner, restriction_id=manual.pk, reason="  ")
    with pytest.raises(ValidationError, match="manuales"):
        revoke_manual_restriction(
            actor=owner, restriction_id=_unsubscribe(contact).pk, reason="Quiero revertirlo"
        )

    manual.refresh_from_db()
    assert manual.is_active


@pytest.mark.django_db
def test_revoking_twice_is_harmless(owner: User, contact: Contact) -> None:
    manual = create_manual_restriction(
        actor=owner, scope="CONTACT", contact_id=contact.pk, reason="Pidió no recibir más"
    )

    first = revoke_manual_restriction(actor=owner, restriction_id=manual.pk, reason="Se arregló")
    second = revoke_manual_restriction(actor=owner, restriction_id=manual.pk, reason="Otra vez")

    assert first.revoked_at == second.revoked_at
    assert second.revocation_reason == "Se arregló"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("scope", "use_contact", "use_email", "reason", "message"),
    [
        ("CONTACT", False, False, "Motivo", "contacto completo"),
        ("CONTACT", True, True, "Motivo", "contacto completo"),
        ("EMAIL", False, False, "Motivo", "único email"),
        ("EMAIL", True, True, "Motivo", "único email"),
        ("WORLD", True, False, "Motivo", "no es válido"),
        ("CONTACT", True, False, "   ", "por qué"),
    ],
)
def test_restrictions_need_one_clear_target_and_a_reason(
    owner: User,
    contact: Contact,
    scope: str,
    use_contact: bool,
    use_email: bool,
    reason: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        create_manual_restriction(
            actor=owner,
            scope=scope,
            contact_id=contact.pk if use_contact else None,
            email_address_id=contact.preferred_email_id if use_email else None,
            reason=reason,
        )

    assert not CommunicationRestriction.objects.exists()


@pytest.mark.django_db
def test_an_email_level_restriction_blocks_that_address_but_not_its_siblings(
    owner: User, contact: Contact
) -> None:
    other = add_contact_email_address(actor=owner, contact_id=contact.pk, email="ventas@example.com")
    first = contact.preferred_email
    assert first is not None

    create_manual_restriction(
        actor=owner, scope="EMAIL", email_address_id=first.pk, reason="Rebota siempre"
    )

    assert communication_is_restricted(contact=contact, email_address=first) is True
    assert communication_is_restricted(contact=contact, email_address=other) is False
    assert communication_is_restricted(contact=contact) is False


@pytest.mark.django_db
def test_an_address_from_another_organization_is_always_treated_as_restricted(
    owner: User, contact: Contact
) -> None:
    stranger = create_manual_contact(actor=owner, email="extrano@elsewhere.example")

    assert communication_is_restricted(contact=contact, email_address=stranger.preferred_email) is True


# --- manual channels ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_manually_added_email_starts_unvalidated_and_is_audited(owner: User, contact: Contact) -> None:
    address = add_contact_email_address(
        actor=owner, contact_id=contact.pk, email=" Ventas@Example.com ", label="Ventas"
    )

    assert address.normalized_email == "ventas@example.com"
    assert address.validity == EmailAddress.Validity.UNKNOWN
    assert address.label == "Ventas"
    assert address.organization_id == contact.organization_id
    assert AuditEvent.objects.filter(action="contact.email_added", entity_id=str(address.pk)).exists()
    contact.refresh_from_db()
    assert contact.preferred_email_id != address.pk  # it must be validated and chosen explicitly


@pytest.mark.django_db
def test_an_address_can_be_made_preferred_and_only_one_is_preferred(owner: User, contact: Contact) -> None:
    address = add_contact_email_address(
        actor=owner, contact_id=contact.pk, email="nuevo@example.com", make_preferred=True
    )

    contact.refresh_from_db()
    assert contact.preferred_email_id == address.pk
    assert EmailAddress.objects.filter(organization=contact.organization, is_preferred=True).count() == 1


@pytest.mark.django_db
def test_an_invalid_address_cannot_become_preferred(owner: User, contact: Contact) -> None:
    address = add_contact_email_address(actor=owner, contact_id=contact.pk, email="malo@example.com")
    EmailAddress.objects.filter(pk=address.pk).update(validity=EmailAddress.Validity.INVALID)

    with pytest.raises(ValidationError, match="inválido"):
        add_contact_email_address(
            actor=owner, contact_id=contact.pk, email="malo@example.com", make_preferred=True
        )


@pytest.mark.django_db
def test_an_address_owned_by_another_organization_is_never_moved(owner: User, contact: Contact) -> None:
    other = create_manual_contact(actor=owner, email="vecino@vecino.example", organization_name="Vecino")

    with pytest.raises(OrganizationResolutionConflict, match="organizaciones distintas"):
        add_contact_email_address(actor=owner, contact_id=contact.pk, email="vecino@vecino.example")

    assert other.preferred_email is not None
    assert other.preferred_email.organization_id == other.organization_id


@pytest.mark.django_db
def test_only_a_contact_manager_can_add_addresses(seller: User, contact: Contact) -> None:
    with pytest.raises(PermissionDenied):
        add_contact_email_address(actor=seller, contact_id=contact.pk, email="x@example.com")
    with pytest.raises(PermissionDenied):
        queue_contact_email_validation(actor=seller, email_address_id=contact.preferred_email_id)


@pytest.mark.django_db
def test_validation_can_only_be_requested_for_unchecked_addresses(owner: User, contact: Contact) -> None:
    address = contact.preferred_email
    assert address is not None

    queue_contact_email_validation(actor=owner, email_address_id=address.pk)  # unknown: allowed
    EmailAddress.objects.filter(pk=address.pk).update(validity=EmailAddress.Validity.VALID)
    with pytest.raises(ValidationError, match="ya tiene un resultado"):
        queue_contact_email_validation(actor=owner, email_address_id=address.pk)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "validity", "invalid_reason"),
    [
        (MXStatus.VALID, EmailAddress.Validity.VALID, ""),
        (MXStatus.INVALID, EmailAddress.Validity.INVALID, "El dominio no recibe correo."),
        (MXStatus.TRANSIENT, EmailAddress.Validity.TRANSIENT, ""),
    ],
)
def test_mx_results_are_recorded_and_an_invalid_domain_is_blocked(
    contact: Contact, status: MXStatus, validity: str, invalid_reason: str
) -> None:
    address = contact.preferred_email
    assert address is not None

    result = validate_contact_email_address(address.pk, resolver=_Resolver(status))

    address.refresh_from_db()
    assert result == validity == address.validity
    assert address.invalid_reason == invalid_reason
    assert (address.invalidated_at is not None) is (validity == EmailAddress.Validity.INVALID)


@pytest.mark.django_db
@pytest.mark.parametrize("final", [EmailAddress.Validity.VALID, EmailAddress.Validity.INVALID])
def test_a_final_validity_is_never_re_checked_or_flipped(contact: Contact, final: str) -> None:
    address = contact.preferred_email
    assert address is not None
    EmailAddress.objects.filter(pk=address.pk).update(validity=final)
    resolver = _Resolver(MXStatus.VALID if final == EmailAddress.Validity.INVALID else MXStatus.INVALID)

    assert validate_contact_email_address(address.pk, resolver=resolver) == final
    assert resolver.asked == []
