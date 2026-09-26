from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from rest_framework.exceptions import APIException
from rest_framework.response import Response


class VersionedResource(Protocol):
    pk: object
    updated_at: object


class PreconditionRequired(APIException):
    status_code = 428
    default_code = "precondition_required"
    default_detail = "La solicitud necesita la versión actual del recurso."


class PreconditionFailed(APIException):
    status_code = 412
    default_code = "precondition_failed"
    default_detail = "El recurso cambió. Recargá los datos e intentá nuevamente."


def etag_for(resource: VersionedResource) -> str:
    value = f"{resource.pk}:{resource.updated_at.isoformat()}"
    return f'"{sha256(value.encode("utf-8")).hexdigest()}"'


def add_etag(response: Response, resource: VersionedResource) -> Response:
    response["ETag"] = etag_for(resource)
    return response


def require_if_match(request: object, resource: VersionedResource) -> None:
    headers = getattr(request, "headers", {})
    supplied = headers.get("If-Match")
    if not supplied:
        raise PreconditionRequired()
    if supplied != etag_for(resource):
        raise PreconditionFailed()
