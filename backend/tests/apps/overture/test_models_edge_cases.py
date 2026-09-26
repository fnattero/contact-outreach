from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.overture.geometry import validate_geojson
from apps.overture.importer import OVERTURE_ATTRIBUTION
from apps.overture.models import (
    OvertureCoveragePartition,
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
    OvertureRelease,
    OvertureReleaseCheck,
    OvertureTaxonomyCode,
)
from apps.overture.releases import official_places_source_uri, official_release_catalog_url


def _square() -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[-59, -35], [-58, -35], [-58, -34], [-59, -34], [-59, -35]]],
    }


def _release(release_id: str = "2026-07-22.0") -> OvertureRelease:
    return OvertureRelease.objects.create(
        release_id=release_id,
        schema_version="v1.18.0",
        taxonomy_version=release_id,
        importer_version="importer-v1",
        mapping_version="mapping-v1",
        source_uri=official_places_source_uri(release_id),
        catalog_url=official_release_catalog_url(release_id),
        manifest_sha256="a" * 64,
        metadata_sha256="b" * 64,
        attribution=OVERTURE_ATTRIBUTION,
    )


def _snapshot(
    *,
    release_id: str = "2026-07-22.0",
    status: str = OvertureDatasetSnapshot.Status.IMPORTING,
    is_active: bool = False,
    province_code: str = "",
    province_name: str = "",
) -> OvertureDatasetSnapshot:
    return OvertureDatasetSnapshot.objects.create(
        release_id=release_id,
        province_code=province_code,
        province_name=province_name,
        schema_version="v1.18.0",
        taxonomy_version=release_id,
        importer_version="importer-v1",
        mapping_version="mapping-v1",
        boundary_version="boundary-v1",
        boundary_manifest_sha256="b" * 64,
        source_uri=official_places_source_uri(release_id),
        manifest_sha256="a" * 64,
        status=status,
        is_active=is_active,
        attribution=OVERTURE_ATTRIBUTION,
    )


# --------------------------------------------------------------------------
# ImmutableCatalogQuerySet / ImmutableCatalogModel
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_immutable_catalog_queryset_rejects_update() -> None:
    snapshot = _snapshot()
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

    with pytest.raises(ValidationError, match="inmutables"):
        OvertureDatasetZone.objects.filter(pk=zone.pk).update(name="Rewritten")


@pytest.mark.django_db
def test_immutable_catalog_bulk_create_rejects_update_conflicts() -> None:
    snapshot = _snapshot()
    zone = OvertureDatasetZone(
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

    with pytest.raises(ValidationError, match="inmutables"):
        OvertureDatasetZone.objects.bulk_create([zone], update_conflicts=True)


# --------------------------------------------------------------------------
# OvertureRelease
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_release_str_and_a_mutable_field_update_succeeds() -> None:
    release = _release()
    assert str(release) == "2026-07-22.0"

    release.attribution = "Updated attribution text"
    release.save()

    release.refresh_from_db()
    assert release.attribution == "Updated attribution text"


@pytest.mark.django_db
def test_release_identity_fields_are_immutable() -> None:
    release = _release()
    release.manifest_sha256 = "c" * 64

    with pytest.raises(ValidationError, match="identidad del release Overture es inmutable"):
        release.save()


@pytest.mark.django_db
def test_release_cannot_be_deleted() -> None:
    release = _release()

    with pytest.raises(ValidationError, match="no se pueden eliminar"):
        release.delete()


# --------------------------------------------------------------------------
# OvertureDatasetSnapshot
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_snapshot_queryset_delete_and_bulk_update_are_rejected() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError, match="no se pueden eliminar"):
        OvertureDatasetSnapshot.objects.filter(pk=snapshot.pk).delete()

    with pytest.raises(ValidationError, match="actualizaciones masivas"):
        OvertureDatasetSnapshot.objects.bulk_update([snapshot], ["error"])


@pytest.mark.django_db
def test_snapshot_release_property_aliases_release_id() -> None:
    snapshot = _snapshot()
    assert snapshot.release == snapshot.release_id


@pytest.mark.django_db
def test_snapshot_rejects_an_invalid_status_transition_from_importing() -> None:
    snapshot = _snapshot(status=OvertureDatasetSnapshot.Status.IMPORTING)
    snapshot.status = OvertureDatasetSnapshot.Status.SUPERSEDED

    with pytest.raises(ValidationError, match="transición del snapshot Overture no es válida"):
        snapshot.save()


@pytest.mark.django_db
def test_snapshot_instance_cannot_be_deleted() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError, match="no se pueden eliminar"):
        snapshot.delete()


@pytest.mark.django_db
def test_snapshot_str_includes_province_when_present() -> None:
    without_province = _snapshot(province_code="", province_name="")
    with_province = _snapshot(release_id="2026-06-17.0", province_code="02", province_name="CABA")

    assert " · " not in str(without_province).split("IMPORTING")[0].replace(
        without_province.release_id, ""
    ) or "CABA" not in str(without_province)
    assert "CABA" in str(with_province)


# --------------------------------------------------------------------------
# OvertureCoveragePartition
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_partition_clean_rejects_a_non_province_or_mismatched_search_zone() -> None:
    from apps.configuration.models import SearchZone

    release = _release()
    snapshot = _snapshot()
    district = SearchZone.objects.filter(level=SearchZone.Level.DISTRICT).first()
    assert district is not None

    partition = OvertureCoveragePartition(
        release=release,
        province=district,
        province_code=district.official_code,
        province_name=district.name,
        province_bbox=[-1, -1, 1, 1],
        snapshot=snapshot,
        boundary_version="boundary-v1",
        boundary_manifest_sha256="d" * 64,
    )

    with pytest.raises(ValidationError, match="pertenecer a una provincia"):
        partition.clean()


@pytest.mark.django_db
def test_partition_clean_rejects_a_province_code_mismatch() -> None:
    from apps.configuration.models import SearchZone

    release = _release()
    snapshot = _snapshot()
    province = SearchZone.objects.filter(level=SearchZone.Level.PROVINCE).first()
    assert province is not None

    partition = OvertureCoveragePartition(
        release=release,
        province=province,
        province_code="not-the-real-code",
        province_name=province.name,
        province_bbox=[-1, -1, 1, 1],
        snapshot=snapshot,
        boundary_version="boundary-v1",
        boundary_manifest_sha256="d" * 64,
    )

    with pytest.raises(ValidationError, match="no coincide con la provincia"):
        partition.clean()


@pytest.mark.django_db
def test_partition_clean_rejects_a_release_mismatch_with_its_snapshot() -> None:
    release = _release("2026-07-22.0")
    other_release_snapshot = _snapshot(release_id="2026-06-17.0")

    partition = OvertureCoveragePartition(
        release=release,
        province_code="02",
        province_name="CABA",
        province_bbox=[-1, -1, 1, 1],
        snapshot=other_release_snapshot,
        boundary_version="boundary-v1",
        boundary_manifest_sha256="d" * 64,
    )

    with pytest.raises(ValidationError, match="mismo release"):
        partition.clean()


@pytest.mark.django_db
def test_partition_clean_rejects_a_snapshot_belonging_to_another_province() -> None:
    release = _release()
    snapshot = _snapshot(province_code="94", province_name="Tierra del Fuego")

    partition = OvertureCoveragePartition(
        release=release,
        province_code="02",
        province_name="CABA",
        province_bbox=[-1, -1, 1, 1],
        snapshot=snapshot,
        boundary_version="boundary-v1",
        boundary_manifest_sha256="d" * 64,
    )

    with pytest.raises(ValidationError, match="otra provincia"):
        partition.clean()


@pytest.mark.django_db
def test_partition_clean_accepts_a_fully_consistent_setup() -> None:
    from apps.configuration.models import SearchZone

    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    province = SearchZone.objects.filter(
        level=SearchZone.Level.PROVINCE, official_code="02"
    ).first()
    assert province is not None

    partition = OvertureCoveragePartition(
        release=release,
        province=province,
        province_code="02",
        province_name=province.name,
        province_bbox=[-1, -1, 1, 1],
        snapshot=snapshot,
        boundary_version="boundary-v1",
        boundary_manifest_sha256="d" * 64,
    )

    partition.clean()  # must not raise


def _partition(
    *,
    release: OvertureRelease,
    snapshot: OvertureDatasetSnapshot,
    status: str = "IMPORTING",
    is_active: bool = False,
) -> OvertureCoveragePartition:
    return OvertureCoveragePartition.objects.create(
        release=release,
        province_code=snapshot.province_code or "02",
        province_name=snapshot.province_name or "CABA",
        province_bbox=[-1, -1, 1, 1],
        snapshot=snapshot,
        boundary_version="boundary-v1",
        boundary_manifest_sha256="d" * 64,
        status=status,
        is_active=is_active,
    )


@pytest.mark.django_db
def test_partition_str_and_immutable_field_change() -> None:
    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    partition = _partition(release=release, snapshot=snapshot)
    assert "CABA" in str(partition)

    partition.boundary_version = "changed"
    with pytest.raises(ValidationError, match="identidad de la partición Overture es inmutable"):
        partition.save()


@pytest.mark.django_db
def test_partition_rejects_invalid_transition_from_importing() -> None:
    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    partition = _partition(release=release, snapshot=snapshot)

    partition.status = OvertureCoveragePartition.Status.SUPERSEDED
    with pytest.raises(ValidationError, match="transición de la partición Overture no es válida"):
        partition.save()


@pytest.mark.django_db
def test_partition_rejects_changes_once_ready_beyond_supersede() -> None:
    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    partition = _partition(
        release=release,
        snapshot=snapshot,
        status=OvertureCoveragePartition.Status.READY,
        is_active=True,
    )

    partition.province_name = "Rewritten"
    with pytest.raises(ValidationError, match="identidad de la partición Overture es inmutable"):
        partition.save()


@pytest.mark.django_db
def test_partition_rejects_any_change_once_terminal() -> None:
    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    partition = _partition(
        release=release,
        snapshot=snapshot,
        status=OvertureCoveragePartition.Status.FAILED,
    )

    with pytest.raises(ValidationError, match="terminal es inmutable"):
        partition.save()


@pytest.mark.django_db
def test_partition_cannot_be_deleted() -> None:
    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    partition = _partition(release=release, snapshot=snapshot)

    with pytest.raises(ValidationError, match="no se pueden eliminar"):
        partition.delete()


# --------------------------------------------------------------------------
# OvertureDatasetZone / OverturePlace __str__
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_zone_and_place_str_representations() -> None:
    snapshot = _snapshot()
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
    place = OverturePlace.objects.create(
        snapshot=snapshot,
        overture_id="place-1",
        name="Taller Fixture",
        normalized_name="taller fixture",
        name_search=" taller fixture ",
        latitude=Decimal("-34.5000000"),
        longitude=Decimal("-58.5000000"),
        sources=[{"dataset": "meta", "record_id": "1"}],
        field_provenance={"/": [{"dataset": "meta", "record_id": "1"}]},
        source_licenses=["CDLA-Permissive-2.0"],
        license="CDLA-Permissive-2.0",
        source_payload_hash="c" * 64,
    )

    assert "Palermo" in str(zone) and snapshot.release_id in str(zone)
    assert "Taller Fixture" in str(place) and "place-1" in str(place)


# --------------------------------------------------------------------------
# OverturePlaceZone.clean()
# --------------------------------------------------------------------------


def _place_and_zone(
    snapshot: OvertureDatasetSnapshot,
    *,
    place_partition: OvertureCoveragePartition | None = None,
    zone_partition: OvertureCoveragePartition | None = None,
) -> tuple[OverturePlace, OvertureDatasetZone]:
    geometry = validate_geojson(_square())
    zone = OvertureDatasetZone.objects.create(
        snapshot=snapshot,
        partition=zone_partition,
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
        snapshot=snapshot,
        partition=place_partition,
        overture_id=str(uuid.uuid4()),
        name="Fixture",
        normalized_name="fixture",
        name_search=" fixture ",
        latitude=Decimal("-34.5000000"),
        longitude=Decimal("-58.5000000"),
        sources=[{"dataset": "meta", "record_id": "1"}],
        field_provenance={"/": [{"dataset": "meta", "record_id": "1"}]},
        source_licenses=["CDLA-Permissive-2.0"],
        license="CDLA-Permissive-2.0",
        source_payload_hash="c" * 64,
    )
    return place, zone


@pytest.mark.django_db
def test_place_zone_clean_rejects_a_place_from_another_snapshot() -> None:
    snapshot = _snapshot()
    other_snapshot = _snapshot(release_id="2026-06-17.0")
    place, zone = _place_and_zone(snapshot)

    link = OverturePlaceZone(snapshot=other_snapshot, place=place, zone=zone)
    with pytest.raises(ValidationError, match="mismo snapshot"):
        link.clean()


@pytest.mark.django_db
def test_place_zone_clean_rejects_a_zone_from_another_snapshot() -> None:
    snapshot = _snapshot()
    other_snapshot = _snapshot(release_id="2026-06-17.0")
    place, _zone = _place_and_zone(snapshot)
    _other_place, other_zone = _place_and_zone(other_snapshot)

    link = OverturePlaceZone(snapshot=snapshot, place=place, zone=other_zone)
    with pytest.raises(ValidationError, match="mismo snapshot"):
        link.clean()


@pytest.mark.django_db
def test_place_zone_clean_rejects_a_place_from_another_partition() -> None:
    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    partition = _partition(release=release, snapshot=snapshot)
    place, zone = _place_and_zone(snapshot, place_partition=partition)

    link = OverturePlaceZone(snapshot=snapshot, place=place, zone=zone)
    with pytest.raises(ValidationError, match="misma provincia"):
        link.clean()


@pytest.mark.django_db
def test_place_zone_clean_rejects_a_zone_from_another_partition() -> None:
    release = _release()
    snapshot = _snapshot(province_code="02", province_name="CABA")
    partition = _partition(release=release, snapshot=snapshot)
    place, zone = _place_and_zone(snapshot, zone_partition=partition)

    link = OverturePlaceZone(snapshot=snapshot, place=place, zone=zone)
    with pytest.raises(ValidationError, match="misma provincia"):
        link.clean()


@pytest.mark.django_db
def test_place_zone_save_rejects_existing_immutable_link() -> None:
    snapshot = _snapshot()
    place, zone = _place_and_zone(snapshot)
    link = OverturePlaceZone.objects.create(snapshot=snapshot, place=place, zone=zone)

    with pytest.raises(ValidationError, match="catálogo Overture son inmutables"):
        link.save(update_fields=("updated_at",))


# --------------------------------------------------------------------------
# OvertureTaxonomyCode / OvertureReleaseCheck __str__
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_taxonomy_code_and_release_check_str_representations() -> None:
    snapshot = _snapshot()
    taxonomy_code = OvertureTaxonomyCode.objects.create(snapshot=snapshot, code="hardware_store")
    check = OvertureReleaseCheck.objects.create(
        status=OvertureReleaseCheck.Status.SUCCEEDED,
        catalog_url="https://stac.overturemaps.org/catalog.json",
        releases=["2026-07-22.0"],
        latest_release="2026-07-22.0",
        manifest_sha256="a" * 64,
    )

    assert str(taxonomy_code) == "hardware_store"
    assert str(check) == "2026-07-22.0"


# --------------------------------------------------------------------------
# _validate_bulk_place_zone_membership
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_place_zone_link_rejects_a_nonexistent_place_or_zone() -> None:
    snapshot = _snapshot()
    _place, zone = _place_and_zone(snapshot)

    with pytest.raises(ValidationError, match="lugar o zona inexistente"):
        OverturePlaceZone.objects.bulk_create(
            [
                OverturePlaceZone(
                    snapshot=snapshot,
                    place_id=uuid.uuid4(),
                    zone=zone,
                )
            ]
        )
