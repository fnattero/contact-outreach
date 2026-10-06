from __future__ import annotations

from typing import NoReturn

from django.core.exceptions import PermissionDenied, ValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied


def validation_error(exc: ValidationError) -> serializers.ValidationError:
    """Translate a domain ``ValidationError`` into a 400 the client can map onto fields.

    Field-keyed errors keep their keys so they surface as ``field_errors``. A plain message list
    becomes ``detail``: ``api_exception_handler`` only passes a string ``detail`` through, so
    handing DRF the bare list would collapse it into the generic "check your data" message.
    """
    if hasattr(exc, "message_dict"):
        return serializers.ValidationError(exc.message_dict)
    return serializers.ValidationError({"detail": " ".join(exc.messages)})


def permission_error(exc: PermissionDenied) -> ApiPermissionDenied:
    del exc
    return ApiPermissionDenied("El recurso no está disponible.")


def raise_domain_error(exc: ValidationError | PermissionDenied) -> NoReturn:
    """Re-raise a service exception as its API equivalent."""
    if isinstance(exc, ValidationError):
        raise validation_error(exc) from exc
    raise permission_error(exc) from exc
