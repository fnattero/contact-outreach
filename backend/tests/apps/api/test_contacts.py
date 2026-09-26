from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Membership
from apps.contacts.models import CommunicationRestriction


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


def _post(client: Client, url: str, payload: dict[str, object], csrf_token: str):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )


@pytest.mark.django_db
def test_contacts_require_authentication_and_return_pagination_metadata() -> None:
    response = Client().get(reverse("api-contacts"))

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


@pytest.mark.django_db
def test_admin_can_create_contact_and_vendor_can_only_read(owner: User) -> None:
    admin_client = Client(enforce_csrf_checks=True)
    admin_client.force_login(owner)
    csrf_token = _csrf(admin_client)
    create = _post(
        admin_client,
        reverse("api-contacts"),
        {
            "email": "principal@example.invalid",
            "organization_name": "Example Organization",
            "contact_name": "Patricia Example",
        },
        csrf_token,
    )

    assert create.status_code == 201
    contact = create.json()["data"]
    assert contact["organization_name"] == "Example Organization"
    assert contact["preferred_email"] == "principal@example.invalid"
    assert contact["status"] == "ACTIVE"

    listed = admin_client.get(reverse("api-contacts"), {"page_size": 100})
    assert listed.status_code == 200
    assert listed.json()["meta"] == {"page": 1, "page_size": 100, "total": 1}
    assert listed.json()["data"][0]["id"] == contact["id"]

    vendor = User.objects.create_user(
        username="vendor",
        password="vendor-password-1234",
        email="vendor@example.invalid",
    )
    assert vendor.membership.role == Membership.Role.VENDEDOR
    vendor_client = Client(enforce_csrf_checks=True)
    vendor_client.force_login(vendor)
    assert vendor_client.get(reverse("api-contacts")).status_code == 200
    denied = _post(
        vendor_client,
        reverse("api-contacts"),
        {"email": "second@example.invalid"},
        _csrf(vendor_client),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"


@pytest.mark.django_db
def test_contact_detail_and_manual_restriction_are_scoped_and_auditable(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf(client)
    created = _post(
        client,
        reverse("api-contacts"),
        {"email": "detail@example.invalid", "organization_name": "Detail Org"},
        csrf_token,
    )
    contact_id = created.json()["data"]["id"]

    detail = client.get(reverse("api-contact-detail", args=(contact_id,)))
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["organization_name"] == "Detail Org"
    assert len(data["emails"]) == 1
    assert data["emails"][0]["original_email"] == "detail@example.invalid"
    assert data["timelines"] == []

    restricted = _post(
        client,
        reverse("api-contact-restrictions", args=(contact_id,)),
        {
            "scope": CommunicationRestriction.Scope.CONTACT,
            "reason": "Pidió no recibir nuevos contactos.",
        },
        csrf_token,
    )
    assert restricted.status_code == 201
    restriction = restricted.json()["data"]
    assert restriction["scope"] == CommunicationRestriction.Scope.CONTACT
    assert restriction["kind"] == CommunicationRestriction.Kind.MANUAL

    detail_after = client.get(reverse("api-contact-detail", args=(contact_id,)))
    assert detail_after.status_code == 200
    assert detail_after.json()["data"]["status"] == "DO_NOT_CONTACT"
    assert len(detail_after.json()["data"]["restrictions"]) == 1

    revoked = _post(
        client,
        reverse(
            "api-contact-restriction-revoke",
            args=(contact_id, restriction["id"]),
        ),
        {"reason": "La persona confirmó que podemos retomar el contacto."},
        csrf_token,
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["revoked_at"] is not None
