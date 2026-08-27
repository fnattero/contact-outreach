from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator, Mapping
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.catalogs.models import Catalog
from apps.overture import services
from apps.overture import tasks as overture_tasks
from apps.overture.geometry import MAX_GEOJSON_BYTES, validate_geojson
from apps.overture.importer import (
    FOURSQUARE_NOTICE,
    OVERTURE_ATTRIBUTION,
    OvertureSchemaError,
    ZoneDefinition,
    activate_snapshot,
    import_snapshot,
)
from apps.overture.models import (
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
    OvertureReleaseCheck,
    OvertureTaxonomyCode,
)
from apps.overture.reader import BBox
from apps.overture.releases import OFFICIAL_STAC_URL, ReleaseCatalog, ReleaseDescriptor


def _square() -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
    }


def _zone() -> ZoneDefinition:
    return ZoneDefinition(
        code="fixture",
        name="Fixture",
        geometry=_square(),
        source="fixture",
        source_version="1",
    )


def _manifest_hash(code: str, boundary_hash: str) -> str:
    payload = json.dumps(
        [{"code": code, "boundary_hash": boundary_hash}],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _descriptor(release_id: str) -> ReleaseDescriptor:
    return ReleaseDescriptor(
        release_id=release_id,
        schema_version="v1.18.0" if release_id == "2026-07-22.0" else "v1.17.0",
        taxonomy_version=release_id,
        source_uri=(f"s3://overturemaps-us-west-2/release/{release_id}/theme=places/type=place/"),
        catalog_url=f"https://stac.overturemaps.org/{release_id}/catalog.json",
        manifest_sha256="a" * 64,
    )


def _row(label: str) -> dict[str, object]:
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://fixture.invalid/{label}")),
        "names": {"primary": f"Business {label}"},
        "geometry": {"type": "Point", "coordinates": [5, 5]},
        "addresses": [{"freeform": "CABA"}],
        "websites": [],
        "emails": [],
        "phones": [],
        "basic_category": "services_and_business",
        "taxonomy": {
            "primary": "auto_electrical_repair",
            "hierarchy": ["services_and_business", "auto_electrical_repair"],
            "alternates": [],
        },
        "confidence": 0.9,
        "operating_status": "open",
        "sources": [{"dataset": "meta", "record_id": label}],
    }


class _Reader:
    def __init__(self, rows: list[Mapping[str, object]]) -> None:
        self.rows = rows
        self.calls = 0

    def iter_places(self, *, release_id: str, bbox: BBox) -> Iterator[Mapping[str, object]]:
        del release_id, bbox
        self.calls += 1
        yield from self.rows


class _LockCursor:
    def __init__(self, connection: _LockConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _LockCursor:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def execute(self, query: str, parameters: list[int]) -> None:
        self.connection.executed.append((query, parameters))

    def fetchone(self) -> tuple[bool] | None:
        return self.connection.rows.pop(0)


class _LockConnection:
    def __init__(self, rows: list[tuple[bool] | None]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, list[int]]] = []

    def cursor(self) -> _LockCursor:
        return _LockCursor(self)


def _import(release_id: str, reader: _Reader) -> OvertureDatasetSnapshot:
    descriptor = _descriptor(release_id)
    return import_snapshot(
        descriptor=descriptor,
        zones=[_zone()],
        reader=reader,
        schema_version=descriptor.schema_version,
        taxonomy_version=descriptor.taxonomy_version,
        importer_version="test-importer",
        mapping_version="test-mapping",
        boundary_version="test-boundary",
        batch_size=1,
    )


def _snapshot(
    release_id: str,
    *,
    status: str,
    is_active: bool = False,
    activated_days_ago: int | None = None,
) -> OvertureDatasetSnapshot:
    activated_at = (
        timezone.now() - timedelta(days=activated_days_ago)
        if activated_days_ago is not None
        else None
    )
    return OvertureDatasetSnapshot.objects.create(
        release_id=release_id,
        schema_version="v1.18.0",
        taxonomy_version=release_id,
        importer_version="test-importer",
        mapping_version="test-mapping",
        boundary_version=f"boundary-{release_id}",
        boundary_manifest_sha256="b" * 64,
        source_uri=(f"s3://overturemaps-us-west-2/release/{release_id}/theme=places/type=place/"),
        manifest_sha256="a" * 64,
        status=status,
        is_active=is_active,
        activated_at=activated_at,
    )


@pytest.mark.django_db
def test_sync_lock_rejects_an_overlapping_import_without_touching_reader() -> None:
    reader = _Reader([_row("must-not-run")])
    cache.set(services.SYNC_LOCK_KEY, "other-import", timeout=60)

    with pytest.raises(ValidationError, match="sincronización Overture en curso"):
        services.sync_catalog(
            descriptor=_descriptor("2026-07-22.0"),
            reader=reader,
            zones=[_zone()],
            boundary_version="test-boundary",
        )

    assert reader.calls == 0
    assert cache.get(services.SYNC_LOCK_KEY) == "other-import"


def test_postgres_advisory_lock_is_acquired_and_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _LockConnection([(True,), (True,)])
    monkeypatch.setattr(services, "connection", connection)

    with services._postgres_catalog_sync_lock():
        pass

    assert [query for query, _ in connection.executed] == [
        "SELECT pg_try_advisory_lock(%s)",
        "SELECT pg_advisory_unlock(%s)",
    ]


@pytest.mark.parametrize("lock_row", [None, (False,)])
def test_postgres_advisory_lock_rejects_busy_or_invalid_result(
    monkeypatch: pytest.MonkeyPatch, lock_row: tuple[bool] | None
) -> None:
    monkeypatch.setattr(services, "connection", _LockConnection([lock_row]))

    with pytest.raises(ValidationError, match="sincronización Overture en curso"):
        with services._postgres_catalog_sync_lock():
            pass


def test_postgres_advisory_lock_reports_cleanup_failure_without_masking_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "connection", _LockConnection([(True,), (False,)]))

    with pytest.raises(RuntimeError, match="body failed") as exc_info:
        with services._postgres_catalog_sync_lock():
            raise RuntimeError("body failed")

    assert exc_info.value.__notes__ == ["Overture advisory-lock cleanup also failed: RuntimeError."]


def test_postgres_advisory_lock_raises_cleanup_failure_after_successful_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "connection", _LockConnection([(True,), None]))

    with pytest.raises(RuntimeError, match="No se pudo liberar"):
        with services._postgres_catalog_sync_lock():
            pass


@pytest.mark.django_db
def test_sync_lock_is_released_when_import_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_import(**kwargs: object) -> OvertureDatasetSnapshot:
        del kwargs
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(services, "import_snapshot", fail_import)

    with pytest.raises(RuntimeError, match="fixture failure"):
        services.sync_catalog(
            descriptor=_descriptor("2026-07-22.0"),
            reader=_Reader([]),
            zones=[_zone()],
            boundary_version="test-boundary",
        )

    assert cache.get(services.SYNC_LOCK_KEY) is None


def test_sync_task_runs_retention_after_a_failed_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_sync(*, release_id: str | None) -> OvertureDatasetSnapshot:
        del release_id
        raise RuntimeError("fixture failure")

    purge = Mock()
    monkeypatch.setattr(overture_tasks, "sync_catalog", fail_sync)
    monkeypatch.setattr(overture_tasks, "purge_retired_snapshots", purge)

    with pytest.raises(RuntimeError, match="fixture failure"):
        overture_tasks.sync_snapshot.run("2026-07-22.0")

    purge.assert_called_once_with()


@pytest.mark.django_db
def test_activation_failure_atomically_restores_previous_active_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _import("2026-06-17.0", _Reader([_row("old")]))
    original_save = OvertureDatasetSnapshot.save

    def fail_new_activation(
        self: OvertureDatasetSnapshot,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if (
            self.release_id == "2026-07-22.0"
            and self.status == OvertureDatasetSnapshot.Status.READY
            and self.is_active
        ):
            raise RuntimeError("activation write failed")
        original_save(self, *args, **kwargs)

    monkeypatch.setattr(OvertureDatasetSnapshot, "save", fail_new_activation)

    with pytest.raises(RuntimeError, match="activation write failed"):
        _import("2026-07-22.0", _Reader([_row("new")]))

    active.refresh_from_db()
    failed = OvertureDatasetSnapshot.objects.get(release_id="2026-07-22.0")
    assert active.status == OvertureDatasetSnapshot.Status.READY
    assert active.is_active
    assert failed.status == OvertureDatasetSnapshot.Status.FAILED
    assert not failed.is_active


@pytest.mark.django_db
def test_activation_rejects_unvalidated_snapshot_without_switching_active() -> None:
    active = _import("2026-06-17.0", _Reader([_row("old")]))
    candidate = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="test-importer",
        mapping_version="test-mapping",
        boundary_version="test-boundary",
        boundary_manifest_sha256="b" * 64,
        source_uri=("s3://overturemaps-us-west-2/release/2026-07-22.0/theme=places/type=place/"),
        manifest_sha256="a" * 64,
        attribution=OVERTURE_ATTRIBUTION,
        validation_results={"passed": False},
    )

    with pytest.raises(ValidationError, match="validación"):
        activate_snapshot(candidate.pk)

    active.refresh_from_db()
    candidate.refresh_from_db()
    assert active.status == OvertureDatasetSnapshot.Status.READY
    assert active.is_active
    assert candidate.status == OvertureDatasetSnapshot.Status.IMPORTING
    assert not candidate.is_active


@pytest.mark.django_db
def test_activation_recomputes_and_rejects_tampered_place_provenance() -> None:
    active = _import("2026-06-17.0", _Reader([_row("old")]))
    geometry = validate_geojson(_square())
    candidate = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="test-importer",
        mapping_version="test-mapping",
        boundary_version="test-boundary",
        boundary_manifest_sha256=_manifest_hash("fixture", geometry.sha256),
        source_uri=("s3://overturemaps-us-west-2/release/2026-07-22.0/theme=places/type=place/"),
        manifest_sha256="a" * 64,
        streamed_count=1,
        place_count=1,
        zone_count=1,
        source_counts={"meta": 1},
        source_licenses=["CDLA-Permissive-2.0"],
        attribution=OVERTURE_ATTRIBUTION,
        validation_results={"passed": True},
    )
    zone = OvertureDatasetZone.objects.create(
        snapshot=candidate,
        code="fixture",
        name="Fixture",
        normalized_name="fixture",
        geometry=geometry.geojson,
        bbox=list(geometry.bbox),
        boundary_hash=geometry.sha256,
        source="fixture",
        source_version="1",
    )
    place = OverturePlace.objects.create(
        snapshot=candidate,
        overture_id=str(uuid.uuid4()),
        name="Fixture",
        normalized_name="fixture",
        name_search=" fixture ",
        latitude=5,
        longitude=5,
        sources=[{"dataset": "meta", "record_id": "meta-1"}],
        # Deliberately disagrees with the root source in ``sources``.
        field_provenance={"/": [{"dataset": "foursquare", "record_id": "fsq-1"}]},
        source_licenses=["CDLA-Permissive-2.0"],
        license="CDLA-Permissive-2.0",
        source_payload_hash="c" * 64,
    )
    OverturePlaceZone.objects.create(
        snapshot=candidate,
        place=place,
        zone=zone,
    )

    with pytest.raises(ValidationError, match="procedencia raíz"):
        activate_snapshot(candidate.pk)

    active.refresh_from_db()
    assert active.is_active
    assert active.status == OvertureDatasetSnapshot.Status.READY


@pytest.mark.django_db
def test_retention_keeps_active_previous_referenced_and_all_failed_snapshots(
    owner: User,
) -> None:
    active = _snapshot(
        "2026-07-22.0",
        status=OvertureDatasetSnapshot.Status.READY,
        is_active=True,
        activated_days_ago=0,
    )
    previous = _snapshot(
        "2026-06-24.0",
        status=OvertureDatasetSnapshot.Status.SUPERSEDED,
        activated_days_ago=30,
    )
    referenced = _snapshot(
        "2026-05-21.0",
        status=OvertureDatasetSnapshot.Status.SUPERSEDED,
        activated_days_ago=60,
    )
    retired = _snapshot(
        "2026-04-22.0",
        status=OvertureDatasetSnapshot.Status.SUPERSEDED,
        activated_days_ago=90,
    )
    older_failed = _snapshot(
        "2026-03-18.0",
        status=OvertureDatasetSnapshot.Status.FAILED,
    )
    latest_failed = _snapshot(
        "2026-03-25.0",
        status=OvertureDatasetSnapshot.Status.FAILED,
    )
    catalog = Catalog.objects.create(
        name="Fixture",
        version=1,
        file="catalogs/fixture.pdf",
        original_filename="fixture.pdf",
        detected_mime="application/pdf",
        byte_size=1,
        sha256="c" * 64,
        uploaded_by=owner,
    )
    Campaign.objects.create(
        name="Pinned snapshot",
        extractor_provider="overture",
        overture_snapshot=referenced,
        catalog=catalog,
        created_by=owner,
    )

    assert services.purge_retired_snapshots() == 1

    remaining_ids = set(OvertureDatasetSnapshot.objects.values_list("pk", flat=True))
    assert remaining_ids == {
        active.pk,
        previous.pk,
        referenced.pk,
        older_failed.pk,
        latest_failed.pk,
    }
    assert retired.pk not in remaining_ids


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("schema_version", "taxonomy_version", "message"),
    [
        ("v9.9.9", "2026-07-22.0", "versión de esquema"),
        ("v1.18.0", "2026-06-24.0", "versión de taxonomía"),
    ],
)
def test_release_metadata_version_drift_is_rejected_before_streaming(
    schema_version: str,
    taxonomy_version: str,
    message: str,
) -> None:
    descriptor = _descriptor("2026-07-22.0")
    reader = _Reader([_row("must-not-run")])

    with pytest.raises(OvertureSchemaError, match=message):
        import_snapshot(
            descriptor=descriptor,
            zones=[_zone()],
            reader=reader,
            schema_version=schema_version,
            taxonomy_version=taxonomy_version,
            importer_version="test-importer",
            mapping_version="test-mapping",
            boundary_version="test-boundary",
        )

    assert reader.calls == 0
    assert not OvertureDatasetSnapshot.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hierarchy", "services_and_business"),
        ("alternates", "auto_electrical_repair"),
        ("hierarchy", ["services_and_business", 123]),
    ],
)
def test_taxonomy_shape_drift_fails_closed(field: str, value: object) -> None:
    row = _row("drift")
    taxonomy = row["taxonomy"]
    assert isinstance(taxonomy, dict)
    taxonomy[field] = value

    with pytest.raises(OvertureSchemaError, match="taxon"):
        _import("2026-07-22.0", _Reader([row]))

    snapshot = OvertureDatasetSnapshot.objects.get()
    assert snapshot.status == OvertureDatasetSnapshot.Status.FAILED
    assert snapshot.validation_results["passed"] is False


@pytest.mark.django_db
def test_taxonomy_registry_aggregates_sorted_alternates_for_primary_code() -> None:
    first = _row("first")
    second = _row("second")
    first_taxonomy = first["taxonomy"]
    second_taxonomy = second["taxonomy"]
    assert isinstance(first_taxonomy, dict)
    assert isinstance(second_taxonomy, dict)
    first_taxonomy["alternates"] = ["electric_motor_repair", "alternator_repair"]
    second_taxonomy["alternates"] = ["battery_repair", "electric_motor_repair"]

    snapshot = _import("2026-07-22.0", _Reader([first, second]))

    primary = OvertureTaxonomyCode.objects.get(
        snapshot=snapshot,
        code="auto_electrical_repair",
    )
    assert primary.alternate_codes == [
        "alternator_repair",
        "battery_repair",
        "electric_motor_repair",
    ]


@pytest.mark.django_db
def test_foursquare_source_persists_current_license_notice() -> None:
    row = _row("foursquare")
    row["sources"] = [{"dataset": "fsq", "record_id": "fsq-fixture"}]

    snapshot = _import("2026-07-22.0", _Reader([row]))

    assert snapshot.source_licenses == ["Apache-2.0"]
    assert snapshot.notices == [FOURSQUARE_NOTICE]
    notice = snapshot.notices[0]
    assert "Copyright 2024 Foursquare Labs, Inc. All rights reserved" in notice
    assert "Apache 2.0" in notice
    assert "transformed to the Overture schema" in notice
    assert "Changed: 2026-03-18" in notice
    assert "https://opensource.foursquare.com/places-notice-txt/" in notice


@pytest.mark.parametrize(
    ("geojson", "message"),
    [
        (
            {**_square(), "crs": {"type": "name", "properties": {"name": "EPSG:3857"}}},
            "CRS",
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [[[0, 0], [4, 4], [0, 4], [3, 0], [0, 0]]],
            },
            "inválida|autointersecta",
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [[[0, 0], [float("nan"), 0], [1, 1], [0, 0]]],
            },
            "JSON válido|finitos",
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [[[0, 0], [181, 0], [1, 1], [0, 0]]],
            },
            "fuera de rango",
        ),
        (
            {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            "Polygon",
        ),
    ],
)
def test_geojson_rejects_unsafe_or_incompatible_documents(
    geojson: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        validate_geojson(geojson)


@pytest.mark.parametrize(
    ("geojson", "message"),
    [
        (
            {
                "type": "MultiPolygon",
                "coordinates": [_square()["coordinates"] for _ in range(101)],
            },
            "100 partes",
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [_square()["coordinates"][0] for _ in range(201)],  # type: ignore[index]
            },
            "200 anillos",
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [[[0, 0] for _ in range(20_001)]],
            },
            "20000 coordenadas",
        ),
        (
            {
                "type": "Feature",
                "properties": {"padding": "x" * (MAX_GEOJSON_BYTES + 1)},
                "geometry": _square(),
            },
            "1 MiB",
        ),
    ],
)
def test_geojson_enforces_upload_complexity_limits(geojson: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        validate_geojson(geojson)


@pytest.mark.django_db
def test_explicit_sync_recovers_interrupted_import_and_builds_a_valid_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interrupted = _snapshot(
        "2026-06-17.0",
        status=OvertureDatasetSnapshot.Status.IMPORTING,
    )
    future = timezone.now() + timedelta(hours=8)
    monkeypatch.setattr(services.timezone, "now", lambda: future)

    snapshot = services.sync_catalog(
        descriptor=_descriptor("2026-07-22.0"),
        reader=_Reader([_row("sync")]),
        zones=[_zone()],
        boundary_version="test-boundary",
    )

    interrupted.refresh_from_db()
    assert interrupted.status == OvertureDatasetSnapshot.Status.FAILED
    assert interrupted.validation_results["error_type"] == "InterruptedImport"
    assert snapshot.status == OvertureDatasetSnapshot.Status.READY
    assert snapshot.is_active


@pytest.mark.django_db
def test_configured_zones_are_frozen_with_boundary_hashes() -> None:
    configured = services.configured_zones()

    assert len(configured.definitions) == 48
    assert configured.boundary_version.startswith("search-zones-")
    assert all(zone.expected_boundary_hash for zone in configured.definitions)


@pytest.mark.django_db
def test_release_metadata_task_persists_success_and_sanitized_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = ReleaseCatalog(
        releases=("2026-06-17.0", "2026-07-22.0"),
        latest_release="2026-07-22.0",
        catalog_url=OFFICIAL_STAC_URL,
        manifest_sha256="f" * 64,
    )
    monkeypatch.setattr(overture_tasks, "discover_releases", lambda: catalog)

    assert overture_tasks.discover_releases_task.run()["status"] == "SUCCEEDED"
    succeeded = OvertureReleaseCheck.objects.get(status=OvertureReleaseCheck.Status.SUCCEEDED)
    assert succeeded.latest_release == "2026-07-22.0"

    def fail_discovery() -> ReleaseCatalog:
        raise RuntimeError("credential-that-must-not-leak")

    monkeypatch.setattr(overture_tasks, "discover_releases", fail_discovery)
    assert overture_tasks.discover_releases_task.run() == {"status": "FAILED"}
    failed = OvertureReleaseCheck.objects.get(status=OvertureReleaseCheck.Status.FAILED)
    assert "credential-that-must-not-leak" not in failed.error
