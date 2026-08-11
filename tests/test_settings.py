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
from contact_outreach.settings import (
    _database_from_environment,
    _database_from_url,
    _validate_redis_url,
    env_bool,
    env_list,
)


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
    assert settings.SESSION_COOKIE_AGE == 60 * 60 * 24 * 7
    assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE is False


def test_static_files_are_configured_for_gunicorn() -> None:
    assert production_settings.MIDDLEWARE[0] == (
        "apps.core.security.TrustedProxySecurityMiddleware"
    )


def test_production_database_url_is_parsed_without_exposing_credentials() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DJANGO_SETTINGS_MODULE": "contact_outreach.settings",
            "DJANGO_SECRET_KEY": (
                "production-test-secret-key-with-more-than-fifty-random-characters-123"
            ),
            "DJANGO_ALLOWED_HOSTS": "outreach.example",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://outreach.example",
            "PUBLIC_BASE_URL": "https://outreach.example",
            "DJANGO_PROXY_HTTPS": "true",
            "DJANGO_TRUSTED_PROXY_IPS": "10.20.0.0/16",
            "DATABASE_URL": "postgresql://app:p%40ss@postgres.railway.internal:5432/railway?sslmode=require",
            "REDIS_URL": "rediss://:redis-secret@redis.railway.internal:6380/0",
            "FIELD_ENCRYPTION_KEY": "production-field-encryption-key-with-more-than-32-chars",
        }
    )
    source_path = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    script = """
from contact_outreach import settings
database = settings.DATABASES['default']
assert database['HOST'] == 'postgres.railway.internal'
assert database['PASSWORD'] == 'p@ss'
assert database['OPTIONS'] == {'sslmode': 'require'}
assert settings.REDIS_URL.startswith('rediss://')
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_railway_proxy_mode_honors_only_marked_https_requests() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DJANGO_SETTINGS_MODULE": "contact_outreach.settings",
            "DJANGO_SECRET_KEY": (
                "production-test-secret-key-with-more-than-fifty-random-characters-123"
            ),
            "DJANGO_ALLOWED_HOSTS": "outreach.example",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://outreach.example",
            "PUBLIC_BASE_URL": "https://outreach.example",
            "DJANGO_PROXY_HTTPS": "true",
            "DJANGO_RAILWAY_PROXY": "true",
            "DATABASE_URL": "postgresql://app:password@postgres.railway.internal:5432/railway",
            "REDIS_URL": "redis://redis.railway.internal:6379/0",
            "FIELD_ENCRYPTION_KEY": "production-field-encryption-key-with-more-than-32-chars",
        }
    )
    source_path = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    script = """
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory
from apps.core.security import TrustedProxySecurityMiddleware

def probe(request):
    return HttpResponse('secure' if request.is_secure() else 'plain')

request = RequestFactory().get('/', REMOTE_ADDR='198.51.100.8',
    HTTP_X_RAILWAY_REQUEST_ID='request-1', HTTP_X_FORWARDED_PROTO='https')
assert TrustedProxySecurityMiddleware(probe)(request).content == b'secure'

request = RequestFactory().get('/', REMOTE_ADDR='198.51.100.8',
    HTTP_X_FORWARDED_PROTO='https')
assert TrustedProxySecurityMiddleware(probe)(request).content == b'plain'
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_production_rejects_insecure_runtime_toggles() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DJANGO_SETTINGS_MODULE": "contact_outreach.settings",
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": (
                "production-test-secret-key-with-more-than-fifty-random-characters-123"
            ),
            "DJANGO_ALLOWED_HOSTS": "outreach.example",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://outreach.example",
            "PUBLIC_BASE_URL": "https://outreach.example",
            "DJANGO_PROXY_HTTPS": "true",
            "DJANGO_RAILWAY_PROXY": "true",
            "DATABASE_URL": "postgresql://app:password@postgres.railway.internal:5432/railway",
            "REDIS_URL": "redis://redis.railway.internal:6379/0",
            "FIELD_ENCRYPTION_KEY": "production-field-encryption-key-with-more-than-32-chars",
        }
    )
    source_path = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    script = """
from django.core.exceptions import ImproperlyConfigured
try:
    from contact_outreach import settings  # noqa: F401
except ImproperlyConfigured:
    pass
else:
    raise AssertionError('Production accepted an insecure runtime toggle')
"""
    invalid_overrides = (
        {"DJANGO_DEBUG": "true"},
        {"DATABASE_ENGINE": "sqlite"},
        {"DJANGO_SECURE_COOKIES": "false"},
        {"DJANGO_SSL_REDIRECT": "false"},
    )
    for overrides in invalid_overrides:
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment | overrides,
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


def test_railway_database_and_redis_urls_are_parsed_and_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database_from_url(
        "postgresql://app:p%40ss@postgres.railway.internal:5432/railway?sslmode=require"
    )

    assert database["HOST"] == "postgres.railway.internal"
    assert database["NAME"] == "railway"
    assert database["PASSWORD"] == "p@ss"
    assert database["OPTIONS"] == {"sslmode": "require"}
    assert _validate_redis_url("rediss://redis.railway.internal:6380/0").startswith("rediss://")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PGHOST", "postgres.railway.internal")
    monkeypatch.setenv("PGPORT", "5432")
    monkeypatch.setenv("PGDATABASE", "railway")
    monkeypatch.setenv("PGUSER", "app")
    monkeypatch.setenv("PGPASSWORD", "password")
    database = _database_from_environment()

    assert database["HOST"] == "postgres.railway.internal"
    assert database["USER"] == "app"


def test_invalid_runtime_database_and_redis_urls_are_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="postgres"):
        _database_from_url("mysql://app:password@database.example/railway")
    with pytest.raises(ImproperlyConfigured, match="REDIS_URL"):
        _validate_redis_url("https://redis.example/0")


def test_invalid_boolean_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_ENABLED", "sometimes")
    with pytest.raises(ImproperlyConfigured, match="must be a boolean"):
        env_bool("FEATURE_ENABLED", False)
