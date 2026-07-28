from __future__ import annotations

import hashlib
import json
import shutil
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F

from apps.campaigns.models import Campaign
from apps.catalogs.models import Catalog
from apps.catalogs.services import verify_catalog
from apps.configuration.integrations import validate_encrypted_integration_credentials
from apps.mailbox.crypto import decrypt_token
from apps.mailbox.models import GmailConnection
from apps.overture.geometry import validate_geojson
from apps.overture.importer import (
    FOURSQUARE_DATASET_KEYS,
    FOURSQUARE_NOTICE,
    OVERTURE_ATTRIBUTION,
    SOURCE_LICENSES,
)
from apps.overture.matching import normalize_search_text
from apps.overture.models import OvertureDatasetSnapshot
from apps.overture.releases import official_places_source_uri


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verified_snapshot_boundary_hashes(
    snapshot: OvertureDatasetSnapshot,
    failures: list[str],
) -> set[str]:
    zones = list(snapshot.zones.only("code", "geometry", "bbox", "boundary_hash"))
    if len(zones) != snapshot.zone_count:
        failures.append(
            f"snapshot Overture {snapshot.release_id} tiene un conteo de zonas inconsistente"
        )

    manifest: list[dict[str, str]] = []
    verified_hashes: set[str] = set()
    for zone in zones:
        try:
            geometry = validate_geojson(zone.geometry)
        except Exception:
            failures.append(
                f"zona {zone.pk} del snapshot Overture {snapshot.release_id} es inválida"
            )
            continue
        if geometry.sha256 != zone.boundary_hash or list(geometry.bbox) != zone.bbox:
            failures.append(
                f"zona {zone.pk} del snapshot Overture {snapshot.release_id} perdió integridad"
            )
            continue
        verified_hashes.add(geometry.sha256)
        manifest.append({"code": zone.code, "boundary_hash": geometry.sha256})

    computed_manifest = _canonical_hash(sorted(manifest, key=lambda item: item["code"]))
    if computed_manifest != snapshot.boundary_manifest_sha256:
        failures.append(
            f"snapshot Overture {snapshot.release_id} tiene un manifiesto de zonas inválido"
        )
    return verified_hashes


def _source_dataset_key(value: str) -> str:
    return normalize_search_text(value).replace(" ", "")


def _verify_snapshot_catalog(
    snapshot: OvertureDatasetSnapshot,
    failures: list[str],
) -> set[str]:
    """Verify the durable catalog, rather than trusting its summary columns."""

    verified_hashes = _verified_snapshot_boundary_hashes(snapshot, failures)
    label = f"snapshot Overture {snapshot.release_id}"
    if not isinstance(snapshot.validation_results, dict) or (
        snapshot.validation_results.get("passed") is not True
    ):
        failures.append(f"{label} no conserva una validación aprobada")
    try:
        expected_source_uri = official_places_source_uri(snapshot.release_id)
    except Exception:
        expected_source_uri = ""
    if snapshot.source_uri != expected_source_uri:
        failures.append(f"{label} no conserva el origen oficial esperado")
    if len(snapshot.manifest_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in snapshot.manifest_sha256
    ):
        failures.append(f"{label} no conserva un hash de manifiesto válido")

    actual_place_count = snapshot.places.count()
    actual_taxonomy_count = snapshot.taxonomy_codes.count()
    if actual_place_count != snapshot.place_count:
        failures.append(f"{label} tiene un conteo de lugares inconsistente")
    if actual_taxonomy_count != snapshot.taxonomy_code_count:
        failures.append(f"{label} tiene un conteo de taxonomía inconsistente")
    if snapshot.streamed_count < actual_place_count:
        failures.append(f"{label} tiene un conteo de lectura inconsistente")

    invalid_links = snapshot.place_zone_links.exclude(
        place__snapshot_id=F("snapshot_id")
    ) | snapshot.place_zone_links.exclude(zone__snapshot_id=F("snapshot_id"))
    linked_place_ids = snapshot.place_zone_links.values_list("place_id", flat=True).distinct()
    if invalid_links.exists() or snapshot.places.exclude(pk__in=linked_place_ids).exists():
        failures.append(f"{label} tiene vínculos lugar-zona incompletos o cruzados")

    source_counts: dict[str, int] = {}
    source_licenses: set[str] = set()
    source_metadata_invalid = False
    for sources, stored_licenses, field_provenance, license_label, payload_hash in (
        snapshot.places.values_list(
            "sources",
            "source_licenses",
            "field_provenance",
            "license",
            "source_payload_hash",
        )
    ).iterator(chunk_size=2_000):
        if not isinstance(sources, list) or not sources:
            source_metadata_invalid = True
            continue
        place_datasets: set[str] = set()
        place_licenses: set[str] = set()
        expected_provenance: dict[str, list[dict[str, object]]] = {}
        for source in sources:
            if not isinstance(source, dict):
                source_metadata_invalid = True
                continue
            dataset = source.get("dataset")
            if not isinstance(dataset, str) or not dataset.strip():
                source_metadata_invalid = True
                continue
            dataset = " ".join(dataset.strip().split())
            dataset_key = _source_dataset_key(dataset)
            if dataset_key not in SOURCE_LICENSES:
                source_metadata_invalid = True
                continue
            place_datasets.add(dataset)
            source_license = SOURCE_LICENSES[dataset_key]
            if source_license:
                place_licenses.add(source_license)
            raw_property = source.get("property")
            property_path = (
                " ".join(raw_property.strip().split())
                if isinstance(raw_property, str) and raw_property.strip()
                else "/"
            )
            raw_record_id = source.get("record_id")
            record_id = (
                " ".join(raw_record_id.strip().split()) if isinstance(raw_record_id, str) else ""
            )
            raw_update_time = source.get("update_time")
            update_time = (
                " ".join(raw_update_time.strip().split())
                if isinstance(raw_update_time, str)
                else ""
            )
            expected_provenance.setdefault(property_path, []).append(
                {
                    "dataset": dataset,
                    "record_id": record_id,
                    "update_time": update_time,
                    "confidence": source.get("confidence"),
                }
            )
        if not isinstance(stored_licenses, list) or sorted(stored_licenses) != sorted(
            place_licenses
        ):
            source_metadata_invalid = True
        if field_provenance != expected_provenance or "/" not in expected_provenance:
            source_metadata_invalid = True
        if license_label != ", ".join(sorted(place_licenses))[:500]:
            source_metadata_invalid = True
        if (
            not isinstance(payload_hash, str)
            or len(payload_hash) != 64
            or any(character not in "0123456789abcdef" for character in payload_hash)
        ):
            source_metadata_invalid = True
        for dataset in place_datasets:
            source_counts[dataset] = source_counts.get(dataset, 0) + 1
        source_licenses.update(place_licenses)

    expected_source_counts = dict(sorted(source_counts.items()))
    expected_source_licenses = sorted(source_licenses)
    if source_metadata_invalid:
        failures.append(f"{label} tiene procedencia o licencias de lugares inválidas")
    if snapshot.source_counts != expected_source_counts:
        failures.append(f"{label} tiene conteos de procedencia inconsistentes")
    if snapshot.source_licenses != expected_source_licenses:
        failures.append(f"{label} tiene licencias de procedencia inconsistentes")
    has_foursquare = any(
        _source_dataset_key(dataset) in FOURSQUARE_DATASET_KEYS
        for dataset in expected_source_counts
    )
    expected_notices = [FOURSQUARE_NOTICE] if has_foursquare else []
    if snapshot.notices != expected_notices or snapshot.attribution != OVERTURE_ATTRIBUTION:
        failures.append(f"{label} tiene atribución o avisos inconsistentes")
    return verified_hashes


def _verify_running_overture_campaigns(failures: list[str]) -> None:
    campaigns = list(
        Campaign.objects.filter(
            extractor_provider="overture",
            state__in=(Campaign.State.RUNNING, Campaign.State.PAUSED),
        )
        .select_related("overture_snapshot")
        .prefetch_related("zone_selections")
    )
    verified_snapshots: dict[object, set[str]] = {}
    for active_snapshot in OvertureDatasetSnapshot.objects.filter(is_active=True):
        verified_snapshots[active_snapshot.pk] = _verify_snapshot_catalog(active_snapshot, failures)
    for campaign in campaigns:
        snapshot = campaign.overture_snapshot
        if snapshot is None:
            failures.append(f"campaña Overture {campaign.pk} no tiene un snapshot fijado")
            continue
        if snapshot.status not in {
            OvertureDatasetSnapshot.Status.READY,
            OvertureDatasetSnapshot.Status.SUPERSEDED,
        }:
            failures.append(
                f"snapshot Overture {snapshot.release_id} referenciado no es utilizable"
            )
            continue

        if snapshot.pk not in verified_snapshots:
            verified_snapshots[snapshot.pk] = _verify_snapshot_catalog(snapshot, failures)
        covered_hashes = verified_snapshots[snapshot.pk]

        selections = list(campaign.zone_selections.all())
        if not selections:
            failures.append(f"campaña Overture {campaign.pk} no tiene zonas fijadas")
            continue
        required_hashes: set[str] = set()
        selection_integrity_failed = False
        for selection in selections:
            try:
                geometry = validate_geojson(selection.boundary_geojson_snapshot)
            except Exception:
                selection_integrity_failed = True
                continue
            if (
                not selection.boundary_hash_snapshot
                or geometry.sha256 != selection.boundary_hash_snapshot
                or list(geometry.bbox) != selection.boundary_bbox_snapshot
            ):
                selection_integrity_failed = True
                continue
            required_hashes.add(geometry.sha256)
        if selection_integrity_failed:
            failures.append(f"campaña Overture {campaign.pk} tiene una zona fijada inválida")
        if not required_hashes or not required_hashes.issubset(covered_hashes):
            failures.append(
                f"snapshot Overture {snapshot.release_id} no cubre las zonas fijadas "
                f"de la campaña {campaign.pk}"
            )


class Command(BaseCommand):
    help = "Verify migrations, private catalog integrity, token decryptability and disk headroom."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--require-kill-switch",
            action="store_true",
            help="Fail if live is effective while the kill switch is disabled.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        failures: list[str] = []
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            failures.append("hay migraciones sin aplicar")
        for catalog in Catalog.objects.all():
            try:
                verify_catalog(catalog)
            except Exception:
                failures.append(f"catálogo {catalog.pk} ausente o con hash inválido")
        for gmail in GmailConnection.objects.exclude(refresh_token_encrypted=""):
            try:
                decrypt_token(gmail.refresh_token_encrypted)
            except Exception:
                failures.append(f"token Gmail {gmail.pk} no descifrable")
        failures.extend(validate_encrypted_integration_credentials())
        _verify_running_overture_campaigns(failures)
        try:
            free = shutil.disk_usage(settings.PRIVATE_STORAGE_ROOT).free
        except OSError:
            failures.append("almacenamiento privado no disponible")
        else:
            if free < settings.MIN_FREE_DISK_BYTES:
                failures.append("espacio libre debajo del margen operativo")
        if (
            options["require_kill_switch"]
            and settings.SEND_MODE == "live"
            and not settings.SEND_KILL_SWITCH
        ):
            failures.append("restore ejecutado con live habilitado y kill switch inactivo")
        if failures:
            raise CommandError("Restore no verificable: " + "; ".join(failures))
        self.stdout.write(self.style.SUCCESS("Restore verificado; live permanece bajo control."))
