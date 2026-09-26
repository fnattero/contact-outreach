from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from apps.overture import reader as reader_module
from apps.overture import releases as releases_module
from apps.overture.geometry import contains_point, validate_geojson
from apps.overture.reader import OfficialOverturePlaceReader, OvertureReaderError, validate_bbox
from apps.overture.releases import (
    OFFICIAL_STAC_URL,
    ReleaseDiscoveryError,
    _official_transport,
    discover_latest_release,
    discover_release,
    discover_releases,
)


def _square(
    west: float = 0, south: float = 0, east: float = 10, north: float = 10
) -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def _catalog(
    *,
    child_href: str = "./2026-07-22.0/catalog.json",
    latest: str = "2026-07-22.0",
) -> bytes:
    return json.dumps(
        {
            "latest": latest,
            "links": [{"rel": "child", "href": child_href}],
        }
    ).encode()


def _release_catalog(
    *, release_id: str = "2026-07-22.0", schema_version: object = "v1.18.0"
) -> bytes:
    return json.dumps(
        {
            "type": "Catalog",
            "id": release_id,
            "schema:version": schema_version,
        }
    ).encode()


def test_geojson_feature_collection_is_canonical_and_point_in_polygon_is_exact() -> None:
    geometry = validate_geojson(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"ignored": True}, "geometry": _square()},
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": _square(20, 20, 30, 30),
                },
            ],
        }
    )

    assert geometry.geojson["type"] == "MultiPolygon"
    assert geometry.bbox == (0.0, 0.0, 30.0, 30.0)
    assert len(geometry.sha256) == 64
    assert contains_point(geometry, longitude=5, latitude=5)
    assert contains_point(geometry, longitude=0, latitude=5)  # boundary is covered
    assert not contains_point(geometry, longitude=15, latitude=15)


def test_geojson_hole_is_excluded_and_invalid_geometry_is_rejected() -> None:
    geometry = validate_geojson(
        {
            "type": "Polygon",
            "coordinates": [
                _square()["coordinates"][0],  # type: ignore[index]
                [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]],
            ],
        }
    )
    assert not contains_point(geometry, longitude=5, latitude=5)
    assert contains_point(geometry, longitude=2, latitude=5)
    with pytest.raises(ValidationError):
        validate_geojson({"type": "LineString", "coordinates": [[0, 0], [1, 1]]})
    with pytest.raises(ValidationError):
        validate_geojson({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]})


def test_release_discovery_uses_only_official_catalog_and_confirms_membership() -> None:
    calls: list[tuple[str, float, int]] = []

    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        calls.append((url, timeout_seconds, max_bytes))
        return _release_catalog() if url.endswith("/2026-07-22.0/catalog.json") else _catalog()

    catalog = discover_releases(transport=transport)
    latest = discover_latest_release(transport=transport)

    assert calls[0][0] == OFFICIAL_STAC_URL
    assert catalog.latest_release == "2026-07-22.0"
    assert latest.release_id == "2026-07-22.0"
    assert latest.schema_version == "v1.18.0"
    assert latest.taxonomy_version == "2026-07-22.0"
    assert latest.catalog_url.endswith("/2026-07-22.0/catalog.json")
    assert latest.source_uri.startswith("s3://overturemaps-us-west-2/release/")


def test_release_discovery_rejects_foreign_child_url() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del url, timeout_seconds, max_bytes
        return _catalog(child_href="https://attacker.example/2026-07-22.0/catalog.json")

    with pytest.raises(ReleaseDiscoveryError, match="host STAC oficial"):
        discover_releases(transport=transport)


def test_release_discovery_uses_reviewed_schema_when_official_stac_value_is_null() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        return _release_catalog(schema_version=None) if url != OFFICIAL_STAC_URL else _catalog()

    descriptor = discover_latest_release(transport=transport)

    assert descriptor.release_id == "2026-07-22.0"
    assert descriptor.schema_version == "v1.18.0"


def test_release_discovery_rejects_unknown_release_even_when_stac_schema_is_null() -> None:
    release_id = "2026-08-19.0"

    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        if url == OFFICIAL_STAC_URL:
            return _catalog(child_href=f"./{release_id}/catalog.json", latest=release_id)
        return _release_catalog(release_id=release_id, schema_version=None)

    with pytest.raises(ReleaseDiscoveryError, match="todavía no fue revisado"):
        discover_release(release_id, transport=transport)


def test_release_discovery_rejects_schema_that_differs_from_reviewed_version() -> None:
    def transport(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
        del timeout_seconds, max_bytes
        return (
            _release_catalog(schema_version="v1.19.0") if url != OFFICIAL_STAC_URL else _catalog()
        )

    with pytest.raises(ReleaseDiscoveryError, match="no coincide"):
        discover_latest_release(transport=transport)


class _TransportResponse:
    status = 200

    def __enter__(self) -> _TransportResponse:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return OFFICIAL_STAC_URL

    def read(self, size: int) -> bytes:
        assert size == 65
        return b"{}"


def test_official_transport_fetches_bounded_bytes_without_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = SimpleNamespace(open=lambda request, timeout: _TransportResponse())
    monkeypatch.setattr(releases_module.urllib.request, "build_opener", lambda handler: opener)

    assert _official_transport(OFFICIAL_STAC_URL, timeout_seconds=2, max_bytes=64) == b"{}"


@pytest.mark.parametrize(
    "bbox",
    [
        None,
        (0, 0, True, 1),
        (0, 0, float("nan"), 1),
        (-181, 0, 1, 1),
    ],
)
def test_reader_rejects_invalid_bounding_boxes(bbox: object) -> None:
    with pytest.raises(ValidationError, match="bbox Overture"):
        validate_bbox(bbox)


class _Batch:
    def to_pylist(self) -> list[object]:
        return [{"id": "place-1"}]


class _Schema:
    def __init__(self, names: list[str]) -> None:
        self.names = names


class _SchemaBatch(_Batch):
    def __init__(self, names: list[str]) -> None:
        self.schema = _Schema(names)


def test_official_reader_streams_injected_record_batches() -> None:
    calls: list[dict[str, object]] = []

    def factory(overture_type: str, **kwargs: object) -> Iterator[object]:
        calls.append({"type": overture_type, **kwargs})
        yield _Batch()

    reader = OfficialOverturePlaceReader(batch_reader_factory=factory)  # type: ignore[arg-type]
    rows = list(reader.iter_places(release_id="2026-07-22.0", bbox=(-1, -1, 1, 1)))

    assert rows == [{"id": "place-1"}]
    assert calls[0]["release"] == "2026-07-22.0"
    assert calls[0]["stac"] is True


def test_official_reader_rejects_invalid_batch() -> None:
    def factory(overture_type: str, **kwargs: object) -> list[object]:
        del overture_type, kwargs
        return [object()]

    reader = OfficialOverturePlaceReader(batch_reader_factory=factory)  # type: ignore[arg-type]
    with pytest.raises(OvertureReaderError):
        list(reader.iter_places(release_id="2026-07-22.0", bbox=(-1, -1, 1, 1)))


def test_official_reader_rejects_deprecated_only_arrow_schema_before_rows() -> None:
    def factory(overture_type: str, **kwargs: object) -> list[object]:
        del overture_type, kwargs
        return [_SchemaBatch(["id", "geometry", "names", "categories", "sources"])]

    reader = OfficialOverturePlaceReader(batch_reader_factory=factory)  # type: ignore[arg-type]

    with pytest.raises(OvertureReaderError, match="taxonomy y basic_category"):
        list(reader.iter_places(release_id="2026-07-22.0", bbox=(-1, -1, 1, 1)))


@pytest.mark.parametrize("timeout", [0, -1])
def test_official_reader_rejects_non_positive_timeouts(timeout: int) -> None:
    with pytest.raises(ValidationError, match="timeouts Overture"):
        OfficialOverturePlaceReader(connect_timeout=timeout)


def test_official_reader_loads_the_official_package_when_no_factory_is_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(overture_type: str, **kwargs: object) -> list[object]:
        del overture_type, kwargs
        return [{"id": "direct-row"}]

    monkeypatch.setattr(
        reader_module.importlib,
        "import_module",
        lambda name: SimpleNamespace(record_batch_reader=factory),
    )

    rows = list(
        OfficialOverturePlaceReader().iter_places(release_id="2026-07-22.0", bbox=(-1, -1, 1, 1))
    )

    assert rows == [{"id": "direct-row"}]


@pytest.mark.parametrize(
    ("module", "message"),
    [
        (SimpleNamespace(), "no está instalado correctamente"),
        (SimpleNamespace(record_batch_reader=None), "no está instalado correctamente"),
    ],
)
def test_official_reader_rejects_a_broken_official_package(
    monkeypatch: pytest.MonkeyPatch, module: object, message: str
) -> None:
    monkeypatch.setattr(reader_module.importlib, "import_module", lambda name: module)

    with pytest.raises(OvertureReaderError, match=message):
        list(
            OfficialOverturePlaceReader().iter_places(
                release_id="2026-07-22.0", bbox=(-1, -1, 1, 1)
            )
        )


@pytest.mark.parametrize(
    ("reader_value", "message"),
    [
        (None, "no pudo abrir"),
        ([type("BadRows", (), {"to_pylist": lambda self: "not-a-list"})()], "filas inválidas"),
        ([type("BadRow", (), {"to_pylist": lambda self: [object()]})()], "fila inválida"),
        ([type("BadKey", (), {"to_pylist": lambda self: [{1: "value"}]})()], "claves inválidas"),
    ],
)
def test_official_reader_rejects_malformed_reader_results(
    reader_value: object, message: str
) -> None:
    def factory(overture_type: str, **kwargs: object) -> object:
        del overture_type, kwargs
        return reader_value

    reader = OfficialOverturePlaceReader(batch_reader_factory=factory)  # type: ignore[arg-type]

    with pytest.raises(OvertureReaderError, match=message):
        list(reader.iter_places(release_id="2026-07-22.0", bbox=(-1, -1, 1, 1)))


def test_official_reader_rejects_non_iterable_arrow_schema_names() -> None:
    batch = _SchemaBatch([])
    batch.schema.names = 1  # type: ignore[assignment]

    def factory(overture_type: str, **kwargs: object) -> list[object]:
        del overture_type, kwargs
        return [batch]

    reader = OfficialOverturePlaceReader(batch_reader_factory=factory)  # type: ignore[arg-type]

    with pytest.raises(OvertureReaderError, match="esquema inválido"):
        list(reader.iter_places(release_id="2026-07-22.0", bbox=(-1, -1, 1, 1)))
