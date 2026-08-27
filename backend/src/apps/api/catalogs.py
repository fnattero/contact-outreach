from __future__ import annotations

from typing import NoReturn, cast
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied as ApiPermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import (
    DownloadPdfsPermission,
    ManageConfigurationPermission,
    authenticated_user,
)
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog, verify_catalog


class CatalogCreateSerializer(serializers.Serializer[dict[str, object]]):
    name = serializers.CharField(max_length=200)
    file = serializers.FileField(write_only=True)


class CatalogSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    version = serializers.IntegerField()
    original_filename = serializers.CharField()
    detected_mime = serializers.CharField()
    byte_size = serializers.IntegerField()
    sha256 = serializers.CharField()
    active = serializers.BooleanField()
    missing = serializers.BooleanField()
    created_at = serializers.DateTimeField()


def _catalog_data(catalog: Catalog) -> dict[str, object]:
    return {
        "id": catalog.pk,
        "name": catalog.name,
        "version": catalog.version,
        "original_filename": catalog.original_filename,
        "detected_mime": catalog.detected_mime,
        "byte_size": catalog.byte_size,
        "sha256": catalog.sha256,
        "active": catalog.active,
        "missing": catalog.missing,
        "created_at": catalog.created_at,
    }


def _raise_catalog_error(exc: ValidationError | PermissionDenied) -> NoReturn:
    if isinstance(exc, ValidationError):
        raise serializers.ValidationError(str(exc)) from exc
    raise ApiPermissionDenied("El catálogo no está disponible.") from exc


class CatalogListView(APIView):
    permission_classes = (IsAuthenticated, ManageConfigurationPermission)

    def get(self, request: Request) -> Response:
        catalogs = Catalog.objects.filter(
            workspace_id=authenticated_user(request).membership.workspace_id
        ).order_by("name", "-version")
        return Response(
            {"data": [CatalogSerializer(_catalog_data(item)).data for item in catalogs]}
        )

    def post(self, request: Request) -> Response:
        serializer = CatalogCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        if not isinstance(upload, UploadedFile):
            raise serializers.ValidationError({"file": "Subí un archivo PDF."})
        try:
            catalog = create_catalog(
                name=cast(str, serializer.validated_data["name"]),
                upload=upload,
                actor=authenticated_user(request),
            )
        except (ValidationError, PermissionDenied) as exc:
            _raise_catalog_error(exc)
        return Response(
            {"data": CatalogSerializer(_catalog_data(catalog)).data},
            status=status.HTTP_201_CREATED,
        )


class CatalogDownloadView(APIView):
    permission_classes = (IsAuthenticated, DownloadPdfsPermission)

    def get(self, request: Request, catalog_id: UUID) -> FileResponse:
        catalog = Catalog.objects.filter(
            pk=catalog_id,
            workspace_id=authenticated_user(request).membership.workspace_id,
        ).first()
        if catalog is None:
            raise NotFound
        try:
            verify_catalog(catalog)
            file_handle = catalog.file.open("rb")
        except (ValidationError, OSError) as exc:
            raise NotFound from exc
        response = FileResponse(
            file_handle,
            as_attachment=True,
            filename=catalog.original_filename,
            content_type="application/pdf",
        )
        response["Cache-Control"] = "private, no-store"
        response["Pragma"] = "no-cache"
        return response
