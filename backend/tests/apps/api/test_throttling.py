"""Each rate class (read, export, reauthentication...) must be counted on its own."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse


def _signed_in(owner: User) -> Client:
    cache.clear()
    client = Client()
    client.force_login(owner)
    return client


@pytest.mark.django_db
@override_settings(API_READ_THROTTLE_RATE="1000/5m", API_EXPORT_THROTTLE_RATE="2/h")
def test_ordinary_requests_do_not_use_up_the_export_allowance(owner: User) -> None:
    client = _signed_in(owner)
    for _ in range(10):
        assert client.get(reverse("api-contacts")).status_code == 200

    export = reverse("api-inbound-message-export")
    assert client.get(export).status_code == 200
    assert client.get(export).status_code == 200
    limited = client.get(export)

    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"


@pytest.mark.django_db
@override_settings(API_READ_THROTTLE_RATE="1000/5m", API_EXPORT_THROTTLE_RATE="2/h")
def test_exports_do_not_use_up_the_read_allowance(owner: User) -> None:
    client = _signed_in(owner)
    export = reverse("api-inbound-message-export")
    for _ in range(3):
        client.get(export)

    assert client.get(reverse("api-contacts")).status_code == 200


@pytest.mark.django_db
@override_settings(API_READ_THROTTLE_RATE="1000/5m")
def test_the_password_check_for_sensitive_actions_is_not_blocked_by_browsing(
    owner: User,
) -> None:
    client = _signed_in(owner)
    for _ in range(8):
        assert client.get(reverse("api-contacts")).status_code == 200
    csrf = client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"]

    response = client.post(
        reverse("api-auth-reauthenticate"),
        data=json.dumps({"password": "correct-password"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert response.status_code == 200, response.content


@pytest.mark.django_db
@override_settings(API_REAUTH_THROTTLE_RATE="3/5m")
def test_guessing_the_password_check_is_still_limited(owner: User) -> None:
    client = _signed_in(owner)
    csrf = client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"]
    statuses = [
        client.post(
            reverse("api-auth-reauthenticate"),
            data=json.dumps({"password": f"guess-{attempt}"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        for attempt in range(5)
    ]

    assert statuses[:3] != [429, 429, 429]
    assert statuses[3:] == [429, 429]
