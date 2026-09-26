from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.overture.geometry import validate_geojson
from apps.overture.importer import OVERTURE_ATTRIBUTION, activate_snapshot
from apps.overture.models import (
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
    OvertureReleaseCheck,
)
from apps.overture.releases import OFFICIAL_STAC_URL
from apps.overture.tasks import sync_snapshot


def _catalog_check() -> OvertureReleaseCheck:
    return OvertureReleaseCheck.objects.create(
        status=OvertureReleaseCheck.Status.SUCCEEDED,
        releases=["2026-06-24.0", "2026-07-22.0"],
        latest_release="2026-07-22.0",
        catalog_url=OFFICIAL_STAC_URL,
        manifest_sha256="f" * 64,
    )


def _snapshot(
    release_id: str,
    *,
    status: str,
    is_active: bool = False,
    error: str = "",
    boundary_manifest_sha256: str = "b" * 64,
) -> OvertureDatasetSnapshot:
    return OvertureDatasetSnapshot.objects.create(
        release_id=release_id,
        schema_version="v1.18.0" if release_id == "2026-07-22.0" else "v1.17.0",
        taxonomy_version=release_id,
        importer_version="contact-outreach-importer-v1",
        mapping_version="contact-outreach-v1",
        boundary_version=f"boundaries-{release_id}",
        boundary_manifest_sha256=boundary_manifest_sha256,
        source_uri=(f"s3://overturemaps-us-west-2/release/{release_id}/theme=places/type=place/"),
        manifest_sha256="a" * 64,
        status=status,
        is_active=is_active,
        streamed_count=13,
        place_count=12,
        zone_count=1,
        taxonomy_code_count=7,
        attribution=OVERTURE_ATTRIBUTION,
        error=error,
    )


@pytest.mark.django_db
def test_dataset_dashboard_and_sync_require_login_and_csrf(owner: User) -> None:
    assert Client().get(reverse("overture-datasets")).status_code == 302
    anonymous_post = Client().post(
        reverse("overture-sync"),
        {"release_id": "2026-07-22.0"},
    )
    assert anonymous_post.status_code == 302

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert csrf_client.get(reverse("overture-sync")).status_code == 405
    response = csrf_client.post(
        reverse("overture-sync"),
        {"release_id": "2026-07-22.0"},
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_dataset_dashboard_shows_status_coverage_counts_errors_and_attribution(
    client: Client,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = validate_geojson(
        {
            "type": "Polygon",
            "coordinates": [[[-59, -35], [-58, -35], [-58, -34], [-59, -34], [-59, -35]]],
        }
    )
    manifest_sha256 = hashlib.sha256(
        json.dumps(
            [{"code": "palermo", "boundary_hash": boundary.sha256}],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    active = _snapshot(
        "2026-06-17.0",
        status=OvertureDatasetSnapshot.Status.IMPORTING,
        is_active=False,
        boundary_manifest_sha256=manifest_sha256,
    )
    OvertureDatasetZone.objects.create(
        snapshot=active,
        code="palermo",
        name="Palermo",
        normalized_name="palermo",
        geometry=boundary.geojson,
        bbox=list(boundary.bbox),
        boundary_hash=boundary.sha256,
        source="Buenos Aires Data · CC-BY-2.5-AR",
        source_version="revision-3",
    )
    place = OverturePlace.objects.create(
        snapshot=active,
        overture_id="gers-fixture",
        name="Taller Fixture",
        names={"primary": "Taller Fixture"},
        latitude=Decimal("-34.5000000"),
        longitude=Decimal("-58.5000000"),
        confidence=Decimal("0.9100"),
        sources=[{"dataset": "meta", "record_id": "fixture"}],
        field_provenance={"/": [{"dataset": "meta", "record_id": "fixture"}]},
        source_licenses=["CDLA-Permissive-2.0"],
        license="CDLA-Permissive-2.0",
        source_payload_hash="d" * 64,
    )
    OverturePlaceZone.objects.create(snapshot=active, place=place, zone=active.zones.get())
    active.streamed_count = 1
    active.place_count = 1
    active.zone_count = 1
    active.taxonomy_code_count = 0
    active.source_counts = {"meta": 1}
    active.source_licenses = ["CDLA-Permissive-2.0"]
    active.validation_results = {"passed": True}
    active.save(
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
    active = activate_snapshot(active.pk)
    _snapshot(
        "2026-07-22.0",
        status=OvertureDatasetSnapshot.Status.FAILED,
        error="OvertureSchemaError: la importación no se pudo completar.",
    )
    _catalog_check()
    client.force_login(owner)

    response = client.get(reverse("overture-datasets"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert "2026-06-17.0" in content
    assert "2026-07-22.0" in content
    assert "1 lugares" in content
    assert boundary.sha256 in content
    assert "Buenos Aires Data · CC-BY-2.5-AR" in content
    assert "CDLA-Permissive-2.0" in content
    assert "OvertureSchemaError: la importación no se pudo completar." in content
    assert "© Overture Maps Foundation y sus colaboradores" in content
    assert reverse("overture-datasets") in content

    integrations = client.get(reverse("integrations"))
    integration_content = integrations.content.decode()
    assert integrations.status_code == 200
    assert "Copia local activa:" in integration_content
    assert "2026-06-17.0" in integration_content
    assert "Última sincronización registrada:" in integration_content
    assert "2026-07-22.0" in integration_content


@pytest.mark.django_db
def test_sync_queues_only_an_officially_discovered_release_and_audits_it(
    client: Client,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_check()
    delay_mock = Mock()
    monkeypatch.setattr(sync_snapshot, "delay", delay_mock)
    client.force_login(owner)

    response = client.post(
        reverse("overture-sync"),
        {"release_id": "2026-07-22.0", "province_code": "02"},
    )

    assert response.status_code == 302
    assert response.url == reverse("overture-datasets")
    delay_mock.assert_called_once_with("2026-07-22.0", "02")
    audit = AuditEvent.objects.get(action="overture.sync_queued")
    assert audit.actor == owner
    assert audit.entity_type == "OvertureRelease"
    assert audit.entity_id == "2026-07-22.0"
    assert audit.after == {
        "release_id": "2026-07-22.0",
        "province_code": "02",
        "queue": "maintenance",
    }


@pytest.mark.django_db
def test_sync_rejects_unlisted_releases_and_arbitrary_source_fields(
    client: Client,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_check()
    delay_mock = Mock()
    monkeypatch.setattr(sync_snapshot, "delay", delay_mock)
    client.force_login(owner)

    unlisted = client.post(
        reverse("overture-sync"),
        {"release_id": "2026-05-20.0"},
    )
    listed_but_not_latest = client.post(
        reverse("overture-sync"),
        {"release_id": "2026-06-24.0"},
    )
    arbitrary_source = client.post(
        reverse("overture-sync"),
        {
            "release_id": "2026-07-22.0",
            "source_url": "https://attacker.example/catalog.json",
        },
    )

    assert unlisted.status_code == 400
    assert listed_but_not_latest.status_code == 400
    assert arbitrary_source.status_code == 400
    assert b"attacker.example" not in arbitrary_source.content
    delay_mock.assert_not_called()


@pytest.mark.django_db
def test_dataset_dashboard_survives_official_release_discovery_failure(
    client: Client,
    owner: User,
) -> None:
    OvertureReleaseCheck.objects.create(
        status=OvertureReleaseCheck.Status.FAILED,
        catalog_url=OFFICIAL_STAC_URL,
        error="ReleaseDiscoveryError: no se pudo consultar el catálogo oficial.",
    )
    client.force_login(owner)

    response = client.get(reverse("overture-datasets"))

    assert response.status_code == 200
    assert b"Todav\xc3\xada no hay una comprobaci\xc3\xb3n exitosa" in response.content
    assert b"ReleaseDiscoveryError" not in response.content
