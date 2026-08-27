from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.mailbox.models import GmailConnection


@pytest.mark.django_db
def test_gmail_connection_status_is_admin_only_and_safe(owner: User) -> None:
    url = reverse("api-gmail-connection")
    assert Client().get(url).status_code == 401

    vendor = User.objects.create_user(username="gmail-vendor", password="vendor-password-1234")
    vendor_client = Client()
    vendor_client.force_login(vendor)
    assert vendor_client.get(url).status_code == 403

    connection = GmailConnection.objects.create(
        workspace=owner.membership.workspace,
        owner=owner,
        email="owner@example.invalid",
        scopes=["gmail.readonly"],
        refresh_token_encrypted="encrypted-refresh-token",
        status=GmailConnection.Status.CONNECTED,
    )
    client = Client()
    client.force_login(owner)
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "connected": True,
        "status": GmailConnection.Status.CONNECTED,
        "email": connection.email,
        "scopes": connection.scopes,
        "last_tested_at": None,
        "error": None,
    }
    assert "refresh_token_encrypted" not in response.content.decode()


@pytest.mark.django_db
def test_gmail_oauth_callback_rejects_missing_or_replayed_session_state(owner: User) -> None:
    client = Client()
    client.force_login(owner)
    response = client.get(
        reverse("api-gmail-oauth-callback"),
        {"state": "wrong", "code": "provider-code"},
    )
    assert response.status_code == 302
    assert response["Location"].endswith("gmail=oauth_failed")
