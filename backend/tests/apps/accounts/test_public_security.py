from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.core.security import (
    ApplicationSecurityHeadersMiddleware,
    TrustedProxySecurityMiddleware,
)


def _security_probe(request: HttpRequest) -> HttpResponse:
    response = HttpResponse("secure" if request.is_secure() else "plain")
    response["Forwarded-Present"] = str("HTTP_X_FORWARDED_FOR" in request.META)
    return response


@override_settings(
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    TRUSTED_PROXY_IPS=["10.20.0.0/16"],
)
def test_untrusted_forwarded_headers_cannot_spoof_https_or_client_ip() -> None:
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="198.51.100.8",
        HTTP_X_FORWARDED_PROTO="https",
        HTTP_X_FORWARDED_FOR="203.0.113.7",
        HTTP_X_FORWARDED_HOST="evil.example",
    )

    response = TrustedProxySecurityMiddleware(_security_probe)(request)

    assert response.content == b"plain"
    assert response["Forwarded-Present"] == "False"
    assert "HTTP_X_FORWARDED_HOST" not in request.META


@override_settings(
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    TRUSTED_PROXY_IPS=["10.20.0.0/16"],
)
def test_forwarded_https_is_honored_from_an_explicit_trusted_proxy_network() -> None:
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="10.20.4.9",
        HTTP_X_FORWARDED_PROTO="https",
        HTTP_X_FORWARDED_FOR="203.0.113.7",
    )

    response = TrustedProxySecurityMiddleware(_security_probe)(request)

    assert response.content == b"secure"
    assert response["Forwarded-Present"] == "True"


@override_settings(
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    TRUST_RAILWAY_PROXY_HEADERS=True,
    TRUSTED_PROXY_IPS=[],
)
def test_railway_proxy_honors_only_its_marked_https_header() -> None:
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="198.51.100.8",
        HTTP_X_RAILWAY_REQUEST_ID="request-1",
        HTTP_X_FORWARDED_PROTO="https",
        HTTP_X_FORWARDED_FOR="203.0.113.7",
        HTTP_X_FORWARDED_HOST="evil.example",
    )

    response = TrustedProxySecurityMiddleware(_security_probe)(request)

    assert response.content == b"secure"
    assert response["Forwarded-Present"] == "False"
    assert "HTTP_X_FORWARDED_HOST" not in request.META


def test_browser_security_headers_are_applied_to_public_pages(client: Client) -> None:
    response = client.get(reverse("login"))

    assert response.status_code == 200
    csp = response["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert "camera=()" in response["Permissions-Policy"]


def test_templates_do_not_contain_inline_scripts() -> None:
    template_root = Path(__file__).resolve().parents[3] / "templates"
    inline_script = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)

    offenders = [
        str(path.relative_to(template_root))
        for path in template_root.rglob("*.html")
        if inline_script.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_production_settings_require_exact_https_origins_and_enable_hardening() -> None:
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
            "DATABASE_URL": "postgresql://app:password@postgres.railway.internal:5432/railway",
            "REDIS_URL": "redis://redis.railway.internal:6379/0",
            "S3_ACCESS_KEY_ID": "test-access-key",
            "S3_SECRET_ACCESS_KEY": "test-secret-key",
            "S3_BUCKET_NAME": "test-private-bucket",
            "FIELD_ENCRYPTION_KEY": "production-field-encryption-key-with-more-than-32-chars",
        }
    )
    source_path = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    script = """
from contact_outreach import settings
assert settings.DEBUG is False
assert settings.ALLOWED_HOSTS == ['outreach.example']
assert settings.CSRF_TRUSTED_ORIGINS == ['https://outreach.example']
assert settings.SESSION_COOKIE_SECURE is True
assert settings.CSRF_COOKIE_SECURE is True
assert settings.SECURE_SSL_REDIRECT is True
assert settings.SECURE_HSTS_SECONDS == 300
assert settings.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https')
"""

    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_production_settings_reject_wildcard_or_non_https_public_origins() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DJANGO_SETTINGS_MODULE": "contact_outreach.settings",
            "DJANGO_SECRET_KEY": (
                "production-test-secret-key-with-more-than-fifty-random-characters-456"
            ),
            "DJANGO_ALLOWED_HOSTS": "*",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "http://outreach.example",
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
    raise AssertionError('Production accepted a wildcard host or insecure origin')
"""

    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_production_settings_reject_subdomain_wildcards_and_origin_paths() -> None:
    base_environment = os.environ.copy()
    base_environment.update(
        {
            "APP_ENV": "production",
            "DJANGO_SETTINGS_MODULE": "contact_outreach.settings",
            "DJANGO_SECRET_KEY": (
                "production-test-secret-key-with-more-than-fifty-random-characters-789"
            ),
        }
    )
    source_path = str(Path.cwd() / "src")
    base_environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, base_environment.get("PYTHONPATH", "")) if part
    )
    script = """
from django.core.exceptions import ImproperlyConfigured
try:
    from contact_outreach import settings  # noqa: F401
except ImproperlyConfigured:
    pass
else:
    raise AssertionError('Production accepted a non-exact host or origin')
"""
    invalid_pairs = (
        (".outreach.example", "https://outreach.example"),
        ("outreach.example", "https://outreach.example/admin/"),
        ("outreach.example", "https://user@outreach.example"),
    )
    for host, origin in invalid_pairs:
        environment = base_environment | {
            "DJANGO_ALLOWED_HOSTS": host,
            "DJANGO_CSRF_TRUSTED_ORIGINS": origin,
        }
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )


def test_security_header_middleware_does_not_weaken_a_stricter_upstream_policy() -> None:
    def upstream(_request: HttpRequest) -> HttpResponse:
        response = HttpResponse("ok")
        response["Content-Security-Policy"] = "default-src 'none'"
        return response

    request = RequestFactory().get("/")
    response = ApplicationSecurityHeadersMiddleware(upstream)(request)

    assert response["Content-Security-Policy"] == "default-src 'none'"
