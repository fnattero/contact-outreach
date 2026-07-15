from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedUUIDModel


class SuppressionEntry(TimestampedUUIDModel):
    class Reason(models.TextChoices):
        UNSUBSCRIBE = "UNSUBSCRIBE", "Baja"
        BOUNCE = "BOUNCE", "Rebote"
        MANUAL = "MANUAL", "Manual"

    original_email = models.EmailField(max_length=320)
    normalized_email = models.CharField(max_length=320, unique=True, editable=False)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    source = models.CharField(max_length=100, default="dashboard")
    evidence = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="suppression_entries",
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.normalized_email} ({self.get_reason_display()})"
