from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.campaigns.models import Campaign, SearchRun
from apps.core.models import TimestampedUUIDModel


class Prospect(TimestampedUUIDModel):
    class PipelineState(models.TextChoices):
        DISCOVERED = "DISCOVERED", "Descubierto"
        EMAIL_FOUND = "EMAIL_FOUND", "Email encontrado"
        ENRICHED = "ENRICHED", "Enriquecido"
        ANALYZED = "ANALYZED", "Analizado"
        SKIPPED_NO_EMAIL = "SKIPPED_NO_EMAIL", "Sin email"
        SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE", "Duplicado"
        SKIPPED_IRRELEVANT = "SKIPPED_IRRELEVANT", "Irrelevante"
        QUEUED = "QUEUED", "En cola"
        ERROR = "ERROR", "Error"

    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="prospects")
    organization = models.ForeignKey(
        "contacts.Organization",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="legacy_prospects",
    )
    campaign_enrollment = models.ForeignKey(
        "contacts.CampaignEnrollment",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="legacy_prospects",
    )
    source_run = models.ForeignKey(SearchRun, on_delete=models.PROTECT, related_name="prospects")
    name = models.CharField(max_length=300)
    normalized_name = models.CharField(max_length=300)
    address = models.CharField(max_length=500, blank=True)
    normalized_address = models.CharField(max_length=500, blank=True)
    neighborhood = models.CharField(max_length=160, blank=True)
    category = models.CharField(max_length=160, blank=True)
    website = models.URLField(max_length=1000, blank=True)
    business_domain = models.CharField(max_length=253, blank=True)
    phone = models.CharField(max_length=80, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    provider_data = models.JSONField(default=dict, blank=True)
    pipeline_state = models.CharField(
        max_length=30, choices=PipelineState.choices, default=PipelineState.DISCOVERED
    )
    error_stage = models.CharField(max_length=50, blank=True)
    last_error = models.TextField(blank=True)
    pipeline_reservation_key = models.CharField(max_length=64, blank=True)
    pipeline_reserved_at = models.DateTimeField(blank=True, null=True)
    pipeline_claimed_at = models.DateTimeField(blank=True, null=True)
    analysis_generation = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=("campaign", "pipeline_state")),
            models.Index(fields=("business_domain",)),
            models.Index(fields=("normalized_name",)),
        ]


class ProspectIdentity(TimestampedUUIDModel):
    class Kind(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        BUSINESS_DOMAIN = "BUSINESS_DOMAIN", "Dominio empresarial"
        PROVIDER_ID = "PROVIDER_ID", "Identificador del proveedor"
        NAME_ADDRESS = "NAME_ADDRESS", "Nombre y dirección"

    prospect = models.ForeignKey(Prospect, on_delete=models.CASCADE, related_name="identities")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    value_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("kind", "value_hash"), name="prospect_identity_global_unique"
            )
        ]


class ProspectEmail(TimestampedUUIDModel):
    class MXStatus(models.TextChoices):
        VALID = "VALID", "Válido"
        INVALID = "INVALID", "Inválido"
        TRANSIENT = "TRANSIENT", "Error temporal"

    prospect = models.ForeignKey(Prospect, on_delete=models.CASCADE, related_name="emails")
    original_email = models.CharField(max_length=320)
    normalized_email = models.CharField(max_length=320, unique=True)
    domain = models.CharField(max_length=253)
    local_part = models.CharField(max_length=64)
    source = models.CharField(max_length=120)
    source_url = models.URLField(max_length=1000, blank=True)
    source_content_hash = models.CharField(max_length=64, blank=True)
    provider_order = models.PositiveIntegerField(default=0)
    syntax_valid = models.BooleanField(default=True)
    mx_status = models.CharField(max_length=20, choices=MXStatus.choices)
    mx_checked_at = models.DateTimeField()
    exclusion_reason = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    is_invalid = models.BooleanField(default=False)
    invalid_reason = models.CharField(max_length=200, blank=True)
    invalidated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("provider_order", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("prospect",),
                condition=Q(is_primary=True),
                name="prospect_one_primary_email",
            )
        ]


class WebsiteSnapshot(TimestampedUUIDModel):
    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Exitoso"
        PARTIAL = "PARTIAL", "Parcial"
        FALLBACK = "FALLBACK", "Sin contexto web"
        REJECTED = "REJECTED", "URL rechazada"

    prospect = models.ForeignKey(Prospect, on_delete=models.PROTECT, related_name="web_snapshots")
    requested_url = models.URLField(max_length=1000, blank=True)
    final_url = models.URLField(max_length=1000, blank=True)
    fetched_at = models.DateTimeField()
    http_status = models.PositiveSmallIntegerField(blank=True, null=True)
    content_type = models.CharField(max_length=120, blank=True)
    content_hash = models.CharField(max_length=64)
    excerpt = models.TextField(blank=True)
    pages = models.JSONField(default=list)
    email_candidates = models.JSONField(default=list, blank=True)
    byte_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-fetched_at",)
        indexes = [
            models.Index(fields=("prospect", "-fetched_at")),
            models.Index(fields=("content_hash",)),
        ]


class AIAnalysis(TimestampedUUIDModel):
    class Status(models.TextChoices):
        VALID = "VALID", "Válido"
        RETRY_WAIT = "RETRY_WAIT", "Esperando reintento"
        ERROR = "ERROR", "Error"

    prospect = models.ForeignKey(Prospect, on_delete=models.PROTECT, related_name="analyses")
    input_hash = models.CharField(max_length=64)
    prompt_version = models.CharField(max_length=40)
    schema_version = models.CharField(max_length=40)
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=120)
    analyzed_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices)
    attempts = models.PositiveSmallIntegerField(default=0)
    generation = models.PositiveIntegerField(default=0)
    regeneration_nonce = models.CharField(max_length=64, blank=True)
    next_retry_at = models.DateTimeField(blank=True, null=True)
    relevance_score = models.PositiveSmallIntegerField(blank=True, null=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, blank=True, null=True)
    relevance_reason = models.TextField(blank=True)
    evidence = models.JSONField(default=list)
    subject = models.CharField(max_length=200, blank=True)
    body_text = models.TextField(blank=True)
    prompt_text = models.TextField()
    output_json = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="requested_ai_analyses",
    )

    class Meta:
        ordering = ("-analyzed_at",)
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "input_hash",
                    "prompt_version",
                    "schema_version",
                    "provider",
                    "model",
                ),
                name="ai_analysis_cache_unique",
            ),
            models.CheckConstraint(
                condition=Q(relevance_score__isnull=True)
                | Q(relevance_score__gte=0, relevance_score__lte=100),
                name="ai_analysis_relevance_0_100",
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True) | Q(confidence__gte=0, confidence__lte=1),
                name="ai_analysis_confidence_0_1",
            ),
        ]
