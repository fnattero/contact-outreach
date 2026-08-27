from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import TimestampedUUIDModel


class ReplyAutomationConfiguration(TimestampedUUIDModel):
    objects: models.Manager[ReplyAutomationConfiguration] = models.Manager()

    class Mode(models.TextChoices):
        OFF = "OFF", "Desactivada"
        SHADOW = "SHADOW", "Sólo observar"
        LIVE = "LIVE", "Respuestas automáticas activas"

    workspace = models.OneToOneField(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="reply_automation_configuration",
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.SHADOW)
    policy_version = models.CharField(max_length=40, default="2026-07")
    live_enabled_at = models.DateTimeField(blank=True, null=True)
    live_enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="enabled_reply_automation",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(mode="LIVE", live_enabled_at__isnull=False, live_enabled_by__isnull=False)
                    | ~Q(mode="LIVE")
                ),
                name="reply_live_activation_recorded",
            )
        ]


class KnowledgeFact(TimestampedUUIDModel):
    objects: models.Manager[KnowledgeFact] = models.Manager()

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="knowledge_facts",
    )
    title = models.CharField(max_length=240)
    category = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_knowledge_facts",
    )

    class Meta:
        ordering = ("category", "title")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "title"),
                name="knowledge_fact_workspace_title_unique",
            )
        ]

    def __str__(self) -> str:
        return self.title


class KnowledgeFactRevision(TimestampedUUIDModel):
    objects: models.Manager[KnowledgeFactRevision] = models.Manager()

    fact = models.ForeignKey(
        KnowledgeFact,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    version = models.PositiveIntegerField()
    text = models.TextField(max_length=4000)
    source_notes = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64)
    approved_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="approved_knowledge_fact_revisions",
    )
    superseded_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("fact", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("fact", "version"), name="knowledge_fact_revision_unique"
            ),
            models.CheckConstraint(
                condition=(
                    Q(approved_at__isnull=True, approved_by__isnull=True)
                    | Q(approved_at__isnull=False, approved_by__isnull=False)
                ),
                name="knowledge_revision_approval_consistent",
            ),
        ]

    @property
    def is_approved(self) -> bool:
        return self.approved_at is not None and self.superseded_at is None

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable = [
                "fact_id",
                "version",
                "text",
                "source_notes",
                "content_hash",
            ]
            if previous.approved_at is not None:
                immutable.extend(("approved_at", "approved_by_id"))
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValidationError(
                    "Una revisión de información aprobada no se edita; creá otra versión."
                )
        super().save(*args, **kwargs)


class WorkspaceKnowledgeContextRevision(TimestampedUUIDModel):
    """Approved global context injected into every automatic-reply request."""

    objects: models.Manager[WorkspaceKnowledgeContextRevision] = models.Manager()

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="knowledge_context_revisions",
    )
    version = models.PositiveIntegerField()
    context_text = models.TextField(max_length=4000)
    source_notes = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64)
    approved_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="approved_workspace_knowledge_context_revisions",
    )
    superseded_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("workspace", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "version"),
                name="workspace_knowledge_context_revision_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(approved_at__isnull=True, approved_by__isnull=True)
                    | Q(approved_at__isnull=False, approved_by__isnull=False)
                ),
                name="workspace_knowledge_context_approval_consistent",
            ),
        ]

    @property
    def is_approved(self) -> bool:
        return self.approved_at is not None and self.superseded_at is None

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable = [
                "workspace_id",
                "version",
                "context_text",
                "source_notes",
                "content_hash",
            ]
            if previous.approved_at is not None:
                immutable.extend(("approved_at", "approved_by_id"))
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValidationError("El contexto aprobado no se edita; creá una nueva versión.")
        super().save(*args, **kwargs)


class KnowledgeFactEmbedding(TimestampedUUIDModel):
    objects: models.Manager[KnowledgeFactEmbedding] = models.Manager()

    class State(models.TextChoices):
        READY = "READY", "Lista"
        FAILED = "FAILED", "No se pudo generar"

    revision = models.ForeignKey(
        KnowledgeFactRevision,
        on_delete=models.CASCADE,
        related_name="embeddings",
    )
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=120)
    dimensions = models.PositiveSmallIntegerField(
        validators=(MinValueValidator(64), MaxValueValidator(3072))
    )
    input_hash = models.CharField(max_length=64)
    vector = models.JSONField(default=list)
    state = models.CharField(max_length=20, choices=State.choices, default=State.READY)
    embedded_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("revision", "provider", "model", "dimensions")
        constraints = [
            models.UniqueConstraint(
                fields=("revision", "provider", "model", "dimensions"),
                name="knowledge_embedding_revision_provider_unique",
            ),
            models.CheckConstraint(
                condition=Q(dimensions__gte=64, dimensions__lte=3072),
                name="knowledge_embedding_dimensions_range",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.state == self.State.READY:
            if not isinstance(self.vector, list) or len(self.vector) != self.dimensions:
                raise ValidationError("El embedding listo debe tener la dimensión configurada.")
            if not all(isinstance(value, (int, float)) for value in self.vector):
                raise ValidationError("El embedding sólo puede contener números.")


class ConversationMemory(TimestampedUUIDModel):
    objects: models.Manager[ConversationMemory] = models.Manager()

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        related_name="memories",
    )
    conversation = models.ForeignKey(
        "contacts.Conversation",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="memories",
    )
    version = models.PositiveIntegerField()
    summary = models.JSONField(default=dict)
    source_message_ids = models.JSONField(default=list)
    source_hash = models.CharField(max_length=64)
    superseded_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("contact", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("contact", "version"), name="contact_memory_version_unique"
            )
        ]

    def clean(self) -> None:
        super().clean()
        contact_id = getattr(self, "contact_id", None)
        if getattr(self, "conversation_id", None) and contact_id:
            if getattr(self.conversation, "contact_id", None) != contact_id:
                raise ValidationError("La memoria debe pertenecer al mismo contacto.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable = (
                "contact_id",
                "conversation_id",
                "version",
                "summary",
                "source_message_ids",
                "source_hash",
            )
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValidationError(
                    "La memoria de conversación es histórica; creá una versión nueva."
                )
        super().save(*args, **kwargs)


class EmailCandidate(TimestampedUUIDModel):
    objects: models.Manager[EmailCandidate] = models.Manager()

    class Region(models.TextChoices):
        NEW_CONTENT = "NEW_CONTENT", "Texto nuevo"
        SIGNATURE = "SIGNATURE", "Firma"
        QUOTED = "QUOTED", "Texto citado"

    class Source(models.TextChoices):
        TEXT = "TEXT", "Texto"
        MAILTO = "MAILTO", "Enlace de email"

    class CheckState(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        VALID = "VALID", "Válido"
        INVALID = "INVALID", "Inválido"
        TRANSIENT = "TRANSIENT", "No se pudo confirmar"
        RESTRICTED = "RESTRICTED", "No se puede contactar"
        CONFLICT = "CONFLICT", "Pertenece a otra empresa"

    inbound = models.ForeignKey(
        "mailbox.InboundMessage",
        on_delete=models.PROTECT,
        related_name="email_candidates",
    )
    original_literal = models.CharField(max_length=320)
    normalized_email = models.CharField(max_length=320)
    region = models.CharField(max_length=20, choices=Region.choices)
    source = models.CharField(max_length=20, choices=Source.choices)
    start_offset = models.PositiveIntegerField(blank=True, null=True)
    end_offset = models.PositiveIntegerField(blank=True, null=True)
    syntax_state = models.CharField(
        max_length=20,
        choices=CheckState.choices,
        default=CheckState.PENDING,
    )
    mx_state = models.CharField(
        max_length=20,
        choices=CheckState.choices,
        default=CheckState.PENDING,
    )
    restriction_state = models.CharField(
        max_length=20,
        choices=CheckState.choices,
        default=CheckState.PENDING,
    )
    ownership_state = models.CharField(
        max_length=20,
        choices=CheckState.choices,
        default=CheckState.PENDING,
    )
    resolved_email_address = models.ForeignKey(
        "contacts.EmailAddress",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="inbound_candidates",
    )
    evidence_hash = models.CharField(max_length=64)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("inbound", "normalized_email", "region"),
                name="inbound_candidate_region_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(start_offset__isnull=True, end_offset__isnull=True)
                    | Q(start_offset__isnull=False, end_offset__isnull=False)
                ),
                name="candidate_offsets_consistent",
            ),
        ]


class ReplyDecision(TimestampedUUIDModel):
    objects: models.Manager[ReplyDecision] = models.Manager()

    class State(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        SHADOW_RECORDED = "SHADOW_RECORDED", "Registrada en modo observación"
        NO_ACTION = "NO_ACTION", "No corresponde responder"
        AUTO_ELIGIBLE = "AUTO_ELIGIBLE", "Puede responder automáticamente"
        AUTHORIZED = "AUTHORIZED", "Autorizada"
        EXECUTING = "EXECUTING", "Enviando"
        COMPLETED = "COMPLETED", "Respondido automáticamente"
        MANUAL_REPLY_RECORDED = "MANUAL_REPLY_RECORDED", "Respondido manualmente"
        HUMAN_REQUIRED = "HUMAN_REQUIRED", "Necesita que lo revises"
        REJECTED_POLICY = "REJECTED_POLICY", "No autorizada por seguridad"
        FAILED = "FAILED", "No se pudo analizar"

    class ReviewOutcome(models.TextChoices):
        CORRECT = "CORRECT", "Correcta"
        INCORRECT = "INCORRECT", "Incorrecta"
        NEEDED_HUMAN = "NEEDED_HUMAN", "Necesitaba una persona"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="reply_decisions",
    )
    inbound = models.OneToOneField(
        "mailbox.InboundMessage",
        on_delete=models.PROTECT,
        related_name="reply_decision",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        related_name="reply_decisions",
    )
    conversation = models.ForeignKey(
        "contacts.Conversation",
        on_delete=models.PROTECT,
        related_name="reply_decisions",
    )
    mode = models.CharField(
        max_length=20,
        choices=ReplyAutomationConfiguration.Mode.choices,
        default=ReplyAutomationConfiguration.Mode.SHADOW,
    )
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=120)
    schema_version = models.CharField(max_length=40, default="1")
    policy_version = models.CharField(max_length=40)
    classification = models.CharField(max_length=40)
    intent = models.CharField(max_length=80)
    action = models.CharField(max_length=40)
    confidence = models.DecimalField(max_digits=4, decimal_places=3)
    candidate = models.ForeignKey(
        EmailCandidate,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="reply_decisions",
    )
    proposed_body = models.TextField(blank=True)
    human_reason = models.CharField(max_length=80, blank=True)
    context_manifest = models.JSONField(default=dict)
    context_hash = models.CharField(max_length=64)
    selected_facts = models.ManyToManyField(
        KnowledgeFactRevision,
        blank=True,
        related_name="reply_decisions",
    )
    state = models.CharField(max_length=30, choices=State.choices, default=State.PENDING)
    reviewed_outcome = models.CharField(max_length=30, choices=ReviewOutcome.choices, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="reviewed_reply_decisions",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    feedback = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(confidence__gte=0, confidence__lte=1),
                name="reply_decision_confidence_0_1",
            ),
            models.CheckConstraint(
                condition=(
                    Q(reviewed_at__isnull=True, reviewed_by__isnull=True, reviewed_outcome="")
                    | Q(
                        reviewed_at__isnull=False,
                        reviewed_by__isnull=False,
                    )
                    & ~Q(reviewed_outcome="")
                ),
                name="reply_decision_review_consistent",
            ),
        ]


class HumanTask(TimestampedUUIDModel):
    objects: models.Manager[HumanTask] = models.Manager()

    class Status(models.TextChoices):
        OPEN = "OPEN", "Necesita que lo revises"
        RESOLVED = "RESOLVED", "Resuelta"
        DISMISSED = "DISMISSED", "Descartada"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="human_tasks",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        related_name="human_tasks",
    )
    conversation = models.ForeignKey(
        "contacts.Conversation",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="human_tasks",
    )
    inbound = models.ForeignKey(
        "mailbox.InboundMessage",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="human_tasks",
    )
    decision = models.ForeignKey(
        ReplyDecision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="human_tasks",
    )
    kind = models.CharField(max_length=80)
    reason = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    friendly_summary = models.CharField(max_length=300)
    opened_at = models.DateTimeField()
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="resolved_human_tasks",
    )
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ("-opened_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("inbound", "reason"),
                condition=Q(status="OPEN", inbound__isnull=False),
                name="open_task_inbound_reason_unique",
            ),
            models.UniqueConstraint(
                fields=("contact", "kind", "reason"),
                condition=Q(
                    status="OPEN",
                    inbound__isnull=True,
                    conversation__isnull=True,
                ),
                name="open_contact_task_kind_reason_unique",
            ),
            models.UniqueConstraint(
                fields=("conversation", "kind", "reason"),
                condition=Q(
                    status="OPEN",
                    inbound__isnull=True,
                    conversation__isnull=False,
                ),
                name="open_conversation_task_kind_reason_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="OPEN", resolved_at__isnull=True, resolved_by__isnull=True)
                    | Q(
                        status__in=("RESOLVED", "DISMISSED"),
                        resolved_at__isnull=False,
                        resolved_by__isnull=False,
                    )
                ),
                name="human_task_resolution_consistent",
            ),
        ]


class NotificationDelivery(TimestampedUUIDModel):
    objects: models.Manager[NotificationDelivery] = models.Manager()

    class State(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        SENDING = "SENDING", "Enviando"
        RECONCILING = "RECONCILING", "Confirmando envío"
        SENT = "SENT", "Enviada"
        FAILED = "FAILED", "Falló"

    task = models.ForeignKey(HumanTask, on_delete=models.PROTECT, related_name="notifications")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attention_notifications",
    )
    channel = models.CharField(max_length=20, default="GMAIL")
    subject = models.CharField(
        max_length=255,
        default="Hay una conversación que necesita revisión",
    )
    secure_url = models.URLField(max_length=1000)
    idempotency_key = models.CharField(max_length=200, unique=True)
    message_id = models.CharField(max_length=255, unique=True)
    gmail_message_id = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("task", "recipient", "channel"),
                name="task_recipient_channel_unique",
            )
        ]


class AutomaticActionReservation(TimestampedUUIDModel):
    """Durable, transactional capacity reserved before an automatic Gmail effect."""

    objects: models.Manager[AutomaticActionReservation] = models.Manager()

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="automatic_action_reservations",
    )
    conversation = models.ForeignKey(
        "contacts.Conversation",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="automatic_action_reservations",
    )
    decision = models.OneToOneField(
        ReplyDecision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="automatic_action_reservation",
    )
    scheduled_attempt = models.OneToOneField(
        "ScheduledContactAttempt",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="automatic_action_reservation",
    )
    reserved_at = models.DateTimeField()
    local_date = models.DateField()
    slots = models.PositiveSmallIntegerField(default=1)

    class Meta:
        indexes = [
            models.Index(fields=("conversation", "reserved_at")),
            models.Index(fields=("workspace", "local_date")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(slots__gte=1, slots__lte=2),
                name="automatic_action_reservation_slots_1_2",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        decision__isnull=False,
                        conversation__isnull=False,
                        scheduled_attempt__isnull=True,
                    )
                    | Q(decision__isnull=True, scheduled_attempt__isnull=False)
                ),
                name="automatic_action_reservation_source",
            ),
        ]


class FollowUpTopic(TimestampedUUIDModel):
    objects: models.Manager[FollowUpTopic] = models.Manager()

    class Mode(models.TextChoices):
        REVIEW_BEFORE_SEND = "REVIEW_BEFORE_SEND", "Revisar antes de enviar"
        AUTOMATIC = "AUTOMATIC", "Enviar automáticamente"

    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.PROTECT,
        related_name="follow_up_topics",
    )
    name = models.CharField(max_length=160)
    objective = models.TextField(max_length=1000)
    instructions = models.TextField(blank=True, max_length=2000)
    cadence_days = models.PositiveSmallIntegerField(
        default=30,
        validators=(MinValueValidator(7),),
    )
    mode = models.CharField(
        max_length=30,
        choices=Mode.choices,
        default=Mode.REVIEW_BEFORE_SEND,
    )
    next_due_at = models.DateTimeField(blank=True, null=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_follow_up_topics",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_follow_up_topics",
    )

    class Meta:
        ordering = ("name", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "name"),
                name="follow_up_topic_workspace_name_unique",
            ),
            models.CheckConstraint(
                condition=Q(cadence_days__gte=7), name="follow_up_topic_cadence_minimum"
            ),
        ]
        indexes = [
            models.Index(
                fields=("workspace", "active", "next_due_at"),
                name="auto_topic_due_idx",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if not self.name.strip():
            raise ValidationError({"name": "Escribí un nombre para el tema."})
        if not self.objective.strip():
            raise ValidationError({"objective": "Escribí el objetivo del seguimiento."})

    def __str__(self) -> str:
        return self.name


class ContactCommunicationPlan(TimestampedUUIDModel):
    objects: models.Manager[ContactCommunicationPlan] = models.Manager()

    class Purpose(models.TextChoices):
        CHECK_IN = "CHECK_IN", "Preguntar cómo está"
        PRODUCT_FEEDBACK = "PRODUCT_FEEDBACK", "Pedir opinión sobre el producto"
        ADMIN_GOAL = "ADMIN_GOAL", "Objetivo escrito por el administrador"

    class Mode(models.TextChoices):
        REVIEW_BEFORE_SEND = "REVIEW_BEFORE_SEND", "Revisar antes de enviar"
        AUTOMATIC = "AUTOMATIC", "Enviar automáticamente"

    class State(models.TextChoices):
        DISABLED = "DISABLED", "Desactivado"
        ACTIVE = "ACTIVE", "Activo"
        PAUSED = "PAUSED", "Pausado"

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        related_name="communication_plans",
    )
    topic = models.ForeignKey(
        FollowUpTopic,
        on_delete=models.PROTECT,
        related_name="contact_approvals",
    )
    preferred_email = models.ForeignKey(
        "contacts.EmailAddress",
        on_delete=models.PROTECT,
        related_name="follow_up_topic_approvals",
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.DISABLED)
    snoozed_until = models.DateTimeField(blank=True, null=True)
    next_due_at = models.DateTimeField(blank=True, null=True)
    last_interaction_at = models.DateTimeField(blank=True, null=True)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_contact_communication_plans",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_contact_communication_plans",
    )

    class Meta:
        indexes = [
            models.Index(
                fields=("contact", "state", "next_due_at"),
                name="auto_plan_due_idx",
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("contact", "topic"),
                name="contact_follow_up_topic_unique",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if getattr(self, "contact_id", None) and getattr(self, "preferred_email_id", None):
            if getattr(self.preferred_email, "organization_id", None) != getattr(
                self.contact, "organization_id", None
            ):
                raise ValidationError("El email del plan debe pertenecer al contacto.")
        if getattr(self, "contact_id", None) and getattr(self, "topic_id", None):
            if getattr(self.topic, "workspace_id", None) != getattr(
                self.contact,
                "workspace_id",
                None,
            ):
                raise ValidationError("El tema debe pertenecer al mismo espacio que el contacto.")


class ScheduledContactAttempt(TimestampedUUIDModel):
    objects: models.Manager[ScheduledContactAttempt] = models.Manager()

    class State(models.TextChoices):
        DUE = "DUE", "Pendiente"
        DRAFT_REVIEW = "DRAFT_REVIEW", "Listo para revisar"
        AUTHORIZED = "AUTHORIZED", "Autorizado"
        SENT = "SENT", "Enviado"
        HUMAN_REQUIRED = "HUMAN_REQUIRED", "Necesita que lo revises"
        CANCELLED = "CANCELLED", "Cancelado"
        INELIGIBLE = "INELIGIBLE", "No se puede enviar"

    plan = models.ForeignKey(
        ContactCommunicationPlan,
        on_delete=models.PROTECT,
        related_name="attempts",
    )
    due_at = models.DateTimeField()
    decision = models.ForeignKey(
        ReplyDecision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="scheduled_attempts",
    )
    outbound_message = models.ForeignKey(
        "campaigns.OutboundMessage",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="scheduled_contact_attempts",
    )
    state = models.CharField(max_length=30, choices=State.choices, default=State.DUE)
    idempotency_key = models.CharField(max_length=200, unique=True)
    reason = models.CharField(max_length=300, blank=True)
    provider = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=120, blank=True)
    context_manifest = models.JSONField(default=dict, blank=True)
    context_hash = models.CharField(max_length=64, blank=True)
    fact_revision_ids = models.JSONField(default=list, blank=True)
    authorized_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("due_at",)
        constraints = [
            models.UniqueConstraint(fields=("plan", "due_at"), name="scheduled_plan_due_unique")
        ]
