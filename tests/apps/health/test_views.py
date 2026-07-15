from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_liveness_is_public_and_does_not_check_dependencies(client: Client) -> None:
    response = client.get(reverse("health-live"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["Cache-Control"] == (
        "max-age=0, no-cache, no-store, must-revalidate, private"
    )


@pytest.mark.django_db
def test_readiness_checks_database_and_cache(client: Client) -> None:
    response = client.get(reverse("health-ready"))
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "components": {"database": "ok", "redis": "ok"},
    }


@pytest.mark.django_db
def test_readiness_reports_dependency_failure_without_details(client: Client) -> None:
    with patch("apps.health.views.cache.set", side_effect=RuntimeError("secret detail")):
        response = client.get(reverse("health-ready"))
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "components": {"database": "ok", "redis": "unavailable"},
    }
    assert b"secret detail" not in response.content


@pytest.mark.django_db
def test_readiness_reports_database_failure_without_details(client: Client) -> None:
    with patch("apps.health.views.connection.cursor", side_effect=RuntimeError("database detail")):
        response = client.get(reverse("health-ready"))
    assert response.status_code == 503
    assert response.json()["components"] == {"database": "unavailable", "redis": "ok"}
    assert b"database detail" not in response.content


def test_health_rejects_post(client: Client) -> None:
    assert client.post(reverse("health-live")).status_code == 405
