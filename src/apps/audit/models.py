from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

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
