from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.configuration.integrations import runtime_integration_configuration
from contact_outreach import settings as production_settings
from contact_outreach.settings import env_bool, env_list


def test_delivery_worker_mounts_private_catalogs_read_only() -> None:
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
    worker_section = compose.split("  worker:\n", maxsplit=1)[1].split("  beat:\n", maxsplit=1)[0]

    assert "- private_catalogs:/app/private:ro" in worker_section


def test_safe_runtime_defaults_use_fake_providers() -> None:
    assert settings.SEND_MODE == "dry-run"
    assert settings.SEND_KILL_SWITCH is True
    assert not hasattr(settings, "EXTRACTOR_PROVIDER")
    assert runtime_integration_configuration().extractor_provider == "fake"
    assert settings.WEBSITE_FETCHER == "fake"
    assert settings.LLM_PROVIDER == "fake"
    assert settings.EMBEDDING_PROVIDER == "fake"
    assert settings.GMAIL_PROVIDER == "fake"
    assert settings.AUTO_REPLY_KILL_SWITCH is True
    assert settings.RELATIONSHIP_KILL_SWITCH is True


def test_static_files_are_configured_for_gunicorn() -> None:
    assert production_settings.MIDDLEWARE[0] == (
        "apps.core.security.TrustedProxySecurityMiddleware"
    )
    assert production_settings.MIDDLEWARE[1] == "django.middleware.security.SecurityMiddleware"
    assert production_settings.MIDDLEWARE[2] == "whitenoise.middleware.WhiteNoiseMiddleware"
    assert production_settings.STORAGES["staticfiles"]["BACKEND"] == (
        "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )


def test_test_settings_override_live_parent_environment() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "contact_outreach.settings_test",
            "SEND_MODE": "live",
            "SEND_KILL_SWITCH": "false",
            "EXTRACTOR_PROVIDER": "overture",
            "WEBSITE_FETCHER": "http",
            "LLM_PROVIDER": "openai-compatible",
            "EMBEDDING_PROVIDER": "openai-compatible",
            "GMAIL_PROVIDER": "api",
        }
    )
    source_path = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    script = """
import django
from django.conf import settings

django.setup()

assert settings.SEND_MODE == "dry-run"
assert settings.SEND_KILL_SWITCH is True
assert not hasattr(settings, "EXTRACTOR_PROVIDER")

from apps.configuration.integrations import runtime_integration_configuration
assert runtime_integration_configuration().extractor_provider == "fake"
assert settings.WEBSITE_FETCHER == "fake"
assert settings.LLM_PROVIDER == "fake"
assert settings.EMBEDDING_PROVIDER == "fake"
assert settings.GMAIL_PROVIDER == "fake"
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_environment_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_ENABLED", "yes")
    monkeypatch.setenv("HOST_LIST", " localhost, 127.0.0.1, ")
    assert env_bool("FEATURE_ENABLED", False) is True
    assert env_bool("MISSING_FEATURE", False) is False
    assert env_list("HOST_LIST") == ["localhost", "127.0.0.1"]


def test_invalid_boolean_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_ENABLED", "sometimes")
    with pytest.raises(ImproperlyConfigured, match="must be a boolean"):
        env_bool("FEATURE_ENABLED", False)
