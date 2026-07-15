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


class ContactLedger(TimestampedUUIDModel):
    normalized_email = models.CharField(max_length=320, unique=True)
    reserved_message = models.ForeignKey(
        "campaigns.OutboundMessage",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="ledger_reservations",
    )
    reserved_at = models.DateTimeField(blank=True, null=True)
    last_sent_message = models.ForeignKey(
        "campaigns.OutboundMessage",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="ledger_confirmations",
    )
    last_sent_at = models.DateTimeField(blank=True, null=True)
    next_sequence = models.PositiveIntegerField(default=1)


class ContactOverride(TimestampedUUIDModel):
    ledger = models.ForeignKey(ContactLedger, on_delete=models.PROTECT, related_name="overrides")
    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.PROTECT, related_name="contact_overrides"
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contact_overrides",
    )
    consumed_at = models.DateTimeField(blank=True, null=True)
    consumed_by_message = models.OneToOneField(
        "campaigns.OutboundMessage",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="consumed_override",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="contact_override_reason_required",
            ),
            models.UniqueConstraint(
                fields=("ledger", "campaign"),
                condition=models.Q(consumed_at__isnull=True),
                name="active_contact_override_unique",
            ),
        ]
