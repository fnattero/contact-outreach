from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse


def _trusted_proxy(remote_address: str) -> bool:
    try:
        remote = ipaddress.ip_address(remote_address)
    except ValueError:
        return False
    for value in getattr(settings, "TRUSTED_PROXY_IPS", ()):
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if remote in network:
            return True
    return False


def internal_proxy_authenticated(request: HttpRequest) -> bool:
    expected = getattr(settings, "INTERNAL_PROXY_TOKEN", "")
    received = request.META.get("HTTP_X_INTERNAL_PROXY_TOKEN", "")
    return bool(expected and received and hmac.compare_digest(received, expected))


def _trusted_railway_proxy(request: HttpRequest) -> bool:
    """Trust only Railway's forwarded HTTPS marker when explicitly configured.

    Railway's public proxy always sends X-Forwarded-Proto=https and a
    X-Railway-Request-Id. The opt-in is intentionally separate from the CIDR
    allowlist because Railway edge addresses are not a customer-configurable
    static range.
    """
    return bool(
        getattr(settings, "TRUST_RAILWAY_PROXY_HEADERS", False)
        and request.META.get("HTTP_X_RAILWAY_REQUEST_ID")
        and request.META.get("HTTP_X_FORWARDED_PROTO") == "https"
    )


class TrustedProxySecurityMiddleware:
    """Honor forwarded HTTPS only when the immediate peer is explicitly trusted."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        trusted_by_cidr = _trusted_proxy(request.META.get("REMOTE_ADDR", ""))
        trusted_by_internal = internal_proxy_authenticated(request)
        trusted_by_railway = _trusted_railway_proxy(request)
        if not trusted_by_cidr and not trusted_by_internal and not trusted_by_railway:
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
            request.META.pop("HTTP_X_FORWARDED_HOST", None)
            request.META.pop("HTTP_X_FORWARDED_FOR", None)
        elif (trusted_by_railway or trusted_by_internal) and not trusted_by_cidr:
            # The Railway marker is only used to establish HTTPS. Do not
            # accept client-address forwarding without a CIDR trust boundary,
            # because that value influences auditing and throttles. The private
            # frontend proxy is separately authenticated by a secret token and
            # is allowed to forward the canonical public host.
            if not trusted_by_internal:
                request.META.pop("HTTP_X_FORWARDED_HOST", None)
            request.META.pop("HTTP_X_FORWARDED_FOR", None)
        return self.get_response(request)


class ApplicationSecurityHeadersMiddleware:
    """Set browser protections for every HTML and error response."""

    CSP = "; ".join(
        (
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
        )
    )

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        return response
