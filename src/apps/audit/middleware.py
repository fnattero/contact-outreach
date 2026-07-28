from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.audit.observability import correlation_id_var

logger = logging.getLogger("contact_outreach.request")


def _safe_request_path(request: HttpRequest) -> str:
    # Single-use account tokens are URL components and must never reach logs.
    if request.path.startswith("/activar/"):
        return "/activar/[REDACTED]/"
    return request.path


class RequestObservabilityMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        correlation_id = str(uuid.uuid4())
        token = correlation_id_var.set(correlation_id)
        request.correlation_id = correlation_id  # type: ignore[attr-defined]
        started = time.monotonic()
        try:
            response = self.get_response(request)
        except Exception:
            logger.exception(
                "La solicitud terminó con una excepción no controlada.",
                extra={
                    "event": "http.request_failed",
                    "method": request.method,
                    "path": _safe_request_path(request),
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "error_code": "unhandled_exception",
                },
            )
            raise
        else:
            response["X-Correlation-ID"] = correlation_id
            logger.info(
                "Solicitud completada.",
                extra={
                    "event": "http.request_completed",
                    "method": request.method,
                    "path": _safe_request_path(request),
                    "status_code": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
            )
            return response
        finally:
            correlation_id_var.reset(token)
