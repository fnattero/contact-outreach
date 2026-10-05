from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from django.test import Client, override_settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "container-healthcheck.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("container_healthcheck", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_probe_presents_the_public_host_so_production_host_validation_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.com")
    request = _load_script().readiness_request()

    assert request.full_url == "http://127.0.0.1:8000/api/v1/health/ready/"
    assert request.get_header("Host") == "app.example.com"


def test_the_probe_keeps_the_default_host_when_no_public_url_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert _load_script().readiness_request().get_header("Host") is None


@pytest.mark.django_db
def test_plain_http_health_probes_are_not_redirected_to_https() -> None:
    # Container and Railway health checks reach the private service over plain HTTP.
    with override_settings(SECURE_SSL_REDIRECT=True):
        client = Client()
        live = client.get("/api/v1/health/live/")
        ready = client.get("/api/v1/health/ready/")
        other = client.get("/api/v1/auth/csrf/")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert other.status_code == 301
