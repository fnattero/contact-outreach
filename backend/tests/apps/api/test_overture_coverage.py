from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.configuration.models import SearchZone
from apps.overture.importer import OVERTURE_ATTRIBUTION
from apps.overture.models import (
    OvertureCoveragePartition,
    OvertureDatasetSnapshot,
    OvertureRelease,
    OvertureReleaseCheck,
)
from apps.overture.releases import official_places_source_uri, official_release_catalog_url

RELEASE = "2026-07-22.0"


def _csrf(client: Client) -> str:
    return str(client.get(reverse("api-auth-csrf")).json()["data"]["csrf_token"])


def _partition(
    code: str, name: str, status: str, *, active: bool = False
) -> OvertureCoveragePartition:
    release, _ = OvertureRelease.objects.get_or_create(
        release_id=RELEASE,
        defaults={
            "schema_version": "v1.18.0",
            "taxonomy_version": RELEASE,
            "importer_version": "importer-v1",
            "mapping_version": "mapping-v1",
            "source_uri": official_places_source_uri(RELEASE),
            "catalog_url": official_release_catalog_url(RELEASE),
            "manifest_sha256": "a" * 64,
            "metadata_sha256": "b" * 64,
            "attribution": OVERTURE_ATTRIBUTION,
        },
    )
    snapshot = OvertureDatasetSnapshot.objects.create(
        release_id=RELEASE,
        province_code=code,
        province_name=name,
        schema_version="v1.18.0",
        taxonomy_version=RELEASE,
        importer_version="importer-v1",
        mapping_version="mapping-v1",
        boundary_version="boundary-v1",
        boundary_manifest_sha256="b" * 64,
        source_uri=official_places_source_uri(RELEASE),
        manifest_sha256="a" * 64,
        status="READY" if status == "READY" else "IMPORTING",
        is_active=active,
        attribution=OVERTURE_ATTRIBUTION,
    )
    return OvertureCoveragePartition.objects.create(
        release=release,
        province_code=code,
        province_name=name,
        province_bbox=[-1, -1, 1, 1],
        snapshot=snapshot,
        boundary_version="boundary-v1",
        boundary_manifest_sha256="d" * 64,
        status=status,
        is_active=active,
        place_count=120 if status == "READY" else 0,
    )


def _verified_release() -> None:
    OvertureReleaseCheck.objects.create(
        status=OvertureReleaseCheck.Status.SUCCEEDED, releases=[RELEASE], latest_release=RELEASE
    )


@pytest.mark.django_db
def test_status_lists_every_province_with_whether_its_data_is_ready(owner: User) -> None:
    province = SearchZone.objects.filter(level=SearchZone.Level.PROVINCE).order_by("name")
    first, second = province[0], province[1]
    _partition(first.official_code, first.name, "READY", active=True)
    _partition(second.official_code, second.name, "IMPORTING")
    client = Client()
    client.force_login(owner)

    data = client.get(reverse("api-overture-status")).json()["data"]

    by_code = {item["code"]: item for item in data["provinces"]}
    assert len(by_code) == province.count()
    assert by_code[first.official_code]["state"] == "READY"
    assert by_code[first.official_code]["place_count"] == 120
    assert by_code[second.official_code]["state"] == "IMPORTING"
    others = [
        item
        for code, item in by_code.items()
        if code not in {first.official_code, second.official_code}
    ]
    assert {item["state"] for item in others} == {"MISSING"}
    assert data["latest_verified_release"] is None


@pytest.mark.django_db
def test_loading_a_province_uses_the_latest_verified_version(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "apps.api.advanced.sync_snapshot",
        SimpleNamespace(
            delay=lambda release, code: calls.append((release, code)) or SimpleNamespace(id="t-1")
        ),
    )
    province = SearchZone.objects.filter(level=SearchZone.Level.PROVINCE).order_by("name").first()
    assert province is not None
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    csrf = _csrf(client)
    url = reverse("api-overture-sync")
    payload = json.dumps({"province_code": province.official_code})

    no_version = client.post(
        url, data=payload, content_type="application/json", HTTP_X_CSRFTOKEN=csrf
    )
    assert no_version.status_code == 400
    assert calls == []

    _verified_release()
    queued = client.post(url, data=payload, content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert queued.status_code == 202, queued.content
    assert calls == [(RELEASE, province.official_code)]


@pytest.mark.django_db
def test_only_one_province_loads_at_a_time(owner: User, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.api.advanced.sync_snapshot",
        SimpleNamespace(delay=lambda *a: pytest.fail("a second load must not start")),
    )
    _verified_release()
    provinces = list(
        SearchZone.objects.filter(level=SearchZone.Level.PROVINCE).order_by("name")[:2]
    )
    _partition(provinces[0].official_code, provinces[0].name, "IMPORTING")
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)

    refused = client.post(
        reverse("api-overture-sync"),
        data=json.dumps({"province_code": provinces[1].official_code}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf(client),
    )

    assert refused.status_code == 400
