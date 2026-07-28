from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.overture.geometry import validate_geojson
from apps.overture.importer import OVERTURE_ATTRIBUTION, activate_snapshot
from apps.overture.models import (
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
    OvertureReleaseCheck,
    OvertureTaxonomyCode,
)
from apps.overture.releases import OFFICIAL_STAC_URL


def _square() -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[-59, -35], [-58, -35], [-58, -34], [-59, -34], [-59, -35]]],
    }


def _manifest_hash(code: str, boundary_hash: str) -> str:
    payload = json.dumps(
        [{"code": code, "boundary_hash": boundary_hash}],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _snapshot(
    *,
    status: str,
    is_active: bool = False,
    boundary_manifest_sha256: str = "b" * 64,
) -> OvertureDatasetSnapshot:
    return OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="importer-v1",
        mapping_version="mapping-v1",
        boundary_version="zones-v1",
        boundary_manifest_sha256=boundary_manifest_sha256,
        source_uri=("s3://overturemaps-us-west-2/release/2026-07-22.0/theme=places/type=place/"),
        manifest_sha256="a" * 64,
        status=status,
        is_active=is_active,
        attribution=OVERTURE_ATTRIBUTION,
    )


@pytest.mark.django_db
def test_queryset_update_cannot_bypass_snapshot_provenance_immutability() -> None:
    snapshot = _snapshot(status=OvertureDatasetSnapshot.Status.READY, is_active=True)

    with pytest.raises(ValidationError, match="actualizaciones masivas"):
        OvertureDatasetSnapshot.objects.filter(pk=snapshot.pk).update(
            source_uri="s3://attacker.invalid/places/",
            manifest_sha256="0" * 64,
        )

    snapshot.refresh_from_db()
    assert snapshot.source_uri.startswith("s3://overturemaps-us-west-2/")
    assert snapshot.manifest_sha256 == "a" * 64


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,is_active",
    [
        (OvertureDatasetSnapshot.Status.READY, True),
        (OvertureDatasetSnapshot.Status.FAILED, False),
        (OvertureDatasetSnapshot.Status.SUPERSEDED, False),
    ],
)
def test_terminal_snapshot_validation_and_attribution_cannot_be_rewritten(
    status: str,
    is_active: bool,
) -> None:
    snapshot = _snapshot(status=status, is_active=is_active)
    snapshot.attribution = "rewritten"
    snapshot.validation_results = {"passed": False}

    with pytest.raises(ValidationError, match="snapshot Overture"):
        snapshot.save(update_fields=("attribution", "validation_results", "updated_at"))

    snapshot.refresh_from_db()
    assert snapshot.attribution == OVERTURE_ATTRIBUTION
    assert snapshot.validation_results == {}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        OvertureDatasetSnapshot.Status.FAILED,
        OvertureDatasetSnapshot.Status.SUPERSEDED,
    ],
)
def test_terminal_snapshot_rejects_even_a_noop_save(status: str) -> None:
    snapshot = _snapshot(status=status)
    updated_at = snapshot.updated_at

    with pytest.raises(ValidationError, match="terminal es inmutable"):
        snapshot.save()

    snapshot.refresh_from_db()
    assert snapshot.updated_at == updated_at


@pytest.mark.django_db
def test_catalog_records_reject_instance_and_bulk_mutation_or_deletion() -> None:
    snapshot = _snapshot(status=OvertureDatasetSnapshot.Status.IMPORTING)
    zone = OvertureDatasetZone.objects.create(
        snapshot=snapshot,
        code="palermo",
        name="Palermo",
        normalized_name="palermo",
        geometry={"type": "Polygon", "coordinates": []},
        bbox=[-58.5, -34.7, -58.3, -34.5],
        boundary_hash="c" * 64,
        source="fixture",
        source_version="1",
    )

    zone.name = "Rewritten"
    with pytest.raises(ValidationError, match="inmutables"):
        OvertureDatasetZone.objects.bulk_update([zone], ["name"])
    with pytest.raises(ValidationError, match="no se pueden eliminar"):
        zone.delete()
    with pytest.raises(ValidationError, match="no se pueden eliminar"):
        OvertureDatasetZone.objects.filter(pk=zone.pk).delete()

    zone.refresh_from_db()
    assert zone.name == "Palermo"


def _place(snapshot: OvertureDatasetSnapshot, identifier: str) -> OverturePlace:
    return OverturePlace(
        snapshot=snapshot,
        overture_id=identifier,
        name=f"Place {identifier}",
        normalized_name=f"place {identifier}",
        name_search=f" place {identifier} ",
        latitude=Decimal("-34.5000000"),
        longitude=Decimal("-58.5000000"),
        sources=[{"dataset": "meta", "record_id": identifier}],
        field_provenance={"/": [{"dataset": "meta", "record_id": identifier}]},
        source_licenses=["CDLA-Permissive-2.0"],
        license="CDLA-Permissive-2.0",
        source_payload_hash=identifier.rjust(64, "0"),
    )


@pytest.mark.django_db
def test_ready_snapshot_rejects_new_catalog_membership_including_bulk_create() -> None:
    geometry = validate_geojson(_square())
    snapshot = _snapshot(
        status=OvertureDatasetSnapshot.Status.IMPORTING,
        boundary_manifest_sha256=_manifest_hash("palermo", geometry.sha256),
    )
    zone = OvertureDatasetZone.objects.create(
        snapshot=snapshot,
        code="palermo",
        name="Palermo",
        normalized_name="palermo",
        geometry=geometry.geojson,
        bbox=list(geometry.bbox),
        boundary_hash=geometry.sha256,
        source="fixture",
        source_version="1",
    )
    place = _place(snapshot, "1")
    place.save()
    OverturePlaceZone.objects.create(snapshot=snapshot, place=place, zone=zone)
    OvertureTaxonomyCode.objects.create(snapshot=snapshot, code="services_and_business")
    snapshot.streamed_count = 1
    snapshot.place_count = 1
    snapshot.zone_count = 1
    snapshot.taxonomy_code_count = 1
    snapshot.source_counts = {"meta": 1}
    snapshot.source_licenses = ["CDLA-Permissive-2.0"]
    snapshot.validation_results = {"passed": True}
    snapshot.save(
        update_fields=(
            "streamed_count",
            "place_count",
            "zone_count",
            "taxonomy_code_count",
            "source_counts",
            "source_licenses",
            "validation_results",
            "updated_at",
        )
    )
    snapshot = activate_snapshot(snapshot.pk)

    with pytest.raises(ValidationError, match="en importación"):
        OvertureDatasetZone.objects.create(
            snapshot=snapshot,
            code="belgrano",
            name="Belgrano",
            normalized_name="belgrano",
            geometry={"type": "Polygon", "coordinates": []},
            bbox=[-58.5, -34.7, -58.3, -34.5],
            boundary_hash="d" * 64,
            source="fixture",
            source_version="1",
        )
    with pytest.raises(ValidationError, match="en importación"):
        OverturePlace.objects.bulk_create([_place(snapshot, "2")])
    with pytest.raises(ValidationError, match="en importación"):
        OverturePlaceZone.objects.bulk_create(
            [OverturePlaceZone(snapshot=snapshot, place=place, zone=zone)]
        )
    with pytest.raises(ValidationError, match="en importación"):
        OvertureTaxonomyCode.objects.bulk_create(
            [OvertureTaxonomyCode(snapshot=snapshot, code="hardware_store")]
        )

    assert snapshot.zones.count() == 1
    assert snapshot.places.count() == 1
    assert snapshot.place_zone_links.count() == 1
    assert snapshot.taxonomy_codes.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,is_active",
    [
        (OvertureDatasetSnapshot.Status.READY, True),
        (OvertureDatasetSnapshot.Status.FAILED, False),
        (OvertureDatasetSnapshot.Status.SUPERSEDED, False),
    ],
)
def test_every_terminal_snapshot_rejects_new_membership(status: str, is_active: bool) -> None:
    snapshot = _snapshot(status=status, is_active=is_active)

    with pytest.raises(ValidationError, match="en importación"):
        OvertureTaxonomyCode.objects.create(snapshot=snapshot, code="hardware_store")


@pytest.mark.django_db
def test_bulk_place_zone_link_rejects_cross_snapshot_membership() -> None:
    first = _snapshot(status=OvertureDatasetSnapshot.Status.IMPORTING)
    second = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-23.0",
        schema_version="1.0.0",
        taxonomy_version="2026-07-23.0",
        importer_version="importer-v1",
        mapping_version="mapping-v1",
        boundary_version="zones-v2",
        boundary_manifest_sha256="d" * 64,
        source_uri=("s3://overturemaps-us-west-2/release/2026-07-23.0/theme=places/type=place/"),
        manifest_sha256="e" * 64,
    )
    place = _place(first, "1")
    place.save()
    zone = OvertureDatasetZone.objects.create(
        snapshot=second,
        code="belgrano",
        name="Belgrano",
        normalized_name="belgrano",
        geometry={"type": "Polygon", "coordinates": []},
        bbox=[-58.5, -34.7, -58.3, -34.5],
        boundary_hash="f" * 64,
        source="fixture",
        source_version="1",
    )

    with pytest.raises(ValidationError, match="mismo snapshot"):
        OverturePlaceZone.objects.bulk_create(
            [OverturePlaceZone(snapshot=first, place=place, zone=zone)]
        )


@pytest.mark.django_db
def test_release_checks_remain_append_only_but_are_not_snapshot_members() -> None:
    checks = OvertureReleaseCheck.objects.bulk_create(
        [
            OvertureReleaseCheck(
                status=OvertureReleaseCheck.Status.SUCCEEDED,
                catalog_url=OFFICIAL_STAC_URL,
                releases=["2026-07-22.0"],
                latest_release="2026-07-22.0",
                manifest_sha256="a" * 64,
            )
        ]
    )

    assert len(checks) == 1
    assert OvertureReleaseCheck.objects.count() == 1
