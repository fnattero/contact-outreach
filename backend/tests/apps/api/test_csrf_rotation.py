"""Login and logout rotate the CSRF secret, so a client must fetch a fresh token afterwards.

The frontend caches its token; these tests pin the server behaviour that makes it clear that
cache on login, activation and logout (see frontend/tests/api-client.test.ts).
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

PASSWORD = "a-long-enough-password-1"


def _post(client: Client, name: str, token: str, payload: dict[str, str] | None = None):
    return client.post(
        reverse(name),
        data=json.dumps(payload or {}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )


def _token(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


@pytest.fixture
def client(db: None) -> Client:
    User.objects.create_user(username="admin", password=PASSWORD)
    return Client(enforce_csrf_checks=True)


def _login(client: Client, token: str):
    return _post(client, "api-auth-login", token, {"username": "admin", "password": PASSWORD})


def test_a_token_fetched_before_login_is_refused_after_it(client: Client) -> None:
    before = _token(client)
    assert _login(client, before).status_code == 200

    assert _post(client, "api-auth-logout", before).status_code == 403


def test_a_token_fetched_after_login_works(client: Client) -> None:
    assert _login(client, _token(client)).status_code == 200

    assert _post(client, "api-auth-logout", _token(client)).status_code in {200, 204}


def test_login_is_refused_without_any_csrf_token(client: Client) -> None:
    response = client.post(
        reverse("api-auth-login"),
        data=json.dumps({"username": "admin", "password": PASSWORD}),
        content_type="application/json",
    )

    assert response.status_code == 403


def test_a_token_from_a_previous_session_is_refused_for_the_next_login(client: Client) -> None:
    assert _login(client, _token(client)).status_code == 200
    stale = _token(client)
    assert _post(client, "api-auth-logout", stale).status_code in {200, 204}

    assert _login(client, stale).status_code == 403
    assert _login(client, _token(client)).status_code == 200


def test_a_forged_token_is_refused(client: Client) -> None:
    _token(client)

    assert _login(client, "x" * 64).status_code == 403
