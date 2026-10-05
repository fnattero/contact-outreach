from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.services import canonical_client_ip
from apps.api.middleware import InternalProxyMiddleware

TOKEN = "t" * 40
FRONTEND = "10.8.0.5"


def _run(request):
    return InternalProxyMiddleware(lambda _request: HttpResponse("ok"))(request)


def test_the_authenticated_proxy_can_report_the_browser_address() -> None:
    request = RequestFactory().get(
        "/api/v1/auth/session/",
        REMOTE_ADDR=FRONTEND,
        HTTP_X_INTERNAL_PROXY_TOKEN=TOKEN,
        HTTP_X_INTERNAL_CLIENT_IP="203.0.113.7",
    )
    with override_settings(APP_ENV="production", INTERNAL_PROXY_TOKEN=TOKEN):
        assert _run(request).status_code == 200

    assert canonical_client_ip(request.META) == "203.0.113.7"
    assert "HTTP_X_INTERNAL_CLIENT_IP" not in request.META


@pytest.mark.parametrize("value", ["", "not-an-ip", "203.0.113.7, 198.51.100.1", "999.1.1.1"])
def test_an_invalid_reported_address_falls_back_to_the_peer(value: str) -> None:
    request = RequestFactory().get(
        "/api/v1/auth/session/",
        REMOTE_ADDR=FRONTEND,
        HTTP_X_INTERNAL_PROXY_TOKEN=TOKEN,
        HTTP_X_INTERNAL_CLIENT_IP=value,
    )
    with override_settings(APP_ENV="production", INTERNAL_PROXY_TOKEN=TOKEN):
        assert _run(request).status_code == 200

    assert canonical_client_ip(request.META) == FRONTEND


def test_the_reported_address_is_ignored_without_a_valid_proxy_token() -> None:
    wrong_token = RequestFactory().get(
        "/api/v1/auth/session/",
        REMOTE_ADDR="198.51.100.9",
        HTTP_X_INTERNAL_PROXY_TOKEN="x" * 40,
        HTTP_X_INTERNAL_CLIENT_IP="203.0.113.7",
    )
    health = RequestFactory().get(
        "/api/v1/health/live/",
        REMOTE_ADDR="198.51.100.9",
        HTTP_X_INTERNAL_CLIENT_IP="203.0.113.7",
    )
    with override_settings(APP_ENV="production", INTERNAL_PROXY_TOKEN=TOKEN):
        assert _run(wrong_token).status_code == 403
        assert _run(health).status_code == 200

    assert canonical_client_ip(wrong_token.META) == "198.51.100.9"
    assert canonical_client_ip(health.META) == "198.51.100.9"


def test_the_reported_address_is_ignored_outside_production() -> None:
    request = RequestFactory().get(
        "/api/v1/auth/session/",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_INTERNAL_PROXY_TOKEN=TOKEN,
        HTTP_X_INTERNAL_CLIENT_IP="203.0.113.7",
    )
    with override_settings(APP_ENV="development", INTERNAL_PROXY_TOKEN=TOKEN):
        assert _run(request).status_code == 200

    assert canonical_client_ip(request.META) == "127.0.0.1"


def _proxied(client: Client, client_ip: str) -> dict[str, str]:
    return {
        "REMOTE_ADDR": FRONTEND,
        "HTTP_X_INTERNAL_PROXY_TOKEN": TOKEN,
        "HTTP_X_INTERNAL_CLIENT_IP": client_ip,
    }


@pytest.mark.django_db
def test_one_browser_locking_itself_out_does_not_lock_everyone_behind_the_proxy() -> None:
    User.objects.create_user(username="shared-user", password="correct-password")
    with override_settings(APP_ENV="production", INTERNAL_PROXY_TOKEN=TOKEN):
        attacker = Client(enforce_csrf_checks=True)
        csrf = str(
            attacker.get(reverse("api-auth-csrf"), **_proxied(attacker, "203.0.113.50")).json()[
                "data"
            ]["csrf_token"]
        )
        for _ in range(5):
            response = attacker.post(
                reverse("api-auth-login"),
                data=json.dumps({"username": "shared-user", "password": "wrong"}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf,
                **_proxied(attacker, "203.0.113.50"),
            )
            assert response.status_code == 401

        victim = Client(enforce_csrf_checks=True)
        victim_csrf = str(
            victim.get(reverse("api-auth-csrf"), **_proxied(victim, "198.51.100.77")).json()[
                "data"
            ]["csrf_token"]
        )
        login = victim.post(
            reverse("api-auth-login"),
            data=json.dumps({"username": "shared-user", "password": "correct-password"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=victim_csrf,
            **_proxied(victim, "198.51.100.77"),
        )

    assert login.status_code == 200, login.content
