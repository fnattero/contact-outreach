from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from contact_outreach.settings import env_bool, env_list


def test_safe_runtime_defaults_use_fake_providers() -> None:
    assert settings.SEND_MODE == "dry-run"
    assert settings.SEND_KILL_SWITCH is True
    assert settings.EXTRACTOR_PROVIDER == "fake"
    assert settings.WEBSITE_FETCHER == "fake"
    assert settings.LLM_PROVIDER == "fake"
    assert settings.GMAIL_PROVIDER == "fake"


def test_test_settings_override_live_parent_environment() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "contact_outreach.settings_test",
            "SEND_MODE": "live",
            "SEND_KILL_SWITCH": "false",
            "EXTRACTOR_PROVIDER": "outscraper",
            "WEBSITE_FETCHER": "http",
            "LLM_PROVIDER": "openai-compatible",
            "GMAIL_PROVIDER": "api",
        }
    )
    source_path = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    script = """
from django.conf import settings

assert settings.SEND_MODE == "dry-run"
assert settings.SEND_KILL_SWITCH is True
assert settings.EXTRACTOR_PROVIDER == "fake"
assert settings.WEBSITE_FETCHER == "fake"
assert settings.LLM_PROVIDER == "fake"
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
