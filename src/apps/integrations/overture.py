from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from django.db.models import Case, F, IntegerField, Q, Value, When

from apps.integrations.contracts import (
    ExtractedBusiness,
    ExtractedEmail,
    ExtractionBatch,
    SearchRequest,
    ValidationProviderError,
)
from apps.overture.matching import normalize_search_text, normalize_taxonomy_code

CURSOR_VERSION = 2
MAX_PAGE_SIZE = 500
QUERY_OPERATION = "overture_places_query"


@dataclass(frozen=True, slots=True)
class OvertureSnapshotProvenance:
    snapshot_id: str
    release_id: str
    schema_version: str
    taxonomy_version: str
    importer_version: str
    mapping_version: str
    boundary_version: str
    source_uri: str
    manifest_sha256: str
    attribution: str
    notices: list[Any]
    source_counts: dict[str, Any]
    source_licenses: list[Any]


@dataclass(frozen=True, slots=True)
class OvertureDatasetZoneProvenance:
    code: str
    name: str
    boundary_hash: str
    source: str
    source_version: str
    attribution: str


@dataclass(frozen=True, slots=True)
class OverturePlaceRecord:
    overture_id: str
    name: str
    address: str
    address_data: dict[str, Any]
    websites: list[Any]
    emails: list[Any]
    phones: list[Any]
    primary_category: str
    basic_category: str
    taxonomy: dict[str, Any] | list[Any]
    taxonomy_codes: list[Any]
    latitude: Decimal | None
    longitude: Decimal | None
    confidence: Decimal | None
    operating_status: str
    sources: list[Any]
    field_provenance: dict[str, Any]
    source_licenses: list[Any]
    license: str
    source_payload_hash: str
    match_quality: int
    matched_rule_index: int
    matched_rule: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OvertureCursorPosition:
    match_quality: int
    confidence: Decimal
    overture_id: str


@dataclass(frozen=True, slots=True)
class OvertureSearchPage:
    snapshot: OvertureSnapshotProvenance
    zone: OvertureDatasetZoneProvenance
    records: tuple[OverturePlaceRecord, ...]
    has_more: bool


class OverturePlacesRepository(Protocol):
    def search(
        self,
        *,
        snapshot_id: str,
        zone_boundary_hash: str,
        category_rules: tuple[dict[str, Any], ...],
        min_confidence: Decimal,
        after: OvertureCursorPosition | None,
        limit: int,
    ) -> OvertureSearchPage: ...


def _rule_filter(rule: dict[str, Any]) -> Q:
    taxonomy_code = normalize_taxonomy_code(str(rule.get("taxonomy_code", "")))
    raw_terms = rule.get("name_terms", [])
    terms = raw_terms if isinstance(raw_terms, list) else []
    rule_filter = Q()
    if taxonomy_code:
        rule_filter &= Q(taxonomy_codes_search__contains=f"|{taxonomy_code}|")
    for raw_term in terms:
        term = str(raw_term).strip()
        is_prefix = term.endswith("*")
        literal = normalize_search_text(term[:-1] if is_prefix else term)
        if not literal:
            continue
        # `name_search` is accent-folded and padded with spaces at import time.
        # A leading space therefore marks an exact token boundary. A literal
        # phrase also requires its trailing boundary; a prefix deliberately does not.
        needle = f" {literal}" if is_prefix else f" {literal} "
        rule_filter &= Q(name_search__contains=needle)
    return rule_filter


def _rule_quality(rule: dict[str, Any]) -> int:
    raw_terms = rule.get("name_terms", [])
    term_count = len(raw_terms) if isinstance(raw_terms, list) else 0
    return term_count + (1 if rule.get("taxonomy_code") else 0)


def _ranked_rules(
    rules: tuple[dict[str, Any], ...],
) -> list[tuple[int, dict[str, Any], int]]:
    ranked = [(index, rule, _rule_quality(rule)) for index, rule in enumerate(rules)]
    return sorted(ranked, key=lambda item: (-item[2], item[0]))


class DjangoOverturePlacesRepository:
    """Read a pinned, already-imported Overture snapshot without network access."""

    def search(
        self,
        *,
        snapshot_id: str,
        zone_boundary_hash: str,
        category_rules: tuple[dict[str, Any], ...],
        min_confidence: Decimal,
        after: OvertureCursorPosition | None,
        limit: int,
    ) -> OvertureSearchPage:
        from apps.overture.models import OvertureDatasetSnapshot, OvertureDatasetZone

        snapshot = OvertureDatasetSnapshot.objects.filter(pk=snapshot_id).first()
        if snapshot is None:
            raise ValidationProviderError("El snapshot Overture seleccionado no existe.")
        if snapshot.status not in {"READY", "SUPERSEDED"}:
            raise ValidationProviderError("El snapshot Overture todavía no está disponible.")
        zone = OvertureDatasetZone.objects.filter(
            snapshot=snapshot,
            boundary_hash=zone_boundary_hash,
        ).first()
        if zone is None:
            raise ValidationProviderError(
                "El snapshot Overture no contiene la revisión geográfica seleccionada."
            )

        ranked_rules = _ranked_rules(category_rules)
        category_filter = Q(pk__in=[])
        for _, rule, _ in ranked_rules:
            category_filter |= _rule_filter(rule)
        quality_expression = Case(
            *(
                When(condition=_rule_filter(rule), then=Value(quality))
                for _, rule, quality in ranked_rules
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
        rule_index_expression = Case(
            *(
                When(condition=_rule_filter(rule), then=Value(index))
                for index, rule, _ in ranked_rules
            ),
            default=Value(-1),
            output_field=IntegerField(),
        )
        places = (
            snapshot.places.filter(
                category_filter,
                zone_links__zone=zone,
                confidence__gte=min_confidence,
            )
            .exclude(operating_status="permanently_closed")
            .annotate(
                match_quality=quality_expression,
                matched_rule_index=rule_index_expression,
            )
            .order_by(
                F("match_quality").desc(),
                F("confidence").desc(nulls_last=True),
                "overture_id",
            )
        )
        if after is not None:
            places = places.filter(
                Q(match_quality__lt=after.match_quality)
                | Q(
                    match_quality=after.match_quality,
                    confidence__lt=after.confidence,
                )
                | Q(
                    match_quality=after.match_quality,
                    confidence=after.confidence,
                    overture_id__gt=after.overture_id,
                )
            )
        selected = list(places[: limit + 1])
        has_more = len(selected) > limit
        selected = selected[:limit]
        return OvertureSearchPage(
            snapshot=OvertureSnapshotProvenance(
                snapshot_id=str(snapshot.pk),
                release_id=snapshot.release_id,
                schema_version=snapshot.schema_version,
                taxonomy_version=snapshot.taxonomy_version,
                importer_version=snapshot.importer_version,
                mapping_version=snapshot.mapping_version,
                boundary_version=snapshot.boundary_version,
                source_uri=snapshot.source_uri,
                manifest_sha256=snapshot.manifest_sha256,
                attribution=snapshot.attribution,
                notices=(snapshot.notices if isinstance(snapshot.notices, list) else []),
                source_counts=(
                    snapshot.source_counts if isinstance(snapshot.source_counts, dict) else {}
                ),
                source_licenses=(
                    snapshot.source_licenses if isinstance(snapshot.source_licenses, list) else []
                ),
            ),
            zone=OvertureDatasetZoneProvenance(
                code=zone.code,
                name=zone.name,
                boundary_hash=zone.boundary_hash,
                source=zone.source,
                source_version=zone.source_version,
                attribution=zone.attribution,
            ),
            records=tuple(
                OverturePlaceRecord(
                    overture_id=place.overture_id,
                    name=place.name,
                    address=place.address,
                    address_data=place.address_data,
                    websites=place.websites,
                    emails=place.emails,
                    phones=place.phones,
                    primary_category=place.primary_category,
                    basic_category=place.basic_category,
                    taxonomy=place.taxonomy,
                    taxonomy_codes=place.taxonomy_codes,
                    latitude=place.latitude,
                    longitude=place.longitude,
                    confidence=place.confidence,
                    operating_status=place.operating_status,
                    sources=place.sources,
                    field_provenance=place.field_provenance,
                    source_licenses=place.source_licenses,
                    license=place.license,
                    source_payload_hash=place.source_payload_hash,
                    match_quality=int(place.match_quality),
                    matched_rule_index=int(place.matched_rule_index),
                    matched_rule=category_rules[int(place.matched_rule_index)],
                )
                for place in selected
            ),
            has_more=has_more,
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _scope_hash(request: SearchRequest) -> str:
    min_confidence = (
        request.min_confidence if request.min_confidence is not None else Decimal("0.750")
    )
    scope = {
        "snapshot_id": request.dataset_snapshot_id,
        "category": request.category,
        "zone_boundary_hash": request.zone_boundary_hash,
        "criteria": request.criteria or {},
        "min_confidence": str(min_confidence),
        "limit": request.limit,
    }
    return hashlib.sha256(_canonical_json(scope).encode()).hexdigest()


def _encode_cursor(*, request: SearchRequest, after: OvertureCursorPosition) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "snapshot_id": request.dataset_snapshot_id,
        "scope": _scope_hash(request),
        "quality": after.match_quality,
        "confidence": str(after.confidence),
        "after": after.overture_id,
    }
    encoded = base64.urlsafe_b64encode(_canonical_json(payload).encode()).decode()
    return encoded.rstrip("=")


def _decode_cursor(cursor: str, *, request: SearchRequest) -> OvertureCursorPosition | None:
    if not cursor:
        return None
    if len(cursor) > 2_000:
        raise ValidationProviderError("El cursor Overture no es válido.")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationProviderError("El cursor Overture no es válido.") from exc
    if not isinstance(payload, dict):
        raise ValidationProviderError("El cursor Overture no es válido.")
    if payload.get("v") != CURSOR_VERSION:
        raise ValidationProviderError("La versión del cursor Overture no es compatible.")
    if str(payload.get("snapshot_id", "")) != request.dataset_snapshot_id:
        raise ValidationProviderError("El cursor pertenece a otro snapshot Overture.")
    cursor_scope = str(payload.get("scope", ""))
    if not hmac.compare_digest(cursor_scope, _scope_hash(request)):
        raise ValidationProviderError("El cursor pertenece a otra consulta Overture.")
    after = payload.get("after")
    if not isinstance(after, str) or not after or len(after) > 500:
        raise ValidationProviderError("El cursor Overture no contiene una posición válida.")
    quality = payload.get("quality")
    if isinstance(quality, bool) or not isinstance(quality, int) or quality < 1 or quality > 100:
        raise ValidationProviderError("El cursor Overture no contiene una calidad válida.")
    try:
        confidence = Decimal(str(payload.get("confidence", "")))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationProviderError(
            "El cursor Overture no contiene una confianza válida."
        ) from exc
    if not confidence.is_finite() or confidence < 0 or confidence > 1:
        raise ValidationProviderError("El cursor Overture no contiene una confianza válida.")
    return OvertureCursorPosition(quality, confidence, after)


def _category_rules(request: SearchRequest) -> tuple[dict[str, Any], ...]:
    criteria = request.criteria or {}
    raw_rules = criteria.get("category_rules", [])
    if not isinstance(raw_rules, list):
        raise ValidationProviderError("Las reglas de categoría Overture no son válidas.")
    rules: list[dict[str, Any]] = []
    if len(raw_rules) > 20:
        raise ValidationProviderError("La consulta contiene demasiadas reglas Overture.")
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            raise ValidationProviderError("Las reglas de categoría Overture no son válidas.")
        raw_taxonomy_code = str(raw_rule.get("taxonomy_code", "")).strip()
        taxonomy_code = normalize_taxonomy_code(raw_taxonomy_code)
        if raw_taxonomy_code and not taxonomy_code:
            raise ValidationProviderError("Una regla contiene una taxonomía inválida.")
        name_terms = raw_rule.get("name_terms", [])
        if not isinstance(name_terms, list) or len(name_terms) > 30:
            raise ValidationProviderError("Las reglas de categoría Overture no son válidas.")
        normalized_terms: list[str] = []
        for raw_term in name_terms:
            if not isinstance(raw_term, str):
                raise ValidationProviderError("Una regla contiene un término inválido.")
            term = raw_term.strip().casefold()
            if len(term) > 80 or not re.fullmatch(r"[\w\s-]+\*?", term):
                raise ValidationProviderError("Una regla contiene un término inválido.")
            is_prefix = term.endswith("*")
            literal = normalize_search_text(term[:-1] if is_prefix else term)
            if not literal or (is_prefix and " " in literal):
                raise ValidationProviderError("Una regla contiene un término inválido.")
            normalized = f"{literal}*" if is_prefix else literal
            if normalized not in normalized_terms:
                normalized_terms.append(normalized)
        if not taxonomy_code and not normalized_terms:
            raise ValidationProviderError("Una regla Overture está vacía.")
        rules.append(
            {
                "taxonomy_code": taxonomy_code,
                "name_terms": normalized_terms,
            }
        )
    if not rules:
        raise ValidationProviderError("La consulta no tiene reglas de categoría Overture.")
    return tuple(rules)


def _minimum_confidence(request: SearchRequest) -> Decimal:
    try:
        raw_value = request.min_confidence if request.min_confidence is not None else "0.750"
        value = Decimal(str(raw_value))
    except InvalidOperation as exc:
        raise ValidationProviderError("La confianza mínima Overture no es válida.") from exc
    if not value.is_finite() or value < 0 or value > 1:
        raise ValidationProviderError("La confianza mínima Overture debe estar entre 0 y 1.")
    return value


def _first_contact_value(values: object, *keys: str) -> str | None:
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            for key in keys:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _emails(values: object) -> tuple[ExtractedEmail, ...]:
    if not isinstance(values, list):
        return ()
    result: list[ExtractedEmail] = []
    for index, item in enumerate(values):
        value = item if isinstance(item, str) else ""
        source = "overture"
        is_primary = index == 0
        if isinstance(item, dict):
            for key in ("value", "email", "address"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    value = candidate
                    break
            source = str(item.get("source", "overture"))
            is_primary = bool(item.get("is_primary", index == 0))
        if isinstance(value, str) and value.strip():
            result.append(
                ExtractedEmail(
                    value=value.strip(),
                    source=source,
                    is_primary=is_primary,
                    order=index,
                )
            )
    return tuple(result)


def _record_sort_key(record: OverturePlaceRecord) -> tuple[int, Decimal, str]:
    if record.confidence is None:
        raise ValidationProviderError("Un resultado filtrado no puede omitir confidence.")
    return (-record.match_quality, -record.confidence, record.overture_id)


def _cursor_sort_key(position: OvertureCursorPosition) -> tuple[int, Decimal, str]:
    return (-position.match_quality, -position.confidence, position.overture_id)


class OverturePlacesProvider:
    """Query immutable local Overture Places data; this adapter never opens a socket."""

    def __init__(self, repository: OverturePlacesRepository | None = None) -> None:
        self.repository = repository or DjangoOverturePlacesRepository()

    def search(self, request: SearchRequest) -> ExtractionBatch:
        if not request.dataset_snapshot_id:
            raise ValidationProviderError("La consulta no fijó un snapshot Overture.")
        if not request.zone_boundary_hash:
            raise ValidationProviderError("La consulta no fijó una revisión geográfica.")
        if request.limit < 1 or request.limit > MAX_PAGE_SIZE:
            raise ValidationProviderError(
                f"El límite Overture debe estar entre 1 y {MAX_PAGE_SIZE}."
            )
        rules = _category_rules(request)
        minimum_confidence = _minimum_confidence(request)
        after = _decode_cursor(request.cursor, request=request)
        page = self.repository.search(
            snapshot_id=request.dataset_snapshot_id,
            zone_boundary_hash=request.zone_boundary_hash,
            category_rules=rules,
            min_confidence=minimum_confidence,
            after=after,
            limit=request.limit,
        )
        if page.snapshot.snapshot_id != request.dataset_snapshot_id:
            raise ValidationProviderError("El repositorio devolvió otro snapshot Overture.")
        if page.zone.boundary_hash != request.zone_boundary_hash:
            raise ValidationProviderError("El repositorio devolvió otra revisión geográfica.")
        record_ids = [record.overture_id for record in page.records]
        if len(page.records) > request.limit:
            raise ValidationProviderError("La página Overture superó el límite solicitado.")
        record_keys = [_record_sort_key(record) for record in page.records]
        if len(record_ids) != len(set(record_ids)) or record_keys != sorted(record_keys):
            raise ValidationProviderError("La página Overture no tiene un orden estable.")
        if any(not overture_id or len(overture_id) > 500 for overture_id in record_ids):
            raise ValidationProviderError("La página Overture contiene identificadores inválidos.")
        if after is not None and any(key <= _cursor_sort_key(after) for key in record_keys):
            raise ValidationProviderError("La página Overture no avanzó el cursor.")
        if page.has_more and not page.records:
            raise ValidationProviderError("La página Overture no puede avanzar de forma segura.")

        next_cursor = ""
        if page.has_more:
            next_cursor = _encode_cursor(
                request=request,
                after=OvertureCursorPosition(
                    page.records[-1].match_quality,
                    page.records[-1].confidence or Decimal("0"),
                    page.records[-1].overture_id,
                ),
            )
        page_hash = hashlib.sha256(
            f"overture:{_scope_hash(request)}:{request.cursor}".encode()
        ).hexdigest()
        snapshot_payload = {
            "id": page.snapshot.snapshot_id,
            "release_id": page.snapshot.release_id,
            "schema_version": page.snapshot.schema_version,
            "taxonomy_version": page.snapshot.taxonomy_version,
            "importer_version": page.snapshot.importer_version,
            "mapping_version": page.snapshot.mapping_version,
            "boundary_version": page.snapshot.boundary_version,
            "source_uri": page.snapshot.source_uri,
            "manifest_sha256": page.snapshot.manifest_sha256,
            "attribution": page.snapshot.attribution,
            "notices": page.snapshot.notices,
            "source_counts": page.snapshot.source_counts,
            "source_licenses": page.snapshot.source_licenses,
        }
        zone_payload = {
            "code": page.zone.code,
            "name": page.zone.name,
            "boundary_hash": page.zone.boundary_hash,
            "source": page.zone.source,
            "source_version": page.zone.source_version,
            "attribution": page.zone.attribution,
        }
        rows = [
            {
                "overture_id": record.overture_id,
                "name": record.name,
                "address": record.address,
                "address_data": record.address_data,
                "websites": record.websites,
                "emails": record.emails,
                "phones": record.phones,
                "primary_category": record.primary_category,
                "basic_category": record.basic_category,
                "taxonomy": record.taxonomy,
                "taxonomy_codes": record.taxonomy_codes,
                "latitude": str(record.latitude) if record.latitude is not None else None,
                "longitude": str(record.longitude) if record.longitude is not None else None,
                "confidence": str(record.confidence) if record.confidence is not None else None,
                "operating_status": record.operating_status,
                "match_quality": record.match_quality,
                "matched_rule_index": record.matched_rule_index,
                "matched_rule": record.matched_rule,
                "provenance": {
                    "sources": record.sources,
                    "field_provenance": record.field_provenance,
                    "source_licenses": record.source_licenses,
                    "license": record.license,
                    "source_payload_hash": record.source_payload_hash,
                },
            }
            for record in page.records
        ]
        raw_payload: dict[str, Any] = {
            "status": "SUCCEEDED",
            "page_hash": page_hash,
            "provider": "overture",
            "snapshot": snapshot_payload,
            "zone": zone_payload,
            "query": {
                "category": request.category,
                "category_rules": list(rules),
                "minimum_confidence": str(minimum_confidence),
                "cursor": (
                    {
                        "match_quality": after.match_quality,
                        "confidence": str(after.confidence),
                        "overture_id": after.overture_id,
                    }
                    if after is not None
                    else None
                ),
                "limit": request.limit,
            },
            "data": rows,
            "next_cursor": next_cursor,
        }
        usage_metadata = {
            "provider": "overture",
            "execution": "local_snapshot",
            "snapshot": snapshot_payload,
            "zone": zone_payload,
            "result_count": len(rows),
            "page_hash": page_hash,
            "rules_revision": (request.criteria or {}).get("rules_revision"),
            "next_cursor": next_cursor,
        }
        return ExtractionBatch(
            status="SUCCEEDED",
            raw_payload=raw_payload,
            operation=QUERY_OPERATION,
            next_cursor=next_cursor,
            exhausted=not page.has_more,
            units=Decimal(len(rows)),
            estimated_cost=Decimal("0"),
            actual_cost=Decimal("0"),
            currency="USD",
            usage_metadata=usage_metadata,
            records=self.parse_response(raw_payload),
        )

    def parse_response(self, raw_payload: dict[str, Any]) -> tuple[ExtractedBusiness, ...]:
        raw_rows = raw_payload.get("data", [])
        if not isinstance(raw_rows, list):
            return ()
        snapshot = raw_payload.get("snapshot", {})
        zone = raw_payload.get("zone", {})
        records: list[ExtractedBusiness] = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            overture_id = str(row.get("overture_id", "")).strip()
            name = str(row.get("name", "")).strip()
            if not overture_id or not name:
                continue
            latitude = self._decimal_or_none(row.get("latitude"))
            longitude = self._decimal_or_none(row.get("longitude"))
            provenance = row.get("provenance", {})
            records.append(
                ExtractedBusiness(
                    provider_id=overture_id,
                    name=name,
                    address=str(row.get("address", "")),
                    email_candidates=_emails(row.get("emails")),
                    website=_first_contact_value(row.get("websites"), "value", "url", "website"),
                    phone=_first_contact_value(row.get("phones"), "value", "phone", "number"),
                    category=(
                        str(row["primary_category"]) if row.get("primary_category") else None
                    ),
                    latitude=latitude,
                    longitude=longitude,
                    provider_data={
                        "overture_id": overture_id,
                        "snapshot": snapshot,
                        "zone": zone,
                        "taxonomy": row.get("taxonomy", {}),
                        "taxonomy_codes": row.get("taxonomy_codes", []),
                        "basic_category": row.get("basic_category", ""),
                        "matched_rule": row.get("matched_rule", {}),
                        "matched_rule_index": row.get("matched_rule_index"),
                        "match_quality": row.get("match_quality"),
                        "address_data": row.get("address_data", {}),
                        "confidence": row.get("confidence"),
                        "operating_status": row.get("operating_status", ""),
                        "provenance": provenance if isinstance(provenance, dict) else {},
                    },
                )
            )
        return tuple(records)

    @staticmethod
    def _decimal_or_none(value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            parsed = Decimal(str(value))
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
