"""Which forwarded headers are believed, and the headers every response carries."""

from __future__ import annotations

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from apps.core.security import (
    ApplicationSecurityHeadersMiddleware,
    TrustedProxySecurityMiddleware,
    _trusted_proxy,
    internal_proxy_authenticated,
)

TOKEN = "p" * 40
FORWARDED = {
    "HTTP_X_FORWARDED_PROTO": "https",
    "HTTP_X_FORWARDED_HOST": "evil.example",
    "HTTP_X_FORWARDED_FOR": "6.6.6.6",
}


def _seen_by_the_view(remote: str = "198.51.100.9", **extra: str) -> dict[str, str]:
    captured: dict[str, str] = {}

    def view(request: HttpRequest) -> HttpResponse:
        captured.update({k: v for k, v in request.META.items() if k.startswith("HTTP_X_")})
        return HttpResponse("ok")

    request = RequestFactory().get("/", REMOTE_ADDR=remote, **{**FORWARDED, **extra})
    TrustedProxySecurityMiddleware(view)(request)
    return captured


@override_settings(
    TRUSTED_PROXY_IPS=(), INTERNAL_PROXY_TOKEN=TOKEN, TRUST_RAILWAY_PROXY_HEADERS=False
)
def test_a_direct_client_cannot_spoof_any_forwarded_header() -> None:
    assert _seen_by_the_view() == {}


@override_settings(TRUSTED_PROXY_IPS=("10.0.0.0/8",), INTERNAL_PROXY_TOKEN=TOKEN)
def test_a_listed_proxy_is_believed_but_a_neighbour_outside_the_list_is_not() -> None:
    assert _seen_by_the_view(remote="10.1.2.3")["HTTP_X_FORWARDED_FOR"] == "6.6.6.6"
    assert _seen_by_the_view(remote="11.1.2.3") == {}


@override_settings(TRUSTED_PROXY_IPS=("not-a-network", "10.0.0.0/8"))
def test_a_malformed_allowlist_entry_is_skipped_instead_of_trusting_everyone() -> None:
    assert _trusted_proxy("10.9.9.9") is True
    assert _trusted_proxy("8.8.8.8") is False


@pytest.mark.parametrize("remote", ["", "garbage", "999.1.1.1"])
@override_settings(TRUSTED_PROXY_IPS=("0.0.0.0/0",))
def test_an_unparseable_peer_address_is_never_trusted(remote: str) -> None:
    assert _trusted_proxy(remote) is False


@override_settings(TRUSTED_PROXY_IPS=(), INTERNAL_PROXY_TOKEN=TOKEN)
def test_the_frontend_proxy_token_unlocks_proto_and_host_but_never_the_client_address() -> None:
    seen = _seen_by_the_view(HTTP_X_INTERNAL_PROXY_TOKEN=TOKEN)

    assert seen["HTTP_X_FORWARDED_PROTO"] == "https"
    assert seen["HTTP_X_FORWARDED_HOST"] == "evil.example"
    assert "HTTP_X_FORWARDED_FOR" not in seen


@override_settings(TRUSTED_PROXY_IPS=(), INTERNAL_PROXY_TOKEN=TOKEN)
def test_a_wrong_or_empty_proxy_token_unlocks_nothing() -> None:
    assert _seen_by_the_view(HTTP_X_INTERNAL_PROXY_TOKEN="x" * 40) == {
        "HTTP_X_INTERNAL_PROXY_TOKEN": "x" * 40
    }
    assert "HTTP_X_FORWARDED_PROTO" not in _seen_by_the_view(HTTP_X_INTERNAL_PROXY_TOKEN="")


@pytest.mark.parametrize("configured", ["", None])
def test_an_unset_server_token_never_matches_an_empty_header(configured: str | None) -> None:
    request = RequestFactory().get("/", HTTP_X_INTERNAL_PROXY_TOKEN="")
    with override_settings(INTERNAL_PROXY_TOKEN=configured):
        assert internal_proxy_authenticated(request) is False


@override_settings(
    TRUSTED_PROXY_IPS=(), INTERNAL_PROXY_TOKEN=TOKEN, TRUST_RAILWAY_PROXY_HEADERS=True
)
def test_railway_markers_only_establish_https_and_drop_the_rest() -> None:
    seen = _seen_by_the_view(HTTP_X_RAILWAY_REQUEST_ID="abc")

    assert seen["HTTP_X_FORWARDED_PROTO"] == "https"
    assert "HTTP_X_FORWARDED_HOST" not in seen
    assert "HTTP_X_FORWARDED_FOR" not in seen


@override_settings(
    TRUSTED_PROXY_IPS=(), INTERNAL_PROXY_TOKEN=TOKEN, TRUST_RAILWAY_PROXY_HEADERS=True
)
def test_railway_trust_needs_both_the_request_id_and_https() -> None:
    assert _seen_by_the_view() == {}
    assert "HTTP_X_FORWARDED_PROTO" not in _seen_by_the_view(
        HTTP_X_RAILWAY_REQUEST_ID="abc", HTTP_X_FORWARDED_PROTO="http"
    )


@override_settings(
    TRUSTED_PROXY_IPS=(), INTERNAL_PROXY_TOKEN=TOKEN, TRUST_RAILWAY_PROXY_HEADERS=False
)
def test_railway_markers_are_ignored_unless_explicitly_enabled() -> None:
    assert _seen_by_the_view(HTTP_X_RAILWAY_REQUEST_ID="abc") == {
        "HTTP_X_RAILWAY_REQUEST_ID": "abc"
    }


def _headers_for(path: str, response: HttpResponse | None = None) -> HttpResponse:
    middleware = ApplicationSecurityHeadersMiddleware(lambda _request: response or HttpResponse())
    return middleware(RequestFactory().get(path))


def test_every_response_gets_a_strict_csp_with_no_inline_script_allowance() -> None:
    csp = _headers_for("/anything/")["Content-Security-Policy"]

    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert "default-src 'self'" in csp
    assert "geolocation=()" in _headers_for("/anything/")["Permissions-Policy"]


def test_api_responses_are_never_cacheable() -> None:
    assert _headers_for("/api/v1/contacts/")["Cache-Control"] == "private, no-store"


def test_a_view_that_sets_its_own_policy_keeps_it() -> None:
    own = HttpResponse()
    own["Cache-Control"] = "public, max-age=60"
    own["Content-Security-Policy"] = "default-src 'none'"

    response = _headers_for("/api/v1/health/live/", own)

    assert response["Cache-Control"] == "public, max-age=60"
    assert response["Content-Security-Policy"] == "default-src 'none'"
