from __future__ import annotations

import uuid
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


RETIRED_REASON = "provider_retired: el proveedor histórico fue retirado; no se reintentará"


def cut_over_legacy_campaigns(apps, schema_editor) -> None:
    del schema_editor
    AuditEvent = apps.get_model("audit", "AuditEvent")
    BackgroundJob = apps.get_model("audit", "BackgroundJob")
    Campaign = apps.get_model("campaigns", "Campaign")
    CategorySelection = apps.get_model("campaigns", "CampaignCategorySelection")
    ZoneSelection = apps.get_model("campaigns", "CampaignZoneSelection")
    SearchQuery = apps.get_model("campaigns", "SearchQuery")
    SearchRun = apps.get_model("campaigns", "SearchRun")
    now = timezone.now()
    legacy_campaign_ids = list(
        Campaign.objects.filter(extractor_provider="outscraper").values_list("pk", flat=True)
    )

    for selection in CategorySelection.objects.select_related("campaign", "category").filter(
        campaign__state="DRAFT"
    ):
        rules = selection.category.rules.filter(active=True).order_by("sort_order", "created_at")
        selection.rules_snapshot = [
            {
                "taxonomy_code": rule.taxonomy_code,
                "name_terms": list(rule.name_terms),
            }
            for rule in rules
        ]
        selection.rules_revision_snapshot = selection.category.rules_revision
        selection.save(update_fields=("rules_snapshot", "rules_revision_snapshot", "updated_at"))

    for selection in ZoneSelection.objects.select_related("campaign", "zone").filter(
        campaign__state="DRAFT"
    ):
        selection.boundary_geojson_snapshot = selection.zone.boundary_geojson
        selection.boundary_bbox_snapshot = selection.zone.boundary_bbox
        selection.boundary_hash_snapshot = selection.zone.boundary_hash
        selection.boundary_revision_snapshot = selection.zone.boundary_revision
        selection.save(
            update_fields=(
                "boundary_geojson_snapshot",
                "boundary_bbox_snapshot",
                "boundary_hash_snapshot",
                "boundary_revision_snapshot",
                "updated_at",
            )
        )

    for campaign in Campaign.objects.filter(state="DRAFT", extractor_provider="outscraper"):
        campaign.extractor_provider = "overture"
        campaign.cost_limit = Decimal("0")
        campaign.cost_currency = "USD"
        campaign.save(
            update_fields=(
                "extractor_provider",
                "cost_limit",
                "cost_currency",
                "updated_at",
            )
        )
        AuditEvent.objects.create(
            actor_type="SYSTEM",
            actor=None,
            action="campaign.extractor_migrated",
            entity_type="Campaign",
            entity_id=str(campaign.pk),
            before={"extractor_provider": "outscraper"},
            after={"extractor_provider": "overture", "reason": "provider_retired"},
            correlation_id=uuid.uuid4(),
        )

    unfinished_runs = SearchRun.objects.filter(
        provider="outscraper",
        state__in=("PENDING", "RUNNING", "RETRY_WAIT"),
    )
    run_states = list(unfinished_runs.values_list("pk", "state", "query_id"))
    run_ids = [row[0] for row in run_states]
    run_query_ids = [row[2] for row in run_states]
    # A query can be pending before its first SearchRun is created. Retiring only
    # queries reached through unfinished runs would leave that durable work
    # recoverable after the provider adapter has been removed.
    query_states = list(
        SearchQuery.objects.filter(
            state__in=("PENDING", "RUNNING", "RETRY_WAIT"),
        )
        .filter(models.Q(campaign_id__in=legacy_campaign_ids) | models.Q(pk__in=run_query_ids))
        .values_list("pk", "state")
    )
    query_ids = [row[0] for row in query_states]
    unfinished_runs.update(
        state="FAILED_PERMANENT",
        error=RETIRED_REASON,
        next_poll_at=None,
        finished_at=now,
        updated_at=now,
    )
    SearchQuery.objects.filter(
        pk__in=query_ids,
        state__in=("PENDING", "RUNNING", "RETRY_WAIT"),
    ).update(state="FAILED_PERMANENT", last_error=RETIRED_REASON, updated_at=now)
    BackgroundJob.objects.filter(
        entity_type="SearchRun", entity_id__in=[str(pk) for pk in run_ids]
    ).update(
        state="FAILED",
        error=RETIRED_REASON,
        next_retry_at=None,
        finished_at=now,
        updated_at=now,
    )
    AuditEvent.objects.bulk_create(
        [
            AuditEvent(
                actor_type="SYSTEM",
                actor=None,
                action="search_run.provider_retired",
                entity_type="SearchRun",
                entity_id=str(run_id),
                before={"state": state, "provider": "outscraper"},
                after={"state": "FAILED_PERMANENT", "reason": RETIRED_REASON},
                correlation_id=uuid.uuid4(),
            )
            for run_id, state, _query_id in run_states
        ]
        + [
            AuditEvent(
                actor_type="SYSTEM",
                actor=None,
                action="search_query.provider_retired",
                entity_type="SearchQuery",
                entity_id=str(query_id),
                before={"state": state},
                after={"state": "FAILED_PERMANENT", "reason": RETIRED_REASON},
                correlation_id=uuid.uuid4(),
            )
            for query_id, state in query_states
        ],
        batch_size=1_000,
    )

    affected = Campaign.objects.filter(
        extractor_provider="outscraper",
        state__in=("RUNNING", "PAUSED"),
    )
    for campaign in affected:
        before = {"state": campaign.state, "discovery_state": campaign.discovery_state}
        campaign.state = "STOPPED_ERROR"
        campaign.discovery_state = "FAILED_PROVIDER"
        campaign.status_reason = RETIRED_REASON
        campaign.discovery_stop_reason = RETIRED_REASON
        campaign.finished_at = now
        campaign.save(
            update_fields=(
                "state",
                "discovery_state",
                "status_reason",
                "discovery_stop_reason",
                "finished_at",
                "updated_at",
            )
        )
        AuditEvent.objects.create(
            actor_type="SYSTEM",
            actor=None,
            action="campaign.provider_retired",
            entity_type="Campaign",
            entity_id=str(campaign.pk),
            before=before,
            after={
                "state": "STOPPED_ERROR",
                "discovery_state": "FAILED_PROVIDER",
                "reason": RETIRED_REASON,
            },
            correlation_id=uuid.uuid4(),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_backgroundjob"),
        ("campaigns", "0009_review_only_mode"),
        ("configuration", "0007_seed_caba_boundaries"),
        ("overture", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="campaign", name="campaign_outscraper_cost_usd"),
        migrations.AddField(
            model_name="campaign",
            name="overture_min_confidence",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("0.750"),
                max_digits=4,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(1),
                ],
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="overture_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaigns",
                to="overture.overturedatasetsnapshot",
            ),
        ),
        migrations.AddField(
            model_name="campaigncategoryselection",
            name="rules_snapshot",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="campaigncategoryselection",
            name="rules_revision_snapshot",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="campaignzoneselection",
            name="boundary_bbox_snapshot",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="campaignzoneselection",
            name="boundary_geojson_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="campaignzoneselection",
            name="boundary_hash_snapshot",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="campaignzoneselection",
            name="boundary_revision_snapshot",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="searchquery",
            name="criteria_json",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="searchquery",
            name="zone_boundary_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="searchrun",
            name="overture_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="search_runs",
                to="overture.overturedatasetsnapshot",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaign",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    overture_min_confidence__gte=0,
                    overture_min_confidence__lte=1,
                ),
                name="campaign_overture_confidence_0_1",
            ),
        ),
        migrations.RunPython(cut_over_legacy_campaigns, migrations.RunPython.noop),
    ]
