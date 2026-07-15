from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.contrib.auth.models import User
from django.db import connection, transaction

OwnerAction = Literal["created", "updated", "unchanged"]


class OwnerConflictError(RuntimeError):
    """Raised when bootstrap would violate the single-active-owner invariant."""


@dataclass(frozen=True, slots=True)
class OwnerBootstrapResult:
    action: OwnerAction
    user_id: int


def _lock_owner_bootstrap() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1_947_201_001])


@transaction.atomic
def ensure_owner(*, username: str, password: str, email: str = "") -> OwnerBootstrapResult:
    """Create or rotate the only active owner without exposing credential values."""
    _lock_owner_bootstrap()
    active_users = list(User.objects.select_for_update().filter(is_active=True)[:2])
    if len(active_users) > 1:
        raise OwnerConflictError("more than one active owner already exists")
    if active_users and active_users[0].username != username:
        raise OwnerConflictError("an active owner with a different username already exists")

    user = (
        active_users[0]
        if active_users
        else User.objects.select_for_update().filter(username=username).first()
    )
    if user is None:
        user = User.objects.create_user(username=username, email=email, password=password)
        return OwnerBootstrapResult(action="created", user_id=user.pk)

    changed_fields: list[str] = []
    if not user.is_active:
        user.is_active = True
        changed_fields.append("is_active")
    if email and user.email != email:
        user.email = email
        changed_fields.append("email")
    if not user.check_password(password):
        user.set_password(password)
        changed_fields.append("password")
    if changed_fields:
        user.save(update_fields=[*changed_fields])
        return OwnerBootstrapResult(action="updated", user_id=user.pk)
    return OwnerBootstrapResult(action="unchanged", user_id=user.pk)
