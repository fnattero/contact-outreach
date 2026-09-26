from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.mailbox.models import GmailConnection


@pytest.mark.django_db
def test_integration_status_is_admin_only_and_contains_no_credentials(owner: User) -> None:
    url = reverse("api-integrations-status")
    anonymous = Client().get(url)
    assert anonymous.status_code == 401

    vendor = User.objects.create_user(
        username="integration-vendor", password="vendor-password-1234"
    )
    vendor_client = Client()
    vendor_client.force_login(vendor)
    assert vendor_client.get(url).status_code == 403

    GmailConnection.objects.create(
        workspace=owner.membership.workspace,
        owner=owner,
        email="owner@example.invalid",
        status=GmailConnection.Status.DISCONNECTED,
    )
    admin = Client()
    admin.force_login(owner)
    response = admin.get(url)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["gmail"]["connection_status"] == GmailConnection.Status.DISCONNECTED
    assert data["gmail"]["email"] == "owner@example.invalid"
    body = response.content.decode()
    assert "refresh_token" not in body
    assert "client_secret" not in body
    assert "FIELD_ENCRYPTION_KEY" not in body
