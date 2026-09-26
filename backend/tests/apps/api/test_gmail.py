from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.mailbox.models import GmailConnection


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


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


@pytest.mark.django_db
def test_gmail_oauth_start_stores_one_time_session_material(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    with patch(
        "apps.api.gmail.authorization_url",
        return_value="https://accounts.google.example/authorize",
    ) as authorization:
        response = client.post(
            reverse("api-gmail-oauth-start"),
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=_csrf(client),
        )

    assert response.status_code == 200
    assert response.json()["data"]["authorization_url"].startswith("https://accounts.google")
    assert client.session.get("api_gmail_oauth_state")
    assert client.session.get("api_gmail_oauth_verifier")
    assert client.session.get("api_gmail_oauth_started_at")
    authorization.assert_called_once()


@pytest.mark.django_db
def test_gmail_oauth_callback_rejects_expired_state(owner: User) -> None:
    client = Client()
    client.force_login(owner)
    session = client.session
    session["api_gmail_oauth_state"] = "expected-state"
    session["api_gmail_oauth_verifier"] = "verifier"
    session["api_gmail_oauth_started_at"] = (timezone.now() - timedelta(minutes=11)).isoformat()
    session.save()

    response = client.get(
        reverse("api-gmail-oauth-callback"),
        {"state": "expected-state", "code": "provider-code"},
    )

    assert response.status_code == 302
    assert response["Location"].endswith("gmail=oauth_failed")
    assert "api_gmail_oauth_state" not in client.session


@pytest.mark.django_db
def test_gmail_test_and_disconnect_fail_without_a_usable_connection(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf(client)
    for name in ("api-gmail-test", "api-gmail-disconnect"):
        response = client.post(
            reverse(name),
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"
