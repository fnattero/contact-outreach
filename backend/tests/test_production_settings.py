"""Production settings must fail closed: each unsafe value is rejected for the stated reason."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SECRET = "production-test-secret-key-with-more-than-fifty-random-characters-123"
VALID_ENVIRONMENT = {
    "APP_ENV": "production",
    "DJANGO_SETTINGS_MODULE": "contact_outreach.settings",
    "DJANGO_SECRET_KEY": SECRET,
    "DJANGO_ALLOWED_HOSTS": "outreach.example",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://outreach.example",
    "PUBLIC_BASE_URL": "https://outreach.example",
    "DJANGO_PROXY_HTTPS": "true",
    "DJANGO_RAILWAY_PROXY": "true",
    "DATABASE_URL": "postgresql://app:password@postgres.railway.internal:5432/railway",
    "REDIS_URL": "redis://redis.railway.internal:6379/0",
    "S3_ACCESS_KEY_ID": "test-access-key",
    "S3_SECRET_ACCESS_KEY": "test-secret-key",
    "S3_BUCKET_NAME": "test-private-bucket",
    "FIELD_ENCRYPTION_KEY": "production-field-encryption-key-with-more-than-32-chars",
    "INTERNAL_PROXY_TOKEN": "production-internal-proxy-token-with-more-than-32-chars",
}
# Variables that would otherwise leak from the developer's or CI's own environment.
SCRUBBED = (
    "DJANGO_DEBUG",
    "DJANGO_SECURE_COOKIES",
    "DJANGO_SSL_REDIRECT",
    "DJANGO_HSTS_SECONDS",
    "DJANGO_TRUSTED_PROXY_IPS",
    "DATABASE_ENGINE",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_AGE",
    "PRIVATE_STORAGE_BACKEND",
    "RAILWAY_PUBLIC_DOMAIN",
)


def _import_settings(overrides: dict[str, str | None], probe: str = "") -> tuple[int, str]:
    environment = {key: value for key, value in os.environ.items() if key not in SCRUBBED}
    environment.update(VALID_ENVIRONMENT)
    for key, value in overrides.items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    source_path = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", f"from contact_outreach import settings\n{probe}"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return result.returncode, result.stderr


def test_the_reference_production_environment_is_accepted_and_hardened() -> None:
    code, error = _import_settings(
        {},
        probe=(
            "assert settings.SESSION_COOKIE_NAME.startswith('__Host-')\n"
            "assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE\n"
            "assert settings.SESSION_COOKIE_HTTPONLY\n"
            "assert settings.SESSION_COOKIE_AGE <= 12 * 60 * 60\n"
            "assert settings.SECURE_SSL_REDIRECT\n"
            "assert settings.SECURE_HSTS_SECONDS >= 31536000\n"
            "assert settings.DEBUG is False\n"
            "assert settings.ALLOWED_HOSTS == ['outreach.example', 'healthcheck.railway.app']\n"
        ),
    )
    assert code == 0, error


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"DJANGO_DEBUG": "true"}, "DJANGO_DEBUG must be false"),
        ({"DJANGO_SECRET_KEY": "short"}, "DJANGO_SECRET_KEY must be a long"),
        ({"DJANGO_SECRET_KEY": None}, "DJANGO_SECRET_KEY must be a long"),
        ({"FIELD_ENCRYPTION_KEY": "too-short"}, "FIELD_ENCRYPTION_KEY must be a long"),
        ({"FIELD_ENCRYPTION_KEY": None}, "FIELD_ENCRYPTION_KEY must be a long"),
        ({"INTERNAL_PROXY_TOKEN": "too-short"}, "INTERNAL_PROXY_TOKEN must be a long"),
        ({"INTERNAL_PROXY_TOKEN": None}, "INTERNAL_PROXY_TOKEN must be a long"),
        ({"DJANGO_SECURE_COOKIES": "false"}, "Production requires secure cookies"),
        ({"DJANGO_SSL_REDIRECT": "false"}, "Production requires secure cookies"),
        ({"DJANGO_HSTS_SECONDS": "0"}, "Production requires secure cookies"),
        ({"DJANGO_PROXY_HTTPS": None}, "DJANGO_PROXY_HTTPS=true is required"),
        ({"DJANGO_RAILWAY_PROXY": None}, "DJANGO_TRUSTED_PROXY_IPS"),
        ({"DJANGO_ALLOWED_HOSTS": "*"}, "exact production hosts"),
        ({"DJANGO_ALLOWED_HOSTS": ".example"}, "exact production hosts"),
        ({"DJANGO_CSRF_TRUSTED_ORIGINS": "http://outreach.example"}, "exact HTTPS"),
        ({"DJANGO_CSRF_TRUSTED_ORIGINS": "https://*.example"}, "exact HTTPS"),
        ({"PUBLIC_BASE_URL": "http://outreach.example"}, "exact HTTPS origin"),
        ({"PUBLIC_BASE_URL": "https://other.example"}, "must be included in DJANGO_ALLOWED_HOSTS"),
        ({"DATABASE_ENGINE": "sqlite"}, "SQLite is not supported in production"),
        ({"DATABASE_URL": None}, "DATABASE_URL or"),
        ({"REDIS_URL": None}, "REDIS_URL is required"),
        ({"S3_BUCKET_NAME": None}, "required for S3 storage"),
        ({"APP_ENV": "prod"}, "APP_ENV must be"),
        ({"APP_ENV": "Production"}, "APP_ENV must be"),
        ({"SESSION_COOKIE_NAME": "contact_outreach_session"}, "__Host-"),
        ({"SESSION_COOKIE_AGE": "604800"}, "SESSION_COOKIE_AGE"),
    ],
    ids=lambda value: str(value)[:60] if not isinstance(value, dict) else ",".join(value),
)
def test_production_rejects_unsafe_configuration(
    overrides: dict[str, str | None], message: str
) -> None:
    code, error = _import_settings(overrides)

    assert code != 0, "production accepted an unsafe configuration"
    assert "ImproperlyConfigured" in error
    assert message in error
