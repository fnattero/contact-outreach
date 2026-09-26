from __future__ import annotations

import pytest

from apps.contacts.models import EmailAddress, Organization
from apps.contacts.tasks import validate_contact_email_task


@pytest.mark.django_db
def test_manual_email_validation_task_uses_network_free_resolver_in_tests(owner) -> None:
    workspace = owner.membership.workspace
    organization = Organization.objects.create(workspace=workspace, name="Cliente")
    email = EmailAddress.objects.create(
        workspace=workspace,
        organization=organization,
        original_email="ventas@example.com",
        normalized_email="ventas@example.com",
        domain="example.com",
        validity=EmailAddress.Validity.UNKNOWN,
    )

    state = validate_contact_email_task(str(email.pk))

    email.refresh_from_db()
    assert state == EmailAddress.Validity.VALID
    assert email.validity == EmailAddress.Validity.VALID
    assert email.validated_at is not None
