from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Mapping
from typing import Any

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def _log_unexpected_error(exc: Exception, request: object) -> None:
    """Record that an unhandled error happened, without its message or arguments.

    Exception messages can carry credentials or personal data, and the JSON log formatter drops
    tracebacks on purpose, so log only the class and the innermost code location.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    location = (
        f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else "unknown"
    )
    logger.error(
        "Error no controlado en la API: %s en %s.",
        type(exc).__name__,
        location,
        extra={
            "event": "api.unhandled_exception",
            "method": getattr(request, "method", ""),
            "path": getattr(request, "path", ""),
            "error_code": "internal_error",
        },
    )


def _safe_detail(data: object) -> str:
    if isinstance(data, Mapping):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        return "Revisá los datos enviados y volvé a intentar."
    if isinstance(data, str):
        return data
    return "Revisá los datos enviados y volvé a intentar."


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    response = drf_exception_handler(exc, context)
    request = context.get("request")
    correlation_id = str(getattr(request, "correlation_id", ""))
    if response is None:
        _log_unexpected_error(exc, request)
        return Response(
            {
                "type": "about:blank",
                "title": "No pudimos completar la operación",
                "status": 500,
                "code": "internal_error",
                "detail": "No fue posible completar la solicitud.",
                "correlation_id": correlation_id,
            },
            status=500,
            content_type="application/problem+json",
        )

    status = response.status_code
    if status == 401:
        title = "Autenticación requerida"
        code = "authentication_required"
    elif status == 403:
        title = "Acción no permitida"
        code = "permission_denied"
    elif status == 404:
        title = "Recurso no encontrado"
        code = "not_found"
    elif status == 405:
        title = "Método no permitido"
        code = "method_not_allowed"
    elif status == 429:
        title = "Demasiadas solicitudes"
        code = "rate_limited"
    elif status == 412:
        title = "El recurso cambió"
        code = "precondition_failed"
    elif status == 428:
        title = "Falta la versión del recurso"
        code = "precondition_required"
    elif status == 400:
        title = "Solicitud inválida"
        code = "validation_error"
    else:
        title = "No pudimos completar la operación"
        code = "api_error"

    raw_data = response.data
    payload: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
        "detail": _safe_detail(raw_data),
        "correlation_id": correlation_id,
    }
    if status == 400 and isinstance(raw_data, Mapping):
        field_errors = {str(key): value for key, value in raw_data.items() if key != "detail"}
        if field_errors:
            payload["field_errors"] = field_errors
    response.data = payload
    response.content_type = "application/problem+json"
    return response
