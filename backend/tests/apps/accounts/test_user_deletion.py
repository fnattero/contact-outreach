from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Membership
from apps.accounts.services import delete_managed_user
from apps.audit.models import AuditEvent
from apps.catalogs.services import create_catalog


def _extra_admin(username: str = "second-admin") -> User:
    user = User.objects.create_user(username=username, password="admin-password-1234")
    membership = Membership.objects.get(user=user)
    membership.role = Membership.Role.ADMIN
    membership.save(update_fields=("role",))
    # Reload so the user does not carry the seller membership created with the account.
    return User.objects.get(pk=user.pk)


@pytest.mark.django_db
def test_a_user_without_history_can_be_deleted_and_the_audit_keeps_the_fact(owner: User) -> None:
    other = User.objects.create_user(username="never-did-anything", password="vendor-password-1234")
    other_id = other.pk

    delete_managed_user(membership=Membership.objects.get(user=other), actor=owner)

    assert not User.objects.filter(pk=other_id).exists()
    event = AuditEvent.objects.get(action="accounts.user_deleted")
    assert event.entity_id == str(other_id)
    assert event.before["username"] == "never-did-anything"
    assert event.actor == owner


@pytest.mark.django_db
def test_a_user_with_history_is_refused_with_a_reason_and_stays(
    owner: User, private_catalog_dir: Path
) -> None:
    del private_catalog_dir
    author = _extra_admin()
    create_catalog(
        name="Catálogo de otro admin",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=author,
    )

    with pytest.raises(ValidationError, match="actividad registrada"):
        delete_managed_user(membership=Membership.objects.get(user=author), actor=owner)

    assert User.objects.filter(pk=author.pk).exists()
    assert Membership.objects.filter(user=author).exists()
    assert not AuditEvent.objects.filter(action="accounts.user_deleted").exists()


@pytest.mark.django_db
def test_nobody_can_delete_themselves(owner: User) -> None:
    with pytest.raises(ValidationError, match="propio usuario"):
        delete_managed_user(membership=Membership.objects.get(user=owner), actor=owner)

    assert User.objects.filter(pk=owner.pk).exists()


@pytest.mark.django_db
def test_a_seller_cannot_delete_anyone(owner: User) -> None:
    seller = User.objects.create_user(username="deleting-vendor", password="vendor-password-1234")
    victim = User.objects.create_user(username="victim", password="vendor-password-1234")

    with pytest.raises(PermissionDenied):
        delete_managed_user(membership=Membership.objects.get(user=victim), actor=seller)

    assert User.objects.filter(pk=victim.pk).exists()


@pytest.mark.django_db
def test_the_api_deletes_with_204_and_explains_a_refusal(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])
    victim = User.objects.create_user(username="api-victim", password="vendor-password-1234")

    gone = client.delete(reverse("api-user-detail", args=(victim.pk,)), HTTP_X_CSRFTOKEN=csrf)
    assert gone.status_code == 204
    assert not User.objects.filter(pk=victim.pk).exists()

    own = client.delete(reverse("api-user-detail", args=(owner.pk,)), HTTP_X_CSRFTOKEN=csrf)
    assert own.status_code == 400
    assert "propio usuario" in json.loads(own.content)["detail"]
