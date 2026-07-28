from __future__ import annotations

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


class TrustedProxySecurityMiddleware:
    """Honor forwarded HTTPS only when the immediate peer is explicitly trusted."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not _trusted_proxy(request.META.get("REMOTE_ADDR", "")):
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
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
