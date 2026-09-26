from __future__ import annotations

import hashlib
import json
import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def _metadata_hash(snapshot) -> str:
    payload = {
        "release_id": snapshot.release_id,
        "schema_version": snapshot.schema_version,
        "taxonomy_version": snapshot.taxonomy_version,
        "importer_version": snapshot.importer_version,
        "mapping_version": snapshot.mapping_version,
        "source_uri": snapshot.source_uri,
        "manifest_sha256": snapshot.manifest_sha256,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def backfill_releases_and_partitions(apps, schema_editor) -> None:
    del schema_editor
    SearchZone = apps.get_model("configuration", "SearchZone")
    Snapshot = apps.get_model("overture", "OvertureDatasetSnapshot")
    DatasetZone = apps.get_model("overture", "OvertureDatasetZone")
    Place = apps.get_model("overture", "OverturePlace")
    PlaceZone = apps.get_model("overture", "OverturePlaceZone")
    Taxonomy = apps.get_model("overture", "OvertureTaxonomyCode")
    Release = apps.get_model("overture", "OvertureRelease")
    Partition = apps.get_model("overture", "OvertureCoveragePartition")

    search_zones = {str(zone.pk): zone for zone in SearchZone.objects.all()}
    caba = SearchZone.objects.filter(level="PROVINCE", official_code="02").first()

    for position, snapshot in enumerate(Snapshot.objects.order_by("created_at", "pk"), start=1):
        release, _ = Release.objects.get_or_create(
            release_id=snapshot.release_id,
            defaults={
                "schema_version": snapshot.schema_version,
                "taxonomy_version": snapshot.taxonomy_version,
                "importer_version": snapshot.importer_version,
                "mapping_version": snapshot.mapping_version,
                "source_uri": snapshot.source_uri,
                "catalog_url": (
                    f"https://stac.overturemaps.org/{snapshot.release_id}/catalog.json"
                ),
                "manifest_sha256": snapshot.manifest_sha256,
                "metadata_sha256": _metadata_hash(snapshot),
                "attribution": snapshot.attribution,
                "source_licenses": snapshot.source_licenses,
                "notices": snapshot.notices,
                "discovered_at": snapshot.created_at,
            },
        )

        dataset_zones = list(DatasetZone.objects.filter(snapshot_id=snapshot.pk))
        represented_zones = [search_zones.get(zone.code) for zone in dataset_zones]
        expected_caba = (
            bool(dataset_zones)
            and caba is not None
            and all(
                represented is not None and represented.province_code == "02"
                for represented in represented_zones
            )
        )
        if expected_caba:
            province = caba
            province_code = "02"
            province_name = caba.name
            province_bbox = caba.boundary_bbox
            partition_status = snapshot.status
            partition_active = bool(snapshot.is_active and snapshot.status == "READY")
        else:
            # Legacy snapshots with unknown/mixed coverage remain queryable through
            # their original IDs, but cannot be selected for a new campaign.
            province = None
            province_code = f"LEGACY-{position:06d}"
            province_name = "Cobertura histórica sin provincia"
            province_bbox = []
            partition_status = "SUPERSEDED"
            partition_active = False

        snapshot.release_record_id = release.pk
        snapshot.province_code = province_code
        snapshot.province_name = province_name
        snapshot.province_bbox = province_bbox
        snapshot.import_revision = 1
        snapshot.save(
            update_fields=(
                "release_record",
                "province_code",
                "province_name",
                "province_bbox",
                "import_revision",
                "updated_at",
            )
        )
        import_payload = (
            f"{snapshot.pk}:{snapshot.manifest_sha256}:{snapshot.boundary_manifest_sha256}"
        )
        partition = Partition.objects.create(
            release_id=release.pk,
            province_id=province.pk if province is not None else None,
            province_code=province_code,
            province_name=province_name,
            province_bbox=province_bbox,
            import_revision=1,
            snapshot_id=snapshot.pk,
            status=partition_status,
            is_active=partition_active,
            boundary_version=snapshot.boundary_version,
            boundary_manifest_sha256=snapshot.boundary_manifest_sha256,
            import_sha256=hashlib.sha256(import_payload.encode("utf-8")).hexdigest(),
            streamed_count=snapshot.streamed_count,
            place_count=snapshot.place_count,
            zone_count=snapshot.zone_count,
            validation_results=snapshot.validation_results,
            source_licenses=snapshot.source_licenses,
            attribution=snapshot.attribution,
            imported_at=snapshot.imported_at,
            activated_at=snapshot.activated_at,
            error=snapshot.error,
        )
        for dataset_zone in dataset_zones:
            source_zone = search_zones.get(dataset_zone.code)
            dataset_zone.partition_id = partition.pk
            dataset_zone.search_zone_id = source_zone.pk if source_zone is not None else None
            dataset_zone.save(update_fields=("partition", "search_zone", "updated_at"))
        Place.objects.filter(snapshot_id=snapshot.pk).update(partition_id=partition.pk)
        PlaceZone.objects.filter(snapshot_id=snapshot.pk).update(partition_id=partition.pk)
        Taxonomy.objects.filter(snapshot_id=snapshot.pk).update(partition_id=partition.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0008_argentina_zone_hierarchy"),
        ("overture", "0002_widen_place_confidence_precision"),
    ]

    operations = [
        migrations.CreateModel(
            name="OvertureRelease",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("release_id", models.CharField(max_length=40, unique=True)),
                ("schema_version", models.CharField(max_length=80)),
                ("taxonomy_version", models.CharField(max_length=80)),
                ("importer_version", models.CharField(max_length=80)),
                ("mapping_version", models.CharField(max_length=80)),
                ("source_uri", models.CharField(max_length=500)),
                ("catalog_url", models.CharField(max_length=500)),
                ("manifest_sha256", models.CharField(max_length=64)),
                ("metadata_sha256", models.CharField(max_length=64)),
                ("attribution", models.TextField(blank=True)),
                ("source_licenses", models.JSONField(default=list)),
                ("notices", models.JSONField(default=list)),
                ("discovered_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ("-release_id",)},
        ),
        migrations.RemoveConstraint(
            model_name="overturedatasetsnapshot",
            name="overture_one_active_snapshot",
        ),
        migrations.AddField(
            model_name="overturedatasetsnapshot",
            name="import_revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="overturedatasetsnapshot",
            name="province_bbox",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="overturedatasetsnapshot",
            name="province_code",
            field=models.CharField(blank=True, db_index=True, max_length=20),
        ),
        migrations.AddField(
            model_name="overturedatasetsnapshot",
            name="province_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="overturedatasetsnapshot",
            name="release_record",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="legacy_snapshots",
                to="overture.overturerelease",
            ),
        ),
        migrations.CreateModel(
            name="OvertureCoveragePartition",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("province_code", models.CharField(max_length=20)),
                ("province_name", models.CharField(max_length=160)),
                ("province_bbox", models.JSONField(default=list)),
                ("import_revision", models.PositiveIntegerField(default=1)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("IMPORTING", "Importando"),
                            ("READY", "Lista"),
                            ("SUPERSEDED", "Reemplazada"),
                            ("FAILED", "Falló"),
                        ],
                        default="IMPORTING",
                        max_length=20,
                    ),
                ),
                ("is_active", models.BooleanField(default=False)),
                ("boundary_version", models.CharField(max_length=120)),
                ("boundary_manifest_sha256", models.CharField(max_length=64)),
                ("import_sha256", models.CharField(blank=True, max_length=64)),
                ("streamed_count", models.PositiveIntegerField(default=0)),
                ("place_count", models.PositiveIntegerField(default=0)),
                ("zone_count", models.PositiveIntegerField(default=0)),
                ("validation_results", models.JSONField(default=dict)),
                ("source_licenses", models.JSONField(default=list)),
                ("attribution", models.TextField(blank=True)),
                ("imported_at", models.DateTimeField(blank=True, null=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                (
                    "province",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="overture_partitions",
                        to="configuration.searchzone",
                    ),
                ),
                (
                    "release",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="partitions",
                        to="overture.overturerelease",
                    ),
                ),
                (
                    "snapshot",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="coverage_partition",
                        to="overture.overturedatasetsnapshot",
                    ),
                ),
            ],
            options={"ordering": ("province_name", "-created_at")},
        ),
        migrations.AddField(
            model_name="overturedatasetzone",
            name="partition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="zones",
                to="overture.overturecoveragepartition",
            ),
        ),
        migrations.AddField(
            model_name="overturedatasetzone",
            name="search_zone",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="overture_dataset_zones",
                to="configuration.searchzone",
            ),
        ),
        migrations.AddField(
            model_name="overtureplace",
            name="partition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="places",
                to="overture.overturecoveragepartition",
            ),
        ),
        migrations.AddField(
            model_name="overtureplacezone",
            name="partition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="place_zone_links",
                to="overture.overturecoveragepartition",
            ),
        ),
        migrations.AddField(
            model_name="overturetaxonomycode",
            name="partition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="taxonomy_codes",
                to="overture.overturecoveragepartition",
            ),
        ),
        migrations.RunPython(backfill_releases_and_partitions, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="overturecoveragepartition",
            index=models.Index(
                fields=["province_code", "status", "is_active"],
                name="overture_partition_ready_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="overturecoveragepartition",
            index=models.Index(
                fields=["release", "province_code"],
                name="overture_partition_release_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="overturecoveragepartition",
            constraint=models.UniqueConstraint(
                fields=("release", "province_code", "import_revision"),
                name="overture_partition_release_province_revision_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="overturecoveragepartition",
            constraint=models.UniqueConstraint(
                condition=Q(is_active=True),
                fields=("province_code",),
                name="overture_one_active_partition_per_province",
            ),
        ),
        migrations.AddConstraint(
            model_name="overturecoveragepartition",
            constraint=models.CheckConstraint(
                condition=Q(is_active=False) | Q(status="READY"),
                name="overture_partition_active_is_ready",
            ),
        ),
    ]
