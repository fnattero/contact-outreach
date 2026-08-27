from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.catalogs.storage import private_catalog_storage
from apps.core.models import TimestampedUUIDModel


def catalog_upload_path(instance: Catalog, filename: str) -> str:
    del filename
    return f"catalogs/{instance.id}/{uuid.uuid4().hex}.pdf"


class Catalog(TimestampedUUIDModel):
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="catalogs",
    )
    name = models.CharField(max_length=200)
    version = models.PositiveIntegerField()
    file = models.FileField(
        storage=private_catalog_storage, upload_to=catalog_upload_path, max_length=300
    )
    original_filename = models.CharField(max_length=255)
    detected_mime = models.CharField(max_length=100)
    byte_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="catalogs",
    )
    active = models.BooleanField(default=True)
    missing = models.BooleanField(default=False)

    class Meta:
        ordering = ("name", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "name", "version"),
                name="catalog_workspace_name_version_unique",
            ),
            models.UniqueConstraint(
                fields=("workspace", "sha256"),
                name="catalog_workspace_sha256_unique",
            ),
            models.CheckConstraint(condition=Q(byte_size__gt=0), name="catalog_size_positive"),
        ]

    @property
    def storage_key(self) -> str:
        return str(self.file.name)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.workspace_id and self.uploaded_by_id:
            self.workspace_id = self.uploaded_by.membership.workspace_id
        if not self._state.adding:
            previous = Catalog.objects.get(pk=self.pk)
            immutable_values = (
                (previous.name, self.name),
                (previous.version, self.version),
                (previous.file.name, self.file.name),
                (previous.original_filename, self.original_filename),
                (previous.detected_mime, self.detected_mime),
                (previous.byte_size, self.byte_size),
                (previous.sha256, self.sha256),
                (previous.workspace_id, self.workspace_id),
                (previous.uploaded_by_id, self.uploaded_by_id),
            )
            if any(before != after for before, after in immutable_values):
                raise ValidationError("Una versión de catálogo es inmutable; cargá una nueva.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"
