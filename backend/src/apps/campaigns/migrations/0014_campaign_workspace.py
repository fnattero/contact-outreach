from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def backfill_campaign_workspace(apps, schema_editor) -> None:
    del schema_editor
    Campaign = apps.get_model("campaigns", "Campaign")
    Membership = apps.get_model("accounts", "Membership")
    Workspace = apps.get_model("accounts", "Workspace")

    workspace = Workspace.objects.order_by("created_at", "pk").first()
    if workspace is None:
        workspace = Workspace.objects.create(singleton_key=1, name="Mi empresa")
    memberships = dict(Membership.objects.values_list("user_id", "workspace_id"))
    for campaign in Campaign.objects.filter(workspace__isnull=True).iterator():
        campaign.workspace_id = memberships.get(campaign.created_by_id, workspace.pk)
        campaign.save(update_fields=("workspace",))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("campaigns", "0013_campaign_province_coverage"),
        ("catalogs", "0002_workspace_ownership"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="workspace",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaigns",
                to="accounts.workspace",
            ),
        ),
        migrations.RunPython(backfill_campaign_workspace, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="campaign",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaigns",
                to="accounts.workspace",
            ),
        ),
        migrations.AddIndex(
            model_name="campaign",
            index=models.Index(
                fields=("workspace", "state", "-created_at"),
                name="campaign_ws_state_created_idx",
            ),
        ),
    ]
