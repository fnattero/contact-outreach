from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedUUIDModel


class GmailConnection(TimestampedUUIDModel):
    class Status(models.TextChoices):
        CONNECTED = "CONNECTED", "Conectada"
        ERROR = "ERROR", "Con error"
        DISCONNECTED = "DISCONNECTED", "Desconectada"

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="gmail_connection",
    )
    email = models.EmailField(max_length=320, blank=True)
    scopes = models.JSONField(default=list)
    refresh_token_encrypted = models.TextField(blank=True)
    history_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    last_tested_at = models.DateTimeField(blank=True, null=True)
    last_sync_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)
    token_version = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ("-created_at",)

    @property
    def is_ready(self) -> bool:
        return bool(
            self.status == self.Status.CONNECTED
            and self.email
            and self.refresh_token_encrypted
            and self.last_tested_at
        )


class FakeGmailMessage(TimestampedUUIDModel):
    """Durable fake-provider outbox used to prove restart-safe idempotency."""

    rfc_message_id = models.CharField(max_length=255, unique=True)
    gmail_message_id = models.CharField(max_length=255, unique=True)
    gmail_thread_id = models.CharField(max_length=255)
    recipient = models.CharField(max_length=320)
    raw_message = models.BinaryField()
    idempotency_key = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ("created_at",)
