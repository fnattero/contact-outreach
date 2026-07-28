import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
        ("campaigns", "0014_campaign_workspace"),
        ("contacts", "0002_backfill_contact_foundation"),
        ("mailbox", "0004_gmail_connection_workspace"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ContactCommunicationPlan",
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
                    "purpose",
                    models.CharField(
                        choices=[
                            ("CHECK_IN", "Preguntar cómo está"),
                            ("PRODUCT_FEEDBACK", "Pedir opinión sobre el producto"),
                            ("ADMIN_GOAL", "Objetivo escrito por el administrador"),
                        ],
                        max_length=40,
                    ),
                ),
                ("goal_text", models.TextField(blank=True, max_length=1000)),
                (
                    "cadence_days",
                    models.PositiveSmallIntegerField(
                        default=30, validators=[django.core.validators.MinValueValidator(7)]
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("REVIEW_BEFORE_SEND", "Revisar antes de enviar"),
                            ("AUTOMATIC", "Enviar automáticamente"),
                        ],
                        default="REVIEW_BEFORE_SEND",
                        max_length=30,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("DISABLED", "Desactivado"),
                            ("ACTIVE", "Activo"),
                            ("PAUSED", "Pausado"),
                        ],
                        default="DISABLED",
                        max_length=20,
                    ),
                ),
                ("snoozed_until", models.DateTimeField(blank=True, null=True)),
                ("next_due_at", models.DateTimeField(blank=True, null=True)),
                ("last_interaction_at", models.DateTimeField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "contact",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="communication_plan",
                        to="contacts.contact",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_contact_communication_plans",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "preferred_email",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="communication_plans",
                        to="contacts.emailaddress",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="updated_contact_communication_plans",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ConversationMemory",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField()),
                ("summary", models.JSONField(default=dict)),
                ("source_message_ids", models.JSONField(default=list)),
                ("source_hash", models.CharField(max_length=64)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="memories",
                        to="contacts.contact",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="memories",
                        to="contacts.conversation",
                    ),
                ),
            ],
            options={
                "ordering": ("contact", "-version"),
            },
        ),
        migrations.CreateModel(
            name="EmailCandidate",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("original_literal", models.CharField(max_length=320)),
                ("normalized_email", models.CharField(max_length=320)),
                (
                    "region",
                    models.CharField(
                        choices=[
                            ("NEW_CONTENT", "Texto nuevo"),
                            ("SIGNATURE", "Firma"),
                            ("QUOTED", "Texto citado"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[("TEXT", "Texto"), ("MAILTO", "Enlace de email")], max_length=20
                    ),
                ),
                ("start_offset", models.PositiveIntegerField(blank=True, null=True)),
                ("end_offset", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "syntax_state",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("VALID", "Válido"),
                            ("INVALID", "Inválido"),
                            ("TRANSIENT", "No se pudo confirmar"),
                            ("RESTRICTED", "No se puede contactar"),
                            ("CONFLICT", "Pertenece a otra empresa"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                (
                    "mx_state",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("VALID", "Válido"),
                            ("INVALID", "Inválido"),
                            ("TRANSIENT", "No se pudo confirmar"),
                            ("RESTRICTED", "No se puede contactar"),
                            ("CONFLICT", "Pertenece a otra empresa"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                (
                    "restriction_state",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("VALID", "Válido"),
                            ("INVALID", "Inválido"),
                            ("TRANSIENT", "No se pudo confirmar"),
                            ("RESTRICTED", "No se puede contactar"),
                            ("CONFLICT", "Pertenece a otra empresa"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                (
                    "ownership_state",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("VALID", "Válido"),
                            ("INVALID", "Inválido"),
                            ("TRANSIENT", "No se pudo confirmar"),
                            ("RESTRICTED", "No se puede contactar"),
                            ("CONFLICT", "Pertenece a otra empresa"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("evidence_hash", models.CharField(max_length=64)),
                (
                    "inbound",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="email_candidates",
                        to="mailbox.inboundmessage",
                    ),
                ),
                (
                    "resolved_email_address",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inbound_candidates",
                        to="contacts.emailaddress",
                    ),
                ),
            ],
            options={
                "ordering": ("created_at",),
            },
        ),
        migrations.CreateModel(
            name="HumanTask",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("kind", models.CharField(max_length=80)),
                ("reason", models.CharField(max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("OPEN", "Necesita que lo revises"),
                            ("RESOLVED", "Resuelta"),
                            ("DISMISSED", "Descartada"),
                        ],
                        default="OPEN",
                        max_length=20,
                    ),
                ),
                ("friendly_summary", models.CharField(max_length=300)),
                ("opened_at", models.DateTimeField()),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("resolution_note", models.TextField(blank=True)),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="human_tasks",
                        to="contacts.contact",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="human_tasks",
                        to="contacts.conversation",
                    ),
                ),
                (
                    "inbound",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="human_tasks",
                        to="mailbox.inboundmessage",
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="resolved_human_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="human_tasks",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "ordering": ("-opened_at",),
            },
        ),
        migrations.CreateModel(
            name="KnowledgeFact",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=240)),
                ("category", models.CharField(blank=True, max_length=120)),
                ("active", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_knowledge_facts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="knowledge_facts",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "ordering": ("category", "title"),
            },
        ),
        migrations.CreateModel(
            name="KnowledgeFactRevision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField()),
                ("text", models.TextField(max_length=4000)),
                ("source_notes", models.TextField(blank=True)),
                ("content_hash", models.CharField(max_length=64)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="approved_knowledge_fact_revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "fact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revisions",
                        to="automation.knowledgefact",
                    ),
                ),
            ],
            options={
                "ordering": ("fact", "-version"),
            },
        ),
        migrations.CreateModel(
            name="NotificationDelivery",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("channel", models.CharField(default="GMAIL", max_length=20)),
                (
                    "subject",
                    models.CharField(
                        default="Hay una conversación que necesita revisión", max_length=255
                    ),
                ),
                ("secure_url", models.URLField(max_length=1000)),
                ("idempotency_key", models.CharField(max_length=200, unique=True)),
                ("message_id", models.CharField(max_length=255, unique=True)),
                ("gmail_message_id", models.CharField(blank=True, max_length=255)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("SENDING", "Enviando"),
                            ("RECONCILING", "Confirmando envío"),
                            ("SENT", "Enviada"),
                            ("FAILED", "Falló"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="attention_notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="notifications",
                        to="automation.humantask",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ReplyAutomationConfiguration",
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
                    "mode",
                    models.CharField(
                        choices=[
                            ("OFF", "Desactivada"),
                            ("SHADOW", "Sólo observar"),
                            ("LIVE", "Respuestas automáticas activas"),
                        ],
                        default="SHADOW",
                        max_length=20,
                    ),
                ),
                ("policy_version", models.CharField(default="2026-07", max_length=40)),
                ("live_enabled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "live_enabled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="enabled_reply_automation",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reply_automation_configuration",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ReplyDecision",
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
                    "mode",
                    models.CharField(
                        choices=[
                            ("OFF", "Desactivada"),
                            ("SHADOW", "Sólo observar"),
                            ("LIVE", "Respuestas automáticas activas"),
                        ],
                        default="SHADOW",
                        max_length=20,
                    ),
                ),
                ("provider", models.CharField(max_length=50)),
                ("model", models.CharField(max_length=120)),
                ("schema_version", models.CharField(default="1", max_length=40)),
                ("policy_version", models.CharField(max_length=40)),
                ("classification", models.CharField(max_length=40)),
                ("intent", models.CharField(max_length=80)),
                ("action", models.CharField(max_length=40)),
                ("confidence", models.DecimalField(decimal_places=3, max_digits=4)),
                ("proposed_body", models.TextField(blank=True)),
                ("human_reason", models.CharField(blank=True, max_length=80)),
                ("context_manifest", models.JSONField(default=dict)),
                ("context_hash", models.CharField(max_length=64)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pendiente"),
                            ("SHADOW_RECORDED", "Registrada en modo observación"),
                            ("NO_ACTION", "No corresponde responder"),
                            ("AUTO_ELIGIBLE", "Puede responder automáticamente"),
                            ("AUTHORIZED", "Autorizada"),
                            ("EXECUTING", "Enviando"),
                            ("COMPLETED", "Respondido automáticamente"),
                            ("HUMAN_REQUIRED", "Necesita que lo revises"),
                            ("REJECTED_POLICY", "No autorizada por seguridad"),
                            ("FAILED", "No se pudo analizar"),
                        ],
                        default="PENDING",
                        max_length=30,
                    ),
                ),
                (
                    "reviewed_outcome",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("CORRECT", "Correcta"),
                            ("INCORRECT", "Incorrecta"),
                            ("NEEDED_HUMAN", "Necesitaba una persona"),
                        ],
                        max_length=30,
                    ),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("feedback", models.TextField(blank=True)),
                ("error", models.TextField(blank=True)),
                (
                    "candidate",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reply_decisions",
                        to="automation.emailcandidate",
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reply_decisions",
                        to="contacts.contact",
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reply_decisions",
                        to="contacts.conversation",
                    ),
                ),
                (
                    "inbound",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reply_decision",
                        to="mailbox.inboundmessage",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviewed_reply_decisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "selected_facts",
                    models.ManyToManyField(
                        blank=True,
                        related_name="reply_decisions",
                        to="automation.knowledgefactrevision",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reply_decisions",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddField(
            model_name="humantask",
            name="decision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="human_tasks",
                to="automation.replydecision",
            ),
        ),
        migrations.CreateModel(
            name="ScheduledContactAttempt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("due_at", models.DateTimeField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("DUE", "Pendiente"),
                            ("DRAFT_REVIEW", "Listo para revisar"),
                            ("AUTHORIZED", "Autorizado"),
                            ("SENT", "Enviado"),
                            ("HUMAN_REQUIRED", "Necesita que lo revises"),
                            ("CANCELLED", "Cancelado"),
                            ("INELIGIBLE", "No se puede enviar"),
                        ],
                        default="DUE",
                        max_length=30,
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=200, unique=True)),
                ("reason", models.CharField(blank=True, max_length=300)),
                ("authorized_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "decision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="scheduled_attempts",
                        to="automation.replydecision",
                    ),
                ),
                (
                    "outbound_message",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="scheduled_contact_attempts",
                        to="campaigns.outboundmessage",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="attempts",
                        to="automation.contactcommunicationplan",
                    ),
                ),
            ],
            options={
                "ordering": ("due_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="contactcommunicationplan",
            constraint=models.CheckConstraint(
                condition=models.Q(("cadence_days__gte", 7)), name="contact_plan_cadence_minimum"
            ),
        ),
        migrations.AddConstraint(
            model_name="conversationmemory",
            constraint=models.UniqueConstraint(
                fields=("contact", "version"), name="contact_memory_version_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="emailcandidate",
            constraint=models.UniqueConstraint(
                fields=("inbound", "normalized_email", "region"),
                name="inbound_candidate_region_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="emailcandidate",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("end_offset__isnull", True), ("start_offset__isnull", True)),
                    models.Q(("end_offset__isnull", False), ("start_offset__isnull", False)),
                    _connector="OR",
                ),
                name="candidate_offsets_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgefact",
            constraint=models.UniqueConstraint(
                fields=("workspace", "title"), name="knowledge_fact_workspace_title_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgefactrevision",
            constraint=models.UniqueConstraint(
                fields=("fact", "version"), name="knowledge_fact_revision_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgefactrevision",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("approved_at__isnull", True), ("approved_by__isnull", True)),
                    models.Q(("approved_at__isnull", False), ("approved_by__isnull", False)),
                    _connector="OR",
                ),
                name="knowledge_revision_approval_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="notificationdelivery",
            constraint=models.UniqueConstraint(
                fields=("task", "recipient", "channel"), name="task_recipient_channel_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="replyautomationconfiguration",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("live_enabled_at__isnull", False),
                        ("live_enabled_by__isnull", False),
                        ("mode", "LIVE"),
                    ),
                    models.Q(("mode", "LIVE"), _negated=True),
                    _connector="OR",
                ),
                name="reply_live_activation_recorded",
            ),
        ),
        migrations.AddConstraint(
            model_name="replydecision",
            constraint=models.CheckConstraint(
                condition=models.Q(("confidence__gte", 0), ("confidence__lte", 1)),
                name="reply_decision_confidence_0_1",
            ),
        ),
        migrations.AddConstraint(
            model_name="replydecision",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("reviewed_at__isnull", True),
                        ("reviewed_by__isnull", True),
                        ("reviewed_outcome", ""),
                    ),
                    models.Q(
                        ("reviewed_at__isnull", False),
                        ("reviewed_by__isnull", False),
                        models.Q(("reviewed_outcome", ""), _negated=True),
                    ),
                    _connector="OR",
                ),
                name="reply_decision_review_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="humantask",
            constraint=models.UniqueConstraint(
                condition=models.Q(("inbound__isnull", False), ("status", "OPEN")),
                fields=("inbound", "reason"),
                name="open_task_inbound_reason_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="humantask",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("resolved_at__isnull", True),
                        ("resolved_by__isnull", True),
                        ("status", "OPEN"),
                    ),
                    models.Q(
                        ("resolved_at__isnull", False),
                        ("resolved_by__isnull", False),
                        ("status__in", ("RESOLVED", "DISMISSED")),
                    ),
                    _connector="OR",
                ),
                name="human_task_resolution_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduledcontactattempt",
            constraint=models.UniqueConstraint(
                fields=("plan", "due_at"), name="scheduled_plan_due_unique"
            ),
        ),
    ]
