from __future__ import annotations

import json

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.models import ActivationToken, Membership
from apps.accounts.services import create_managed_user
from apps.api.middleware import InternalProxyMiddleware


def _json_post(client: Client, url: str, payload: dict[str, str], csrf_token: str):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )


def _csrf_token(client: Client) -> str:
    response = client.get(reverse("api-auth-csrf"))
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


@pytest.mark.django_db
def test_csrf_endpoint_uses_a_session_and_does_not_set_a_csrf_cookie() -> None:
    client = Client()

    response = client.get(reverse("api-auth-csrf"))

    assert response.status_code == 200
    assert response.json()["data"]["csrf_token"]
    assert settings.CSRF_USE_SESSIONS is True
    assert settings.CSRF_COOKIE_NAME not in response.cookies
    assert settings.SESSION_COOKIE_NAME in response.cookies


@pytest.mark.django_db
def test_login_requires_csrf_and_returns_an_authenticated_session(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    payload = {"username": owner.username, "password": "correct-password"}

    without_csrf = client.post(
        reverse("api-auth-login"),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert without_csrf.status_code == 403

    response = _json_post(client, reverse("api-auth-login"), payload, _csrf_token(client))

    assert response.status_code == 200
    session = response.json()["data"]
    assert session["username"] == owner.username
    assert session["role"] == Membership.Role.ADMIN
    assert "manage_users" in session["capabilities"]
    assert response["X-Correlation-ID"]
    assert response.cookies[settings.SESSION_COOKIE_NAME]["httponly"]
    assert response.cookies[settings.SESSION_COOKIE_NAME]["samesite"] == "Lax"

    session_response = client.get(reverse("api-auth-session"))
    assert session_response.status_code == 200
    assert session_response.json()["data"]["username"] == owner.username


@pytest.mark.django_db
def test_invalid_login_is_neutral_and_records_durable_throttle() -> None:
    User.objects.create_user(username="known-user", password="correct-password")
    client = Client(enforce_csrf_checks=True)
    csrf_token = _csrf_token(client)

    response = _json_post(
        client,
        reverse("api-auth-login"),
        {"username": "unknown-user", "password": "wrong-password"},
        csrf_token,
    )

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json()["code"] == "invalid_credentials"
    assert "unknown-user" not in response.content.decode()


@pytest.mark.django_db
def test_logout_requires_csrf_and_flushes_the_session(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf_token(client)

    response = client.post(
        reverse("api-auth-logout"),
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 204
    assert client.get(reverse("api-auth-session")).status_code == 401


@pytest.mark.django_db
def test_activation_token_is_one_use_and_logs_in_the_new_user(owner: User) -> None:
    user, issued = create_managed_user(
        username="new-vendor",
        email="vendor@example.invalid",
        role=Membership.Role.VENDEDOR,
        actor=owner,
    )
    client = Client(enforce_csrf_checks=True)
    csrf_token = _csrf_token(client)
    password = "a-very-long-new-password"

    response = _json_post(
        client,
        reverse("api-auth-activate"),
        {
            "token": issued.raw_token,
            "password": password,
            "password_confirmation": password,
        },
        csrf_token,
    )

    assert response.status_code == 200
    assert response.json()["data"]["role"] == Membership.Role.VENDEDOR
    assert User.objects.get(pk=user.pk).check_password(password)
    assert ActivationToken.objects.get(pk=issued.token.pk).used_at is not None

    second_attempt = _json_post(
        client,
        reverse("api-auth-activate"),
        {
            "token": issued.raw_token,
            "password": password,
            "password_confirmation": password,
        },
        _csrf_token(client),
    )
    assert second_attempt.status_code == 400
    assert second_attempt.json()["code"] == "validation_error"


@pytest.mark.django_db
def test_reauthentication_expires_and_is_not_a_login_bypass(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf_token(client)

    wrong = _json_post(
        client,
        reverse("api-auth-reauthenticate"),
        {"password": "wrong-password"},
        csrf_token,
    )
    assert wrong.status_code == 401

    valid = _json_post(
        client,
        reverse("api-auth-reauthenticate"),
        {"password": "correct-password"},
        csrf_token,
    )
    assert valid.status_code == 200
    assert valid.json()["data"]["reauthentication_active"] is True
    assert client.get(reverse("api-auth-session")).json()["data"]["reauthentication_active"] is True


def test_mfa_routes_and_runtime_dependency_are_absent() -> None:
    assert "django_otp" not in settings.INSTALLED_APPS
    assert "django_otp.plugins.otp_totp" not in settings.INSTALLED_APPS
    response = Client().get("/seguridad/mfa/")
    assert response.status_code == 404


def test_internal_api_requires_the_frontend_proxy_in_production() -> None:
    request_factory = RequestFactory()

    def endpoint(request):
        return HttpResponse("ok")

    with override_settings(APP_ENV="production", INTERNAL_PROXY_TOKEN="a" * 40):
        denied = InternalProxyMiddleware(endpoint)(request_factory.get("/api/v1/auth/session/"))
        allowed_request = request_factory.get(
            "/api/v1/auth/session/",
            HTTP_X_INTERNAL_PROXY_TOKEN="a" * 40,
        )
        allowed = InternalProxyMiddleware(endpoint)(allowed_request)
        health = InternalProxyMiddleware(endpoint)(request_factory.get("/api/v1/health/live/"))

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert "HTTP_X_INTERNAL_PROXY_TOKEN" not in allowed_request.META
    assert health.status_code == 200


@pytest.mark.django_db
def test_admin_user_api_scopes_targets_and_returns_fragment_activation_links(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf_token(client)

    created = _json_post(
        client,
        reverse("api-users"),
        {
            "username": "api-vendor",
            "email": "api-vendor@example.invalid",
            "role": Membership.Role.VENDEDOR,
        },
        csrf_token,
    )

    assert created.status_code == 201
    data = created.json()["data"]
    assert data["user"]["username"] == "api-vendor"
    assert data["user"]["is_active"] is False
    assert data["user"]["role"] == Membership.Role.VENDEDOR
    assert data["activation_url"].startswith("/activate#token=")
    assert "?token=" not in data["activation_url"]

    listed = client.get(reverse("api-users"))
    assert listed.status_code == 200
    assert {item["username"] for item in listed.json()["data"]} == {"api-vendor", "owner"}

    user_id = data["user"]["id"]
    changed = _json_post(
        client,
        reverse("api-user-role", args=(user_id,)),
        {"role": Membership.Role.ADMIN},
        csrf_token,
    )
    assert changed.status_code == 405
    changed = client.patch(
        reverse("api-user-role", args=(user_id,)),
        data=json.dumps({"role": Membership.Role.ADMIN}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["role"] == Membership.Role.ADMIN


@pytest.mark.django_db
def test_business_profile_is_admin_only_and_uses_explicit_fields(owner: User) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf_token = _csrf_token(client)
    url = reverse("api-workspace-profile")
    values = {
        "company_name": "Acme",
        "salesperson_name": "Ana",
        "address": "Calle 1",
        "signature": "Ana · Acme",
        "products": "Products",
        "relevance_threshold": 80,
    }

    response = client.patch(
        url,
        data=json.dumps(values),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 200
    profile = response.json()["data"]
    assert profile["company_name"] == "Acme"
    assert profile["relevance_threshold"] == 80
    assert set(profile) == {
        "company_name",
        "salesperson_name",
        "phone",
        "whatsapp",
        "description",
        "products",
        "differentiators",
        "address",
        "website",
        "signature",
        "additional_instructions",
        "relevance_threshold",
        "profile_version",
    }
