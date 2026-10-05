from __future__ import annotations

import ipaddress
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.core.security import CLIENT_IP_META_KEY, internal_proxy_authenticated


class InternalProxyMiddleware:
    """Require the private frontend proxy for production API traffic."""

    health_paths = frozenset({"/api/v1/health/live/", "/api/v1/health/ready/"})

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Neither value may reach the application unless the authenticated proxy supplied it.
        reported_ip = request.META.pop("HTTP_X_INTERNAL_CLIENT_IP", "")
        request.META.pop(CLIENT_IP_META_KEY, None)
        if (
            getattr(settings, "APP_ENV", "development") == "production"
            and request.path.startswith("/api/v1/")
            and request.path not in self.health_paths
        ):
            authenticated = internal_proxy_authenticated(request)
            request.META.pop("HTTP_X_INTERNAL_PROXY_TOKEN", None)
            if authenticated:
                # The browser's address, as seen by the proxy. Without it every user would share
                # the proxy's address and therefore one login lockout and one rate limit.
                try:
                    request.META[CLIENT_IP_META_KEY] = ipaddress.ip_address(
                        str(reported_ip).strip()
                    ).compressed
                except ValueError:
                    pass
            if not authenticated:
                return JsonResponse(
                    {
                        "type": "about:blank",
                        "title": "Acceso no permitido",
                        "status": 403,
                        "code": "private_api_only",
                        "detail": "La API solo está disponible a través de la aplicación.",
                        "correlation_id": getattr(request, "correlation_id", ""),
                    },
                    status=403,
                    content_type="application/problem+json",
                )
        else:
            request.META.pop("HTTP_X_INTERNAL_PROXY_TOKEN", None)
        return self.get_response(request)
