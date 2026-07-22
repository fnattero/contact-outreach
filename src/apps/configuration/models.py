from __future__ import annotations

import re
import unicodedata
from typing import Any

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import TimestampedUUIDModel


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized.strip()).casefold()


class BusinessProfile(TimestampedUUIDModel):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="business_profile",
    )
    company_name = models.CharField(max_length=200)
    salesperson_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=50, blank=True)
    whatsapp = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    products = models.TextField(blank=True)
    differentiators = models.TextField(blank=True)
    address = models.CharField(max_length=300)
    website = models.URLField(blank=True)
    signature = models.TextField()
    additional_instructions = models.TextField(blank=True)
    relevance_threshold = models.PositiveSmallIntegerField(
        default=70,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
    )
    profile_version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(relevance_threshold__gte=0, relevance_threshold__lte=100),
                name="business_profile_relevance_0_100",
            )
        ]

    def __str__(self) -> str:
        return self.company_name


class IntegrationConfiguration(TimestampedUUIDModel):
    class SecretSource(models.TextChoices):
        ENVIRONMENT = "ENVIRONMENT", "Entorno"
        ENCRYPTED = "ENCRYPTED", "Dashboard cifrado"
        NONE = "NONE", "Sin configurar"

    class ExtractorProvider(models.TextChoices):
        FAKE = "fake", "Mock (sin red)"
        OUTSCRAPER = "outscraper", "Outscraper"

    class LLMProvider(models.TextChoices):
        FAKE = "fake", "Mock (sin red)"
        OLLAMA = "ollama", "Ollama"
        OPENAI_COMPATIBLE = "openai-compatible", "OpenAI compatible"

    class GmailProvider(models.TextChoices):
        FAKE = "fake", "Fake (sin red)"
        API = "api", "Google Gmail"

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="integration_configuration",
    )
    extractor_provider = models.CharField(
        max_length=30,
        choices=ExtractorProvider.choices,
        default=ExtractorProvider.FAKE,
    )
    outscraper_api_key_encrypted = models.TextField(blank=True, editable=False)
    outscraper_api_key_source = models.CharField(
        max_length=20,
        choices=SecretSource.choices,
        default=SecretSource.ENVIRONMENT,
    )
    outscraper_base_url = models.URLField(default="https://api.outscraper.cloud")
    outscraper_max_cost_per_result = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default="0.010000",
        validators=(MinValueValidator(0),),
    )
    outscraper_batch_size = models.PositiveIntegerField(default=20)
    outscraper_poll_seconds = models.PositiveIntegerField(default=30)
    llm_provider = models.CharField(
        max_length=30,
        choices=LLMProvider.choices,
        default=LLMProvider.FAKE,
    )
    llm_model = models.CharField(max_length=120, default="fake-deterministic")
    llm_api_key_encrypted = models.TextField(blank=True, editable=False)
    llm_api_key_source = models.CharField(
        max_length=20,
        choices=SecretSource.choices,
        default=SecretSource.ENVIRONMENT,
    )
    ollama_base_url = models.URLField(default="http://127.0.0.1:11434")
    openai_compatible_base_url = models.URLField(blank=True)
    gmail_provider = models.CharField(
        max_length=20,
        choices=GmailProvider.choices,
        default=GmailProvider.FAKE,
    )
    gmail_oauth_client_id = models.CharField(max_length=500, blank=True)
    gmail_oauth_client_secret_encrypted = models.TextField(blank=True, editable=False)
    gmail_oauth_client_secret_source = models.CharField(
        max_length=20,
        choices=SecretSource.choices,
        default=SecretSource.ENVIRONMENT,
    )
    revision = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(outscraper_max_cost_per_result__gte=0),
                name="integration_outscraper_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(outscraper_batch_size__gt=0),
                name="integration_outscraper_batch_positive",
            ),
            models.CheckConstraint(
                condition=Q(outscraper_poll_seconds__gt=0),
                name="integration_outscraper_poll_positive",
            ),
            models.CheckConstraint(
                condition=~Q(outscraper_api_key_source="ENCRYPTED")
                | ~Q(outscraper_api_key_encrypted=""),
                name="integration_outscraper_cipher_required",
            ),
            models.CheckConstraint(
                condition=~Q(llm_api_key_source="ENCRYPTED") | ~Q(llm_api_key_encrypted=""),
                name="integration_llm_cipher_required",
            ),
            models.CheckConstraint(
                condition=~Q(gmail_oauth_client_secret_source="ENCRYPTED")
                | ~Q(gmail_oauth_client_secret_encrypted=""),
                name="integration_gmail_cipher_required",
            ),
        ]

    def __str__(self) -> str:
        return f"Integraciones de {self.owner.username}"


class SearchCategory(TimestampedUUIDModel):
    name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160, editable=False)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("normalized_name",),
                condition=Q(archived_at__isnull=True),
                name="configuration_category_active_name_unique",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name = re.sub(r"\s+", " ", self.name.strip())
        self.normalized_name = normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class SearchZone(TimestampedUUIDModel):
    class Kind(models.TextChoices):
        NEIGHBORHOOD = "NEIGHBORHOOD", "Barrio"
        CUSTOM = "CUSTOM", "Personalizada"

    name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.CUSTOM)
    location_text = models.CharField(max_length=300)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("normalized_name",),
                condition=Q(archived_at__isnull=True),
                name="configuration_zone_active_name_unique",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name = re.sub(r"\s+", " ", self.name.strip())
        self.normalized_name = normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name
