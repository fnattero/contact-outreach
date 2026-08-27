from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from django.db import IntegrityError
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import ManageConfigurationPermission, authenticated_user
from apps.configuration.models import SearchCategory, SearchZone


class SearchCategoryInputSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=160)
    sort_order = serializers.IntegerField(min_value=0, required=False, default=0)


class SearchCategorySerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    sort_order = serializers.IntegerField()
    rules_revision = serializers.IntegerField()
    rules = serializers.ListField(child=serializers.DictField())


class SearchZoneSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    official_code = serializers.CharField()
    level = serializers.CharField()
    province_code = serializers.CharField()
    province_name = serializers.CharField()
    parent_id = serializers.UUIDField(allow_null=True)
    selectable = serializers.BooleanField()
    location_text = serializers.CharField()
    boundary_revision = serializers.IntegerField()
    boundary_hash = serializers.CharField()


def _category_data(category: SearchCategory) -> dict[str, object]:
    return {
        "id": category.pk,
        "name": category.name,
        "sort_order": category.sort_order,
        "rules_revision": category.rules_revision,
        "rules": [
            {
                "id": rule.pk,
                "taxonomy_code": rule.taxonomy_code,
                "name_terms": rule.name_terms,
                "active": rule.active,
                "sort_order": rule.sort_order,
            }
            for rule in category.rules.filter(active=True).order_by("sort_order", "created_at")
        ],
    }


def _zone_data(zone: SearchZone) -> dict[str, object]:
    return {
        "id": zone.pk,
        "name": zone.name,
        "official_code": zone.official_code,
        "level": zone.level,
        "province_code": zone.province_code,
        "province_name": zone.province_name,
        "parent_id": zone.parent_id,
        "selectable": zone.selectable,
        "location_text": zone.location_text,
        "boundary_revision": zone.boundary_revision,
        "boundary_hash": zone.boundary_hash,
    }


class SearchCategoryListView(APIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        workspace_id = authenticated_user(request).membership.workspace_id
        categories = SearchCategory.objects.filter(
            workspace_id=workspace_id,
            active=True,
            archived_at__isnull=True,
        ).prefetch_related("rules")
        return Response(
            {"data": [SearchCategorySerializer(_category_data(item)).data for item in categories]}
        )

    def post(self, request: Request) -> Response:
        serializer = SearchCategoryInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            category = SearchCategory.objects.create(
                workspace=authenticated_user(request).membership.workspace,
                name=cast(str, serializer.validated_data["name"]),
                sort_order=cast(int, serializer.validated_data["sort_order"]),
            )
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"name": "Ya existe una categoría con ese nombre."}
            ) from exc
        return Response(
            {"data": SearchCategorySerializer(_category_data(category)).data},
            status=status.HTTP_201_CREATED,
        )


class SearchCategoryRulesView(APIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def _category(self, request: Request, category_id: UUID) -> SearchCategory:
        try:
            return SearchCategory.objects.get(
                pk=category_id,
                workspace_id=authenticated_user(request).membership.workspace_id,
                archived_at__isnull=True,
            )
        except SearchCategory.DoesNotExist as exc:
            raise NotFound from exc

    def get(self, request: Request, category_id: UUID) -> Response:
        category = self._category(request, category_id)
        return Response({"data": SearchCategorySerializer(_category_data(category)).data})


class SearchZoneListView(APIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        zones = SearchZone.objects.filter(
            workspace_id=authenticated_user(request).membership.workspace_id,
            active=True,
            archived_at__isnull=True,
        ).order_by("province_name", "sort_order", "name")
        level = request.query_params.get("level")
        if level:
            zones = zones.filter(level=level)
        parent_id = request.query_params.get("parent_id")
        if parent_id:
            try:
                parent_uuid = UUID(parent_id)
            except ValueError as exc:
                raise serializers.ValidationError(
                    {"parent_id": "El identificador de la zona no es válido."}
                ) from exc
            zones = zones.filter(parent_id=parent_uuid)
        return Response({"data": [SearchZoneSerializer(_zone_data(zone)).data for zone in zones]})


class SearchZoneGeometryView(APIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request, zone_id: UUID) -> Response:
        try:
            zone = SearchZone.objects.get(
                pk=zone_id,
                workspace_id=authenticated_user(request).membership.workspace_id,
                active=True,
                archived_at__isnull=True,
            )
        except SearchZone.DoesNotExist as exc:
            raise NotFound from exc
        return Response(
            {
                "data": {
                    "id": str(zone.pk),
                    "boundary_revision": zone.boundary_revision,
                    "boundary_hash": zone.boundary_hash,
                    "geojson": zone.boundary_geojson,
                    "bbox": zone.boundary_bbox,
                }
            }
        )
