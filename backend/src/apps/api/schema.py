from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.views import APIView


class ApiSchemaSerializer(serializers.Serializer[dict[str, Any]]):
    """Conservative fallback for hand-written APIViews in the public schema.

    Runtime validation is performed by each endpoint's explicit serializer. This
    fallback prevents undocumented free-form responses when a view intentionally
    returns a small, assembled JSON envelope and gives generated clients a stable
    object shape until a feature-specific response serializer is added.
    """

    data = serializers.JSONField(required=False)
    meta = serializers.JSONField(required=False)
    detail = serializers.CharField(required=False)


class SchemaAPIView(APIView):
    """APIView with a safe schema fallback; it does not alter runtime behavior."""

    serializer_class = ApiSchemaSerializer
