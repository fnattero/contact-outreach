from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.request import Request


def json_object(request: Request) -> dict[str, Any]:
    """Return the request body when it is a JSON object, otherwise answer 400.

    ``request.data`` is a dict, a list, or a scalar depending on what the client sent. Calling
    ``.get`` on a list raises ``AttributeError`` and surfaces as a 500, so views that read keys
    directly must go through this guard.
    """
    data = request.data
    if not isinstance(data, dict):
        raise serializers.ValidationError("El cuerpo de la solicitud debe ser un objeto JSON.")
    return data
