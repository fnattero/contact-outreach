from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def backfill_configuration_workspace(apps, schema_editor) -> None:
    del schema_editor
    Workspace = apps.get_model("accounts", "Workspace")
    workspace = Workspace.objects.order_by("created_at", "pk").first()
    if workspace is None:
        workspace = Workspace.objects.create(singleton_key=1, name="Mi empresa")

    for model_name in (
        "BusinessProfile",
        "IntegrationConfiguration",
        "PromptConfiguration",
    ):
        model = apps.get_model("configuration", model_name)
        rows = list(model.objects.filter(workspace__isnull=True).order_by("created_at", "pk"))
        if len(rows) > 1:
            raise RuntimeError(
                f"No se puede asignar {model_name}: hay más de una configuración heredada."
            )
        if rows:
            rows[0].workspace_id = workspace.pk
            rows[0].save(update_fields=("workspace",))

    SearchCategory = apps.get_model("configuration", "SearchCategory")
    SearchCategory.objects.filter(workspace__isnull=True).update(workspace_id=workspace.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("configuration", "0008_argentina_zone_hierarchy"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile",
            name="workspace",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="business_profile",
                to="accounts.workspace",
            ),
        ),
        migrations.AddField(
            model_name="integrationconfiguration",
            name="workspace",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="integration_configuration",
                to="accounts.workspace",
            ),
        ),
        migrations.AddField(
            model_name="promptconfiguration",
            name="workspace",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="prompt_configuration",
                to="accounts.workspace",
            ),
        ),
        migrations.AddField(
            model_name="searchcategory",
            name="workspace",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="search_categories",
                to="accounts.workspace",
            ),
        ),
        migrations.RunPython(backfill_configuration_workspace, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="searchcategory",
            name="configuration_category_active_name_unique",
        ),
        migrations.AlterField(
            model_name="businessprofile",
            name="workspace",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="business_profile",
                to="accounts.workspace",
            ),
        ),
        migrations.AlterField(
            model_name="integrationconfiguration",
            name="workspace",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="integration_configuration",
                to="accounts.workspace",
            ),
        ),
        migrations.AlterField(
            model_name="promptconfiguration",
            name="workspace",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="prompt_configuration",
                to="accounts.workspace",
            ),
        ),
        migrations.AlterField(
            model_name="searchcategory",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="search_categories",
                to="accounts.workspace",
            ),
        ),
        migrations.AddConstraint(
            model_name="searchcategory",
            constraint=models.UniqueConstraint(
                condition=Q(archived_at__isnull=True),
                fields=("workspace", "normalized_name"),
                name="configuration_category_workspace_active_name_unique",
            ),
        ),
    ]
