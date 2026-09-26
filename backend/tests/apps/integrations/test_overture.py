from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from apps.integrations.contracts import SearchRequest, ValidationProviderError
from apps.integrations.overture import (
    OvertureCursorPosition,
    OvertureDatasetZoneProvenance,
    OverturePlaceRecord,
    OverturePlacesProvider,
    OvertureSearchPage,
    OvertureSnapshotProvenance,
)
from apps.overture.geometry import validate_geojson
from apps.overture.importer import OVERTURE_ATTRIBUTION, activate_snapshot
from apps.overture.matching import padded_name_search, taxonomy_codes_search
from apps.overture.models import (
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
)
from apps.overture.releases import official_places_source_uri

TEST_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[-59, -35], [-58, -35], [-58, -34], [-59, -34], [-59, -35]]],
}
TEST_BOUNDARY = validate_geojson(TEST_GEOMETRY)


def _place(identifier: str, *, email: str = "") -> OverturePlaceRecord:
    return OverturePlaceRecord(
        overture_id=identifier,
        name=f"Taller {identifier}",
        address="Palermo, CABA",
        address_data={"country": "AR"},
        websites=[{"url": f"https://{identifier}.example"}],
        emails=[{"value": email, "source": "source-record"}] if email else [],
        phones=[{"number": "+54 11 5555-0000"}],
        primary_category="b2b_equipment_maintenance_and_repair",
        basic_category="services_and_business",
        taxonomy={"alternate": ["industrial_service"]},
        taxonomy_codes=["b2b_equipment_maintenance_and_repair"],
        latitude=Decimal("-34.58"),
        longitude=Decimal("-58.42"),
        confidence=Decimal("0.91"),
        operating_status="open",
        sources=[{"dataset": "Overture", "record_id": identifier}],
        field_provenance={"/names/primary": [{"dataset": "meta"}]},
        source_licenses=["CDLA-Permissive-2.0"],
        license="Overture Maps data license",
        source_payload_hash=f"hash-{identifier}",
        match_quality=2,
        matched_rule_index=0,
        matched_rule={
            "taxonomy_code": "b2b_equipment_maintenance_and_repair",
            "name_terms": ["bobinad*"],
        },
    )


class StubRepository:
    def __init__(self) -> None:
        self.records = (_place("place-001", email="ventas@example.com"), _place("place-002"))
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        *,
        snapshot_id: str,
        zone_boundary_hash: str,
        category_rules: tuple[dict[str, object], ...],
        min_confidence: Decimal,
        after: OvertureCursorPosition | None,
        limit: int,
    ) -> OvertureSearchPage:
        self.calls.append(
            {
                "snapshot_id": snapshot_id,
                "zone_boundary_hash": zone_boundary_hash,
                "category_rules": category_rules,
                "min_confidence": min_confidence,
                "after": after,
                "limit": limit,
            }
        )
        start = 0
        if after is not None:
            start = next(
                index + 1
                for index, record in enumerate(self.records)
                if record.overture_id == after.overture_id
            )
        records = self.records[start : start + limit]
        return OvertureSearchPage(
            snapshot=OvertureSnapshotProvenance(
                snapshot_id=snapshot_id,
                release_id="2026-07-15.0",
                schema_version="1.1.0",
                taxonomy_version="taxonomy-v1",
                importer_version="importer-v1",
                mapping_version="category-map-v1",
                boundary_version="caba-v3",
                source_uri="s3://overturemaps-us-west-2/release/2026-07-15.0",
                manifest_sha256="a" * 64,
                attribution="Overture Maps Foundation",
                notices=[],
                source_counts={"meta": 2},
                source_licenses=["CDLA-Permissive-2.0"],
            ),
            zone=OvertureDatasetZoneProvenance(
                code="palermo",
                name="Palermo",
                boundary_hash=zone_boundary_hash,
                source="Buenos Aires Data",
                source_version="2026-01",
                attribution="CC-BY-2.5-AR",
            ),
            records=records,
            has_more=start + len(records) < len(self.records),
        )


def _request(**overrides: object) -> SearchRequest:
    values: dict[str, object] = {
        "query": "Bobinados de motores en Palermo",
        "correlation_id": "run-1",
        "idempotency_key": "extract:query-1:1",
        "category": "Bobinados de motores",
        "zone": "Palermo",
        "location": "CABA",
        "criteria": {
            "category_rules": [
                {
                    "taxonomy_code": "b2b_equipment_maintenance_and_repair",
                    "name_terms": ["bobinad*"],
                }
            ]
        },
        "zone_boundary_hash": TEST_BOUNDARY.sha256,
        "min_confidence": Decimal("0.800"),
        "dataset_snapshot_id": "11111111-1111-1111-1111-111111111111",
        "limit": 1,
    }
    values.update(overrides)
    return SearchRequest(**values)  # type: ignore[arg-type]


def _catalog() -> tuple[OvertureDatasetSnapshot, OvertureDatasetZone]:
    manifest = hashlib.sha256(
        json.dumps(
            [{"code": "palermo", "boundary_hash": TEST_BOUNDARY.sha256}],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    snapshot = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="importer-v1",
        mapping_version="category-map-v1",
        boundary_version="caba-v3",
        boundary_manifest_sha256=manifest,
        source_uri=official_places_source_uri("2026-07-22.0"),
        manifest_sha256="a" * 64,
        status=OvertureDatasetSnapshot.Status.IMPORTING,
        is_active=False,
        zone_count=1,
        attribution=OVERTURE_ATTRIBUTION,
    )
    zone = OvertureDatasetZone.objects.create(
        snapshot=snapshot,
        code="palermo",
        name="Palermo",
        normalized_name="palermo",
        geometry=TEST_BOUNDARY.geojson,
        bbox=list(TEST_BOUNDARY.bbox),
        boundary_hash=TEST_BOUNDARY.sha256,
        source="Buenos Aires Data",
        source_version="2026-01",
    )
    return snapshot, zone


def _catalog_place(
    snapshot: OvertureDatasetSnapshot,
    zone: OvertureDatasetZone,
    identifier: str,
    *,
    name: str,
    taxonomy_codes: list[str] | None = None,
    confidence: Decimal | None = Decimal("0.9000"),
    status: str = "open",
    **overrides: Any,
) -> OverturePlace:
    codes = (
        taxonomy_codes if taxonomy_codes is not None else ["b2b_equipment_maintenance_and_repair"]
    )
    values: dict[str, Any] = {
        "snapshot": snapshot,
        "overture_id": identifier,
        "name": name,
        "address": "CABA",
        "websites": ["https://example.com"],
        "emails": [],
        "phones": [],
        "primary_category": codes[0] if codes else "",
        "basic_category": "services_and_business",
        "taxonomy": {},
        "taxonomy_codes": codes,
        "taxonomy_codes_search": taxonomy_codes_search(codes),
        "normalized_name": name.casefold(),
        "name_search": padded_name_search(name),
        "latitude": Decimal("-34.5800000"),
        "longitude": Decimal("-58.4200000"),
        "confidence": confidence,
        "operating_status": status,
        "sources": [{"dataset": "Overture"}],
        "field_provenance": {"/": [{"dataset": "Overture"}]},
        "source_licenses": [],
        "license": "",
        "source_payload_hash": hashlib.sha256(identifier.encode()).hexdigest(),
    }
    values.update(overrides)
    place = OverturePlace.objects.create(**values)
    OverturePlaceZone.objects.create(snapshot=snapshot, place=place, zone=zone)
    return place


def _activate_catalog(snapshot: OvertureDatasetSnapshot) -> OvertureDatasetSnapshot:
    place_count = snapshot.places.count()
    snapshot.streamed_count = place_count
    snapshot.place_count = place_count
    snapshot.zone_count = snapshot.zones.count()
    snapshot.taxonomy_code_count = snapshot.taxonomy_codes.count()
    snapshot.source_counts = {"Overture": place_count} if place_count else {}
    snapshot.source_licenses = []
    snapshot.notices = []
    snapshot.validation_results = {"passed": True, "place_limit": 500_000}
    snapshot.save(
        update_fields=(
            "streamed_count",
            "place_count",
            "zone_count",
            "taxonomy_code_count",
            "source_counts",
            "source_licenses",
            "notices",
            "validation_results",
            "updated_at",
        )
    )
    return activate_snapshot(snapshot.pk)


def test_search_is_zero_cost_provenance_rich_and_keyset_paginated() -> None:
    repository = StubRepository()
    provider = OverturePlacesProvider(repository)
    request = _request()

    first = provider.search(request)
    replay = provider.search(request)
    second = provider.search(
        replace(
            request,
            idempotency_key="extract:query-1:2",
            cursor=first.next_cursor,
        )
    )

    assert first == replay
    assert first.operation == "overture_places_query"
    assert first.estimated_cost == first.actual_cost == Decimal("0")
    assert first.units == Decimal("1")
    assert first.next_cursor
    assert first.raw_payload["snapshot"]["release_id"] == "2026-07-15.0"
    assert first.raw_payload["zone"]["boundary_hash"] == TEST_BOUNDARY.sha256
    assert first.raw_payload["data"][0]["provenance"]["sources"][0]["dataset"] == "Overture"
    assert first.records[0].email_candidates[0].value == "ventas@example.com"
    assert first.records[0].provider_data is not None
    assert first.records[0].provider_data["snapshot"]["mapping_version"] == "category-map-v1"
    assert second.records[0].provider_id == "place-002"
    assert second.next_cursor == ""
    after = repository.calls[-1]["after"]
    assert isinstance(after, OvertureCursorPosition)
    assert after.overture_id == "place-001"


def test_page_hash_covers_page_size_but_not_the_callers_idempotency_label() -> None:
    provider = OverturePlacesProvider(StubRepository())

    first = provider.search(_request(idempotency_key="attempt-one"))
    relabelled = provider.search(_request(idempotency_key="attempt-two"))
    larger_page = provider.search(_request(idempotency_key="attempt-one", limit=2))

    assert first.raw_payload["page_hash"] == relabelled.raw_payload["page_hash"]
    assert first.raw_payload["page_hash"] != larger_page.raw_payload["page_hash"]


def test_cursor_is_bound_to_snapshot_and_structured_query_scope() -> None:
    provider = OverturePlacesProvider(StubRepository())
    first = provider.search(_request())

    with pytest.raises(ValidationProviderError, match="otra consulta"):
        provider.search(
            _request(
                cursor=first.next_cursor,
                zone_boundary_hash="different-boundary",
            )
        )

    with pytest.raises(ValidationProviderError, match="otro snapshot"):
        provider.search(
            _request(
                cursor=first.next_cursor,
                dataset_snapshot_id="22222222-2222-2222-2222-222222222222",
            )
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"dataset_snapshot_id": ""}, "snapshot"),
        ({"zone_boundary_hash": ""}, "revisión geográfica"),
        ({"criteria": {}}, "reglas de categoría"),
        ({"min_confidence": Decimal("1.1")}, "entre 0 y 1"),
        ({"min_confidence": Decimal("NaN")}, "entre 0 y 1"),
        ({"limit": 0}, "límite Overture"),
        ({"cursor": "not-base64!"}, "cursor Overture"),
    ],
)
def test_search_rejects_unfrozen_or_broad_inputs(
    overrides: dict[str, object], message: str
) -> None:
    provider = OverturePlacesProvider(StubRepository())

    with pytest.raises(ValidationProviderError, match=message):
        provider.search(_request(**overrides))


def test_zero_is_a_valid_explicit_confidence_threshold() -> None:
    repository = StubRepository()
    provider = OverturePlacesProvider(repository)

    provider.search(_request(min_confidence=Decimal("0"), limit=2))

    assert repository.calls[-1]["min_confidence"] == Decimal("0")


@pytest.mark.parametrize(
    "term",
    (
        "motor.*",
        "(motor)",
        "motor?",
        "*motor",
        "motor**",
        "motor|bomba",
        "motor[es]",
        "motor eléctrico*",
    ),
)
def test_search_rejects_regex_and_non_trailing_token_patterns(term: str) -> None:
    repository = StubRepository()
    provider = OverturePlacesProvider(repository)

    with pytest.raises(ValidationProviderError, match="término inválido"):
        provider.search(
            _request(
                criteria={
                    "category_rules": [
                        {
                            "taxonomy_code": "",
                            "name_terms": [term],
                        }
                    ]
                }
            )
        )

    assert repository.calls == []


@pytest.mark.django_db
def test_name_rules_are_accent_insensitive_exact_phrases_or_token_prefixes() -> None:
    snapshot, zone = _catalog()
    _catalog_place(
        snapshot,
        zone,
        "exact-phrase",
        name="Taller de REPARACION de Motores Industriales",
        confidence=Decimal("0.9500"),
    )
    _catalog_place(
        snapshot,
        zone,
        "token-prefix",
        name="Servicio motorizado",
        confidence=Decimal("0.9000"),
    )
    _catalog_place(
        snapshot,
        zone,
        "phrase-not-token",
        name="Reparación de Motoress",
        confidence=Decimal("0.9900"),
    )
    _catalog_place(
        snapshot,
        zone,
        "prefix-not-inside-token",
        name="Automotorización integral",
        confidence=Decimal("0.9900"),
    )
    snapshot = _activate_catalog(snapshot)

    batch = OverturePlacesProvider().search(
        _request(
            dataset_snapshot_id=str(snapshot.pk),
            criteria={
                "category_rules": [
                    {"taxonomy_code": "", "name_terms": ["reparación de motores"]},
                    {"taxonomy_code": "", "name_terms": ["motoriz*"]},
                ]
            },
            limit=20,
        )
    )

    assert [record.provider_id for record in batch.records] == [
        "exact-phrase",
        "token-prefix",
    ]
    assert batch.records[0].provider_data is not None
    assert batch.records[0].provider_data["matched_rule"]["name_terms"] == ["reparacion de motores"]


@pytest.mark.django_db
def test_taxonomy_and_terms_are_anded_while_rules_are_ored() -> None:
    snapshot, zone = _catalog()
    repair_code = "b2b_equipment_maintenance_and_repair"
    hardware_code = "hardware_store"
    _catalog_place(
        snapshot,
        zone,
        "matches-both",
        name="Bobinados del Sur",
        taxonomy_codes=[repair_code],
        confidence=Decimal("0.8000"),
    )
    _catalog_place(
        snapshot,
        zone,
        "taxonomy-only",
        name="Mecánica general",
        taxonomy_codes=[repair_code],
        confidence=Decimal("0.9900"),
    )
    _catalog_place(
        snapshot,
        zone,
        "term-only",
        name="Bobinados sin categoría",
        taxonomy_codes=["automotive_service"],
        confidence=Decimal("0.9900"),
    )
    _catalog_place(
        snapshot,
        zone,
        "matches-or-rule",
        name="Casa Industrial",
        taxonomy_codes=[hardware_code],
        confidence=Decimal("0.9900"),
    )
    snapshot = _activate_catalog(snapshot)

    batch = OverturePlacesProvider().search(
        _request(
            dataset_snapshot_id=str(snapshot.pk),
            criteria={
                "category_rules": [
                    {"taxonomy_code": repair_code, "name_terms": ["bobinad*"]},
                    {"taxonomy_code": hardware_code, "name_terms": []},
                ]
            },
            limit=20,
        )
    )

    assert [record.provider_id for record in batch.records] == [
        "matches-both",
        "matches-or-rule",
    ]
    assert batch.records[0].provider_data is not None
    assert batch.records[1].provider_data is not None
    assert batch.records[0].provider_data["match_quality"] == 2
    assert batch.records[1].provider_data["match_quality"] == 1


@pytest.mark.django_db
def test_confidence_threshold_is_inclusive_and_null_confidence_fails_closed() -> None:
    snapshot, zone = _catalog()
    _catalog_place(
        snapshot,
        zone,
        "at-threshold",
        name="Taller exacto",
        confidence=Decimal("0.7500"),
    )
    _catalog_place(
        snapshot,
        zone,
        "below-threshold",
        name="Taller bajo",
        confidence=Decimal("0.7499"),
    )
    _catalog_place(
        snapshot,
        zone,
        "missing-confidence",
        name="Taller sin confianza",
        confidence=None,
    )
    snapshot = _activate_catalog(snapshot)
    criteria = {"category_rules": [{"taxonomy_code": "", "name_terms": ["taller"]}]}

    threshold_batch = OverturePlacesProvider().search(
        _request(
            dataset_snapshot_id=str(snapshot.pk),
            criteria=criteria,
            min_confidence=Decimal("0.7500"),
            limit=20,
        )
    )
    zero_batch = OverturePlacesProvider().search(
        _request(
            dataset_snapshot_id=str(snapshot.pk),
            criteria=criteria,
            min_confidence=Decimal("0"),
            limit=20,
        )
    )

    assert [record.provider_id for record in threshold_batch.records] == ["at-threshold"]
    assert [record.provider_id for record in zero_batch.records] == [
        "at-threshold",
        "below-threshold",
    ]


@pytest.mark.django_db
def test_keyset_pagination_is_stable_across_quality_confidence_and_id_ties() -> None:
    snapshot, zone = _catalog()
    repair_code = "b2b_equipment_maintenance_and_repair"
    _catalog_place(
        snapshot,
        zone,
        "quality-2-high",
        name="Bobinados Alfa",
        taxonomy_codes=[repair_code],
        confidence=Decimal("0.9500"),
    )
    _catalog_place(
        snapshot,
        zone,
        "quality-2-tie-a",
        name="Bobinados Beta",
        taxonomy_codes=[repair_code],
        confidence=Decimal("0.9000"),
    )
    _catalog_place(
        snapshot,
        zone,
        "quality-2-tie-b",
        name="Bobinados Gamma",
        taxonomy_codes=[repair_code],
        confidence=Decimal("0.9000"),
    )
    _catalog_place(
        snapshot,
        zone,
        "quality-1-higher-confidence",
        name="Taller general",
        taxonomy_codes=["automotive_service"],
        confidence=Decimal("0.9900"),
    )
    snapshot = _activate_catalog(snapshot)
    request = _request(
        dataset_snapshot_id=str(snapshot.pk),
        criteria={
            "category_rules": [
                {"taxonomy_code": repair_code, "name_terms": ["bobinad*"]},
                {"taxonomy_code": "", "name_terms": ["taller"]},
            ]
        },
        limit=2,
    )

    first = OverturePlacesProvider().search(request)
    first_replay = OverturePlacesProvider().search(request)
    second_request = replace(
        request,
        idempotency_key="extract:query-1:2",
        cursor=first.next_cursor,
    )
    second = OverturePlacesProvider().search(second_request)
    second_replay = OverturePlacesProvider().search(second_request)

    assert first == first_replay
    assert second == second_replay
    assert [record.provider_id for record in first.records] == [
        "quality-2-high",
        "quality-2-tie-a",
    ]
    assert [record.provider_id for record in second.records] == [
        "quality-2-tie-b",
        "quality-1-higher-confidence",
    ]
    assert first.next_cursor
    assert second.next_cursor == ""
    assert second.exhausted


@pytest.mark.parametrize(
    "overrides",
    (
        {"category": "Otra categoría"},
        {"min_confidence": Decimal("0.810")},
        {
            "criteria": {
                "category_rules": [
                    {
                        "taxonomy_code": "b2b_equipment_maintenance_and_repair",
                        "name_terms": ["motor*"],
                    }
                ]
            }
        },
        {
            "criteria": {
                "category_rules": [
                    {
                        "taxonomy_code": "b2b_equipment_maintenance_and_repair",
                        "name_terms": ["bobinad*"],
                    }
                ],
                "rules_revision": 2,
            }
        },
        {"limit": 2},
    ),
)
def test_cursor_is_bound_to_rules_confidence_category_and_revision(
    overrides: dict[str, object],
) -> None:
    provider = OverturePlacesProvider(StubRepository())
    first = provider.search(_request())

    with pytest.raises(ValidationProviderError, match="otra consulta"):
        provider.search(_request(cursor=first.next_cursor, **overrides))


def test_human_readable_query_label_does_not_change_cursor_scope() -> None:
    provider = OverturePlacesProvider(StubRepository())
    first = provider.search(_request())

    second = provider.search(
        _request(
            query="Etiqueta de auditoría editada",
            idempotency_key="extract:query-1:2",
            cursor=first.next_cursor,
        )
    )

    assert [record.provider_id for record in second.records] == ["place-002"]


@pytest.mark.django_db
def test_django_repository_applies_snapshot_zone_rule_status_and_confidence_filters() -> None:
    snapshot, target_zone = _catalog()
    _catalog_place(snapshot, target_zone, "place-002", name="Bobinados Norte")
    _catalog_place(snapshot, target_zone, "place-001", name="Bobinados Centro")
    _catalog_place(
        snapshot,
        target_zone,
        "place-low",
        name="Bobinados Baja",
        confidence=Decimal("0.7000"),
    )
    _catalog_place(snapshot, target_zone, "place-wrong-name", name="Mecánica general")
    snapshot = _activate_catalog(snapshot)

    batch = OverturePlacesProvider().search(
        _request(
            dataset_snapshot_id=str(snapshot.pk),
            limit=20,
        )
    )

    assert [record.provider_id for record in batch.records] == ["place-001", "place-002"]
