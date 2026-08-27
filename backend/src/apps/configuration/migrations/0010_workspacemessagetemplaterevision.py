import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("configuration", "0009_workspace_ownership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceMessageTemplateRevision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("INITIAL", "Propuesta inicial"),
                            ("REMINDER", "Recordatorio"),
                            ("REFERRED_PROPOSAL", "Propuesta reenviada"),
                        ],
                        max_length=30,
                    ),
                ),
                ("subject", models.CharField(blank=True, max_length=255)),
                ("body", models.TextField()),
                ("revision", models.PositiveIntegerField()),
                ("content_hash", models.CharField(max_length=64)),
                ("approved_at", models.DateTimeField()),
                ("active", models.BooleanField(default=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="approved_workspace_message_templates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="message_template_revisions",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "ordering": ("kind", "-revision"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("workspace", "kind", "revision"),
                        name="workspace_message_template_revision_unique",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("active", True)),
                        fields=("workspace", "kind"),
                        name="workspace_message_template_one_active",
                    ),
                ],
            },
        ),
    ]
