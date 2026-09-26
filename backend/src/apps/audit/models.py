from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TimestampedUUIDModel


class ImmutableAuditQuerySet(models.QuerySet["AuditEvent"]):
    def update(self, **kwargs: Any) -> int:
        raise ValidationError("Los eventos de auditoría no se pueden modificar.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ValidationError("Los eventos de auditoría no se pueden eliminar.")


class AuditEvent(TimestampedUUIDModel):
    class ActorType(models.TextChoices):
        USER = "USER", "Usuario"
        SYSTEM = "SYSTEM", "Sistema"

    actor_type = models.CharField(max_length=10, choices=ActorType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=64)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    correlation_id = models.UUIDField()
    local_ip = models.GenericIPAddressField(blank=True, null=True)

    objects = ImmutableAuditQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("entity_type", "entity_id", "-created_at"))]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Los eventos de auditoría no se pueden modificar.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Los eventos de auditoría no se pueden eliminar.")

    def __str__(self) -> str:
        return f"{self.action} · {self.entity_type}:{self.entity_id}"


class BackgroundJob(TimestampedUUIDModel):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En curso"
        SUCCEEDED = "SUCCEEDED", "Completado"
        RETRY_WAIT = "RETRY_WAIT", "Esperando reintento"
        FAILED = "FAILED", "Falló"
        CANCELLED = "CANCELLED", "Cancelado"

    task_name = models.CharField(max_length=150)
    celery_task_id = models.CharField(max_length=255, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=64)
    queue = models.CharField(max_length=50, default="delivery")
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    heartbeat_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    next_retry_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("state", "next_retry_at"))]
        constraints = [
            models.CheckConstraint(condition=Q(attempts__gte=0), name="job_attempts_nonnegative")
        ]


class ApiIdempotencyRecord(TimestampedUUIDModel):
    """Durable replay record for effect-bearing API requests."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="api_idempotency_records",
    )
    key = models.UUIDField()
    request_fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField(default=dict)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "key"),
                name="api_idempotency_user_key_unique",
            )
        ]
        indexes = [models.Index(fields=("expires_at",))]
