from __future__ import annotations

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
