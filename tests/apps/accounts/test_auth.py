from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_anonymous_user_is_redirected_to_login(client: Client) -> None:
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next=/"


def test_admin_authentication_route_is_not_exposed(client: Client) -> None:
    assert client.get("/admin/login/").status_code == 404


@pytest.mark.django_db
def test_owner_can_login_view_dashboard_and_logout(client: Client) -> None:
    User.objects.create_user(username="owner", password="correct-password")
    response = client.post(reverse("login"), {"username": "owner", "password": "correct-password"})
    assert response.status_code == 302
    assert response.url == reverse("dashboard")

    dashboard = client.get(reverse("dashboard"))
    assert dashboard.status_code == 200
    assert b"Dashboard" in dashboard.content
    assert b"dry-run" in dashboard.content
    assert dashboard.content.count(b"fake") == 4

    assert client.get(reverse("logout")).status_code == 405
    assert client.post(reverse("logout")).status_code == 302
    assert client.get(reverse("dashboard")).status_code == 302


@pytest.mark.django_db
def test_login_post_requires_csrf() -> None:
    csrf_client = Client(enforce_csrf_checks=True)
    response = csrf_client.post(reverse("login"), {"username": "owner", "password": "wrong"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_failed_login_is_neutral_and_throttled(client: Client) -> None:
    cache.clear()
    for _ in range(5):
        response = client.post(
            reverse("login"),
            {"username": "missing", "password": "wrong"},
            REMOTE_ADDR="127.0.0.55",
        )
        assert response.status_code == 200
        assert b"No se pudo iniciar sesi" in response.content

    blocked = client.post(
        reverse("login"),
        {"username": "missing", "password": "wrong"},
        REMOTE_ADDR="127.0.0.55",
    )
    assert blocked.status_code == 429
