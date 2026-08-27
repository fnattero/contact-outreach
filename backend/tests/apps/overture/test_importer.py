from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.overture.importer import (
    OvertureImportError,
    OvertureSchemaError,
    ZoneDefinition,
    import_snapshot,
)
from apps.overture.models import (
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
    OvertureTaxonomyCode,
)
from apps.overture.reader import BBox
from apps.overture.releases import ReleaseDescriptor
from apps.overture.services import get_active_snapshot


def _square() -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[-59, -35], [-58, -35], [-58, -34], [-59, -34], [-59, -35]]],
    }


def _zone() -> ZoneDefinition:
    return ZoneDefinition(
        code="palermo",
        name="Palermo",
        geometry=_square(),
        source="fixture",
        source_version="1",
    )


def _descriptor(release_id: str) -> ReleaseDescriptor:
    return ReleaseDescriptor(
        release_id=release_id,
        schema_version="v1.18.0" if release_id == "2026-07-22.0" else "v1.17.0",
        taxonomy_version=release_id,
        source_uri=f"s3://overturemaps-us-west-2/release/{release_id}/theme=places/type=place/",
        catalog_url=f"https://stac.overturemaps.org/{release_id}/catalog.json",
        manifest_sha256="a" * 64,
    )


def _row(
    identifier: str,
    *,
    longitude: float = -58.5,
    latitude: float = -34.5,
    operating_status: str = "open",
) -> dict[str, object]:
    return {
        "id": _gers(identifier),
        "names": {"primary": f"Business {identifier}"},
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "addresses": [{"freeform": "CABA"}],
        "websites": ["https://example.test"],
        "emails": ["ventas@example.test"],
        "phones": ["+54 11 5555 5555"],
        "basic_category": "services_and_business",
        "taxonomy": {
            "primary": "auto_electrical_repair",
            "hierarchy": [
                "services_and_business",
                "automotive_services",
                "auto_electrical_repair",
            ],
            "alternates": ["electric_motor_repair"],
        },
        "confidence": 0.91,
        "operating_status": operating_status,
        "sources": [{"dataset": "meta", "record_id": identifier}],
    }


def _gers(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://fixture.invalid/{label}"))


class FakeReader:
    def __init__(self, rows: list[Mapping[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, BBox]] = []

    def iter_places(self, *, release_id: str, bbox: BBox) -> Iterator[Mapping[str, object]]:
        self.calls.append((release_id, bbox))
        yield from self.rows


def _import(
    release_id: str, reader: FakeReader, *, max_places: int = 500_000
) -> OvertureDatasetSnapshot:
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
        max_places=max_places,
        batch_size=1,
    )


@pytest.mark.django_db
def test_import_streams_catalog_assigns_exact_zone_and_activates_snapshot() -> None:
    snapshot = _import(
        "2026-07-22.0",
        FakeReader([_row("inside"), _row("outside", longitude=-57.5)]),
    )

    assert snapshot.status == OvertureDatasetSnapshot.Status.READY
    assert snapshot.is_active
    assert snapshot.streamed_count == 2
    assert snapshot.place_count == 1
    assert OvertureDatasetZone.objects.count() == 1
    assert OverturePlace.objects.count() == 1
    assert OverturePlaceZone.objects.count() == 1
    assert OvertureTaxonomyCode.objects.count() == 4
    inside = OverturePlace.objects.get(overture_id=_gers("inside"))
    assert inside.basic_category == "services_and_business"
    assert inside.license == "CDLA-Permissive-2.0"
    assert inside.field_provenance["/"][0]["dataset"] == "meta"
    assert inside.zone_links.get().zone.name == "Palermo"
    assert snapshot.validation_results["outside_exact_zones"] == 1
    assert get_active_snapshot() == snapshot


@pytest.mark.django_db
def test_polygon_filter_uses_unrounded_source_coordinates() -> None:
    snapshot = _import(
        "2026-07-22.0",
        FakeReader(
            [
                _row("inside", longitude=-58.00000001),
                # This rounds to -58.0000000 at the storage precision but is
                # strictly east of the polygon and must not be imported.
                _row("outside-near-boundary", longitude=-57.99999999),
            ]
        ),
    )

    assert snapshot.place_count == 1
    assert list(snapshot.places.values_list("overture_id", flat=True)) == [_gers("inside")]
    assert snapshot.validation_results["outside_exact_zones"] == 1


@pytest.mark.django_db
def test_nullable_official_names_remain_empty_without_inventing_a_name() -> None:
    row = _row("unnamed")
    row["names"] = None

    snapshot = _import("2026-07-22.0", FakeReader([row]))
    place = snapshot.places.get()

    assert place.name == ""
    assert place.names == {}
    assert place.normalized_name == ""
    assert place.name_search == ""


@pytest.mark.django_db
def test_confidence_is_not_rounded_to_four_decimals_before_storage() -> None:
    row = _row("confidence")
    row["confidence"] = 0.75004

    snapshot = _import("2026-07-22.0", FakeReader([row]))

    assert snapshot.places.get().confidence == Decimal("0.75004000")


@pytest.mark.django_db
def test_import_accepts_and_preserves_official_schema_source_identifiers() -> None:
    row = _row("official-source")
    row["sources"] = [
        {
            "property": "",
            "dataset": "metaPlaces",
            "record_id": "meta-1",
            "update_time": datetime(2026, 7, 22, 12, 30, tzinfo=UTC),
        },
        {
            "property": "/brand",
            "dataset": "msftPlaces",
            "record_id": "msft-1",
        },
        {"property": "/confidence", "dataset": "Overture", "record_id": "derived-1"},
    ]

    snapshot = _import("2026-07-22.0", FakeReader([row]))
    place = snapshot.places.get()

    assert snapshot.source_counts == {"Overture": 1, "metaPlaces": 1, "msftPlaces": 1}
    assert snapshot.source_licenses == ["CDLA-Permissive-2.0"]
    assert place.field_provenance["/"][0]["dataset"] == "metaPlaces"
    assert place.field_provenance["/"][0]["update_time"] == "2026-07-22T12:30:00+00:00"
    assert place.field_provenance["/brand"][0]["dataset"] == "msftPlaces"
    assert place.field_provenance["/confidence"][0]["dataset"] == "Overture"


@pytest.mark.django_db
def test_activation_supersedes_previous_snapshot_and_same_import_is_idempotent() -> None:
    first = _import("2026-06-17.0", FakeReader([_row("old")]))
    second = _import("2026-07-22.0", FakeReader([_row("new")]))
    unused_reader = FakeReader([_row("must-not-run")])
    repeated = _import("2026-07-22.0", unused_reader)

    first.refresh_from_db()
    assert first.status == OvertureDatasetSnapshot.Status.SUPERSEDED
    assert not first.is_active
    assert second.is_active
    assert repeated.pk == second.pk
    assert unused_reader.calls == []


@pytest.mark.django_db
def test_failed_capped_import_does_not_replace_active_snapshot() -> None:
    active = _import("2026-06-17.0", FakeReader([_row("old")]))

    with pytest.raises(OvertureImportError, match="límite"):
        _import(
            "2026-07-22.0",
            FakeReader([_row("one"), _row("two")]),
            max_places=1,
        )

    active.refresh_from_db()
    failed = OvertureDatasetSnapshot.objects.get(release_id="2026-07-22.0")
    assert active.is_active
    assert failed.status == OvertureDatasetSnapshot.Status.FAILED
    assert not failed.is_active
    assert get_active_snapshot() == active


@pytest.mark.django_db
def test_catalog_rows_and_snapshot_identity_are_immutable() -> None:
    snapshot = _import("2026-07-22.0", FakeReader([_row("inside")]))
    place = OverturePlace.objects.get()
    place.name = "Changed"
    with pytest.raises(ValidationError, match="inmutables"):
        place.save()
    snapshot.release_id = "2026-06-17.0"
    with pytest.raises(ValidationError, match="procedencia"):
        snapshot.save()


@pytest.mark.django_db
def test_import_deduplicates_overlapping_downloads_and_excludes_closed_places() -> None:
    snapshot = _import(
        "2026-07-22.0",
        FakeReader(
            [
                _row("duplicate"),
                _row("duplicate"),
                _row("closed", operating_status="permanently_closed"),
            ]
        ),
    )

    assert snapshot.place_count == 1
    assert OverturePlace.objects.values_list("overture_id", flat=True).get() == _gers("duplicate")
    assert snapshot.validation_results["duplicate_gers_ids"] == 1
    assert snapshot.validation_results["permanently_closed_excluded"] == 1


@pytest.mark.django_db
def test_schema_drift_fails_closed_and_preserves_the_active_snapshot() -> None:
    active = _import("2026-06-17.0", FakeReader([_row("old")]))
    incompatible = _row("new")
    incompatible.pop("taxonomy")
    incompatible["categories"] = {"primary": "deprecated"}

    with pytest.raises(OvertureSchemaError, match="taxonomy y basic_category"):
        _import("2026-07-22.0", FakeReader([incompatible]))

    active.refresh_from_db()
    failed = OvertureDatasetSnapshot.objects.get(release_id="2026-07-22.0")
    assert active.is_active
    assert failed.status == OvertureDatasetSnapshot.Status.FAILED
    assert failed.validation_results["passed"] is False
