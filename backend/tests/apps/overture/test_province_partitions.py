from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

from apps.configuration.models import SearchZone
from apps.overture.models import OvertureCoveragePartition, OvertureRelease
from apps.overture.reader import BBox
from apps.overture.releases import ReleaseDescriptor
from apps.overture.services import sync_province_coverages


class RecordingReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, BBox]] = []

    def iter_places(self, *, release_id: str, bbox: BBox) -> Iterator[Mapping[str, object]]:
        self.calls.append((release_id, bbox))
        return iter(())


def _descriptor() -> ReleaseDescriptor:
    release_id = "2026-07-22.0"
    return ReleaseDescriptor(
        release_id=release_id,
        schema_version="v1.18.0",
        taxonomy_version=release_id,
        source_uri=(f"s3://overturemaps-us-west-2/release/{release_id}/theme=places/type=place/"),
        catalog_url=f"https://stac.overturemaps.org/{release_id}/catalog.json",
        manifest_sha256="a" * 64,
    )


@pytest.mark.django_db
def test_two_distant_provinces_use_two_bounded_reads_and_one_release() -> None:
    reader = RecordingReader()
    province_codes = ("38", "94")  # Jujuy and Tierra del Fuego.

    partitions = sync_province_coverages(
        province_codes,
        descriptor=_descriptor(),
        reader=reader,
    )

    provinces = {
        zone.official_code: zone
        for zone in SearchZone.objects.filter(
            level=SearchZone.Level.PROVINCE,
            official_code__in=province_codes,
        )
    }
    assert len(reader.calls) == 2
    assert [call[0] for call in reader.calls] == ["2026-07-22.0", "2026-07-22.0"]
    assert reader.calls[0][1] == tuple(provinces["38"].boundary_bbox)
    assert reader.calls[1][1] == tuple(provinces["94"].boundary_bbox)
    assert {partition.release_id for partition in partitions} == {OvertureRelease.objects.get().pk}
    assert {partition.province_code for partition in partitions} == set(province_codes)
    assert all(
        partition.status == OvertureCoveragePartition.Status.READY and partition.is_active
        for partition in partitions
    )
    assert OvertureCoveragePartition.objects.filter(is_active=True).count() == 2
    for partition in partitions:
        assert (
            partition.zones.count()
            == SearchZone.objects.filter(
                parent=partition.province,
                selectable=True,
                active=True,
                archived_at__isnull=True,
            ).count()
        )


@pytest.mark.django_db
def test_reimport_replaces_only_the_same_province_partition() -> None:
    reader = RecordingReader()
    first = sync_province_coverages(("38", "94"), descriptor=_descriptor(), reader=reader)
    jujuy_first, tierra_del_fuego = first

    # A changed local boundary revision makes this a distinct import while the
    # official release remains the same.
    jujuy_district = SearchZone.objects.filter(parent=jujuy_first.province).first()
    assert jujuy_district is not None
    jujuy_district.boundary_revision += 1
    jujuy_district.save(update_fields=("boundary_revision", "updated_at"))
    jujuy_second = sync_province_coverages(
        ("38",),
        descriptor=_descriptor(),
        reader=reader,
    )[0]

    jujuy_first.refresh_from_db()
    tierra_del_fuego.refresh_from_db()
    assert jujuy_first.status == OvertureCoveragePartition.Status.SUPERSEDED
    assert not jujuy_first.is_active
    assert jujuy_second.status == OvertureCoveragePartition.Status.READY
    assert jujuy_second.is_active
    assert tierra_del_fuego.status == OvertureCoveragePartition.Status.READY
    assert tierra_del_fuego.is_active
