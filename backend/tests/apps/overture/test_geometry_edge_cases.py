from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.overture.geometry import (
    _coordinate,
    _extract_polygons,
    _GeometryBudget,
    _mapping,
    _polygon,
    _position,
    _ring,
    _sequence,
    _validated_shape,
    contains_point,
    validate_geojson,
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


def test_feature_budget_rejects_too_many_features() -> None:
    budget = _GeometryBudget()
    for _ in range(100):
        budget.add_feature()
    with pytest.raises(ValidationError, match="features"):
        budget.add_feature()


def test_mapping_rejects_non_mapping_and_non_string_keys() -> None:
    with pytest.raises(ValidationError, match="objeto GeoJSON"):
        _mapping(["not", "a", "mapping"], label="El GeoJSON")
    with pytest.raises(ValidationError, match="clave inválida"):
        _mapping({1: "value"}, label="El GeoJSON")


def test_sequence_rejects_non_sequence_values() -> None:
    with pytest.raises(ValidationError, match="debe ser una lista"):
        _sequence({"not": "a-list"}, label="Un anillo")
    with pytest.raises(ValidationError, match="debe ser una lista"):
        _sequence("a-string-is-rejected", label="Un anillo")


def test_coordinate_rejects_non_numeric_and_boolean_values() -> None:
    with pytest.raises(ValidationError, match="números finitos"):
        _coordinate("not-a-number", longitude=True)
    with pytest.raises(ValidationError, match="números finitos"):
        _coordinate(True, longitude=True)


def test_coordinate_rejects_non_finite_reals_directly() -> None:
    # ``validate_geojson`` rejects NaN/Infinity earlier via JSON encoding
    # (``allow_nan=False``); exercise the belt-and-suspenders isfinite check
    # on ``_coordinate`` itself using values that survive isinstance checks.
    with pytest.raises(ValidationError, match="números finitos"):
        _coordinate(float("nan"), longitude=True)
    with pytest.raises(ValidationError, match="números finitos"):
        _coordinate(float("inf"), longitude=False)


def test_position_requires_exactly_two_coordinates() -> None:
    with pytest.raises(ValidationError, match="longitud y latitud"):
        _position([0, 0, 0])
    with pytest.raises(ValidationError, match="longitud y latitud"):
        _position([0])


def test_ring_rejects_fewer_than_four_positions() -> None:
    budget = _GeometryBudget()
    with pytest.raises(ValidationError, match="al menos cuatro posiciones"):
        _ring([[0, 0], [1, 1], [0, 0]], budget=budget)


def test_ring_rejects_zero_area() -> None:
    budget = _GeometryBudget()
    with pytest.raises(ValidationError, match="área cero"):
        _ring([[0, 0], [1, 0], [2, 0], [0, 0]], budget=budget)


def test_polygon_rejects_empty_ring_list() -> None:
    budget = _GeometryBudget()
    with pytest.raises(ValidationError, match="al menos un anillo"):
        _polygon([], budget=budget)


def test_multipolygon_rejects_empty_polygon_list() -> None:
    with pytest.raises(ValidationError, match="al menos un polígono"):
        validate_geojson({"type": "MultiPolygon", "coordinates": []})


def test_extract_polygons_accepts_a_bare_feature_document() -> None:
    budget = _GeometryBudget()
    polygons = _extract_polygons({"type": "Feature", "geometry": _square()}, budget=budget)
    assert len(polygons) == 1


def test_feature_collection_rejects_empty_features() -> None:
    with pytest.raises(ValidationError, match="al menos un feature"):
        validate_geojson({"type": "FeatureCollection", "features": []})


def test_feature_collection_rejects_non_feature_elements() -> None:
    with pytest.raises(ValidationError, match="Feature GeoJSON"):
        validate_geojson(
            {
                "type": "FeatureCollection",
                "features": [{"type": "NotAFeature", "geometry": _square()}],
            }
        )


def test_validated_shape_rejects_empty_geometry() -> None:
    with pytest.raises(ValidationError, match="no puede estar vacía"):
        _validated_shape({"type": "Polygon", "coordinates": []})


def test_validated_shape_rejects_unsupported_geometry_type() -> None:
    with pytest.raises(ValidationError, match="Polygon y MultiPolygon"):
        _validated_shape({"type": "Point", "coordinates": [0, 0]})


def test_contains_point_rejects_non_finite_and_out_of_range_points() -> None:
    geometry = validate_geojson(_square())
    with pytest.raises(ValidationError, match="finitas"):
        contains_point(geometry, longitude=float("nan"), latitude=0)
    with pytest.raises(ValidationError, match="rango geográfico"):
        contains_point(geometry, longitude=200, latitude=0)


def test_contains_point_accepts_a_raw_mapping_geometry() -> None:
    geometry = validate_geojson(_square())
    assert contains_point(geometry.geojson, longitude=5, latitude=5)
