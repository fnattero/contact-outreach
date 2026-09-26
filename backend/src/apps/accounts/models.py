from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TimestampedUUIDModel


class Workspace(TimestampedUUIDModel):
    """The single company whose outreach data lives in this installation."""

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    name = models.CharField(max_length=160, default="Mi empresa")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(singleton_key=1), name="workspace_is_singleton")
        ]

    def __str__(self) -> str:
        return self.name


class Membership(TimestampedUUIDModel):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrador"
        VENDEDOR = "VENDEDOR", "Vendedor"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="membership",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    session_version = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ("user__username",)

    def __str__(self) -> str:
        return f"{self.user.get_username()} · {self.get_role_display()}"


class LoginThrottle(TimestampedUUIDModel):
    """Durable fixed-window counters; keys contain only HMAC digests."""

    class Scope(models.TextChoices):
        PAIR = "PAIR", "Usuario y dirección"
        IP = "IP", "Dirección"

    scope = models.CharField(max_length=10, choices=Scope.choices)
    key_hash = models.CharField(max_length=64)
    username_hash = models.CharField(max_length=64, blank=True)
    ip_hash = models.CharField(max_length=64)
    failure_count = models.PositiveSmallIntegerField(default=0)
    window_started_at = models.DateTimeField()
    last_failed_at = models.DateTimeField(blank=True, null=True)
    locked_until = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("scope", "key_hash"), name="login_throttle_scope_key"),
            models.CheckConstraint(
                condition=Q(failure_count__gte=0),
                name="login_throttle_failure_count_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=("scope", "username_hash")),
            models.Index(fields=("scope", "ip_hash")),
            models.Index(fields=("locked_until",)),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.key_hash[:8]}"


class ActivationToken(TimestampedUUIDModel):
    class Kind(models.TextChoices):
        ACTIVATE = "ACTIVATE", "Activación"
        RESET = "RESET", "Cambio de contraseña"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="activation_tokens",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_activation_tokens",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.ACTIVATE)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("token_hash", "expires_at"))]

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = ActivationToken.objects.filter(pk=self.pk).values("token_hash").first()
            if original is not None and original["token_hash"] != self.token_hash:
                raise ValidationError("El identificador del enlace no se puede modificar.")
        super().save(*args, **kwargs)
