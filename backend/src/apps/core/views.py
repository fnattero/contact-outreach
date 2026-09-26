from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def _error(
    request: HttpRequest,
    *,
    status: int,
    title: str,
    message: str,
) -> HttpResponse:
    return render(
        request,
        f"errors/{status}.html",
        {
            "error_title": title,
            "error_message": message,
            "correlation_id": getattr(request, "correlation_id", ""),
        },
        status=status,
    )


def bad_request(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    del exception
    return _error(
        request,
        status=400,
        title="Solicitud inválida",
        message="Revisá los datos enviados y volvé a intentar.",
    )


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    del exception
    return _error(
        request,
        status=403,
        title="Acción no permitida",
        message="La sesión no tiene permiso para realizar esta acción o la confirmación expiró.",
    )


def page_not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    del exception
    return _error(
        request,
        status=404,
        title="Página no encontrada",
        message="El recurso no existe o ya no está disponible.",
    )


def server_error(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
    del args, kwargs
    return _error(
        request,
        status=500,
        title="No pudimos completar la operación",
        message=(
            "No se repitió ningún efecto externo. Consultá Jobs y Auditoría antes de reintentar."
        ),
    )


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    del reason
    if request.path.startswith("/api/"):
        response = JsonResponse(
            {
                "type": "about:blank",
                "title": "Confirmación de seguridad inválida",
                "status": 403,
                "code": "csrf_failed",
                "detail": "Actualizá la página y volvé a intentar.",
                "correlation_id": getattr(request, "correlation_id", ""),
            },
            status=403,
            content_type="application/problem+json",
        )
        response["Cache-Control"] = "private, no-store"
        return response
    return permission_denied(request)
