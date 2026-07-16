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

    class Direction(models.TextChoices):
        OUTBOUND = "OUTBOUND", "Saliente"
        INBOUND = "INBOUND", "Entrante"

    rfc_message_id = models.CharField(max_length=255, unique=True)
    gmail_message_id = models.CharField(max_length=255, unique=True)
    gmail_thread_id = models.CharField(max_length=255)
    direction = models.CharField(
        max_length=20,
        choices=Direction.choices,
        default=Direction.OUTBOUND,
    )
    sender = models.CharField(max_length=320, blank=True)
    recipient = models.CharField(max_length=320, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    headers = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(blank=True, null=True)
    history_id = models.PositiveBigIntegerField(default=0)
    raw_message = models.BinaryField(default=bytes)
    idempotency_key = models.CharField(max_length=200, unique=True, blank=True, null=True)

    class Meta:
        ordering = ("created_at",)


class InboundMessage(TimestampedUUIDModel):
    class Classification(models.TextChoices):
        INTERESTED = "INTERESTED", "Interesado"
        NOT_INTERESTED = "NOT_INTERESTED", "No interesado"
        UNSUBSCRIBE = "UNSUBSCRIBE", "Baja"
        AUTO_REPLY = "AUTO_REPLY", "Respuesta automática"
        BOUNCE = "BOUNCE", "Rebote"
        OTHER = "OTHER", "Otro"

    connection = models.ForeignKey(
        GmailConnection,
        on_delete=models.PROTECT,
        related_name="inbound_messages",
    )
    related_outbound = models.ForeignKey(
        "campaigns.OutboundMessage",
        on_delete=models.PROTECT,
        related_name="inbound_messages",
    )
    gmail_message_id = models.CharField(max_length=255, unique=True)
    gmail_thread_id = models.CharField(max_length=255)
    message_id = models.CharField(max_length=255, blank=True)
    in_reply_to = models.CharField(max_length=255, blank=True)
    references = models.JSONField(default=list, blank=True)
    sender = models.CharField(max_length=320)
    recipients = models.JSONField(default=list)
    subject = models.CharField(max_length=255, blank=True)
    external_at = models.DateTimeField()
    received_at = models.DateTimeField()
    body_text = models.TextField()
    body_html_sanitized = models.TextField(blank=True)
    headers = models.JSONField(default=dict, blank=True)
    classification = models.CharField(
        max_length=20,
        choices=Classification.choices,
        default=Classification.OTHER,
    )
    classification_confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=0,
    )
    is_human = models.BooleanField(default=True)
    is_read = models.BooleanField(default=False)
    classification_error = models.TextField(blank=True)

    class Meta:
        ordering = ("-external_at", "-created_at")
        indexes = [
            models.Index(fields=("gmail_thread_id", "external_at")),
            models.Index(fields=("classification", "-external_at")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("message_id",),
                condition=~models.Q(message_id=""),
                name="inbound_rfc_message_id_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    classification_confidence__gte=0,
                    classification_confidence__lte=1,
                ),
                name="inbound_classification_confidence_0_1",
            ),
        ]
