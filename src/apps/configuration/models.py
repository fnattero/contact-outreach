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
