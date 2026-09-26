from __future__ import annotations

import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def backfill_campaign_coverage(apps, schema_editor) -> None:
    del schema_editor
    Campaign = apps.get_model("campaigns", "Campaign")
    Coverage = apps.get_model("campaigns", "CampaignCoverageSelection")
    SearchQuery = apps.get_model("campaigns", "SearchQuery")
    SearchRun = apps.get_model("campaigns", "SearchRun")
    DatasetZone = apps.get_model("overture", "OvertureDatasetZone")
    Partition = apps.get_model("overture", "OvertureCoveragePartition")

    partitions = {partition.snapshot_id: partition for partition in Partition.objects.all()}
    for run in SearchRun.objects.exclude(overture_snapshot_id=None).iterator():
        partition = partitions.get(run.overture_snapshot_id)
        if partition is not None:
            run.overture_partition_id = partition.pk
            run.save(update_fields=("overture_partition", "updated_at"))

    campaigns = Campaign.objects.exclude(overture_snapshot_id=None).select_related(
        "overture_snapshot"
    )
    for campaign in campaigns.iterator():
        snapshot = campaign.overture_snapshot
        partition = partitions.get(snapshot.pk)
        if snapshot.release_record_id is not None:
            campaign.overture_release_id = snapshot.release_record_id
            campaign.save(update_fields=("overture_release", "updated_at"))
        if partition is None:
            continue
        dataset_zones = {
            zone.boundary_hash: zone for zone in DatasetZone.objects.filter(snapshot_id=snapshot.pk)
        }
        for position, selection in enumerate(
            campaign.zone_selections.select_related("zone").order_by("sort_order", "created_at")
        ):
            district = selection.zone
            dataset_zone = dataset_zones.get(selection.boundary_hash_snapshot)
            if not district.province_code or snapshot.release_record_id is None:
                continue
            coverage, _ = Coverage.objects.get_or_create(
                campaign_id=campaign.pk,
                district_id=district.pk,
                defaults={
                    "release_id": snapshot.release_record_id,
                    "partition_id": partition.pk,
                    "province_id": district.parent_id,
                    "province_code_snapshot": district.province_code,
                    "province_name_snapshot": district.province_name,
                    "district_code_snapshot": district.official_code,
                    "district_name_snapshot": selection.name_snapshot,
                    "district_level_snapshot": district.level,
                    "district_label_snapshot": district.label_plural,
                    "boundary_geojson_snapshot": selection.boundary_geojson_snapshot,
                    "boundary_bbox_snapshot": selection.boundary_bbox_snapshot,
                    "boundary_hash_snapshot": selection.boundary_hash_snapshot,
                    "boundary_revision_snapshot": selection.boundary_revision_snapshot,
                    "boundary_source_snapshot": (
                        district.boundary_source if dataset_zone is None else dataset_zone.source
                    ),
                    "boundary_attribution_snapshot": (
                        district.boundary_attribution
                        if dataset_zone is None
                        else dataset_zone.attribution
                    ),
                    "sort_order": position,
                },
            )
            SearchQuery.objects.filter(
                campaign_id=campaign.pk,
                zone_boundary_hash=selection.boundary_hash_snapshot,
                coverage_selection__isnull=True,
            ).update(coverage_selection_id=coverage.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("campaigns", "0012_outboundmessage_campaign_enrollment_and_more"),
        ("overture", "0003_overture_release_province_partitions"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="overture_release",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaigns",
                to="overture.overturerelease",
            ),
        ),
        migrations.AddField(
            model_name="searchrun",
            name="overture_partition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="search_runs",
                to="overture.overturecoveragepartition",
            ),
        ),
        migrations.CreateModel(
            name="CampaignCoverageSelection",
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
                ("province_code_snapshot", models.CharField(max_length=20)),
                ("province_name_snapshot", models.CharField(max_length=160)),
                ("district_code_snapshot", models.CharField(max_length=120)),
                ("district_name_snapshot", models.CharField(max_length=160)),
                ("district_level_snapshot", models.CharField(max_length=20)),
                ("district_label_snapshot", models.CharField(max_length=40)),
                ("boundary_geojson_snapshot", models.JSONField(default=dict)),
                ("boundary_bbox_snapshot", models.JSONField(default=list)),
                ("boundary_hash_snapshot", models.CharField(max_length=64)),
                ("boundary_revision_snapshot", models.PositiveIntegerField(default=1)),
                ("boundary_source_snapshot", models.CharField(blank=True, max_length=300)),
                (
                    "boundary_attribution_snapshot",
                    models.CharField(blank=True, max_length=500),
                ),
                ("sort_order", models.PositiveIntegerField(default=0)),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="coverage_selections",
                        to="campaigns.campaign",
                    ),
                ),
                (
                    "district",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="campaign_district_coverage",
                        to="configuration.searchzone",
                    ),
                ),
                (
                    "partition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="campaign_selections",
                        to="overture.overturecoveragepartition",
                    ),
                ),
                (
                    "province",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="campaign_province_coverage",
                        to="configuration.searchzone",
                    ),
                ),
                (
                    "release",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="campaign_selections",
                        to="overture.overturerelease",
                    ),
                ),
            ],
            options={"ordering": ("sort_order", "district_name_snapshot")},
        ),
        migrations.AddField(
            model_name="searchquery",
            name="coverage_selection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="search_queries",
                to="campaigns.campaigncoverageselection",
            ),
        ),
        migrations.RunPython(backfill_campaign_coverage, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="campaigncoverageselection",
            constraint=models.UniqueConstraint(
                condition=Q(district__isnull=False),
                fields=("campaign", "district"),
                name="campaign_coverage_district_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigncoverageselection",
            constraint=models.UniqueConstraint(
                fields=("campaign", "sort_order"),
                name="campaign_coverage_order_unique",
            ),
        ),
    ]
