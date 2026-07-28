import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("campaigns", "0014_campaign_workspace"),
        ("catalogs", "0002_workspace_ownership"),
        ("configuration", "0009_workspace_ownership"),
        ("contacts", "0002_backfill_contact_foundation"),
        ("mailbox", "0004_gmail_connection_workspace"),
        ("overture", "0003_overture_release_province_partitions"),
        ("prospects", "0005_prospect_campaign_enrollment_prospect_organization"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CampaignAttachment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveSmallIntegerField()),
            ],
            options={
                "ordering": ("position", "created_at"),
            },
        ),
        migrations.CreateModel(
            name="CampaignDeliveryReservation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("normalized_email", models.CharField(max_length=320)),
                ("local_date", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("RESERVED", "Reservado"),
                            ("CONSUMED", "Enviado"),
                            ("RELEASED", "Liberado antes del envío"),
                        ],
                        default="RESERVED",
                        max_length=20,
                    ),
                ),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("released_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="OutboundAttachment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveSmallIntegerField()),
                ("catalog_version", models.PositiveIntegerField()),
                ("storage_key", models.CharField(max_length=300)),
                ("filename", models.CharField(max_length=255)),
                ("byte_size", models.PositiveBigIntegerField()),
                ("sha256", models.CharField(max_length=64)),
            ],
            options={
                "ordering": ("position", "created_at"),
            },
        ),
        migrations.RemoveConstraint(
            model_name="outboundmessage",
            name="first_contact_email_sequence_unique",
        ),
        migrations.AddField(
            model_name="campaign",
            name="approval_mode",
            field=models.CharField(
                choices=[
                    ("CAMPAIGN", "Aprobar toda la campaña"),
                    ("PER_MESSAGE", "Revisar cada mensaje"),
                ],
                default="CAMPAIGN",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campaign",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="approved_campaigns",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="attachment_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="campaign",
            name="audience_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="campaign",
            name="content_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="campaign",
            name="initial_body_snapshot",
            field=models.TextField(
                default="Buen día:\n\nNos ponemos en contacto para acercarle nuestra propuesta de carbones para motores y compartir nuestros catálogos. Trabajamos con distintas medidas y aplicaciones para motores y herramientas eléctricas. Si le resulta de interés, puede responder este correo y con gusto ampliaremos la información.\n\nSaludos."
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="initial_subject_snapshot",
            field=models.CharField(default="Propuesta comercial", max_length=255),
        ),
        migrations.AddField(
            model_name="campaign",
            name="referred_body_snapshot",
            field=models.TextField(
                default="Buen día:\n\nNos indicaron que esta es la dirección adecuada para enviar nuestra propuesta comercial. Adjuntamos la información y los catálogos correspondientes. Quedamos a disposición ante cualquier consulta.\n\nSaludos."
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="referred_subject_snapshot",
            field=models.CharField(default="Propuesta comercial", max_length=255),
        ),
        migrations.AddField(
            model_name="campaign",
            name="reminder_body_snapshot",
            field=models.TextField(
                default="Buen día:\n\nRetomamos nuestro correo anterior para saber si pudo revisar la propuesta y los catálogos. Si necesita información sobre alguna medida o aplicación, puede responder este mensaje.\n\nSaludos."
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="reminder_delay_days",
            field=models.PositiveSmallIntegerField(
                default=3, validators=[django.core.validators.MinValueValidator(1)]
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="reminder_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="campaign",
            name="schedule_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="campaign",
            name="signature_snapshot",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="campaign",
            name="template_revision_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="content_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="email_address",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="outbound_messages",
                to="contacts.emailaddress",
            ),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="mime_size",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="reminder_for",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaign_reminder",
                to="campaigns.outboundmessage",
            ),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="scheduled_for",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="semantic_action_key",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="signature_snapshot",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="campaign",
            name="state",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Borrador"),
                    ("DISCOVERING", "Buscando destinatarios"),
                    ("AWAITING_APPROVAL", "Lista para aprobar"),
                    ("RUNNING", "En curso"),
                    ("PAUSED", "Pausada"),
                    ("CANCELLED", "Cancelada"),
                    ("COMPLETED", "Completada"),
                    ("STOPPED_ERROR", "Detenida por error"),
                ],
                default="DRAFT",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="campaign",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="messages",
                to="campaigns.campaign",
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="catalog",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="messages",
                to="catalogs.catalog",
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="catalog_version",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="kind",
            field=models.CharField(
                choices=[
                    ("FIRST_CONTACT", "Primer contacto (histórico)"),
                    ("INITIAL", "Propuesta inicial"),
                    ("CAMPAIGN_REMINDER", "Recordatorio de campaña"),
                    ("MANUAL_REPLY", "Respuesta manual"),
                    ("AUTOMATIC_REPLY", "Respuesta automática"),
                    ("REFERRED_PROPOSAL", "Propuesta reenviada"),
                    ("REDIRECT_ACK", "Confirmación de reenvío"),
                    ("SCHEDULED_CONTACT", "Contacto programado"),
                ],
                default="INITIAL",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="parent_inbound",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="outbound_replies",
                to="mailbox.inboundmessage",
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="prospect",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="outbound_messages",
                to="prospects.prospect",
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="prospect_email",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="outbound_messages",
                to="prospects.prospectemail",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaign",
            constraint=models.CheckConstraint(
                condition=models.Q(("reminder_delay_days__gte", 1)),
                name="campaign_reminder_delay_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaign",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("approved_at__isnull", True), ("approved_by__isnull", True)),
                    models.Q(("approved_at__isnull", False), ("approved_by__isnull", False)),
                    _connector="OR",
                ),
                name="campaign_approval_fields_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundmessage",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("contact_sequence__isnull", False), ("kind__in", ("FIRST_CONTACT", "INITIAL"))
                ),
                fields=("recipient_normalized", "contact_sequence"),
                name="first_contact_email_sequence_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundmessage",
            constraint=models.UniqueConstraint(
                condition=models.Q(("semantic_action_key", ""), _negated=True),
                fields=("semantic_action_key",),
                name="outbound_semantic_action_unique",
            ),
        ),
        migrations.AddField(
            model_name="campaignattachment",
            name="campaign",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="campaigns.campaign",
            ),
        ),
        migrations.AddField(
            model_name="campaignattachment",
            name="catalog",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaign_attachments",
                to="catalogs.catalog",
            ),
        ),
        migrations.AddField(
            model_name="campaigndeliveryreservation",
            name="campaign",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="delivery_reservations",
                to="campaigns.campaign",
            ),
        ),
        migrations.AddField(
            model_name="campaigndeliveryreservation",
            name="email_address",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="delivery_reservations",
                to="contacts.emailaddress",
            ),
        ),
        migrations.AddField(
            model_name="campaigndeliveryreservation",
            name="message",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="same_day_reservation",
                to="campaigns.outboundmessage",
            ),
        ),
        migrations.AddField(
            model_name="campaigndeliveryreservation",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaign_delivery_reservations",
                to="accounts.workspace",
            ),
        ),
        migrations.AddField(
            model_name="outboundattachment",
            name="catalog",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="outbound_attachments",
                to="catalogs.catalog",
            ),
        ),
        migrations.AddField(
            model_name="outboundattachment",
            name="message",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attachments",
                to="campaigns.outboundmessage",
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignattachment",
            constraint=models.UniqueConstraint(
                fields=("campaign", "catalog"), name="campaign_attachment_catalog_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="campaignattachment",
            constraint=models.UniqueConstraint(
                fields=("campaign", "position"), name="campaign_attachment_position_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="campaigndeliveryreservation",
            index=models.Index(
                fields=["campaign", "local_date"], name="campaigns_c_campaig_d23f95_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="campaigndeliveryreservation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "RELEASED"), _negated=True),
                fields=("workspace", "normalized_email", "local_date"),
                name="workspace_email_campaign_day_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundattachment",
            constraint=models.UniqueConstraint(
                fields=("message", "catalog"), name="outbound_attachment_catalog_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundattachment",
            constraint=models.UniqueConstraint(
                fields=("message", "position"), name="outbound_attachment_position_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundattachment",
            constraint=models.CheckConstraint(
                condition=models.Q(("byte_size__gt", 0)), name="outbound_attachment_size_positive"
            ),
        ),
    ]
