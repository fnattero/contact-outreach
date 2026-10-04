from __future__ import annotations

from typing import Any

from django.db.models import Model, QuerySet
from rest_framework import serializers


class PageQuerySerializer(serializers.Serializer[dict[str, Any]]):
    """Query-string contract shared by list endpoints: ``q``, ``page`` and ``page_size``."""

    q = serializers.CharField(required=False, allow_blank=True, max_length=150)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)


def page_slice[ModelT: Model](
    queryset: QuerySet[ModelT], *, page: int, page_size: int
) -> tuple[list[ModelT], dict[str, int]]:
    """Return one page of rows plus the ``meta`` block every paged envelope carries."""
    total = queryset.count()
    start = (page - 1) * page_size
    return list(queryset[start : start + page_size]), {
        "page": page,
        "page_size": page_size,
        "total": total,
    }
