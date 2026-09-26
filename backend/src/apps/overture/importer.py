from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.overture.geometry import ValidatedGeometry, contains_point, validate_geojson
from apps.overture.matching import (
    normalize_search_text,
    normalize_taxonomy_code,
    padded_name_search,
    taxonomy_codes_search,
)
from apps.overture.models import (
    OvertureCoveragePartition,
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
    OvertureRelease,
    OvertureTaxonomyCode,
)
from apps.overture.reader import BBox, OverturePlaceReader, validate_bbox
from apps.overture.releases import (
    REVIEWED_RELEASE_SCHEMA_VERSIONS,
    ReleaseDescriptor,
    official_places_source_uri,
    official_release_catalog_url,
    validate_release_id,
)

MAX_CATALOG_PLACES = 500_000
MAX_ZONES = 256
MAX_TOTAL_BOUNDARY_BYTES = 4 * 1024 * 1024
DEFAULT_BATCH_SIZE = 1_000
_TEXT_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
OVERTURE_ATTRIBUTION = "Overture Maps Foundation, overturemaps.org"
FOURSQUARE_NOTICE = (
    "Data from Foursquare. Copyright 2024 Foursquare Labs, Inc. All rights reserved. "
    "Available under Apache 2.0. Foursquare data was transformed to the Overture schema. "
    "Changed: 2026-03-18. NOTICE.txt: "
    "https://opensource.foursquare.com/places-notice-txt/"
)
SOURCE_LICENSES = {
    "alltheplaces": "CC0-1.0",
    "brightquery": "CDLA-Permissive-2.0",
    "dac": "CDLA-Permissive-2.0",
    "foursquare": "Apache-2.0",
    "fsq": "Apache-2.0",
    "krick": "CDLA-Permissive-2.0",
    "meta": "CDLA-Permissive-2.0",
    "metaplaces": "CDLA-Permissive-2.0",
    "microsoft": "CDLA-Permissive-2.0",
    "msftplaces": "CDLA-Permissive-2.0",
    # Overture can appear as the provenance of a derived/conflated property.
    # It does not add a separate upstream license to the place's source license.
    "overture": "",
    "pinmeto": "CDLA-Permissive-2.0",
    "renderseo": "CDLA-Permissive-2.0",
}
FOURSQUARE_DATASET_KEYS = frozenset({"foursquare", "fsq"})
OPERATING_STATUSES = frozenset({"", "open", "temporarily_closed", "permanently_closed"})


class OvertureImportError(RuntimeError):
    pass


class OvertureSchemaError(OvertureImportError):
    pass


@dataclass(frozen=True, slots=True)
class ZoneDefinition:
    code: str
    name: str
    geometry: object
    source: str
    source_version: str
    attribution: str = ""
    expected_boundary_hash: str = ""
    search_zone_id: uuid.UUID | str | None = None
    trusted_official_geometry: bool = False


@dataclass(frozen=True, slots=True)
class ProvinceCoverageDefinition:
    province_id: uuid.UUID | str
    province_code: str
    province_name: str
    province_bbox: BBox
    import_revision: int = 0


@dataclass(frozen=True, slots=True)
class _PreparedZone:
    code: str
    name: str
    normalized_name: str
    geometry: ValidatedGeometry
    source: str
    source_version: str
    attribution: str
    search_zone_id: uuid.UUID | str | None
    trusted_official_geometry: bool


@dataclass(frozen=True, slots=True)
class _ParsedPlace:
    overture_id: str
    name: str
    normalized_name: str
    name_search: str
    names: object
    address: str
    address_data: object
    websites: list[str]
    emails: list[str]
    phones: list[str]
    primary_category: str
    basic_category: str
    taxonomy: object
    taxonomy_codes_search: str
    match_latitude: float
    match_longitude: float
    latitude: Decimal
    longitude: Decimal
    confidence: Decimal | None
    operating_status: str
    sources: object
    field_provenance: object
    source_licenses: list[str]
    source_datasets: tuple[str, ...]
    license: str
    source_payload_hash: str
    taxonomy_codes: tuple[str, ...]


def normalize_zone_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_marks.casefold().split())


def _text(value: object, *, max_length: int, required: bool = False) -> str:
    if value is None:
        result = ""
    elif isinstance(value, str):
        result = " ".join(value.strip().split())
    else:
        result = ""
    if _TEXT_CONTROL_PATTERN.search(result):
        raise OvertureSchemaError("Overture devolvió texto con caracteres de control.")
    if len(result) > max_length:
        raise OvertureSchemaError("Overture devolvió un campo de texto demasiado largo.")
    if required and not result:
        raise OvertureSchemaError("Overture omitió un campo de texto obligatorio.")
    return result


def _string_list(value: object, *, max_items: int, max_length: int) -> list[str]:
    if value is None:
        return []
    raw_values: Sequence[object]
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_values = value
    else:
        return []
    results: list[str] = []
    for raw in raw_values[:max_items]:
        item = _text(raw, max_length=max_length)
        if item and item not in results:
            results.append(item)
    return results


def _json_safe(value: object, *, depth: int = 0) -> object:
    if depth > 8:
        raise OvertureSchemaError("Overture devolvió metadatos demasiado anidados.")
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OvertureSchemaError("Overture devolvió un número no finito.")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        if len(value) > 100:
            raise OvertureSchemaError("Overture devolvió demasiados campos anidados.")
        result: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise OvertureSchemaError("Overture devolvió una clave no textual.")
            result[key[:120]] = _json_safe(child, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 100:
            raise OvertureSchemaError("Overture devolvió demasiados valores anidados.")
        return [_json_safe(child, depth=depth + 1) for child in value]
    raise OvertureSchemaError("Overture devolvió un tipo de metadato inesperado.")


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _float_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError
    return float(value)


def _coordinates_from_wkb(value: bytes) -> tuple[float, float] | None:
    if len(value) < 21:
        return None
    byte_order = value[0]
    if byte_order not in (0, 1):
        return None
    prefix = ">" if byte_order == 0 else "<"
    geometry_type = struct.unpack(f"{prefix}I", value[1:5])[0]
    base_type = geometry_type & 0xFF
    offset = 5
    if geometry_type & 0x20000000:
        if len(value) < 25:
            return None
        offset += 4
    if base_type != 1 or len(value) < offset + 16:
        return None
    longitude, latitude = struct.unpack(f"{prefix}dd", value[offset : offset + 16])
    return longitude, latitude


def _coordinates(row: Mapping[str, object]) -> tuple[float, float]:
    geometry = row.get("geometry")
    if isinstance(geometry, Mapping) and geometry.get("type") == "Point":
        raw_coordinates = geometry.get("coordinates")
        if (
            isinstance(raw_coordinates, Sequence)
            and not isinstance(raw_coordinates, (str, bytes, bytearray))
            and len(raw_coordinates) >= 2
        ):
            try:
                return _float_value(raw_coordinates[0]), _float_value(raw_coordinates[1])
            except (TypeError, ValueError):
                pass
    if isinstance(geometry, (bytes, bytearray, memoryview)):
        parsed = _coordinates_from_wkb(bytes(geometry))
        if parsed is not None:
            return parsed
    bbox = _mapping(row.get("bbox"))
    try:
        west = _float_value(bbox["xmin"])
        south = _float_value(bbox["ymin"])
        east = _float_value(bbox["xmax"])
        north = _float_value(bbox["ymax"])
    except (KeyError, TypeError, ValueError):
        raise OvertureSchemaError("Overture omitió la geometría Point de un lugar.") from None
    if not math.isclose(west, east, abs_tol=1e-10) or not math.isclose(south, north, abs_tol=1e-10):
        raise OvertureSchemaError("Overture devolvió un bbox que no representa un Point.")
    return west, south


def _decimal_coordinate(value: float, *, longitude: bool) -> Decimal:
    if not math.isfinite(value):
        raise OvertureSchemaError("Overture devolvió coordenadas no finitas.")
    lower, upper = (-180, 180) if longitude else (-90, 90)
    if value < lower or value > upper:
        raise OvertureSchemaError("Overture devolvió coordenadas fuera de rango.")
    return Decimal(str(value))


def _primary_name(row: Mapping[str, object]) -> tuple[str, object]:
    raw_names = row.get("names")
    names = _mapping(raw_names)
    primary = _text(names.get("primary"), max_length=300)
    if not primary:
        primary = _text(row.get("name"), max_length=300)
    # ``names`` is nullable in the official Places schema. Keep the faithful
    # empty value instead of turning an identifier into a business name; local
    # search/prospect parsing consequently skips unnamed records.
    return primary, _json_safe(raw_names if raw_names is not None else {})


def _address(row: Mapping[str, object]) -> tuple[str, object]:
    addresses = row.get("addresses")
    if not isinstance(addresses, Sequence) or isinstance(addresses, (str, bytes, bytearray)):
        return "", []
    safe_addresses = _json_safe(addresses[:10])
    if not addresses:
        return "", safe_addresses
    first = _mapping(addresses[0])
    freeform = _text(first.get("freeform"), max_length=500)
    if freeform:
        return freeform, safe_addresses
    parts = [
        _text(first.get(field), max_length=160)
        for field in ("locality", "region", "postcode", "country")
    ]
    return ", ".join(part for part in parts if part)[:500], safe_addresses


def _taxonomy_code(value: object, *, required: bool = False) -> str:
    if value is not None and not isinstance(value, str):
        raise OvertureSchemaError("Overture devolvió un código taxonómico incompatible.")
    code = _text(value, max_length=160, required=required)
    normalized = normalize_taxonomy_code(code)
    if code and not normalized:
        raise OvertureSchemaError("Overture devolvió un código taxonómico incompatible.")
    return normalized


def _taxonomy_code_list(
    value: object,
    *,
    required: bool,
    field_name: str,
) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise OvertureSchemaError(
            f"Overture devolvió {field_name} con una estructura incompatible."
        )
    if len(value) > 50:
        raise OvertureSchemaError(f"Overture devolvió demasiados valores en {field_name}.")
    return tuple(_taxonomy_code(item, required=True) for item in value)


def _taxonomy(
    row: Mapping[str, object],
) -> tuple[str, str, object, tuple[str, ...]]:
    # The legacy `categories` field is intentionally ignored. Requiring the two
    # replacement columns makes a release with an incompatible schema fail closed.
    if "taxonomy" not in row or "basic_category" not in row:
        raise OvertureSchemaError(
            "El release Overture no contiene taxonomy y basic_category compatibles."
        )
    raw_taxonomy = row.get("taxonomy")
    basic_category = _taxonomy_code(row.get("basic_category"))
    if raw_taxonomy is None:
        return "", basic_category, {}, ((basic_category,) if basic_category else ())
    taxonomy = _mapping(raw_taxonomy)
    if not taxonomy:
        raise OvertureSchemaError("Overture devolvió una taxonomía con estructura inválida.")
    primary = _taxonomy_code(taxonomy.get("primary"), required=True)
    hierarchy = _taxonomy_code_list(
        taxonomy.get("hierarchy"),
        required=True,
        field_name="taxonomy.hierarchy",
    )
    if not hierarchy or hierarchy[-1] != primary or len(set(hierarchy)) != len(hierarchy):
        raise OvertureSchemaError("La jerarquía taxonómica Overture es incompatible.")
    alternates = _taxonomy_code_list(
        taxonomy.get("alternates"),
        required=False,
        field_name="taxonomy.alternates",
    )
    if len(set(alternates)) != len(alternates):
        raise OvertureSchemaError("La taxonomía Overture contiene alternates duplicados.")
    codes = tuple(dict.fromkeys(code for code in (*hierarchy, *alternates, basic_category) if code))
    canonical = {
        "primary": primary,
        "hierarchy": list(hierarchy),
        "alternates": list(alternates),
    }
    return primary, basic_category, canonical, codes


def _confidence(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise OvertureSchemaError("Overture devolvió una confianza inválida.") from None
    if not confidence.is_finite() or confidence < 0 or confidence > 1:
        raise OvertureSchemaError("Overture devolvió una confianza fuera de rango.")
    return confidence


def _source_metadata(
    value: object,
) -> tuple[object, object, list[str], tuple[str, ...]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise OvertureSchemaError("Overture omitió la procedencia del lugar.")
    if not value or len(value) > 100:
        raise OvertureSchemaError("Overture devolvió una procedencia vacía o excesiva.")
    safe_sources = _json_safe(value)
    provenance: dict[str, list[dict[str, object]]] = {}
    datasets: list[str] = []
    licenses: list[str] = []
    for raw_source in value:
        source = _mapping(raw_source)
        dataset = _text(source.get("dataset"), max_length=120, required=True)
        dataset_key = normalize_search_text(dataset).replace(" ", "")
        if dataset_key not in SOURCE_LICENSES:
            raise OvertureSchemaError(
                f"Overture devolvió una fuente sin licencia revisada: {dataset_key}."
            )
        if dataset not in datasets:
            datasets.append(dataset)
        source_license = SOURCE_LICENSES[dataset_key]
        if source_license and source_license not in licenses:
            licenses.append(source_license)
        property_path = _text(source.get("property"), max_length=300) or "/"
        raw_update_time = source.get("update_time")
        update_time = (
            raw_update_time.isoformat()
            if isinstance(raw_update_time, (date, datetime))
            else _text(raw_update_time, max_length=80)
        )
        provenance.setdefault(property_path, []).append(
            {
                "dataset": dataset,
                "record_id": _text(source.get("record_id"), max_length=300),
                "update_time": update_time,
                "confidence": _json_safe(source.get("confidence")),
            }
        )
    if "/" not in provenance:
        raise OvertureSchemaError("Overture omitió la procedencia raíz del lugar.")
    return safe_sources, provenance, licenses, tuple(datasets)


def _parse_place(row: Mapping[str, object]) -> _ParsedPlace:
    raw_overture_id = _text(row.get("id"), max_length=80, required=True)
    try:
        overture_id = str(uuid.UUID(raw_overture_id))
    except ValueError as exc:
        raise OvertureSchemaError("Overture devolvió un identificador GERS inválido.") from exc
    name, names = _primary_name(row)
    longitude_float, latitude_float = _coordinates(row)
    longitude = _decimal_coordinate(longitude_float, longitude=True)
    latitude = _decimal_coordinate(latitude_float, longitude=False)
    address, address_data = _address(row)
    primary_category, basic_category, taxonomy, taxonomy_codes = _taxonomy(row)
    raw_sources = row.get("sources")
    sources, field_provenance, source_licenses, source_datasets = _source_metadata(raw_sources)
    websites = _string_list(row.get("websites"), max_items=20, max_length=1000)
    emails = _string_list(row.get("emails"), max_items=30, max_length=320)
    phones = _string_list(row.get("phones"), max_items=30, max_length=80)
    operating_status = _text(row.get("operating_status"), max_length=40)
    if operating_status not in OPERATING_STATUSES:
        raise OvertureSchemaError("Overture devolvió un estado operativo incompatible.")
    selected_payload = {
        "id": overture_id,
        "names": names,
        "address": address_data,
        "websites": websites,
        "emails": emails,
        "phones": phones,
        "basic_category": basic_category,
        "taxonomy": taxonomy,
        "longitude": str(longitude),
        "latitude": str(latitude),
        "confidence": row.get("confidence"),
        "operating_status": operating_status,
        "sources": sources,
    }
    return _ParsedPlace(
        overture_id=overture_id,
        name=name,
        normalized_name=normalize_search_text(name),
        name_search=padded_name_search(name),
        names=names,
        address=address,
        address_data=address_data,
        websites=websites,
        emails=emails,
        phones=phones,
        primary_category=primary_category,
        basic_category=basic_category,
        taxonomy=taxonomy,
        taxonomy_codes_search=taxonomy_codes_search(list(taxonomy_codes)),
        match_latitude=latitude_float,
        match_longitude=longitude_float,
        latitude=latitude,
        longitude=longitude,
        confidence=_confidence(row.get("confidence")),
        operating_status=operating_status,
        sources=sources,
        field_provenance=field_provenance,
        source_licenses=source_licenses,
        source_datasets=source_datasets,
        license=", ".join(source_licenses)[:500],
        source_payload_hash=_canonical_hash(selected_payload),
        taxonomy_codes=taxonomy_codes,
    )


def _prepare_zones(definitions: Iterable[ZoneDefinition]) -> tuple[_PreparedZone, ...]:
    prepared: list[_PreparedZone] = []
    codes: set[str] = set()
    names: set[str] = set()
    total_bytes = 0
    for definition in definitions:
        if len(prepared) >= MAX_ZONES:
            raise ValidationError("El catálogo supera el máximo de zonas permitido.")
        code = _text(definition.code, max_length=120, required=True)
        name = _text(definition.name, max_length=200, required=True)
        normalized_name = normalize_zone_name(name)
        if code in codes or normalized_name in names:
            raise ValidationError("Las zonas Overture deben tener código y nombre únicos.")
        geometry = validate_geojson(
            definition.geometry,
            trusted_official=definition.trusted_official_geometry,
        )
        if (
            definition.expected_boundary_hash
            and geometry.sha256 != definition.expected_boundary_hash
        ):
            raise ValidationError("El hash de una frontera configurada no coincide.")
        total_bytes += len(
            json.dumps(geometry.geojson, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        if total_bytes > MAX_TOTAL_BOUNDARY_BYTES:
            raise ValidationError("El conjunto de fronteras supera el tamaño máximo permitido.")
        prepared.append(
            _PreparedZone(
                code=code,
                name=name,
                normalized_name=normalized_name,
                geometry=geometry,
                source=_text(definition.source, max_length=120, required=True),
                source_version=_text(
                    definition.source_version,
                    max_length=120,
                    required=True,
                ),
                attribution=_text(definition.attribution, max_length=500),
                search_zone_id=definition.search_zone_id,
                trusted_official_geometry=definition.trusted_official_geometry,
            )
        )
        codes.add(code)
        names.add(normalized_name)
    if not prepared:
        raise ValidationError("Se necesita al menos una zona con frontera para importar Overture.")
    return tuple(prepared)


def _boundary_manifest_hash(zones: tuple[_PreparedZone, ...]) -> str:
    manifest = [
        {"code": zone.code, "boundary_hash": zone.geometry.sha256}
        for zone in sorted(zones, key=lambda item: item.code)
    ]
    return _canonical_hash(manifest)


def _combined_bbox(zones: tuple[_PreparedZone, ...]) -> BBox:
    return (
        min(zone.geometry.bbox[0] for zone in zones),
        min(zone.geometry.bbox[1] for zone in zones),
        max(zone.geometry.bbox[2] for zone in zones),
        max(zone.geometry.bbox[3] for zone in zones),
    )


def _create_snapshot(
    *,
    descriptor: ReleaseDescriptor,
    schema_version: str,
    taxonomy_version: str,
    importer_version: str,
    mapping_version: str,
    boundary_version: str,
    boundary_manifest_sha256: str,
    zone_count: int,
    coverage: ProvinceCoverageDefinition | None,
) -> tuple[OvertureDatasetSnapshot, OvertureCoveragePartition | None]:
    release_id = validate_release_id(descriptor.release_id)
    if descriptor.source_uri != official_places_source_uri(release_id):
        raise ValidationError("El origen Places no pertenece al bucket oficial de Overture.")
    if descriptor.catalog_url != official_release_catalog_url(release_id):
        raise ValidationError("La metadata no pertenece al catálogo STAC oficial de Overture.")
    if not re.fullmatch(r"[0-9a-f]{64}", descriptor.manifest_sha256):
        raise ValidationError("El hash de metadata Overture no es válido.")
    metadata_payload = {
        "release_id": release_id,
        "schema_version": schema_version,
        "taxonomy_version": taxonomy_version,
        "importer_version": importer_version,
        "mapping_version": mapping_version,
        "source_uri": descriptor.source_uri,
        "manifest_sha256": descriptor.manifest_sha256,
    }
    metadata_sha256 = _canonical_hash(metadata_payload)
    release_record, created = OvertureRelease.objects.get_or_create(
        release_id=release_id,
        defaults={
            "schema_version": _text(schema_version, max_length=80, required=True),
            "taxonomy_version": _text(taxonomy_version, max_length=80, required=True),
            "importer_version": _text(importer_version, max_length=80, required=True),
            "mapping_version": _text(mapping_version, max_length=80, required=True),
            "source_uri": descriptor.source_uri,
            "catalog_url": descriptor.catalog_url,
            "manifest_sha256": descriptor.manifest_sha256,
            "metadata_sha256": metadata_sha256,
            "attribution": OVERTURE_ATTRIBUTION,
            "discovered_at": timezone.now(),
        },
    )
    if not created and (
        release_record.schema_version != schema_version
        or release_record.taxonomy_version != taxonomy_version
        or release_record.source_uri != descriptor.source_uri
        or release_record.manifest_sha256 != descriptor.manifest_sha256
    ):
        raise ValidationError("La metadata del release Overture no coincide con la ya registrada.")

    province_code = ""
    province_name = ""
    province_bbox: list[float] = []
    import_revision = 1
    if coverage is not None:
        province_code = _text(coverage.province_code, max_length=20, required=True)
        province_name = _text(coverage.province_name, max_length=160, required=True)
        province_bbox = list(validate_bbox(coverage.province_bbox))
        import_revision = coverage.import_revision
        if import_revision <= 0:
            latest_revision = (
                OvertureCoveragePartition.objects.filter(
                    release=release_record,
                    province_code=province_code,
                ).aggregate(value=Max("import_revision"))["value"]
                or 0
            )
            import_revision = int(latest_revision) + 1

    snapshot = OvertureDatasetSnapshot.objects.create(
        release_id=release_id,
        release_record=release_record,
        province_code=province_code,
        province_name=province_name,
        province_bbox=province_bbox,
        import_revision=import_revision,
        schema_version=_text(schema_version, max_length=80, required=True),
        taxonomy_version=_text(taxonomy_version, max_length=80, required=True),
        importer_version=_text(importer_version, max_length=80, required=True),
        mapping_version=_text(mapping_version, max_length=80, required=True),
        boundary_version=_text(boundary_version, max_length=120, required=True),
        boundary_manifest_sha256=boundary_manifest_sha256,
        source_uri=descriptor.source_uri,
        manifest_sha256=descriptor.manifest_sha256,
        zone_count=zone_count,
        attribution=OVERTURE_ATTRIBUTION,
        notices=[],
    )
    partition: OvertureCoveragePartition | None = None
    if coverage is not None:
        partition = OvertureCoveragePartition.objects.create(
            release=release_record,
            province_id=coverage.province_id,
            province_code=province_code,
            province_name=province_name,
            province_bbox=province_bbox,
            import_revision=import_revision,
            snapshot=snapshot,
            boundary_version=boundary_version,
            boundary_manifest_sha256=boundary_manifest_sha256,
            zone_count=zone_count,
            attribution=OVERTURE_ATTRIBUTION,
        )
    return snapshot, partition


def _mark_failed(
    snapshot: OvertureDatasetSnapshot,
    *,
    streamed_count: int,
    place_count: int,
    error_type: str,
) -> None:
    snapshot.streamed_count = streamed_count
    snapshot.place_count = place_count
    snapshot.status = OvertureDatasetSnapshot.Status.FAILED
    snapshot.is_active = False
    snapshot.error = f"{error_type}: la importación no se pudo completar."
    validation_results = dict(snapshot.validation_results)
    validation_results.update({"passed": False, "error_type": error_type})
    snapshot.validation_results = validation_results
    snapshot.save(
        update_fields=(
            "streamed_count",
            "place_count",
            "status",
            "is_active",
            "error",
            "validation_results",
            "updated_at",
        )
    )
    OvertureCoveragePartition.objects.filter(snapshot=snapshot).update(
        streamed_count=streamed_count,
        place_count=place_count,
        status=OvertureCoveragePartition.Status.FAILED,
        is_active=False,
        error=snapshot.error,
        validation_results=validation_results,
        updated_at=timezone.now(),
    )


def _mark_failed_without_masking(
    snapshot: OvertureDatasetSnapshot,
    *,
    streamed_count: int,
    place_count: int,
    original_error: Exception,
) -> None:
    try:
        _mark_failed(
            snapshot,
            streamed_count=streamed_count,
            place_count=place_count,
            error_type=type(original_error).__name__,
        )
    except Exception as persistence_error:
        original_error.add_note(
            f"Overture could not persist FAILED state: {type(persistence_error).__name__}."
        )


def _flush_batch(
    places: list[OverturePlace],
    links: list[OverturePlaceZone],
    *,
    batch_size: int,
) -> None:
    if not places:
        return
    with transaction.atomic():
        OverturePlace.objects.bulk_create(places, batch_size=batch_size)
        OverturePlaceZone.objects.bulk_create(links, batch_size=batch_size)
    places.clear()
    links.clear()


def _activation_error(detail: str) -> ValidationError:
    return ValidationError(f"El snapshot Overture no puede activarse: {detail}.")


def _validate_metadata_list(value: object, *, label: str) -> list[str]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, list):
        raise _activation_error(f"{label} tiene una estructura inválida")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise _activation_error(f"{label} contiene valores inválidos")
    if len(value) != len(set(value)):
        raise _activation_error(f"{label} contiene valores duplicados")
    return value


def _validate_persisted_source_provenance(
    snapshot: OvertureDatasetSnapshot,
) -> tuple[dict[str, int], list[str], list[str]]:
    source_counts: dict[str, int] = {}
    all_licenses: set[str] = set()
    has_foursquare = False
    places = snapshot.places.values(
        "sources",
        "field_provenance",
        "source_licenses",
        "license",
        "operating_status",
    )
    for place in places.iterator(chunk_size=1_000):
        if str(place["operating_status"]).casefold() == "permanently_closed":
            raise _activation_error("el catálogo contiene un lugar cerrado permanentemente")
        raw_sources = place["sources"]
        if (
            isinstance(raw_sources, (str, bytes, bytearray))
            or not isinstance(raw_sources, list)
            or not raw_sources
            or len(raw_sources) > 100
        ):
            raise _activation_error("un lugar no conserva procedencia válida")
        datasets: list[str] = []
        expected_place_licenses: list[str] = []
        root_datasets: set[str] = set()
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                raise _activation_error("un lugar no conserva procedencia válida")
            dataset = raw_source.get("dataset")
            if not isinstance(dataset, str) or not dataset.strip():
                raise _activation_error("un lugar no conserva procedencia válida")
            dataset_key = normalize_search_text(dataset).replace(" ", "")
            if dataset_key not in SOURCE_LICENSES:
                raise _activation_error("un lugar conserva una fuente sin licencia revisada")
            if dataset not in datasets:
                datasets.append(dataset)
            source_license = SOURCE_LICENSES[dataset_key]
            if source_license and source_license not in expected_place_licenses:
                expected_place_licenses.append(source_license)
            raw_property = raw_source.get("property")
            property_path = raw_property.strip() if isinstance(raw_property, str) else ""
            if not property_path or property_path == "/":
                root_datasets.add(dataset)
            has_foursquare = has_foursquare or dataset_key in FOURSQUARE_DATASET_KEYS
        if not root_datasets:
            raise _activation_error("un lugar no conserva procedencia raíz")

        raw_provenance = place["field_provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise _activation_error("un lugar no conserva procedencia por campo")
        root_provenance = raw_provenance.get("/")
        if not isinstance(root_provenance, list) or not root_provenance:
            raise _activation_error("un lugar no conserva procedencia raíz")
        persisted_root_datasets = {
            item.get("dataset")
            for item in root_provenance
            if isinstance(item, Mapping) and isinstance(item.get("dataset"), str)
        }
        if persisted_root_datasets != root_datasets:
            raise _activation_error("la procedencia raíz de un lugar no coincide")

        place_licenses = _validate_metadata_list(
            place["source_licenses"],
            label="las licencias de un lugar",
        )
        if place_licenses != expected_place_licenses:
            raise _activation_error("las licencias de un lugar no coinciden con sus fuentes")
        if place["license"] != ", ".join(expected_place_licenses)[:500]:
            raise _activation_error("la atribución de licencia de un lugar no coincide")
        all_licenses.update(expected_place_licenses)
        for dataset in datasets:
            source_counts[dataset] = source_counts.get(dataset, 0) + 1
    notices = [FOURSQUARE_NOTICE] if has_foursquare else []
    return dict(sorted(source_counts.items())), sorted(all_licenses), notices


def _validate_snapshot_for_activation(snapshot: OvertureDatasetSnapshot) -> None:
    if snapshot.is_active:
        raise _activation_error("un snapshot IMPORTING no puede estar activo")
    if snapshot.imported_at is not None or snapshot.activated_at is not None:
        raise _activation_error("las fechas de activación ya estaban definidas")
    if snapshot.error:
        raise _activation_error("el import conserva un error")

    release_id = validate_release_id(snapshot.release_id)
    if snapshot.source_uri != official_places_source_uri(release_id):
        raise _activation_error("el origen Places no es el oficial")
    if snapshot.taxonomy_version != release_id:
        raise _activation_error("la versión de taxonomía no coincide con el release")
    reviewed_schema_version = REVIEWED_RELEASE_SCHEMA_VERSIONS.get(release_id)
    if reviewed_schema_version is None or snapshot.schema_version != reviewed_schema_version:
        raise _activation_error("la versión de esquema no fue revisada para este release")
    required_versions = (
        snapshot.schema_version,
        snapshot.importer_version,
        snapshot.mapping_version,
        snapshot.boundary_version,
    )
    if any(not isinstance(value, str) or not value.strip() for value in required_versions):
        raise _activation_error("faltan versiones requeridas")
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot.manifest_sha256):
        raise _activation_error("el hash del manifest no es válido")
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot.boundary_manifest_sha256):
        raise _activation_error("el hash de fronteras no es válido")
    if snapshot.attribution != OVERTURE_ATTRIBUTION:
        raise _activation_error("falta la atribución oficial")
    if not isinstance(snapshot.validation_results, Mapping):
        raise _activation_error("los resultados de validación son inválidos")
    if snapshot.validation_results.get("passed") is not True:
        raise _activation_error("la validación del import no fue aprobada")
    if snapshot.streamed_count < snapshot.place_count:
        raise _activation_error("el conteo transmitido es menor al importado")

    partition = OvertureCoveragePartition.objects.filter(snapshot=snapshot).first()
    if snapshot.province_code and partition is None:
        raise _activation_error("falta la partición provincial")
    if partition is not None:
        if (
            snapshot.release_record_id != partition.release_id
            or snapshot.province_code != partition.province_code
            or snapshot.province_name != partition.province_name
            or snapshot.province_bbox != partition.province_bbox
            or snapshot.boundary_version != partition.boundary_version
            or snapshot.boundary_manifest_sha256 != partition.boundary_manifest_sha256
        ):
            raise _activation_error("la identidad de la partición provincial no coincide")
        if (
            snapshot.zones.exclude(partition=partition).exists()
            or snapshot.places.exclude(partition=partition).exists()
            or snapshot.place_zone_links.exclude(partition=partition).exists()
            or snapshot.taxonomy_codes.exclude(partition=partition).exists()
        ):
            raise _activation_error("un registro pertenece a otra partición provincial")

    if not isinstance(snapshot.source_counts, Mapping):
        raise _activation_error("los conteos de fuentes son inválidos")
    for dataset, count in snapshot.source_counts.items():
        if (
            not isinstance(dataset, str)
            or not dataset.strip()
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
            or count > snapshot.place_count
        ):
            raise _activation_error("los conteos de fuentes son inválidos")
    if snapshot.place_count > 0 and not snapshot.source_counts:
        raise _activation_error("faltan los conteos de fuentes usadas")
    if snapshot.place_count == 0 and snapshot.source_counts:
        raise _activation_error("un catálogo vacío no puede declarar fuentes usadas")
    source_licenses = _validate_metadata_list(
        snapshot.source_licenses,
        label="las licencias",
    )
    if source_licenses != sorted(source_licenses):
        raise _activation_error("las licencias no están canonicalizadas")
    _validate_metadata_list(snapshot.notices, label="los notices")

    actual_zone_count = snapshot.zones.count()
    actual_place_count = snapshot.places.count()
    actual_taxonomy_count = snapshot.taxonomy_codes.count()
    if snapshot.zone_count != actual_zone_count or actual_zone_count < 1:
        raise _activation_error("el conteo de zonas no coincide")
    if snapshot.place_count != actual_place_count:
        raise _activation_error("el conteo de lugares no coincide")
    if snapshot.taxonomy_code_count != actual_taxonomy_count:
        raise _activation_error("el conteo de taxonomía no coincide")

    manifest: list[dict[str, str]] = []
    for zone in (
        snapshot.zones.select_related("search_zone").order_by("code").iterator(chunk_size=100)
    ):
        if (
            not zone.code.strip()
            or not zone.name.strip()
            or not zone.source.strip()
            or not zone.source_version.strip()
            or zone.normalized_name != normalize_zone_name(zone.name)
        ):
            raise _activation_error("una zona no contiene metadata válida")
        trusted_official = bool(
            zone.search_zone_id and zone.search_zone.source in {"GEOREF", "BUENOS_AIRES_DATA"}
        )
        validated = validate_geojson(
            zone.geometry,
            trusted_official=trusted_official,
        )
        if validated.geojson != zone.geometry:
            raise _activation_error("una frontera no está canonicalizada")
        if validated.sha256 != zone.boundary_hash:
            raise _activation_error("el hash de una frontera no coincide")
        if (
            not isinstance(zone.bbox, list)
            or len(zone.bbox) != 4
            or any(
                isinstance(item, bool) or not isinstance(item, (int, float)) for item in zone.bbox
            )
            or tuple(float(item) for item in zone.bbox) != validated.bbox
        ):
            raise _activation_error("el bbox de una frontera no coincide")
        manifest.append({"code": zone.code, "boundary_hash": zone.boundary_hash})
    if _canonical_hash(manifest) != snapshot.boundary_manifest_sha256:
        raise _activation_error("el manifest de fronteras no coincide")

    links = snapshot.place_zone_links
    if (
        links.exclude(place__snapshot_id=snapshot.pk).exists()
        or links.exclude(zone__snapshot_id=snapshot.pk).exists()
    ):
        raise _activation_error("un vínculo lugar-zona cruza snapshots")
    if snapshot.places.filter(zone_links__isnull=True).exists():
        raise _activation_error("existen lugares sin una zona exacta")

    expected_source_counts, expected_source_licenses, expected_notices = (
        _validate_persisted_source_provenance(snapshot)
    )
    if snapshot.source_counts != expected_source_counts:
        raise _activation_error("los conteos de fuentes no coinciden con los lugares")
    if source_licenses != expected_source_licenses:
        raise _activation_error("las licencias no coinciden con los lugares")
    if snapshot.notices != expected_notices:
        raise _activation_error("los notices no coinciden con las fuentes usadas")


@transaction.atomic
def activate_snapshot(snapshot_id: uuid.UUID | str) -> OvertureDatasetSnapshot:
    requested = OvertureDatasetSnapshot.objects.only("province_code").get(pk=snapshot_id)
    if requested.province_code:
        snapshots = list(
            OvertureDatasetSnapshot.objects.select_for_update().filter(
                province_code=requested.province_code
            )
        )
    else:
        # Compatibility for injected legacy imports. These retain the former
        # single-active behavior without superseding another province.
        snapshots = list(
            OvertureDatasetSnapshot.objects.select_for_update().filter(province_code="")
        )
    snapshot = next((item for item in snapshots if item.pk == snapshot_id), None)
    if snapshot is None:
        raise OvertureDatasetSnapshot.DoesNotExist
    if snapshot.status != OvertureDatasetSnapshot.Status.IMPORTING:
        raise ValidationError("Solo se puede activar un snapshot en preparación.")
    _validate_snapshot_for_activation(snapshot)
    now = timezone.now()
    partition = (
        OvertureCoveragePartition.objects.select_for_update().filter(snapshot=snapshot).first()
    )
    if partition is not None:
        current_partitions = list(
            OvertureCoveragePartition.objects.select_for_update().filter(
                province_code=partition.province_code,
                is_active=True,
            )
        )
        for current_partition in current_partitions:
            if current_partition.pk == partition.pk:
                continue
            current_partition.is_active = False
            current_partition.status = OvertureCoveragePartition.Status.SUPERSEDED
            current_partition.save(update_fields=("is_active", "status", "updated_at"))
    for current in snapshots:
        if current.pk != snapshot.pk and current.is_active:
            current.is_active = False
            current.status = OvertureDatasetSnapshot.Status.SUPERSEDED
            current.save(update_fields=("is_active", "status", "updated_at"))
    snapshot.status = OvertureDatasetSnapshot.Status.READY
    snapshot.is_active = True
    snapshot.imported_at = now
    snapshot.activated_at = now
    snapshot.error = ""
    snapshot.save(
        update_fields=(
            "status",
            "is_active",
            "imported_at",
            "activated_at",
            "error",
            "updated_at",
        )
    )
    if partition is not None:
        import_payload = (
            f"{snapshot.pk}:{snapshot.manifest_sha256}:{snapshot.boundary_manifest_sha256}:"
            f"{snapshot.place_count}:{snapshot.zone_count}"
        )
        partition.status = OvertureCoveragePartition.Status.READY
        partition.is_active = True
        partition.import_sha256 = hashlib.sha256(import_payload.encode("utf-8")).hexdigest()
        partition.streamed_count = snapshot.streamed_count
        partition.place_count = snapshot.place_count
        partition.zone_count = snapshot.zone_count
        partition.validation_results = snapshot.validation_results
        partition.source_licenses = snapshot.source_licenses
        partition.imported_at = now
        partition.activated_at = now
        partition.error = ""
        partition.save(
            update_fields=(
                "status",
                "is_active",
                "import_sha256",
                "streamed_count",
                "place_count",
                "zone_count",
                "validation_results",
                "source_licenses",
                "imported_at",
                "activated_at",
                "error",
                "updated_at",
            )
        )
    return snapshot


def import_snapshot(
    *,
    descriptor: ReleaseDescriptor,
    zones: Iterable[ZoneDefinition],
    reader: OverturePlaceReader,
    schema_version: str,
    taxonomy_version: str,
    importer_version: str,
    mapping_version: str,
    boundary_version: str,
    coverage: ProvinceCoverageDefinition | None = None,
    max_places: int = MAX_CATALOG_PLACES,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> OvertureDatasetSnapshot:
    if max_places <= 0 or max_places > MAX_CATALOG_PLACES:
        raise ValidationError(f"max_places debe estar entre 1 y {MAX_CATALOG_PLACES}.")
    if batch_size <= 0 or batch_size > 10_000:
        raise ValidationError("batch_size debe estar entre 1 y 10000.")
    if schema_version != descriptor.schema_version:
        raise OvertureSchemaError(
            "La versión de esquema solicitada no coincide con la metadata del release Overture."
        )
    if taxonomy_version != descriptor.taxonomy_version:
        raise OvertureSchemaError(
            "La versión de taxonomía solicitada no coincide con la metadata del release Overture."
        )
    prepared_zones = _prepare_zones(zones)
    boundary_hash = _boundary_manifest_hash(prepared_zones)
    existing_query = OvertureDatasetSnapshot.objects.active().filter(
        release_id=descriptor.release_id,
        schema_version=schema_version,
        taxonomy_version=taxonomy_version,
        importer_version=importer_version,
        mapping_version=mapping_version,
        boundary_version=boundary_version,
        boundary_manifest_sha256=boundary_hash,
    )
    existing_query = (
        existing_query.filter(province_code="")
        if coverage is None
        else existing_query.filter(province_code=coverage.province_code)
    )
    existing = existing_query.first()
    if existing is not None:
        return existing
    snapshot, partition = _create_snapshot(
        descriptor=descriptor,
        schema_version=schema_version,
        taxonomy_version=taxonomy_version,
        importer_version=importer_version,
        mapping_version=mapping_version,
        boundary_version=boundary_version,
        boundary_manifest_sha256=boundary_hash,
        zone_count=len(prepared_zones),
        coverage=coverage,
    )
    try:
        zone_models = [
            OvertureDatasetZone(
                snapshot=snapshot,
                partition=partition,
                search_zone_id=zone.search_zone_id,
                code=zone.code,
                name=zone.name,
                normalized_name=zone.normalized_name,
                geometry=zone.geometry.geojson,
                bbox=list(zone.geometry.bbox),
                boundary_hash=zone.geometry.sha256,
                source=zone.source,
                source_version=zone.source_version,
                attribution=zone.attribution,
            )
            for zone in prepared_zones
        ]
        OvertureDatasetZone.objects.bulk_create(zone_models)
    except Exception as exc:
        _mark_failed_without_masking(
            snapshot,
            streamed_count=0,
            place_count=0,
            original_error=exc,
        )
        raise
    zone_pairs = tuple(zip(prepared_zones, zone_models, strict=True))
    streamed_count = 0
    place_count = 0
    seen_ids: set[str] = set()
    taxonomy: dict[str, tuple[str, ...]] = {}
    taxonomy_parents: dict[str, str] = {}
    taxonomy_alternates: dict[str, set[str]] = {}
    basic_categories: set[str] = set()
    source_counts: dict[str, int] = {}
    source_licenses: set[str] = set()
    duplicate_count = 0
    outside_count = 0
    permanently_closed_count = 0
    missing_taxonomy_count = 0
    null_confidence_count = 0
    place_batch: list[OverturePlace] = []
    link_batch: list[OverturePlaceZone] = []
    try:
        for row in reader.iter_places(
            release_id=descriptor.release_id,
            bbox=(
                validate_bbox(coverage.province_bbox)
                if coverage is not None
                else _combined_bbox(prepared_zones)
            ),
        ):
            streamed_count += 1
            parsed = _parse_place(row)
            if parsed.overture_id in seen_ids:
                duplicate_count += 1
                continue
            seen_ids.add(parsed.overture_id)
            if parsed.operating_status.casefold() == "permanently_closed":
                permanently_closed_count += 1
                continue
            matched_zones = [
                zone_model
                for prepared_zone, zone_model in zone_pairs
                if contains_point(
                    prepared_zone.geometry,
                    longitude=parsed.match_longitude,
                    latitude=parsed.match_latitude,
                )
            ]
            if not matched_zones:
                outside_count += 1
                continue
            if place_count >= max_places:
                raise OvertureImportError(
                    f"El catálogo supera el límite de {max_places} lugares importados."
                )
            place = OverturePlace(
                snapshot=snapshot,
                partition=partition,
                overture_id=parsed.overture_id,
                name=parsed.name,
                normalized_name=parsed.normalized_name,
                name_search=parsed.name_search,
                names=parsed.names,
                address=parsed.address,
                address_data=parsed.address_data,
                websites=parsed.websites,
                emails=parsed.emails,
                phones=parsed.phones,
                primary_category=parsed.primary_category,
                basic_category=parsed.basic_category,
                taxonomy=parsed.taxonomy,
                taxonomy_codes=list(parsed.taxonomy_codes),
                taxonomy_codes_search=parsed.taxonomy_codes_search,
                latitude=parsed.latitude,
                longitude=parsed.longitude,
                confidence=parsed.confidence,
                operating_status=parsed.operating_status,
                sources=parsed.sources,
                field_provenance=parsed.field_provenance,
                source_licenses=parsed.source_licenses,
                license=parsed.license,
                source_payload_hash=parsed.source_payload_hash,
            )
            place_batch.append(place)
            taxonomy_mapping = _mapping(parsed.taxonomy)
            raw_hierarchy = taxonomy_mapping.get("hierarchy", [])
            if not isinstance(raw_hierarchy, list):
                raw_hierarchy = []
            hierarchy = tuple(value for value in raw_hierarchy if isinstance(value, str))
            raw_alternates = taxonomy_mapping.get("alternates", [])
            if not isinstance(raw_alternates, list):
                raw_alternates = []
            alternates = tuple(value for value in raw_alternates if isinstance(value, str))
            for index, code in enumerate(hierarchy):
                taxonomy.setdefault(code, hierarchy[: index + 1])
                taxonomy_parents.setdefault(code, hierarchy[index - 1] if index else "")
            for code in alternates:
                taxonomy.setdefault(code, (code,))
                taxonomy_parents.setdefault(code, "")
            primary_code = taxonomy_mapping.get("primary")
            if isinstance(primary_code, str) and alternates:
                taxonomy_alternates.setdefault(primary_code, set()).update(alternates)
            if parsed.basic_category:
                taxonomy.setdefault(parsed.basic_category, (parsed.basic_category,))
                taxonomy_parents.setdefault(parsed.basic_category, "")
                basic_categories.add(parsed.basic_category)
            if not parsed.taxonomy_codes:
                missing_taxonomy_count += 1
            if parsed.confidence is None:
                null_confidence_count += 1
            for dataset in parsed.source_datasets:
                source_counts[dataset] = source_counts.get(dataset, 0) + 1
            source_licenses.update(parsed.source_licenses)
            for zone_model in matched_zones:
                link_batch.append(
                    OverturePlaceZone(
                        snapshot=snapshot,
                        partition=partition,
                        place=place,
                        zone=zone_model,
                    )
                )
            place_count += 1
            if len(place_batch) >= batch_size:
                _flush_batch(place_batch, link_batch, batch_size=batch_size)
                snapshot.streamed_count = streamed_count
                snapshot.place_count = place_count
                snapshot.save(update_fields=("streamed_count", "place_count", "updated_at"))
        _flush_batch(place_batch, link_batch, batch_size=batch_size)
        taxonomy_models = [
            OvertureTaxonomyCode(
                snapshot=snapshot,
                partition=partition,
                code=code,
                hierarchy=list(codes),
                parent_code=taxonomy_parents.get(code, ""),
                alternate_codes=sorted(taxonomy_alternates.get(code, set())),
                is_basic_category=code in basic_categories,
            )
            for code, codes in sorted(taxonomy.items())
        ]
        OvertureTaxonomyCode.objects.bulk_create(taxonomy_models, batch_size=batch_size)
        snapshot.streamed_count = streamed_count
        snapshot.place_count = place_count
        snapshot.taxonomy_code_count = len(taxonomy_models)
        snapshot.source_counts = dict(sorted(source_counts.items()))
        snapshot.source_licenses = sorted(source_licenses)
        has_foursquare = any(
            normalize_search_text(dataset).replace(" ", "") in FOURSQUARE_DATASET_KEYS
            for dataset in source_counts
        )
        snapshot.notices = [FOURSQUARE_NOTICE] if has_foursquare else []
        snapshot.validation_results = {
            "passed": True,
            "duplicate_gers_ids": duplicate_count,
            "outside_exact_zones": outside_count,
            "permanently_closed_excluded": permanently_closed_count,
            "places_without_taxonomy": missing_taxonomy_count,
            "places_without_confidence": null_confidence_count,
            "place_limit": max_places,
        }
        snapshot.save(
            update_fields=(
                "streamed_count",
                "place_count",
                "taxonomy_code_count",
                "source_counts",
                "source_licenses",
                "notices",
                "validation_results",
                "updated_at",
            )
        )
        return activate_snapshot(snapshot.pk)
    except Exception as exc:
        _mark_failed_without_masking(
            snapshot,
            streamed_count=streamed_count,
            place_count=place_count,
            original_error=exc,
        )
        raise
