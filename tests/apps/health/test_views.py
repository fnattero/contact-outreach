from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignZoneSelection
from apps.catalogs.services import create_catalog
from apps.configuration.integrations import (
    GMAIL_CLIENT_SECRET_PURPOSE,
    LLM_KEY_PURPOSE,
)
from apps.configuration.models import IntegrationConfiguration, SearchZone
from apps.core.crypto import encrypt_secret
from apps.integrations.gmail import GMAIL_SCOPES
from apps.mailbox.crypto import encrypt_token
from apps.mailbox.models import GmailConnection
from apps.overture.importer import OVERTURE_ATTRIBUTION, activate_snapshot, normalize_zone_name
from apps.overture.models import (
    OvertureDatasetSnapshot,
    OvertureDatasetZone,
    OverturePlace,
    OverturePlaceZone,
)
from apps.overture.releases import official_places_source_uri


@pytest.mark.django_db
def test_liveness_is_public_and_does_not_check_dependencies(client: Client) -> None:
    response = client.get(reverse("health-live"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["Cache-Control"] == (
        "max-age=0, no-cache, no-store, must-revalidate, private"
    )


@pytest.mark.django_db
def test_readiness_checks_database_and_cache(client: Client) -> None:
    response = client.get(reverse("health-ready"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_reports_dependency_failure_without_details(client: Client) -> None:
    with patch("apps.health.views.cache.set", side_effect=RuntimeError("secret detail")):
        response = client.get(reverse("health-ready"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"secret detail" not in response.content


@pytest.mark.django_db
def test_readiness_reports_database_failure_without_details(client: Client) -> None:
    with patch("apps.health.views.connection.cursor", side_effect=RuntimeError("database detail")):
        response = client.get(reverse("health-ready"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"database detail" not in response.content


def test_health_rejects_post(client: Client) -> None:
    assert client.post(reverse("health-live")).status_code == 405


@pytest.mark.django_db
def test_degraded_health_reports_storage_and_provider_configuration(
    client: Client, tmp_path: Path
) -> None:
    admin = User.objects.create_user(username="health-admin", password="password")
    client.force_login(admin)
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        response = client.get(reverse("health-degraded"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["storage"] == "ok"
    assert payload["components"]["extractor"] == "fake"
    assert payload["components"]["gmail"] == "not_connected"
    assert payload["storage_free_bytes"] > 0


@pytest.mark.django_db
def test_degraded_health_requires_tested_gmail_connection(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    connection = GmailConnection.objects.create(
        owner=owner,
        email="owner@example.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh"),
        status=GmailConnection.Status.CONNECTED,
    )
    settings_override = override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        GMAIL_PROVIDER="fake",
    )
    with settings_override:
        not_tested = client.get(reverse("health-degraded"))
        assert not_tested.json()["components"]["gmail"] == "not_ready"

        connection.last_tested_at = timezone.now()
        connection.save(update_fields=("last_tested_at", "updated_at"))
        ready = client.get(reverse("health-degraded"))

    assert ready.json()["components"]["gmail"] == "ready"


@pytest.mark.django_db
def test_degraded_health_reports_missing_local_gmail_api_configuration(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    GmailConnection.objects.create(
        owner=owner,
        email="owner@gmail.com",
        scopes=list(GMAIL_SCOPES),
        refresh_token_encrypted=encrypt_token("refresh"),
        status=GmailConnection.Status.CONNECTED,
        last_tested_at=timezone.now(),
    )
    with override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        GMAIL_PROVIDER="api",
        GMAIL_OAUTH_CLIENT_ID="",
        GMAIL_OAUTH_CLIENT_SECRET="",
    ):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"]["gmail"] == "missing_configuration"


@pytest.mark.django_db
def test_degraded_health_uses_encrypted_dashboard_configuration(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    configuration = IntegrationConfiguration.objects.create(
        owner=owner,
        extractor_provider="fake",
        llm_provider="openai-compatible",
        llm_model="model",
        openai_compatible_base_url="https://llm.example.test/v1",
        llm_api_key_source=IntegrationConfiguration.SecretSource.ENCRYPTED,
        llm_api_key_encrypted=encrypt_secret("health-llm-secret", purpose=LLM_KEY_PURPOSE),
        gmail_provider="api",
        gmail_oauth_client_id="client-id.apps.googleusercontent.com",
        gmail_oauth_client_secret_source=IntegrationConfiguration.SecretSource.ENCRYPTED,
        gmail_oauth_client_secret_encrypted=encrypt_secret(
            "health-google-secret", purpose=GMAIL_CLIENT_SECRET_PURPOSE
        ),
    )
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"] == {
        "storage": "ok",
        "gmail": "not_connected",
        "extractor": "fake",
        "llm": "configured",
    }
    assert b"health-" not in response.content
    assert configuration.llm_api_key_encrypted.encode() not in response.content
    assert configuration.gmail_oauth_client_secret_encrypted.encode() not in response.content


def _boundary_manifest(*, code: str, boundary_hash: str) -> str:
    encoded = json.dumps(
        [{"code": code, "boundary_hash": boundary_hash}],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _running_overture_campaign(
    owner: User,
    *,
    selection_zone: SearchZone | None = None,
    manifest_sha256: str = "",
) -> tuple[Campaign, OvertureDatasetSnapshot, CampaignZoneSelection]:
    snapshot_zone = SearchZone.objects.exclude(boundary_hash="").order_by("pk").first()
    assert snapshot_zone is not None
    selected_zone = selection_zone or snapshot_zone
    code = str(snapshot_zone.pk)
    manifest = _boundary_manifest(
        code=code,
        boundary_hash=snapshot_zone.boundary_hash,
    )
    snapshot = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="test-importer-v1",
        mapping_version="test-mapping-v1",
        boundary_version="test-boundaries-v1",
        boundary_manifest_sha256=manifest,
        source_uri=official_places_source_uri("2026-07-22.0"),
        manifest_sha256="a" * 64,
        status=OvertureDatasetSnapshot.Status.IMPORTING,
        is_active=False,
        streamed_count=1,
        place_count=1,
        zone_count=1,
        taxonomy_code_count=0,
        source_counts={"meta": 1},
        source_licenses=["CDLA-Permissive-2.0"],
        attribution=OVERTURE_ATTRIBUTION,
        notices=[],
        validation_results={"passed": True, "place_limit": 500_000},
    )
    dataset_zone = OvertureDatasetZone.objects.create(
        snapshot=snapshot,
        code=code,
        name=snapshot_zone.name,
        normalized_name=normalize_zone_name(snapshot_zone.name),
        geometry=snapshot_zone.boundary_geojson,
        bbox=snapshot_zone.boundary_bbox,
        boundary_hash=snapshot_zone.boundary_hash,
        source=snapshot_zone.boundary_source or "test",
        source_version=str(snapshot_zone.boundary_revision),
        attribution=snapshot_zone.boundary_attribution,
    )
    place = OverturePlace.objects.create(
        snapshot=snapshot,
        overture_id="00000000-0000-4000-8000-000000000001",
        name="Restore Fixture",
        normalized_name="restore fixture",
        name_search=" restore fixture ",
        latitude=-34.58,
        longitude=-58.42,
        confidence=0.9,
        operating_status="open",
        sources=[
            {
                "dataset": "meta",
                "record_id": "restore",
                "property": "/",
                "update_time": "",
                "confidence": None,
            }
        ],
        field_provenance={
            "/": [
                {
                    "dataset": "meta",
                    "record_id": "restore",
                    "update_time": "",
                    "confidence": None,
                }
            ]
        },
        source_licenses=["CDLA-Permissive-2.0"],
        license="CDLA-Permissive-2.0",
        source_payload_hash="e" * 64,
    )
    OverturePlaceZone.objects.create(
        snapshot=snapshot,
        place=place,
        zone=dataset_zone,
    )
    snapshot = activate_snapshot(snapshot.pk)
    if manifest_sha256:
        table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET boundary_manifest_sha256 = %s WHERE id = %s",
                [manifest_sha256, snapshot.pk.hex],
            )
    replacement = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-22.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-22.0",
        importer_version="test-importer-v1",
        mapping_version="test-mapping-v1",
        boundary_version="test-boundaries-v1",
        boundary_manifest_sha256=manifest,
        source_uri=official_places_source_uri("2026-07-22.0"),
        manifest_sha256="d" * 64,
        zone_count=1,
        attribution=OVERTURE_ATTRIBUTION,
        validation_results={"passed": True, "place_limit": 500_000},
    )
    OvertureDatasetZone.objects.create(
        snapshot=replacement,
        code=code,
        name=snapshot_zone.name,
        normalized_name=normalize_zone_name(snapshot_zone.name),
        geometry=snapshot_zone.boundary_geojson,
        bbox=snapshot_zone.boundary_bbox,
        boundary_hash=snapshot_zone.boundary_hash,
        source=snapshot_zone.boundary_source or "test",
        source_version=str(snapshot_zone.boundary_revision),
        attribution=snapshot_zone.boundary_attribution,
    )
    activate_snapshot(replacement.pk)
    snapshot.refresh_from_db()
    catalog = create_catalog(
        name="Restore Overture",
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
            content_type="application/pdf",
        ),
        actor=owner,
    )
    campaign = Campaign.objects.create(
        name="Restore Overture",
        state=Campaign.State.DRAFT,
        discovery_state=Campaign.DiscoveryState.PENDING,
        extractor_provider="overture",
        overture_snapshot=snapshot,
        catalog=catalog,
        created_by=owner,
    )
    selection = CampaignZoneSelection.objects.create(
        campaign=campaign,
        zone=selected_zone,
        name_snapshot=selected_zone.name,
        location_snapshot=selected_zone.location_text,
        boundary_geojson_snapshot=selected_zone.boundary_geojson,
        boundary_bbox_snapshot=selected_zone.boundary_bbox,
        boundary_hash_snapshot=selected_zone.boundary_hash,
        boundary_revision_snapshot=selected_zone.boundary_revision,
    )
    campaign.state = Campaign.State.RUNNING
    campaign.discovery_state = Campaign.DiscoveryState.RUNNING
    campaign.save(update_fields=("state", "discovery_state", "updated_at"))
    return campaign, snapshot, selection


@pytest.mark.django_db
def test_verify_restore_checks_migrations_and_safe_live_state(tmp_path: Path) -> None:
    with override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        SEND_MODE="dry-run",
        SEND_KILL_SWITCH=True,
    ):
        call_command("verify_restore", verbosity=0)
    with override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        SEND_MODE="live",
        SEND_KILL_SWITCH=False,
    ):
        with pytest.raises(CommandError, match="kill switch"):
            call_command("verify_restore", "--require-kill-switch", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_accepts_a_valid_pinned_superseded_snapshot(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _running_overture_campaign(owner)

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_corrupt_snapshot_boundary_manifest(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _running_overture_campaign(owner, manifest_sha256="f" * 64)

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="manifiesto de zonas inválido"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_frozen_zone_missing_from_snapshot_coverage(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    snapshot_zone = SearchZone.objects.exclude(boundary_hash="").order_by("pk").first()
    assert snapshot_zone is not None
    different_zone = (
        SearchZone.objects.exclude(boundary_hash="")
        .exclude(pk=snapshot_zone.pk)
        .order_by("pk")
        .first()
    )
    assert different_zone is not None
    _running_overture_campaign(owner, selection_zone=different_zone)

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="no cubre las zonas fijadas"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_partial_place_catalog(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET place_count = %s WHERE id = %s",
            [2, snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="conteo de lugares inconsistente"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_checks_active_snapshot_without_a_running_campaign(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    campaign, _, _ = _running_overture_campaign(owner)
    Campaign.objects.filter(pk=campaign.pk).update(
        state=Campaign.State.COMPLETED,
        discovery_state=Campaign.DiscoveryState.TARGET_REACHED,
    )
    active = OvertureDatasetSnapshot.objects.get(is_active=True)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET validation_results = %s WHERE id = %s",
            [json.dumps({"passed": False}), active.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="validación aprobada"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_detects_undecryptable_refresh_token(owner: User, tmp_path: Path) -> None:
    GmailConnection.objects.create(
        owner=owner,
        status=GmailConnection.Status.ERROR,
        refresh_token_encrypted="ciphertext-invalid",
    )
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="no descifrable") as error:
            call_command("verify_restore", verbosity=0)
    assert "ciphertext-invalid" not in str(error.value)


@pytest.mark.django_db
def test_verify_restore_detects_undecryptable_integration_secret(
    owner: User, tmp_path: Path
) -> None:
    IntegrationConfiguration.objects.create(
        owner=owner,
        llm_api_key_source=IntegrationConfiguration.SecretSource.ENCRYPTED,
        llm_api_key_encrypted="v1:ciphertext-invalid",
    )
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="credencial LLM") as error:
            call_command("verify_restore", verbosity=0)
    assert "ciphertext-invalid" not in str(error.value)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_an_inconsistent_zone_count(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {table} SET zone_count = %s WHERE id = %s", [5, snapshot.pk.hex])

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="conteo de zonas inconsistente"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_zone_with_invalid_geometry(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    zone = snapshot.zones.get()
    table = connection.ops.quote_name(OvertureDatasetZone._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET geometry = %s WHERE id = %s",
            [json.dumps({"type": "Point", "coordinates": [0, 0]}), zone.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="es inválida"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_zone_with_a_mismatched_boundary_hash(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    zone = snapshot.zones.get()
    table = connection.ops.quote_name(OvertureDatasetZone._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET boundary_hash = %s WHERE id = %s",
            ["0" * 64, zone.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="perdió integridad"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_an_invalid_release_id(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET release_id = %s WHERE id = %s",
            ["not a valid release!!", snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="origen oficial esperado"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_an_invalid_manifest_hash(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET manifest_sha256 = %s WHERE id = %s",
            ["too-short", snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="hash de manifiesto válido"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_an_inconsistent_taxonomy_count(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET taxonomy_code_count = %s WHERE id = %s",
            [99, snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="conteo de taxonomía inconsistente"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_an_inconsistent_read_count(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET streamed_count = %s WHERE id = %s",
            [0, snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="conteo de lectura inconsistente"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_place_missing_its_zone_link(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OverturePlaceZone._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {table} WHERE snapshot_id = %s", [snapshot.pk.hex])

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="vínculos lugar-zona incompletos o cruzados"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "sources_payload",
    [
        [],
        ["not-a-dict"],
        [{"property": "/"}],
        [{"dataset": "unknown-vendor", "property": "/"}],
        [{"dataset": "overture", "property": "/"}],
    ],
    ids=[
        "empty-sources",
        "non-dict-source",
        "missing-dataset",
        "unlicensed-dataset",
        "unattributed-license",
    ],
)
def test_verify_restore_rejects_places_with_invalid_source_metadata(
    owner: User,
    private_catalog_dir: Path,
    sources_payload: list[object],
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    place = snapshot.places.get()
    table = connection.ops.quote_name(OverturePlace._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET sources = %s WHERE id = %s",
            [json.dumps(sources_payload), place.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="procedencia o licencias de lugares inválidas"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("source_licenses", json.dumps(["WRONG-LICENSE"])),
        ("field_provenance", json.dumps({})),
        ("license", "WRONG-LICENSE-LABEL"),
        ("source_payload_hash", "not-a-valid-hash"),
    ],
    ids=["stored-licenses", "field-provenance", "license-label", "payload-hash"],
)
def test_verify_restore_rejects_a_place_with_a_stored_field_mismatch(
    owner: User,
    private_catalog_dir: Path,
    column: str,
    value: str,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    place = snapshot.places.get()
    table = connection.ops.quote_name(OverturePlace._meta.db_table)
    column_name = connection.ops.quote_name(column)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET {column_name} = %s WHERE id = %s",
            [value, place.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="procedencia o licencias de lugares inválidas"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_inconsistent_source_counts(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET source_counts = %s WHERE id = %s",
            [json.dumps({"another-dataset": 99}), snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="conteos de procedencia inconsistentes"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_inconsistent_source_licenses(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET source_licenses = %s WHERE id = %s",
            [json.dumps(["Wrong-License"]), snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="licencias de procedencia inconsistentes"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_snapshot_with_inconsistent_attribution(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, snapshot, _ = _running_overture_campaign(owner)
    table = connection.ops.quote_name(OvertureDatasetSnapshot._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET notices = %s WHERE id = %s",
            [json.dumps(["a notice that should not be there"]), snapshot.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="atribución o avisos inconsistentes"):
            call_command("verify_restore", verbosity=0)


def _new_catalog(owner: User, *, name: str) -> object:
    return create_catalog(
        name=name,
        upload=SimpleUploadedFile(
            "catalogo.pdf",
            f"%PDF-1.4\n% {name}\n1 0 obj\n<<>>\nendobj\n%%EOF".encode(),
            content_type="application/pdf",
        ),
        actor=owner,
    )


@pytest.mark.django_db
def test_verify_restore_rejects_a_running_campaign_without_a_pinned_snapshot(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    catalog = _new_catalog(owner, name="No Snapshot Overture")
    Campaign.objects.create(
        name="No Snapshot Overture",
        state=Campaign.State.RUNNING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
        extractor_provider="overture",
        overture_snapshot=None,
        catalog=catalog,
        created_by=owner,
    )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="no tiene un snapshot fijado"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_campaign_pinned_to_an_unusable_snapshot(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    importing_snapshot = OvertureDatasetSnapshot.objects.create(
        release_id="2026-07-01.0",
        schema_version="v1.18.0",
        taxonomy_version="2026-07-01.0",
        importer_version="test-importer-v1",
        mapping_version="test-mapping-v1",
        boundary_version="test-boundaries-v1",
        boundary_manifest_sha256="a" * 64,
        source_uri=official_places_source_uri("2026-07-01.0"),
        manifest_sha256="b" * 64,
    )
    catalog = _new_catalog(owner, name="Unusable Snapshot Overture")
    Campaign.objects.create(
        name="Unusable Snapshot Overture",
        state=Campaign.State.RUNNING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
        extractor_provider="overture",
        overture_snapshot=importing_snapshot,
        catalog=catalog,
        created_by=owner,
    )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="referenciado no es utilizable"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_skips_reverifying_an_already_active_snapshot(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _running_overture_campaign(owner)
    active_snapshot = OvertureDatasetSnapshot.objects.get(is_active=True)
    snapshot_zone = SearchZone.objects.exclude(boundary_hash="").order_by("pk").first()
    assert snapshot_zone is not None
    catalog = _new_catalog(owner, name="Second Restore Overture")
    campaign = Campaign.objects.create(
        name="Second Restore Overture",
        state=Campaign.State.DRAFT,
        discovery_state=Campaign.DiscoveryState.PENDING,
        extractor_provider="overture",
        overture_snapshot=active_snapshot,
        catalog=catalog,
        created_by=owner,
    )
    CampaignZoneSelection.objects.create(
        campaign=campaign,
        zone=snapshot_zone,
        name_snapshot=snapshot_zone.name,
        location_snapshot=snapshot_zone.location_text,
        boundary_geojson_snapshot=snapshot_zone.boundary_geojson,
        boundary_bbox_snapshot=snapshot_zone.boundary_bbox,
        boundary_hash_snapshot=snapshot_zone.boundary_hash,
        boundary_revision_snapshot=snapshot_zone.boundary_revision,
    )
    Campaign.objects.filter(pk=campaign.pk).update(
        state=Campaign.State.RUNNING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
    )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_running_campaign_without_pinned_zones(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _running_overture_campaign(owner)
    active_snapshot = OvertureDatasetSnapshot.objects.get(is_active=True)
    catalog = _new_catalog(owner, name="No Pinned Zones Overture")
    Campaign.objects.create(
        name="No Pinned Zones Overture",
        state=Campaign.State.RUNNING,
        discovery_state=Campaign.DiscoveryState.RUNNING,
        extractor_provider="overture",
        overture_snapshot=active_snapshot,
        catalog=catalog,
        created_by=owner,
    )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="no tiene zonas fijadas"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_pinned_zone_with_invalid_geometry(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, _, selection = _running_overture_campaign(owner)
    table = connection.ops.quote_name(CampaignZoneSelection._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET boundary_geojson_snapshot = %s WHERE id = %s",
            [json.dumps({"type": "Point", "coordinates": [0, 0]}), selection.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="zona fijada inválida"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_pinned_zone_with_a_mismatched_boundary_hash(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    _, _, selection = _running_overture_campaign(owner)
    table = connection.ops.quote_name(CampaignZoneSelection._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET boundary_hash_snapshot = %s WHERE id = %s",
            ["0" * 64, selection.pk.hex],
        )

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="zona fijada inválida"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_unapplied_migrations(private_catalog_dir: Path) -> None:
    with patch(
        "apps.health.management.commands.verify_restore.MigrationExecutor.migration_plan",
        return_value=[("fake-migration",)],
    ):
        with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
            with pytest.raises(CommandError, match="migraciones sin aplicar"):
                call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_a_catalog_missing_its_file(
    owner: User,
    private_catalog_dir: Path,
) -> None:
    catalog = _new_catalog(owner, name="Missing File Catalog")
    catalog.file.storage.delete(catalog.file.name)

    with override_settings(PRIVATE_STORAGE_ROOT=private_catalog_dir, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="ausente o con hash inválido"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_reports_unavailable_storage_when_root_is_missing(tmp_path: Path) -> None:
    missing_root = tmp_path / "does-not-exist"
    with override_settings(PRIVATE_STORAGE_ROOT=missing_root, MIN_FREE_DISK_BYTES=1):
        with pytest.raises(CommandError, match="almacenamiento privado no disponible"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_verify_restore_rejects_low_disk_headroom(tmp_path: Path) -> None:
    with override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=10**18):
        with pytest.raises(CommandError, match="espacio libre debajo del margen operativo"):
            call_command("verify_restore", verbosity=0)


@pytest.mark.django_db
def test_degraded_health_reports_storage_unavailable_when_root_is_missing(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    missing_root = tmp_path / "does-not-exist"
    with override_settings(PRIVATE_STORAGE_ROOT=missing_root, MIN_FREE_DISK_BYTES=1):
        response = client.get(reverse("health-degraded"))

    payload = response.json()
    assert payload["components"]["storage"] == "unavailable"
    assert payload["storage_free_bytes"] == 0


@pytest.mark.django_db
def test_degraded_health_reports_unsupported_gmail_provider(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    with override_settings(
        PRIVATE_STORAGE_ROOT=tmp_path,
        MIN_FREE_DISK_BYTES=1,
        GMAIL_PROVIDER="outscraper",
    ):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"]["gmail"] == "unsupported"


@pytest.mark.django_db
def test_degraded_health_reports_gmail_check_failure_without_details(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    with (
        override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1),
        patch(
            "apps.health.views.configured_integration_owner_id",
            side_effect=RuntimeError("secret owner detail"),
        ),
    ):
        response = client.get(reverse("health-degraded"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["gmail"] == "unavailable"
    assert b"secret owner detail" not in response.content


@pytest.mark.django_db
def test_degraded_health_reports_llm_check_failure_without_details(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    IntegrationConfiguration.objects.create(owner=owner, llm_provider="openai-compatible")
    with (
        override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1),
        patch(
            "apps.health.views.get_llm_api_key",
            side_effect=RuntimeError("secret api key detail"),
        ),
    ):
        response = client.get(reverse("health-degraded"))

    payload = response.json()
    assert payload["components"]["llm"] == "unavailable"
    assert b"secret api key detail" not in response.content


@pytest.mark.django_db
def test_degraded_health_reports_missing_overture_dataset(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    IntegrationConfiguration.objects.create(owner=owner, extractor_provider="overture")
    with (
        override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1),
        patch("apps.health.views.get_active_snapshot", return_value=None),
    ):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"]["extractor"] == "missing_dataset"


@pytest.mark.django_db
def test_degraded_health_reports_ready_extractor_when_snapshot_covers_all_active_zones(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    IntegrationConfiguration.objects.create(owner=owner, extractor_provider="overture")
    active_hashes = list(
        SearchZone.objects.filter(active=True, archived_at__isnull=True).values_list(
            "boundary_hash", flat=True
        )
    )
    assert active_hashes

    mock_snapshot = MagicMock()
    mock_snapshot.zones.filter.return_value.values_list.return_value = active_hashes
    with (
        override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1),
        patch("apps.health.views.get_active_snapshot", return_value=mock_snapshot),
    ):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"]["extractor"] == "ready"


@pytest.mark.django_db
def test_degraded_health_reports_stale_extractor_when_snapshot_misses_active_zones(
    client: Client, owner: User, tmp_path: Path
) -> None:
    client.force_login(owner)
    IntegrationConfiguration.objects.create(owner=owner, extractor_provider="overture")
    active_hashes = list(
        SearchZone.objects.filter(active=True, archived_at__isnull=True).values_list(
            "boundary_hash", flat=True
        )
    )
    assert active_hashes

    mock_snapshot = MagicMock()
    mock_snapshot.zones.filter.return_value.values_list.return_value = []
    with (
        override_settings(PRIVATE_STORAGE_ROOT=tmp_path, MIN_FREE_DISK_BYTES=1),
        patch("apps.health.views.get_active_snapshot", return_value=mock_snapshot),
    ):
        response = client.get(reverse("health-degraded"))

    assert response.json()["components"]["extractor"] == "stale"
