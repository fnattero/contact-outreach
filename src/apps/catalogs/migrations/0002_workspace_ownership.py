from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def backfill_catalog_workspace(apps, schema_editor) -> None:
    Catalog = apps.get_model("catalogs", "Catalog")
    Membership = apps.get_model("accounts", "Membership")
    Workspace = apps.get_model("accounts", "Workspace")

    workspace = Workspace.objects.order_by("created_at", "pk").first()
    if workspace is None:
        workspace = Workspace.objects.create(singleton_key=1, name="Mi empresa")
    memberships = dict(Membership.objects.values_list("user_id", "workspace_id"))
    for catalog in Catalog.objects.filter(workspace__isnull=True).iterator():
        catalog.workspace_id = memberships.get(catalog.uploaded_by_id, workspace.pk)
        catalog.save(update_fields=("workspace",))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("catalogs", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalog",
            name="workspace",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="catalogs",
                to="accounts.workspace",
            ),
        ),
        migrations.RunPython(backfill_catalog_workspace, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="catalog",
            name="catalog_name_version_unique",
        ),
        migrations.AlterField(
            model_name="catalog",
            name="sha256",
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name="catalog",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="catalogs",
                to="accounts.workspace",
            ),
        ),
        migrations.AddConstraint(
            model_name="catalog",
            constraint=models.UniqueConstraint(
                fields=("workspace", "name", "version"),
                name="catalog_workspace_name_version_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="catalog",
            constraint=models.UniqueConstraint(
                fields=("workspace", "sha256"),
                name="catalog_workspace_sha256_unique",
            ),
        ),
    ]
