from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.configuration.integrations import (
    GMAIL_CLIENT_SECRET_PURPOSE,
    LLM_KEY_PURPOSE,
    OUTSCRAPER_KEY_PURPOSE,
)
from apps.configuration.models import IntegrationConfiguration
from apps.core.crypto import encrypt_secret
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection


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


@pytest.mark.django_db
def test_degraded_health_reports_storage_and_provider_configuration(
    client: Client, tmp_path: Path
) -> None:
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        response = client.get(reverse("health-degraded"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["storage"] == "ok"
    assert payload["components"]["extractor"] == "fake"
    assert payload["components"]["gmail"] == "not_connected"
    assert payload["storage_free_bytes"] > 0


@pytest.mark.django_db
def test_degraded_health_requires_tested_gmail_connection(
    client: Client, owner: User, tmp_path: Path
) -> None:
    connection = GmailConnection.objects.create(
        owner=owner,
        email="owner@example.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh"),
        status=GmailConnection.Status.CONNECTED,
    )
    settings_override = override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        GMAIL_PROVIDER="fake",
    )
    with settings_override:
        not_tested = client.get(reverse("health-degraded"))
        assert not_tested.json()["components"]["gmail"] == "not_ready"

        connection.last_tested_at = timezone.now()
        connection.save(update_fields=("last_tested_at", "updated_at"))
        ready = client.get(reverse("health-degraded"))

    assert ready.json()["components"]["gmail"] == "ready"


@pytest.mark.django_db
def test_degraded_health_reports_missing_local_gmail_api_configuration(
    client: Client, owner: User, tmp_path: Path
) -> None:
    GmailConnection.objects.create(
        owner=owner,
        email="owner@gmail.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )
    with override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        GMAIL_PROVIDER="api",
        GMAIL_OAUTH_CLIENT_ID="",
        GMAIL_OAUTH_CLIENT_SECRET="",
    ):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"]["gmail"] == "missing_configuration"


@pytest.mark.django_db
def test_degraded_health_uses_encrypted_dashboard_configuration(
    client: Client, owner: User, tmp_path: Path
) -> None:
    configuration = IntegrationConfiguration.objects.create(
        owner=owner,
        extractor_provider="outscraper",
        outscraper_api_key_source=IntegrationConfiguration.SecretSource.ENCRYPTED,
        outscraper_api_key_encrypted=encrypt_secret(
            "health-outscraper-secret", purpose=OUTSCRAPER_KEY_PURPOSE
        ),
        llm_provider="openai-compatible",
        llm_model="model",
        openai_compatible_base_url="https://llm.example.test/v1",
        llm_api_key_source=IntegrationConfiguration.SecretSource.ENCRYPTED,
        llm_api_key_encrypted=encrypt_secret("health-llm-secret", purpose=LLM_KEY_PURPOSE),
        gmail_provider="api",
        gmail_oauth_client_id="client-id.apps.googleusercontent.com",
        gmail_oauth_client_secret_source=IntegrationConfiguration.SecretSource.ENCRYPTED,
        gmail_oauth_client_secret_encrypted=encrypt_secret(
            "health-google-secret", purpose=GMAIL_CLIENT_SECRET_PURPOSE
        ),
    )
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"] == {
        "storage": "ok",
        "gmail": "not_connected",
        "extractor": "configured",
        "llm": "configured",
    }
    assert b"health-" not in response.content
    assert configuration.outscraper_api_key_encrypted.encode() not in response.content


@pytest.mark.django_db
def test_verify_restore_checks_migrations_and_safe_live_state(tmp_path: Path) -> None:
    with override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        SEND_MODE="dry-run",
        SEND_KILL_SWITCH=True,
    ):
        call_command("verify_restore", verbosity=0)
    with override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        SEND_MODE="live",
        SEND_KILL_SWITCH=False,
    ):
        with pytest.raises(CommandError, match="kill switch"):
            call_command("verify_restore", "--require-kill-switch", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_detects_undecryptable_refresh_token(owner: User, tmp_path: Path) -> None:
    GmailConnection.objects.create(
        owner=owner,
        status=GmailConnection.Status.ERROR,
        refresh_token_encrypted="ciphertext-invalid",
    )
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="no descifrable") as error:
            call_command("verify_restore", verbosity=0)
    assert "ciphertext-invalid" not in str(error.value)


@pytest.mark.django_db
def test_verify_restore_detects_undecryptable_integration_secret(
    owner: User, tmp_path: Path
) -> None:
    IntegrationConfiguration.objects.create(
        owner=owner,
        outscraper_api_key_source=IntegrationConfiguration.SecretSource.ENCRYPTED,
        outscraper_api_key_encrypted="v1:ciphertext-invalid",
    )
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="credencial Outscraper") as error:
            call_command("verify_restore", verbosity=0)
    assert "ciphertext-invalid" not in str(error.value)
