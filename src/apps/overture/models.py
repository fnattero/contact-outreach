from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models import Q

from apps.core.models import TimestampedUUIDModel


class ImmutableCatalogQuerySet(models.QuerySet[Any]):
    def update(self, **kwargs: Any) -> int:
        del kwargs
        raise ValidationError("Los registros de un catálogo Overture son inmutables.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ValidationError("Los registros de un catálogo Overture no se pueden eliminar.")

    def bulk_update(
        self,
        objs: Iterable[Any],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        del objs, fields, batch_size
        raise ValidationError("Los registros de un catálogo Overture son inmutables.")

    def bulk_create(
        self,
        objs: Iterable[Any],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[Any]:
        objects = list(objs)
        if update_conflicts:
            raise ValidationError("Los registros de un catálogo Overture son inmutables.")
        snapshot_ids = {
            item.snapshot_id for item in objects if getattr(item, "snapshot_id", None) is not None
        }
        if not snapshot_ids:
            return super().bulk_create(
                objects,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )
        with transaction.atomic(using=self.db):
            _lock_importing_snapshots(snapshot_ids, using=self.db)
            _validate_bulk_place_zone_membership(objects, using=self.db)
            return super().bulk_create(
                objects,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )


class ImmutableCatalogModel(TimestampedUUIDModel):
    objects = ImmutableCatalogQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Los registros de un catálogo Overture son inmutables.")
        snapshot_id = getattr(self, "snapshot_id", None)
        if snapshot_id is None:
            super().save(*args, **kwargs)
            return
        database = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=database):
            _lock_importing_snapshots({snapshot_id}, using=database)
            super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        del args, kwargs
        raise ValidationError("Los registros de un catálogo Overture no se pueden eliminar.")


class OvertureRelease(TimestampedUUIDModel):
    """Immutable identity and reviewed metadata for one official Overture release."""

    release_id = models.CharField(max_length=40, unique=True)
    schema_version = models.CharField(max_length=80)
    taxonomy_version = models.CharField(max_length=80)
    importer_version = models.CharField(max_length=80)
    mapping_version = models.CharField(max_length=80)
    source_uri = models.CharField(max_length=500)
    catalog_url = models.CharField(max_length=500)
    manifest_sha256 = models.CharField(max_length=64)
    metadata_sha256 = models.CharField(max_length=64)
    attribution = models.TextField(blank=True)
    source_licenses = models.JSONField(default=list)
    notices = models.JSONField(default=list)
    discovered_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-release_id",)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "release_id",
                "schema_version",
                "taxonomy_version",
                "importer_version",
                "mapping_version",
                "source_uri",
                "catalog_url",
                "manifest_sha256",
                "metadata_sha256",
            )
            if any(
                getattr(previous, field_name) != getattr(self, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError("La identidad del release Overture es inmutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        del args, kwargs
        raise ValidationError("Los releases Overture no se pueden eliminar.")

    def __str__(self) -> str:
        return self.release_id


class OvertureDatasetSnapshotQuerySet(models.QuerySet["OvertureDatasetSnapshot"]):
    def active(self) -> OvertureDatasetSnapshotQuerySet:
        return self.filter(
            status=OvertureDatasetSnapshot.Status.READY,
            is_active=True,
        )

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ValidationError("Los snapshots Overture no se pueden eliminar.")

    def update(self, **kwargs: Any) -> int:
        del kwargs
        # QuerySet.update() bypasses the state-aware validation in save(). All
        # legitimate importer/activation changes use locked model instances.
        raise ValidationError("Los snapshots Overture no admiten actualizaciones masivas.")

    def bulk_update(
        self,
        objs: Iterable[OvertureDatasetSnapshot],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        del objs, fields, batch_size
        raise ValidationError("Los snapshots Overture no admiten actualizaciones masivas.")

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """Retention-only deletion entrypoint; callers must establish eligibility first."""

        return super().delete()


class OvertureDatasetSnapshot(TimestampedUUIDModel):
    class Status(models.TextChoices):
        IMPORTING = "IMPORTING", "Importando"
        READY = "READY", "Listo"
        SUPERSEDED = "SUPERSEDED", "Reemplazado"
        FAILED = "FAILED", "Falló"

    release_id = models.CharField(max_length=40, db_index=True)
    release_record = models.ForeignKey(
        OvertureRelease,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="legacy_snapshots",
    )
    province_code = models.CharField(max_length=20, blank=True, db_index=True)
    province_name = models.CharField(max_length=160, blank=True)
    province_bbox = models.JSONField(default=list, blank=True)
    import_revision = models.PositiveIntegerField(default=1)
    schema_version = models.CharField(max_length=80)
    taxonomy_version = models.CharField(max_length=80)
    importer_version = models.CharField(max_length=80)
    mapping_version = models.CharField(max_length=80)
    boundary_version = models.CharField(max_length=120)
    boundary_manifest_sha256 = models.CharField(max_length=64)
    source_uri = models.CharField(max_length=500)
    manifest_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IMPORTING)
    is_active = models.BooleanField(default=False)
    streamed_count = models.PositiveIntegerField(default=0)
    place_count = models.PositiveIntegerField(default=0)
    zone_count = models.PositiveIntegerField(default=0)
    taxonomy_code_count = models.PositiveIntegerField(default=0)
    source_counts = models.JSONField(default=dict)
    source_licenses = models.JSONField(default=list)
    attribution = models.TextField(blank=True)
    notices = models.JSONField(default=list)
    validation_results = models.JSONField(default=dict)
    imported_at = models.DateTimeField(blank=True, null=True)
    activated_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    objects = OvertureDatasetSnapshotQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "-created_at"), name="overture_snap_status_idx"),
            models.Index(
                fields=("release_id", "mapping_version", "boundary_version"),
                name="overture_snap_versions_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(is_active=False) | Q(status="READY"),
                name="overture_active_is_ready",
            ),
            models.CheckConstraint(
                condition=~Q(status="READY") | Q(is_active=True),
                name="overture_ready_is_active",
            ),
        ]

    @property
    def release(self) -> str:
        """Compatibility alias used by provider adapters."""

        return self.release_id

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "release_id",
                "schema_version",
                "taxonomy_version",
                "importer_version",
                "mapping_version",
                "boundary_version",
                "boundary_manifest_sha256",
                "source_uri",
                "manifest_sha256",
                "release_record_id",
                "province_code",
                "province_name",
                "province_bbox",
                "import_revision",
            )
            if any(
                getattr(previous, field_name) != getattr(self, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError("La identidad y procedencia del snapshot son inmutables.")
            changed_fields = {
                field.name
                for field in self._meta.concrete_fields
                if field.name not in {"updated_at"}
                and getattr(previous, field.name) != getattr(self, field.name)
            }
            if previous.status in {self.Status.FAILED, self.Status.SUPERSEDED}:
                raise ValidationError("Un snapshot Overture terminal es inmutable.")
            if previous.status == self.Status.READY:
                if not (
                    changed_fields <= {"status", "is_active"}
                    and self.status == self.Status.SUPERSEDED
                    and not self.is_active
                ):
                    raise ValidationError("Un snapshot Overture activo sólo puede ser reemplazado.")
            if previous.status == self.Status.IMPORTING and self.status not in {
                self.Status.IMPORTING,
                self.Status.READY,
                self.Status.FAILED,
            }:
                raise ValidationError("La transición del snapshot Overture no es válida.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        del args, kwargs
        raise ValidationError("Los snapshots Overture no se pueden eliminar.")

    def __str__(self) -> str:
        coverage = f" · {self.province_name}" if self.province_name else ""
        return f"Overture {self.release_id}{coverage} · {self.status}"


class OvertureCoveragePartition(TimestampedUUIDModel):
    """Independently replaceable province coverage within an Overture release."""

    class Status(models.TextChoices):
        IMPORTING = "IMPORTING", "Importando"
        READY = "READY", "Lista"
        SUPERSEDED = "SUPERSEDED", "Reemplazada"
        FAILED = "FAILED", "Falló"

    release = models.ForeignKey(
        OvertureRelease,
        on_delete=models.PROTECT,
        related_name="partitions",
    )
    province = models.ForeignKey(
        "configuration.SearchZone",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="overture_partitions",
    )
    province_code = models.CharField(max_length=20)
    province_name = models.CharField(max_length=160)
    province_bbox = models.JSONField(default=list)
    import_revision = models.PositiveIntegerField(default=1)
    snapshot = models.OneToOneField(
        OvertureDatasetSnapshot,
        on_delete=models.PROTECT,
        related_name="coverage_partition",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IMPORTING)
    is_active = models.BooleanField(default=False)
    boundary_version = models.CharField(max_length=120)
    boundary_manifest_sha256 = models.CharField(max_length=64)
    import_sha256 = models.CharField(max_length=64, blank=True)
    streamed_count = models.PositiveIntegerField(default=0)
    place_count = models.PositiveIntegerField(default=0)
    zone_count = models.PositiveIntegerField(default=0)
    validation_results = models.JSONField(default=dict)
    source_licenses = models.JSONField(default=list)
    attribution = models.TextField(blank=True)
    imported_at = models.DateTimeField(blank=True, null=True)
    activated_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("province_name", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("release", "province_code", "import_revision"),
                name="overture_partition_release_province_revision_unique",
            ),
            models.UniqueConstraint(
                fields=("province_code",),
                condition=Q(is_active=True),
                name="overture_one_active_partition_per_province",
            ),
            models.CheckConstraint(
                condition=Q(is_active=False) | Q(status="READY"),
                name="overture_partition_active_is_ready",
            ),
        ]
        indexes = [
            models.Index(
                fields=("province_code", "status", "is_active"),
                name="overture_partition_ready_idx",
            ),
            models.Index(
                fields=("release", "province_code"),
                name="overture_partition_release_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.province_id:
            province = self.province
            if province is None or province.level != "PROVINCE":
                errors["province"] = "La cobertura debe pertenecer a una provincia."
            elif province.official_code != self.province_code:
                errors["province_code"] = "El código no coincide con la provincia."
        if self.snapshot_id:
            if self.snapshot.release_id != self.release.release_id:
                errors["release"] = "El snapshot y la partición deben usar el mismo release."
            if self.snapshot.province_code and self.snapshot.province_code != self.province_code:
                errors["snapshot"] = "El snapshot pertenece a otra provincia."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "release_id",
                "province_id",
                "province_code",
                "province_name",
                "province_bbox",
                "import_revision",
                "snapshot_id",
                "boundary_version",
                "boundary_manifest_sha256",
            )
            if any(
                getattr(previous, field_name) != getattr(self, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError("La identidad de la partición Overture es inmutable.")
            changed_fields = {
                field.name
                for field in self._meta.concrete_fields
                if field.name != "updated_at"
                and getattr(previous, field.name) != getattr(self, field.name)
            }
            if previous.status in {self.Status.FAILED, self.Status.SUPERSEDED}:
                raise ValidationError("Una partición Overture terminal es inmutable.")
            if previous.status == self.Status.READY and not (
                changed_fields <= {"status", "is_active"}
                and self.status == self.Status.SUPERSEDED
                and not self.is_active
            ):
                raise ValidationError("Una partición lista sólo puede ser reemplazada.")
            if previous.status == self.Status.IMPORTING and self.status not in {
                self.Status.IMPORTING,
                self.Status.READY,
                self.Status.FAILED,
            }:
                raise ValidationError("La transición de la partición Overture no es válida.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        del args, kwargs
        raise ValidationError("Las particiones Overture no se pueden eliminar.")

    def __str__(self) -> str:
        return f"{self.province_name} · {self.release.release_id} · {self.status}"


def _lock_importing_snapshots(snapshot_ids: set[Any], *, using: str) -> None:
    statuses = dict(
        OvertureDatasetSnapshot.objects.using(using)
        .select_for_update()
        .filter(pk__in=snapshot_ids)
        .values_list("pk", "status")
    )
    if set(statuses) != snapshot_ids or any(
        status != OvertureDatasetSnapshot.Status.IMPORTING for status in statuses.values()
    ):
        raise ValidationError(
            "Sólo se pueden agregar registros a un snapshot Overture en importación."
        )


class OvertureDatasetZone(ImmutableCatalogModel):
    snapshot = models.ForeignKey(
        OvertureDatasetSnapshot,
        on_delete=models.CASCADE,
        related_name="zones",
    )
    partition = models.ForeignKey(
        OvertureCoveragePartition,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="zones",
    )
    search_zone = models.ForeignKey(
        "configuration.SearchZone",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="overture_dataset_zones",
    )
    code = models.CharField(max_length=120)
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200)
    geometry = models.JSONField()
    bbox = models.JSONField()
    boundary_hash = models.CharField(max_length=64)
    source = models.CharField(max_length=120)
    source_version = models.CharField(max_length=120)
    attribution = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("name", "code")
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "code"),
                name="overture_zone_snapshot_code_unique",
            ),
            models.UniqueConstraint(
                fields=("snapshot", "normalized_name"),
                name="overture_zone_snapshot_name_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=("snapshot", "normalized_name"),
                name="overture_zone_name_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.snapshot.release_id}"


class OverturePlace(ImmutableCatalogModel):
    snapshot = models.ForeignKey(
        OvertureDatasetSnapshot,
        on_delete=models.CASCADE,
        related_name="places",
    )
    partition = models.ForeignKey(
        OvertureCoveragePartition,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="places",
    )
    overture_id = models.CharField(max_length=80)
    name = models.CharField(max_length=300)
    normalized_name = models.CharField(max_length=300)
    name_search = models.CharField(max_length=320)
    names = models.JSONField(default=dict)
    address = models.CharField(max_length=500, blank=True)
    address_data = models.JSONField(default=dict)
    websites = models.JSONField(default=list)
    emails = models.JSONField(default=list)
    phones = models.JSONField(default=list)
    primary_category = models.CharField(max_length=160, blank=True)
    basic_category = models.CharField(max_length=160, blank=True)
    taxonomy = models.JSONField(default=dict)
    taxonomy_codes = models.JSONField(default=list)
    taxonomy_codes_search = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    confidence = models.DecimalField(
        max_digits=9,
        decimal_places=8,
        blank=True,
        null=True,
    )
    operating_status = models.CharField(max_length=40, blank=True)
    sources = models.JSONField(default=list)
    field_provenance = models.JSONField(default=dict)
    source_licenses = models.JSONField(default=list)
    license = models.CharField(max_length=500, blank=True)
    source_payload_hash = models.CharField(max_length=64)
    zones: models.ManyToManyField[OvertureDatasetZone, OverturePlaceZone] = models.ManyToManyField(
        OvertureDatasetZone,
        through="OverturePlaceZone",
        related_name="places",
    )

    class Meta:
        ordering = ("name", "overture_id")
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "overture_id"),
                name="overture_place_snapshot_id_unique",
            ),
            models.CheckConstraint(
                condition=Q(latitude__gte=-90, latitude__lte=90),
                name="overture_place_latitude_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__gte=-180, longitude__lte=180),
                name="overture_place_longitude_range",
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True) | Q(confidence__gte=0, confidence__lte=1),
                name="overture_place_confidence_range",
            ),
        ]
        indexes = [
            models.Index(
                fields=("snapshot", "primary_category"),
                name="overture_place_category_idx",
            ),
            models.Index(
                fields=("snapshot", "normalized_name"),
                name="overture_place_name_idx",
            ),
            models.Index(
                fields=("snapshot", "latitude", "longitude"),
                name="overture_place_coords_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.overture_id}"


class OverturePlaceZone(ImmutableCatalogModel):
    class MatchMethod(models.TextChoices):
        POINT_IN_POLYGON = "POINT_IN_POLYGON", "Punto en polígono"

    snapshot = models.ForeignKey(
        OvertureDatasetSnapshot,
        on_delete=models.CASCADE,
        related_name="place_zone_links",
    )
    partition = models.ForeignKey(
        OvertureCoveragePartition,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="place_zone_links",
    )
    place = models.ForeignKey(
        OverturePlace,
        on_delete=models.CASCADE,
        related_name="zone_links",
    )
    zone = models.ForeignKey(
        OvertureDatasetZone,
        on_delete=models.CASCADE,
        related_name="place_links",
    )
    match_method = models.CharField(
        max_length=30,
        choices=MatchMethod.choices,
        default=MatchMethod.POINT_IN_POLYGON,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("place", "zone"),
                name="overture_place_zone_unique",
            )
        ]
        indexes = [models.Index(fields=("snapshot", "zone"), name="overture_link_zone_idx")]

    def clean(self) -> None:
        super().clean()
        if self.place_id and self.snapshot_id != self.place.snapshot_id:
            raise ValidationError("El lugar y el vínculo deben pertenecer al mismo snapshot.")
        if self.zone_id and self.snapshot_id != self.zone.snapshot_id:
            raise ValidationError("La zona y el vínculo deben pertenecer al mismo snapshot.")
        if self.place_id and self.partition_id != self.place.partition_id:
            raise ValidationError("El lugar y el vínculo deben pertenecer a la misma provincia.")
        if self.zone_id and self.partition_id != self.zone.partition_id:
            raise ValidationError("La zona y el vínculo deben pertenecer a la misma provincia.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding:
            self.clean()
        super().save(*args, **kwargs)


class OvertureTaxonomyCode(ImmutableCatalogModel):
    snapshot = models.ForeignKey(
        OvertureDatasetSnapshot,
        on_delete=models.CASCADE,
        related_name="taxonomy_codes",
    )
    partition = models.ForeignKey(
        OvertureCoveragePartition,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="taxonomy_codes",
    )
    code = models.CharField(max_length=160)
    name = models.CharField(max_length=200, blank=True)
    parent_code = models.CharField(max_length=160, blank=True)
    hierarchy = models.JSONField(default=list)
    alternate_codes = models.JSONField(default=list)
    is_basic_category = models.BooleanField(default=False)

    class Meta:
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "code"),
                name="overture_taxonomy_snapshot_code_unique",
            )
        ]

    def __str__(self) -> str:
        return self.code


def _validate_bulk_place_zone_membership(objects: list[Any], *, using: str) -> None:
    links = [item for item in objects if isinstance(item, OverturePlaceZone)]
    if not links:
        return
    place_ids = {item.place_id for item in links}
    zone_ids = {item.zone_id for item in links}
    place_memberships = {
        pk: (snapshot_id, partition_id)
        for pk, snapshot_id, partition_id in OverturePlace.objects.using(using)
        .filter(pk__in=place_ids)
        .values_list("pk", "snapshot_id", "partition_id")
    }
    zone_memberships = {
        pk: (snapshot_id, partition_id)
        for pk, snapshot_id, partition_id in (
            OvertureDatasetZone.objects.using(using)
            .filter(pk__in=zone_ids)
            .values_list("pk", "snapshot_id", "partition_id")
        )
    }
    if set(place_memberships) != place_ids or set(zone_memberships) != zone_ids:
        raise ValidationError("El vínculo Overture referencia un lugar o zona inexistente.")
    if any(
        place_memberships[item.place_id][0] != item.snapshot_id
        or zone_memberships[item.zone_id][0] != item.snapshot_id
        or place_memberships[item.place_id][1] != item.partition_id
        or zone_memberships[item.zone_id][1] != item.partition_id
        for item in links
    ):
        raise ValidationError("El lugar, la zona y el vínculo deben pertenecer al mismo snapshot.")


class OvertureReleaseCheck(ImmutableCatalogModel):
    class Status(models.TextChoices):
        SUCCEEDED = "SUCCEEDED", "Correcto"
        FAILED = "FAILED", "Falló"

    status = models.CharField(max_length=20, choices=Status.choices)
    catalog_url = models.CharField(max_length=500)
    releases = models.JSONField(default=list)
    latest_release = models.CharField(max_length=40, blank=True)
    manifest_sha256 = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.latest_release or self.status
