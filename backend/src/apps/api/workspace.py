from __future__ import annotations

from typing import Any, cast

from django.core.exceptions import ValidationError
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import ManageConfigurationPermission, authenticated_user
from apps.configuration.models import BusinessProfile
from apps.configuration.services import profile_snapshot, save_business_profile


class BusinessProfileSerializer(serializers.Serializer[dict[str, object]]):
    company_name = serializers.CharField(max_length=200, required=False)
    salesperson_name = serializers.CharField(max_length=200, required=False)
    phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    whatsapp = serializers.CharField(max_length=50, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    products = serializers.CharField(required=False, allow_blank=True)
    differentiators = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(max_length=300, required=False)
    website = serializers.URLField(required=False, allow_blank=True)
    signature = serializers.CharField(required=False)
    additional_instructions = serializers.CharField(required=False, allow_blank=True)
    relevance_threshold = serializers.IntegerField(min_value=0, max_value=100, required=False)
    profile_version = serializers.IntegerField(read_only=True)


class BusinessProfileView(APIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        profile = BusinessProfile.objects.filter(
            workspace=authenticated_user(request).membership.workspace
        ).first()
        return Response(
            {"data": BusinessProfileSerializer(profile_snapshot(profile)).data if profile else None}
        )

    def patch(self, request: Request) -> Response:
        serializer = BusinessProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            profile = save_business_profile(
                owner=authenticated_user(request),
                values=cast(dict[str, Any], serializer.validated_data),
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict) from exc
            raise serializers.ValidationError(str(exc)) from exc
        return Response({"data": BusinessProfileSerializer(profile_snapshot(profile)).data})
