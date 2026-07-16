from __future__ import annotations

from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.catalogs.models import Catalog
from apps.configuration.models import SearchCategory, SearchZone
from apps.core.models import TimestampedUUIDModel


def default_weekdays() -> list[int]:
    return [0, 1, 2, 3, 4]


class Campaign(TimestampedUUIDModel):
    IMMUTABLE_AFTER_START = (
        "location_text",
        "objective",
        "max_raw_records",
        "cost_limit",
        "cost_currency",
        "daily_limit",
        "message_interval_minutes",
        "weekdays",
        "window_start",
        "window_end",
        "timezone_name",
        "relevance_threshold",
        "extractor_provider",
        "llm_provider",
        "llm_base_url",
        "llm_model",
        "catalog_id",
        "delivery_mode",
        "settings_snapshot",
        "profile_snapshot",
        "prompt_snapshot",
    )

    class State(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        RUNNING = "RUNNING", "En curso"
        PAUSED = "PAUSED", "Pausada"
        CANCELLED = "CANCELLED", "Cancelada"
        COMPLETED = "COMPLETED", "Completada"
        STOPPED_ERROR = "STOPPED_ERROR", "Detenida por error"

    class DiscoveryState(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En curso"
        TARGET_REACHED = "TARGET_REACHED", "Objetivo alcanzado"
        EXHAUSTED_QUERIES = "EXHAUSTED_QUERIES", "Consultas agotadas"
        EXHAUSTED_RAW_LIMIT = "EXHAUSTED_RAW_LIMIT", "Límite crudo agotado"
        EXHAUSTED_COST = "EXHAUSTED_COST", "Costo agotado"
        FAILED_PROVIDER = "FAILED_PROVIDER", "Proveedor falló"

    class DeliveryMode(models.TextChoices):
        DRY_RUN = "DRY_RUN", "Simulación"
        LIVE = "LIVE", "En vivo"

    name = models.CharField(max_length=200)
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    discovery_state = models.CharField(
        max_length=30,
        choices=DiscoveryState.choices,
        default=DiscoveryState.PENDING,
    )
    discovery_stop_reason = models.CharField(max_length=200, blank=True)
    delivery_mode = models.CharField(
        max_length=20,
        choices=DeliveryMode.choices,
        default=DeliveryMode.DRY_RUN,
    )
    status_reason = models.TextField(blank=True)
    location_text = models.CharField(
        max_length=300,
        default="Ciudad Autónoma de Buenos Aires, Argentina",
    )
    objective = models.PositiveIntegerField(default=300)
    max_raw_records = models.PositiveIntegerField(default=3000)
    cost_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("10.00"))
    cost_currency = models.CharField(max_length=3, default="USD")
    daily_limit = models.PositiveIntegerField(default=30)
    message_interval_minutes = models.PositiveIntegerField(default=5)
    weekdays = models.JSONField(default=default_weekdays)
    window_start = models.TimeField(default="09:00")
    window_end = models.TimeField(default="17:00")
    timezone_name = models.CharField(max_length=64, default="America/Argentina/Buenos_Aires")
    relevance_threshold = models.PositiveSmallIntegerField(default=70)
    extractor_provider = models.CharField(max_length=50, default="fake")
    llm_provider = models.CharField(max_length=50, default="fake")
    llm_base_url = models.URLField(blank=True)
    llm_model = models.CharField(max_length=120, default="fake-deterministic")
    catalog = models.ForeignKey(Catalog, on_delete=models.PROTECT, related_name="campaigns")
    settings_snapshot = models.JSONField(default=dict, blank=True)
    profile_snapshot = models.JSONField(default=dict, blank=True)
    prompt_snapshot = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="campaigns",
    )
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    categories: models.ManyToManyField[SearchCategory, CampaignCategorySelection] = (
        models.ManyToManyField(SearchCategory, through="CampaignCategorySelection")
    )
    zones: models.ManyToManyField[SearchZone, CampaignZoneSelection] = models.ManyToManyField(
        SearchZone, through="CampaignZoneSelection"
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("state", "-created_at"))]
        constraints = [
            models.CheckConstraint(
                condition=Q(objective__gt=0), name="campaign_objective_positive"
            ),
            models.CheckConstraint(
                condition=Q(max_raw_records__gt=0), name="campaign_raw_limit_positive"
            ),
            models.CheckConstraint(
                condition=Q(cost_limit__gte=0), name="campaign_cost_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(daily_limit__gt=0), name="campaign_daily_limit_positive"
            ),
            models.CheckConstraint(
                condition=Q(message_interval_minutes__gt=0), name="campaign_interval_positive"
            ),
            models.CheckConstraint(
                condition=Q(relevance_threshold__gte=0, relevance_threshold__lte=100),
                name="campaign_relevance_0_100",
            ),
            models.CheckConstraint(
                condition=~Q(extractor_provider="outscraper") | Q(cost_currency="USD"),
                name="campaign_outscraper_cost_usd",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.max_raw_records < self.objective:
            errors["max_raw_records"] = "El límite crudo no puede ser menor que el objetivo."
        if self.window_start >= self.window_end:
            errors["window_end"] = "El fin del horario debe ser posterior al inicio."
        if (
            not isinstance(self.weekdays, list)
            or not self.weekdays
            or any(not isinstance(day, int) or day < 0 or day > 6 for day in self.weekdays)
        ):
            errors["weekdays"] = "Seleccioná al menos un día válido."
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            errors["timezone_name"] = "La zona horaria no es válida."
        if self.extractor_provider not in {"fake", "outscraper"}:
            errors["extractor_provider"] = "El proveedor de extracción no es válido."
        if self.extractor_provider == "outscraper" and self.cost_currency != "USD":
            errors["cost_currency"] = "Outscraper sólo admite costos de campaña en USD."
        if self.llm_provider not in {"fake", "ollama", "openai-compatible"}:
            errors["llm_provider"] = "El proveedor IA no es válido."
        if self.llm_provider != "fake" and not self.llm_base_url:
            errors["llm_base_url"] = "El proveedor IA seleccionado requiere una URL base."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = Campaign.objects.get(pk=self.pk)
            if previous.state != self.State.DRAFT and any(
                getattr(previous, field) != getattr(self, field)
                for field in self.IMMUTABLE_AFTER_START
            ):
                raise ValidationError("La configuración iniciada de una campaña es inmutable.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class CampaignCategorySelection(TimestampedUUIDModel):
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="category_selections"
    )
    category = models.ForeignKey(
        SearchCategory, on_delete=models.PROTECT, related_name="campaign_selections"
    )
    name_snapshot = models.CharField(max_length=160)
    normalized_name_snapshot = models.CharField(max_length=160)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "category"), name="campaign_category_unique"
            )
        ]


class CampaignZoneSelection(TimestampedUUIDModel):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="zone_selections")
    zone = models.ForeignKey(
        SearchZone, on_delete=models.PROTECT, related_name="campaign_selections"
    )
    name_snapshot = models.CharField(max_length=160)
    location_snapshot = models.CharField(max_length=300)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name_snapshot")
        constraints = [
            models.UniqueConstraint(fields=("campaign", "zone"), name="campaign_zone_unique")
        ]


class SearchQuery(TimestampedUUIDModel):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En curso"
        SUCCEEDED = "SUCCEEDED", "Completada"
        RETRY_WAIT = "RETRY_WAIT", "Esperando reintento"
        FAILED_PERMANENT = "FAILED_PERMANENT", "Falló"
        CANCELLED = "CANCELLED", "Cancelada"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="search_queries")
    category_snapshot = models.CharField(max_length=160)
    zone_snapshot = models.CharField(max_length=160)
    location_snapshot = models.CharField(max_length=300)
    query_text = models.CharField(max_length=700)
    normalized_query = models.CharField(max_length=700)
    sort_order = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=30, choices=State.choices, default=State.PENDING)
    run_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("sort_order",)
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "normalized_query"), name="campaign_query_unique"
            )
        ]


class SearchRun(TimestampedUUIDModel):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En curso"
        SUCCEEDED = "SUCCEEDED", "Completada"
        RETRY_WAIT = "RETRY_WAIT", "Esperando reintento"
        FAILED_PERMANENT = "FAILED_PERMANENT", "Falló permanentemente"
        CANCELLED = "CANCELLED", "Cancelada"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="search_runs")
    query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="runs")
    provider = models.CharField(max_length=50)
    idempotency_key = models.CharField(max_length=200, unique=True)
    request_json = models.JSONField(default=dict)
    response_json = models.JSONField(default=dict, blank=True)
    response_hash = models.CharField(max_length=64, blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True)
    state = models.CharField(max_length=30, choices=State.choices, default=State.PENDING)
    cursor = models.CharField(max_length=500, blank=True)
    requested_limit = models.PositiveIntegerField()
    attempts = models.PositiveIntegerField(default=0)
    raw_count = models.PositiveIntegerField(default=0)
    email_count = models.PositiveIntegerField(default=0)
    no_email_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    cost_reserved = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"))
    cost_estimated = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    cost_actual = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    currency = models.CharField(max_length=3, default="USD")
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    raw_persisted_at = models.DateTimeField(blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    next_poll_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=("state", "next_poll_at")),
            models.Index(fields=("campaign", "state")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "provider_request_id"),
                condition=~Q(provider_request_id=""),
                name="provider_request_id_unique",
            ),
            models.CheckConstraint(
                condition=Q(cost_reserved__gte=0), name="search_run_reserved_cost_nonnegative"
            ),
        ]


class ProviderUsage(TimestampedUUIDModel):
    provider = models.CharField(max_length=50)
    operation = models.CharField(max_length=100)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="provider_usage")
    run = models.OneToOneField(SearchRun, on_delete=models.PROTECT, related_name="usage")
    units = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    actual_cost = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True)
    currency = models.CharField(max_length=3)
    request_id = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=("provider", "campaign", "created_at"))]


class OutboundMessage(TimestampedUUIDModel):
    class Kind(models.TextChoices):
        FIRST_CONTACT = "FIRST_CONTACT", "Primer contacto"
        MANUAL_REPLY = "MANUAL_REPLY", "Respuesta manual"

    class State(models.TextChoices):
        PREPARED = "PREPARED", "Preparado"
        QUEUED = "QUEUED", "En cola"
        SENDING = "SENDING", "Enviando"
        RECONCILING = "RECONCILING", "Reconciliando"
        SENT = "SENT", "Enviado"
        DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED", "Simulado"
        SEND_FAILED = "SEND_FAILED", "Falló"
        CANCELLED = "CANCELLED", "Cancelado"

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.FIRST_CONTACT)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="messages")
    prospect = models.ForeignKey(
        "prospects.Prospect", on_delete=models.PROTECT, related_name="outbound_messages"
    )
    prospect_email = models.ForeignKey(
        "prospects.ProspectEmail", on_delete=models.PROTECT, related_name="outbound_messages"
    )
    analysis = models.OneToOneField(
        "prospects.AIAnalysis",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="outbound_message",
    )
    parent_inbound = models.ForeignKey(
        "mailbox.InboundMessage",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="manual_replies",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="manual_replies",
    )
    recipient = models.CharField(max_length=320)
    recipient_normalized = models.CharField(max_length=320)
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    catalog = models.ForeignKey(Catalog, on_delete=models.PROTECT, related_name="messages")
    catalog_version = models.PositiveIntegerField()
    state = models.CharField(max_length=30, choices=State.choices, default=State.PREPARED)
    delivery_mode = models.CharField(max_length=20, choices=Campaign.DeliveryMode.choices)
    idempotency_key = models.CharField(max_length=200, unique=True)
    contact_sequence = models.PositiveIntegerField(blank=True, null=True)
    message_id = models.CharField(max_length=255, blank=True)
    in_reply_to = models.CharField(max_length=255, blank=True)
    references = models.JSONField(default=list, blank=True)
    mime_sha256 = models.CharField(max_length=64, blank=True)
    gmail_message_id = models.CharField(max_length=255, blank=True)
    gmail_thread_id = models.CharField(max_length=255, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(blank=True, null=True)
    delivery_reserved_at = models.DateTimeField(blank=True, null=True)
    sending_started_at = models.DateTimeField(blank=True, null=True)
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    simulated_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("campaign", "state")),
            models.Index(fields=("state", "next_attempt_at")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("recipient_normalized", "contact_sequence"),
                condition=Q(kind="FIRST_CONTACT", contact_sequence__isnull=False),
                name="first_contact_email_sequence_unique",
            ),
            models.UniqueConstraint(
                fields=("message_id",),
                condition=~Q(message_id=""),
                name="outbound_rfc_message_id_unique",
            ),
            models.UniqueConstraint(
                fields=("gmail_message_id",),
                condition=~Q(gmail_message_id=""),
                name="outbound_gmail_message_id_unique",
            ),
            models.UniqueConstraint(
                fields=("parent_inbound",),
                condition=Q(kind="MANUAL_REPLY", parent_inbound__isnull=False),
                name="manual_reply_parent_inbound_unique",
            ),
        ]
