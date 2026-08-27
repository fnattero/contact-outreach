from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.overture.importer import (
    ProvinceCoverageDefinition,
    ZoneDefinition,
    import_snapshot,
)
from apps.overture.models import OvertureCoveragePartition, OvertureDatasetSnapshot
from apps.overture.reader import OfficialOverturePlaceReader, OverturePlaceReader, validate_bbox
from apps.overture.releases import (
    CatalogTransport,
    ReleaseDescriptor,
    discover_release,
)

OVERTURE_IMPORTER_VERSION = "contact-outreach-overture-importer-v1"
OVERTURE_MAPPING_VERSION = "contact-outreach-v1"
SYNC_LOCK_KEY = "overture:catalog-sync:v1"
SYNC_LOCK_SECONDS = 7 * 60 * 60
SYNC_ADVISORY_LOCK_ID = int.from_bytes(
    hashlib.sha256(SYNC_LOCK_KEY.encode("utf-8")).digest()[:8],
    byteorder="big",
    signed=True,
)


@dataclass(frozen=True, slots=True)
class ConfiguredZones:
    definitions: tuple[ZoneDefinition, ...]
    boundary_version: str
    province_id: uuid.UUID | str
    province_code: str
    province_name: str
    province_bbox: tuple[float, float, float, float]


@contextmanager
def _postgres_catalog_sync_lock() -> Iterator[None]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [SYNC_ADVISORY_LOCK_ID])
        row = cursor.fetchone()
    if row is None or row[0] is not True:
        raise ValidationError("Ya hay una sincronización Overture en curso.")
    original_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        original_error = exc
        raise
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [SYNC_ADVISORY_LOCK_ID])
                release_row = cursor.fetchone()
            if release_row is None or release_row[0] is not True:
                raise RuntimeError("No se pudo liberar el lock PostgreSQL de Overture.")
        except Exception as release_error:
            if original_error is not None:
                original_error.add_note(
                    f"Overture advisory-lock cleanup also failed: {type(release_error).__name__}."
                )
            else:
                raise


@contextmanager
def _cache_catalog_sync_lock() -> Iterator[None]:
    """Test/non-PostgreSQL fallback with token-checked cleanup."""

    token = uuid.uuid4().hex
    if not cache.add(SYNC_LOCK_KEY, token, timeout=SYNC_LOCK_SECONDS):
        raise ValidationError("Ya hay una sincronización Overture en curso.")
    original_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        original_error = exc
        raise
    finally:
        try:
            # Never delete a successor's lock if this token expired or was
            # replaced while the non-production fallback was running.
            if cache.get(SYNC_LOCK_KEY) == token:
                cache.delete(SYNC_LOCK_KEY)
        except Exception as release_error:
            if original_error is not None:
                original_error.add_note(
                    f"Overture cache-lock cleanup also failed: {type(release_error).__name__}."
                )
            else:
                raise


@contextmanager
def _catalog_sync_lock() -> Iterator[None]:
    if connection.vendor == "postgresql":
        with _postgres_catalog_sync_lock():
            yield
        return
    with _cache_catalog_sync_lock():
        yield


def recover_stale_imports(*, now: datetime | None = None) -> int:
    """Fail interrupted imports before an explicit replacement sync starts."""

    moment = now or timezone.now()
    if timezone.is_naive(moment):
        raise ValueError("now must be timezone-aware")
    cutoff = moment - timedelta(seconds=SYNC_LOCK_SECONDS)
    with transaction.atomic():
        snapshots = list(
            OvertureDatasetSnapshot.objects.select_for_update().filter(
                status=OvertureDatasetSnapshot.Status.IMPORTING,
                updated_at__lt=cutoff,
            )
        )
        for snapshot in snapshots:
            results = (
                dict(snapshot.validation_results)
                if isinstance(snapshot.validation_results, dict)
                else {}
            )
            results.update({"passed": False, "error_type": "InterruptedImport"})
            snapshot.status = OvertureDatasetSnapshot.Status.FAILED
            snapshot.is_active = False
            snapshot.error = "InterruptedImport: la importación no se pudo completar."
            snapshot.validation_results = results
            snapshot.save(
                update_fields=(
                    "status",
                    "is_active",
                    "error",
                    "validation_results",
                    "updated_at",
                )
            )
            OvertureCoveragePartition.objects.filter(snapshot=snapshot).update(
                status=OvertureCoveragePartition.Status.FAILED,
                is_active=False,
                error=snapshot.error,
                validation_results=results,
                updated_at=moment,
            )
    return len(snapshots)


def get_active_snapshot() -> OvertureDatasetSnapshot | None:
    # Compatibility for callers that have not selected a province yet. New
    # campaign code resolves one explicit partition per province.
    return OvertureDatasetSnapshot.objects.active().order_by("-activated_at", "-created_at").first()


def get_active_partition(province_code: str) -> OvertureCoveragePartition | None:
    return (
        OvertureCoveragePartition.objects.select_related("release", "province", "snapshot")
        .filter(
            province_code=province_code,
            status=OvertureCoveragePartition.Status.READY,
            is_active=True,
        )
        .first()
    )


def get_latest_snapshot() -> OvertureDatasetSnapshot | None:
    return OvertureDatasetSnapshot.objects.order_by("-created_at").first()


def configured_zones(province_code: str = "02") -> ConfiguredZones:
    # The late import keeps the catalog importer independently testable and avoids
    # making its streaming/provider boundary depend on configuration model internals.
    from apps.configuration.models import SearchZone

    province = SearchZone.objects.filter(
        level=SearchZone.Level.PROVINCE,
        official_code=province_code,
        active=True,
        archived_at__isnull=True,
    ).first()
    if province is None:
        raise ValidationError("La provincia elegida no está disponible.")
    province_bbox = validate_bbox(province.boundary_bbox)
    zone_models = list(
        SearchZone.objects.filter(
            parent=province,
            selectable=True,
            active=True,
            archived_at__isnull=True,
        ).order_by("sort_order", "name")
    )
    if not zone_models:
        raise ValidationError(f"{province.name} no tiene distritos activos para importar.")
    definitions: list[ZoneDefinition] = []
    manifest: list[dict[str, object]] = []
    for zone in zone_models:
        if not zone.boundary_geojson or not zone.boundary_hash:
            raise ValidationError(f"La zona {zone.name} no tiene una frontera GeoJSON válida.")
        definitions.append(
            ZoneDefinition(
                code=zone.official_code,
                name=zone.name,
                geometry=zone.boundary_geojson,
                source=zone.boundary_source or "Configuración local",
                source_version=f"revision-{zone.boundary_revision}",
                attribution=zone.boundary_attribution,
                expected_boundary_hash=zone.boundary_hash,
                search_zone_id=zone.pk,
                trusted_official_geometry=zone.source
                in {SearchZone.Source.GEOREF, SearchZone.Source.BUENOS_AIRES_DATA},
            )
        )
        manifest.append(
            {
                "code": zone.official_code,
                "revision": zone.boundary_revision,
                "boundary_hash": zone.boundary_hash,
            }
        )
    encoded = json.dumps(
        sorted(manifest, key=lambda item: str(item["code"])),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    boundary_version = f"search-zones-{hashlib.sha256(encoded).hexdigest()[:24]}"
    return ConfiguredZones(
        tuple(definitions),
        boundary_version,
        province.pk,
        province.official_code,
        province.name,
        province_bbox,
    )


def sync_catalog(
    *,
    release_id: str | None = None,
    descriptor: ReleaseDescriptor | None = None,
    reader: OverturePlaceReader | None = None,
    zones: Iterable[ZoneDefinition] | None = None,
    boundary_version: str = "",
    province_code: str = "02",
    transport: CatalogTransport | Callable[..., bytes] | None = None,
) -> OvertureDatasetSnapshot:
    with _catalog_sync_lock():
        recover_stale_imports()
        selected_descriptor = descriptor or discover_release(release_id, transport=transport)
        if descriptor is not None and release_id not in (None, "", descriptor.release_id):
            raise ValidationError("El descriptor y el release Overture no coinciden.")
        if zones is None:
            configured = configured_zones(province_code)
            selected_zones = configured.definitions
            selected_boundary_version = configured.boundary_version
            coverage = ProvinceCoverageDefinition(
                province_id=configured.province_id,
                province_code=configured.province_code,
                province_name=configured.province_name,
                province_bbox=configured.province_bbox,
            )
        else:
            selected_zones = tuple(zones)
            if not boundary_version:
                raise ValidationError("Una importación inyectada debe indicar boundary_version.")
            selected_boundary_version = boundary_version
            coverage = None
        return import_snapshot(
            descriptor=selected_descriptor,
            zones=selected_zones,
            reader=reader or OfficialOverturePlaceReader(),
            schema_version=selected_descriptor.schema_version,
            taxonomy_version=selected_descriptor.taxonomy_version,
            importer_version=OVERTURE_IMPORTER_VERSION,
            mapping_version=OVERTURE_MAPPING_VERSION,
            boundary_version=selected_boundary_version,
            coverage=coverage,
        )


def sync_province_coverages(
    province_codes: Iterable[str],
    *,
    release_id: str | None = None,
    descriptor: ReleaseDescriptor | None = None,
    reader: OverturePlaceReader | None = None,
    transport: CatalogTransport | Callable[..., bytes] | None = None,
) -> tuple[OvertureCoveragePartition, ...]:
    """Import each province with one independently bounded reader call."""

    codes = tuple(dict.fromkeys(str(code).strip() for code in province_codes if str(code).strip()))
    if not codes:
        raise ValidationError("Elegí al menos una provincia para actualizar.")
    selected_reader = reader or OfficialOverturePlaceReader()
    with _catalog_sync_lock():
        recover_stale_imports()
        selected_descriptor = descriptor or discover_release(release_id, transport=transport)
        if descriptor is not None and release_id not in (None, "", descriptor.release_id):
            raise ValidationError("El descriptor y el release Overture no coinciden.")
        partitions: list[OvertureCoveragePartition] = []
        for code in codes:
            configured = configured_zones(code)
            snapshot = import_snapshot(
                descriptor=selected_descriptor,
                zones=configured.definitions,
                reader=selected_reader,
                schema_version=selected_descriptor.schema_version,
                taxonomy_version=selected_descriptor.taxonomy_version,
                importer_version=OVERTURE_IMPORTER_VERSION,
                mapping_version=OVERTURE_MAPPING_VERSION,
                boundary_version=configured.boundary_version,
                coverage=ProvinceCoverageDefinition(
                    province_id=configured.province_id,
                    province_code=configured.province_code,
                    province_name=configured.province_name,
                    province_bbox=configured.province_bbox,
                ),
            )
            partitions.append(snapshot.coverage_partition)
        return tuple(partitions)


def purge_retired_snapshots() -> int:
    """Purge only unreferenced superseded payloads, never failed-import evidence."""

    # Province partitions are immutable provenance records and their snapshot FK
    # is deliberately protected. Retention for partitioned imports is therefore
    # handled by keeping those payloads; this legacy purge only applies to old,
    # unpartitioned snapshots.
    previous_id = (
        OvertureDatasetSnapshot.objects.filter(
            status=OvertureDatasetSnapshot.Status.SUPERSEDED,
            coverage_partition__isnull=True,
        )
        .order_by("-activated_at", "-created_at")
        .values_list("pk", flat=True)
        .first()
    )
    eligible = OvertureDatasetSnapshot.objects.filter(
        status=OvertureDatasetSnapshot.Status.SUPERSEDED,
        is_active=False,
        coverage_partition__isnull=True,
    ).filter(Q(campaigns__isnull=True) & Q(search_runs__isnull=True))
    if previous_id is not None:
        eligible = eligible.exclude(pk=previous_id)
    snapshot_ids = list(eligible.values_list("pk", flat=True).distinct())
    if not snapshot_ids:
        return 0
    OvertureDatasetSnapshot.objects.filter(pk__in=snapshot_ids).hard_delete()
    return len(snapshot_ids)
