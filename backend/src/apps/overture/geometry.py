from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from numbers import Real

from django.core.exceptions import ValidationError
from shapely.geometry import Point, shape  # type: ignore[import-untyped]
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]
from shapely.validation import explain_validity  # type: ignore[import-untyped]

# These are upload limits, not merely recommendations. Keeping them here means
# model validation, the dashboard and the importer all enforce the same budget.
MAX_GEOJSON_BYTES = 1024 * 1024
MAX_FEATURES = 100
MAX_POLYGONS = 100
MAX_RINGS = 200
MAX_VERTICES = 20_000
MAX_RING_VERTICES = 20_000
MAX_OFFICIAL_POLYGONS = 2_000
MAX_OFFICIAL_RINGS = 2_500

type Position = list[float]
type Ring = list[Position]
type PolygonCoordinates = list[Ring]
type MultiPolygonCoordinates = list[PolygonCoordinates]


@dataclass(frozen=True, slots=True)
class ValidatedGeometry:
    geojson: dict[str, object]
    bbox: tuple[float, float, float, float]
    sha256: str


@dataclass(slots=True)
class _GeometryBudget:
    features: int = 0
    polygons: int = 0
    rings: int = 0
    vertices: int = 0
    max_polygons: int = MAX_POLYGONS
    max_rings: int = MAX_RINGS

    def add_feature(self) -> None:
        self.features += 1
        if self.features > MAX_FEATURES:
            raise ValidationError("El GeoJSON supera el máximo de features permitido.")

    def add_polygon(self) -> None:
        self.polygons += 1
        if self.polygons > self.max_polygons:
            raise ValidationError(
                f"El GeoJSON supera el máximo de {self.max_polygons} partes poligonales."
            )

    def add_ring(self, vertex_count: int) -> None:
        self.rings += 1
        self.vertices += vertex_count
        if self.rings > self.max_rings:
            raise ValidationError(
                f"El GeoJSON supera el máximo de {self.max_rings} anillos permitido."
            )
        if vertex_count > MAX_RING_VERTICES or self.vertices > MAX_VERTICES:
            raise ValidationError("El GeoJSON supera el máximo de 20000 coordenadas permitido.")


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} debe ser un objeto GeoJSON.")
    if not all(isinstance(key, str) for key in value):
        raise ValidationError(f"{label} contiene una clave inválida.")
    if "crs" in value:
        raise ValidationError("No se permiten declaraciones CRS; las coordenadas deben ser WGS84.")
    return value


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValidationError(f"{label} debe ser una lista.")
    return value


def _coordinate(value: object, *, longitude: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValidationError("Las coordenadas GeoJSON deben ser números finitos.")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError("Las coordenadas GeoJSON deben ser números finitos.")
    lower, upper = (-180.0, 180.0) if longitude else (-90.0, 90.0)
    if number < lower or number > upper:
        raise ValidationError("Una coordenada GeoJSON está fuera de rango WGS84.")
    return number


def _position(value: object) -> Position:
    coordinates = _sequence(value, label="Una posición")
    if len(coordinates) != 2:
        raise ValidationError("Cada posición GeoJSON debe tener longitud y latitud.")
    return [
        _coordinate(coordinates[0], longitude=True),
        _coordinate(coordinates[1], longitude=False),
    ]


def _signed_area(ring: Ring) -> float:
    return (
        sum(
            (ring[index][0] * ring[index + 1][1]) - (ring[index + 1][0] * ring[index][1])
            for index in range(len(ring) - 1)
        )
        / 2.0
    )


def _ring(value: object, *, budget: _GeometryBudget) -> Ring:
    raw_ring = _sequence(value, label="Un anillo")
    budget.add_ring(len(raw_ring))
    if len(raw_ring) < 4:
        raise ValidationError("Cada anillo GeoJSON debe tener al menos cuatro posiciones.")
    ring = [_position(raw_position) for raw_position in raw_ring]
    if ring[0] != ring[-1]:
        raise ValidationError("Cada anillo GeoJSON debe estar cerrado.")
    if math.isclose(_signed_area(ring), 0.0, abs_tol=1e-15):
        raise ValidationError("Los anillos GeoJSON no pueden tener área cero.")
    return ring


def _polygon(value: object, *, budget: _GeometryBudget) -> PolygonCoordinates:
    raw_polygon = _sequence(value, label="Las coordenadas de Polygon")
    if not raw_polygon:
        raise ValidationError("Un Polygon debe contener al menos un anillo.")
    budget.add_polygon()
    return [_ring(raw_ring, budget=budget) for raw_ring in raw_polygon]


def _geometry_polygons(
    value: object,
    *,
    budget: _GeometryBudget,
) -> MultiPolygonCoordinates:
    geometry = _mapping(value, label="La geometría")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        return [_polygon(coordinates, budget=budget)]
    if geometry_type == "MultiPolygon":
        raw_polygons = _sequence(coordinates, label="Las coordenadas de MultiPolygon")
        if not raw_polygons:
            raise ValidationError("Un MultiPolygon debe contener al menos un polígono.")
        return [_polygon(raw_polygon, budget=budget) for raw_polygon in raw_polygons]
    raise ValidationError("Solo se permiten geometrías Polygon y MultiPolygon.")


def _extract_polygons(value: object, *, budget: _GeometryBudget) -> MultiPolygonCoordinates:
    document = _mapping(value, label="El GeoJSON")
    document_type = document.get("type")
    if document_type == "Feature":
        budget.add_feature()
        return _geometry_polygons(document.get("geometry"), budget=budget)
    if document_type == "FeatureCollection":
        features = _sequence(document.get("features"), label="features")
        if not features:
            raise ValidationError("Un FeatureCollection debe contener al menos un feature.")
        polygons: MultiPolygonCoordinates = []
        for raw_feature in features:
            feature = _mapping(raw_feature, label="Un feature")
            if feature.get("type") != "Feature":
                raise ValidationError("Cada elemento debe ser un Feature GeoJSON.")
            budget.add_feature()
            polygons.extend(_geometry_polygons(feature.get("geometry"), budget=budget))
        return polygons
    return _geometry_polygons(document, budget=budget)


def _encoded_json(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError("El GeoJSON debe ser un documento JSON válido.") from exc
    if len(encoded) > MAX_GEOJSON_BYTES:
        raise ValidationError("El GeoJSON supera el tamaño máximo de 1 MiB.")
    return encoded


@lru_cache(maxsize=512)
def _shape_from_canonical(encoded: str) -> BaseGeometry:
    value = json.loads(encoded)
    return shape(value)


def _validated_shape(canonical: dict[str, object]) -> BaseGeometry:
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    geometry = _shape_from_canonical(encoded)
    if geometry.is_empty:
        raise ValidationError("La geometría GeoJSON no puede estar vacía.")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValidationError("Solo se permiten geometrías Polygon y MultiPolygon.")
    if not geometry.is_valid:
        detail = explain_validity(geometry)
        raise ValidationError(f"La geometría GeoJSON es inválida o se autointersecta: {detail}.")
    return geometry


def validate_geojson(value: object, *, trusted_official: bool = False) -> ValidatedGeometry:
    """Validate and canonicalize a bounded WGS84 Polygon/MultiPolygon document."""

    _encoded_json(value)
    budget = (
        _GeometryBudget(
            max_polygons=MAX_OFFICIAL_POLYGONS,
            max_rings=MAX_OFFICIAL_RINGS,
        )
        if trusted_official
        else _GeometryBudget()
    )
    polygons = _extract_polygons(value, budget=budget)
    if len(polygons) == 1:
        canonical: dict[str, object] = {
            "type": "Polygon",
            "coordinates": polygons[0],
        }
    else:
        canonical = {"type": "MultiPolygon", "coordinates": polygons}
    encoded = _encoded_json(canonical)
    geometry = _validated_shape(canonical)
    west, south, east, north = geometry.bounds
    return ValidatedGeometry(
        geojson=canonical,
        bbox=(float(west), float(south), float(east), float(north)),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def contains_point(
    geometry: ValidatedGeometry | Mapping[str, object],
    *,
    longitude: float,
    latitude: float,
) -> bool:
    """Return whether the exact polygon covers a point, including its boundary."""

    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise ValidationError("El punto debe contener coordenadas finitas.")
    if longitude < -180 or longitude > 180 or latitude < -90 or latitude > 90:
        raise ValidationError("El punto está fuera del rango geográfico válido.")
    validated = geometry if isinstance(geometry, ValidatedGeometry) else validate_geojson(geometry)
    west, south, east, north = validated.bbox
    if longitude < west or longitude > east or latitude < south or latitude > north:
        return False
    canonical = json.dumps(validated.geojson, sort_keys=True, separators=(",", ":"))
    return bool(_shape_from_canonical(canonical).covers(Point(longitude, latitude)))
