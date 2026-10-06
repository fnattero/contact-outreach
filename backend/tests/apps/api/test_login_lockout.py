from __future__ import annotations

import json
import logging

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.accounts.models import LoginThrottle


def _login(client: Client, csrf: str, username: str, password: str, address: str):
    return client.post(
        reverse("api-auth-login"),
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        REMOTE_ADDR=address,
    )


def _csrf(client: Client, address: str) -> str:
    return str(
        client.get(reverse("api-auth-csrf"), REMOTE_ADDR=address).json()["data"]["csrf_token"]
    )


@pytest.mark.django_db
def test_fifth_failure_locks_the_pair_and_a_locked_request_does_not_extend_it() -> None:
    address = "127.0.0.80"
    client = Client(enforce_csrf_checks=True)
    csrf = _csrf(client, address)

    for _ in range(5):
        assert _login(client, csrf, " Missing ", "wrong", address).status_code == 401

    throttle = LoginThrottle.objects.get(scope=LoginThrottle.Scope.PAIR)
    locked_until = throttle.locked_until
    assert locked_until is not None

    blocked = _login(client, csrf, "missing", "wrong", address)

    assert blocked.status_code == 429
    assert 1 <= int(blocked["Retry-After"]) <= 1800
    throttle.refresh_from_db()
    assert throttle.failure_count == 5
    assert throttle.locked_until == locked_until


@pytest.mark.django_db
def test_a_locked_account_rejects_the_correct_password_too() -> None:
    User.objects.create_user(username="locked-user", password="correct-password")
    address = "127.0.0.82"
    client = Client(enforce_csrf_checks=True)
    csrf = _csrf(client, address)
    for _ in range(5):
        _login(client, csrf, "locked-user", "wrong", address)

    response = _login(client, csrf, "locked-user", "correct-password", address)

    assert response.status_code == 429
    assert "sessionid" not in response.cookies


@pytest.mark.django_db
def test_successful_login_clears_only_the_username_and_address_pair() -> None:
    User.objects.create_user(username="owner", password="correct-password")
    address = "127.0.0.81"
    client = Client(enforce_csrf_checks=True)
    csrf = _csrf(client, address)
    for _ in range(4):
        _login(client, csrf, "OWNER", "wrong", address)
    assert LoginThrottle.objects.filter(scope=LoginThrottle.Scope.PAIR).exists()

    response = _login(client, csrf, "owner", "correct-password", address)

    assert response.status_code == 200
    assert not LoginThrottle.objects.filter(scope=LoginThrottle.Scope.PAIR).exists()
    # The address-wide counter is deliberately kept so spraying many usernames still trips it.
    assert LoginThrottle.objects.filter(scope=LoginThrottle.Scope.IP).exists()


@pytest.mark.django_db
def test_activation_secret_is_never_written_to_request_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_token = "sensitive-single-use-token"
    client = Client(enforce_csrf_checks=True)
    csrf = _csrf(client, "127.0.0.90")

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            reverse("api-auth-activate"),
            data=json.dumps(
                {
                    "token": raw_token,
                    "password": "Another-Strong-Pass-1",
                    "password_confirmation": "Another-Strong-Pass-1",
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
            REMOTE_ADDR="127.0.0.90",
        )

    assert response.status_code == 400
    assert raw_token not in caplog.text
    assert raw_token not in response.content.decode()
