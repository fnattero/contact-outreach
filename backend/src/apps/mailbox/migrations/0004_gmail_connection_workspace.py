from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def backfill_gmail_connection_workspace(apps, schema_editor) -> None:
    GmailConnection = apps.get_model("mailbox", "GmailConnection")
    Membership = apps.get_model("accounts", "Membership")
    Workspace = apps.get_model("accounts", "Workspace")

    workspace = Workspace.objects.order_by("created_at", "pk").first()
    if workspace is None:
        workspace = Workspace.objects.create(singleton_key=1, name="Mi empresa")
    memberships = dict(Membership.objects.values_list("user_id", "workspace_id"))
    for connection in GmailConnection.objects.filter(workspace__isnull=True).iterator():
        connection.workspace_id = memberships.get(connection.owner_id, workspace.pk)
        connection.save(update_fields=("workspace",))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("mailbox", "0003_inboundmessage_campaign_enrollment_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="gmailconnection",
            name="workspace",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="gmail_connection",
                to="accounts.workspace",
            ),
        ),
        migrations.RunPython(backfill_gmail_connection_workspace, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="gmailconnection",
            name="workspace",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="gmail_connection",
                to="accounts.workspace",
            ),
        ),
    ]
