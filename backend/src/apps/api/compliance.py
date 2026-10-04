from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.api.errors import raise_domain_error
from apps.api.pagination import PageQuerySerializer, page_slice
from apps.api.permissions import ManageContactsPermission, authenticated_user
from apps.api.schema import SchemaAPIView
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email


class SuppressionInputSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(max_length=320)
    reason = serializers.ChoiceField(choices=SuppressionEntry.Reason.choices)
    evidence = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)


def _suppression_data(entry: SuppressionEntry) -> dict[str, object]:
    return {
        "id": str(entry.pk),
        "email": entry.normalized_email,
        "reason": entry.reason,
        "reason_label": entry.get_reason_display(),
        "source": entry.source,
        "evidence": entry.evidence,
        "created_at": entry.created_at,
    }


class SuppressionListView(SchemaAPIView):
    """The global suppression list: every listed address is blocked for all outreach."""

    permission_classes = (IsAuthenticated, ManageContactsPermission)

    def get(self, request: Request) -> Response:
        query = PageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        entries = SuppressionEntry.objects.order_by("-created_at")
        if values.get("q"):
            entries = entries.filter(normalized_email__icontains=values["q"].strip().casefold())
        rows, meta = page_slice(entries, page=values["page"], page_size=values["page_size"])
        return Response({"data": [_suppression_data(entry) for entry in rows], "meta": meta})

    def post(self, request: Request) -> Response:
        serializer = SuppressionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        started = timezone.now()
        try:
            entry = suppress_email(
                email=serializer.validated_data["email"],
                reason=serializer.validated_data["reason"],
                evidence=serializer.validated_data["evidence"],
                actor=authenticated_user(request),
            )
        except (ValidationError, PermissionDenied) as exc:
            raise_domain_error(exc)
        # suppress_email merges into an existing entry instead of failing, so report which happened.
        created = entry.created_at >= started
        return Response(
            {"data": _suppression_data(entry)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
