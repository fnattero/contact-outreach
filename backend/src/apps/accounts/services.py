from __future__ import annotations

import ipaddress
import math
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.accounts.models import (
    ActivationToken,
    LoginThrottle,
    Membership,
    Workspace,
)
from apps.accounts.permissions import Capability, require_user_capability
from apps.accounts.signals import get_workspace

OwnerAction = Literal["created", "updated", "unchanged"]

PAIR_FAILURE_LIMIT = 5
IP_FAILURE_LIMIT = 20
LOGIN_FAILURE_WINDOW = timedelta(minutes=30)
LOGIN_LOCK_DURATION = timedelta(minutes=30)
ACTIVATION_LIFETIME = timedelta(hours=24)


class OwnerConflictError(RuntimeError):
    """Raised when bootstrap would replace a configured administrator unexpectedly."""


class LastActiveAdminError(ValidationError):
    pass


class ActivationError(ValidationError):
    pass


@dataclass(frozen=True, slots=True)
class OwnerBootstrapResult:
    action: OwnerAction
    user_id: int


@dataclass(frozen=True, slots=True)
class LoginThrottleStatus:
    locked: bool
    retry_after: int = 0


@dataclass(frozen=True, slots=True)
class IssuedActivation:
    token: ActivationToken
    raw_token: str


def _digest(purpose: str, value: str) -> str:
    return salted_hmac(
        f"contact_outreach.accounts.{purpose}",
        value,
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


def normalize_login_username(username: object) -> str:
    return User.normalize_username(str(username)).strip().casefold()


def canonical_client_ip(meta: dict[str, object]) -> str:
    """Return a proxy-safe client address; untrusted forwarding headers are ignored."""
    remote_raw = str(meta.get("REMOTE_ADDR", "") or "")
    try:
        remote = ipaddress.ip_address(remote_raw)
    except ValueError:
        return "unknown"

    trusted_raw = getattr(settings, "TRUSTED_PROXY_IPS", ())
    trusted: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in trusted_raw:
        try:
            trusted.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    if not any(remote in network for network in trusted):
        return remote.compressed

    forwarded = str(meta.get("HTTP_X_FORWARDED_FOR", "") or "")
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in forwarded.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            candidates.append(ipaddress.ip_address(value))
        except ValueError:
            return remote.compressed
    for candidate in reversed(candidates):
        if not any(candidate in network for network in trusted):
            return candidate.compressed
    return remote.compressed


def _login_keys(username: object, client_ip: str) -> tuple[str, str, str, str]:
    normalized = normalize_login_username(username)
    username_hash = _digest("login-username", normalized)
    ip_hash = _digest("login-ip", client_ip)
    pair_hash = _digest("login-pair", f"{normalized}\0{client_ip}")
    return normalized, username_hash, ip_hash, pair_hash


def _retry_after(locked_until: datetime, now: datetime) -> int:
    return max(1, math.ceil((locked_until - now).total_seconds()))


def login_throttle_status(
    *, username: object, client_ip: str, now: datetime | None = None
) -> LoginThrottleStatus:
    checked_at = now or timezone.now()
    _, _, ip_hash, pair_hash = _login_keys(username, client_ip)
    active = LoginThrottle.objects.filter(
        scope__in=(LoginThrottle.Scope.PAIR, LoginThrottle.Scope.IP),
        key_hash__in=(pair_hash, ip_hash),
        locked_until__gt=checked_at,
    ).order_by("-locked_until")
    throttle = active.first()
    if throttle is None or throttle.locked_until is None:
        return LoginThrottleStatus(locked=False)
    return LoginThrottleStatus(
        locked=True,
        retry_after=_retry_after(throttle.locked_until, checked_at),
    )


def _record_scope_failure(
    *,
    scope: str,
    key_hash: str,
    username_hash: str,
    ip_hash: str,
    limit: int,
    now: datetime,
) -> LoginThrottle:
    throttle, _ = LoginThrottle.objects.select_for_update().get_or_create(
        scope=scope,
        key_hash=key_hash,
        defaults={
            "username_hash": username_hash,
            "ip_hash": ip_hash,
            "window_started_at": now,
        },
    )
    if throttle.window_started_at <= now - LOGIN_FAILURE_WINDOW:
        throttle.failure_count = 0
        throttle.window_started_at = now
        throttle.locked_until = None
    throttle.failure_count += 1
    throttle.last_failed_at = now
    if throttle.failure_count >= limit and not (
        throttle.locked_until is not None and throttle.locked_until > now
    ):
        throttle.locked_until = now + LOGIN_LOCK_DURATION
    throttle.username_hash = username_hash
    throttle.ip_hash = ip_hash
    throttle.save(
        update_fields=(
            "failure_count",
            "window_started_at",
            "last_failed_at",
            "locked_until",
            "username_hash",
            "ip_hash",
            "updated_at",
        )
    )
    return throttle


@transaction.atomic
def record_login_failure(
    *, username: object, client_ip: str, now: datetime | None = None
) -> LoginThrottleStatus:
    failed_at = now or timezone.now()
    _, username_hash, ip_hash, pair_hash = _login_keys(username, client_ip)
    pair = _record_scope_failure(
        scope=LoginThrottle.Scope.PAIR,
        key_hash=pair_hash,
        username_hash=username_hash,
        ip_hash=ip_hash,
        limit=PAIR_FAILURE_LIMIT,
        now=failed_at,
    )
    ip = _record_scope_failure(
        scope=LoginThrottle.Scope.IP,
        key_hash=ip_hash,
        username_hash="",
        ip_hash=ip_hash,
        limit=IP_FAILURE_LIMIT,
        now=failed_at,
    )
    locked_until = max(
        (value for value in (pair.locked_until, ip.locked_until) if value is not None),
        default=None,
    )
    if locked_until is None or locked_until <= failed_at:
        return LoginThrottleStatus(locked=False)
    return LoginThrottleStatus(
        locked=True,
        retry_after=_retry_after(locked_until, failed_at),
    )


def clear_login_pair(*, username: object, client_ip: str) -> int:
    _, _, _, pair_hash = _login_keys(username, client_ip)
    return LoginThrottle.objects.filter(
        scope=LoginThrottle.Scope.PAIR,
        key_hash=pair_hash,
    ).delete()[0]


@transaction.atomic
def unlock_login(*, username: str = "", client_ip: str = "", actor: User | None = None) -> int:
    if actor is not None:
        require_user_capability(actor, Capability.MANAGE_USERS)
    if not username and not client_ip:
        raise ValidationError("Indicá un usuario o una dirección IP.")
    queryset = LoginThrottle.objects.select_for_update().all()
    if username:
        username_hash = _digest("login-username", normalize_login_username(username))
        queryset = queryset.filter(username_hash=username_hash)
    if client_ip:
        try:
            canonical_ip = ipaddress.ip_address(client_ip).compressed
        except ValueError as exc:
            raise ValidationError("La dirección IP no es válida.") from exc
        ip_hash = _digest("login-ip", canonical_ip)
        queryset = queryset.filter(ip_hash=ip_hash)
    rows = list(queryset)
    count = len(rows)
    for throttle in rows:
        throttle.failure_count = 0
        throttle.locked_until = None
        throttle.window_started_at = timezone.now()
        throttle.save(
            update_fields=("failure_count", "locked_until", "window_started_at", "updated_at")
        )
    if actor is not None and count:
        from apps.audit.services import record_event

        record_event(
            action="accounts.login_unlocked",
            entity=actor,
            actor=actor,
            after={"records_unlocked": count},
        )
    return count


def invalidate_user_sessions(user: User) -> int:
    deleted = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        try:
            session_user_id = session.get_decoded().get("_auth_user_id")
        except Exception:  # pragma: no cover - malformed third-party session backends
            continue
        if str(session_user_id) == str(user.pk):
            session.delete()
            deleted += 1
    return deleted


def _active_admins(
    *, workspace_id: uuid.UUID | str, excluding: Membership | None = None
) -> QuerySet[Membership]:
    queryset = Membership.objects.filter(
        workspace_id=workspace_id,
        role=Membership.Role.ADMIN,
        user__is_active=True,
    )
    if excluding is not None:
        queryset = queryset.exclude(pk=excluding.pk)
    return queryset


@transaction.atomic
def change_membership_role(*, membership: Membership, role: str, actor: User) -> Membership:
    actor_membership = require_user_capability(
        actor,
        Capability.MANAGE_USERS,
        workspace_id=membership.workspace_id,
    )
    locked = Membership.objects.select_for_update().select_related("user").get(pk=membership.pk)
    if locked.workspace_id != actor_membership.workspace_id:
        raise PermissionDenied
    Workspace.objects.select_for_update().get(pk=actor_membership.workspace_id)
    if role not in Membership.Role.values:
        raise ValidationError("El rol indicado no existe.")
    if (
        locked.role == Membership.Role.ADMIN
        and role != Membership.Role.ADMIN
        and locked.user.is_active
        and not _active_admins(
            workspace_id=actor_membership.workspace_id,
            excluding=locked,
        ).exists()
    ):
        raise LastActiveAdminError("No podés cambiar el rol del último administrador activo.")
    before = locked.role
    if before == role:
        return locked
    locked.role = role
    locked.session_version += 1
    locked.save(update_fields=("role", "session_version", "updated_at"))
    invalidate_user_sessions(locked.user)
    from apps.audit.services import record_event

    record_event(
        action="accounts.role_changed",
        entity=locked,
        actor=actor,
        before={"role": before},
        after={"role": role},
    )
    return locked


@transaction.atomic
def set_user_active(*, membership: Membership, active: bool, actor: User) -> User:
    actor_membership = require_user_capability(
        actor,
        Capability.MANAGE_USERS,
        workspace_id=membership.workspace_id,
    )
    locked = Membership.objects.select_for_update().select_related("user").get(pk=membership.pk)
    if locked.workspace_id != actor_membership.workspace_id:
        raise PermissionDenied
    Workspace.objects.select_for_update().get(pk=actor_membership.workspace_id)
    if (
        not active
        and locked.user.is_active
        and locked.role == Membership.Role.ADMIN
        and not _active_admins(
            workspace_id=actor_membership.workspace_id,
            excluding=locked,
        ).exists()
    ):
        raise LastActiveAdminError("No podés desactivar al último administrador activo.")
    before = locked.user.is_active
    if before == active:
        return locked.user
    locked.user.is_active = active
    locked.user.save(update_fields=("is_active",))
    locked.session_version += 1
    locked.save(update_fields=("session_version", "updated_at"))
    invalidate_user_sessions(locked.user)
    from apps.audit.services import record_event

    record_event(
        action="accounts.user_status_changed",
        entity=locked.user,
        actor=actor,
        before={"is_active": before},
        after={"is_active": active},
    )
    return locked.user


@transaction.atomic
def create_managed_user(
    *, username: str, email: str, role: str, actor: User
) -> tuple[User, IssuedActivation]:
    actor_membership = require_user_capability(actor, Capability.MANAGE_USERS)
    normalized = User.normalize_username(username).strip()
    if not normalized:
        raise ValidationError("Ingresá un nombre de usuario.")
    if User.objects.filter(username__iexact=normalized).exists():
        raise ValidationError("Ya existe un usuario con ese nombre.")
    if role not in Membership.Role.values:
        raise ValidationError("El rol indicado no existe.")
    user = User(username=normalized, email=email.strip(), is_active=False)
    user.set_unusable_password()
    user.full_clean(exclude=("password",))
    user.save()
    membership = Membership.objects.select_for_update().get(user=user)
    membership.role = role
    membership.workspace = actor_membership.workspace
    membership.save(update_fields=("role", "workspace", "updated_at"))
    issued = issue_activation_token(user=user, actor=actor, kind=ActivationToken.Kind.ACTIVATE)
    return user, issued


@transaction.atomic
def issue_activation_token(*, user: User, actor: User, kind: str) -> IssuedActivation:
    target_membership = Membership.objects.filter(user=user).only("workspace_id").first()
    if target_membership is None:
        raise ValidationError("El usuario no pertenece al espacio de trabajo.")
    require_user_capability(
        actor,
        Capability.MANAGE_USERS,
        workspace_id=target_membership.workspace_id,
    )
    issued_at = timezone.now()
    ActivationToken.objects.select_for_update().filter(
        user=user,
        used_at__isnull=True,
    ).update(used_at=issued_at)
    raw = secrets.token_urlsafe(32)
    token = ActivationToken.objects.create(
        user=user,
        created_by=actor,
        kind=kind,
        token_hash=_digest("activation", raw),
        expires_at=issued_at + ACTIVATION_LIFETIME,
    )
    if kind == ActivationToken.Kind.RESET:
        user.set_unusable_password()
        user.save(update_fields=("password",))
    invalidate_user_sessions(user)
    return IssuedActivation(token=token, raw_token=raw)


def activation_for_token(raw_token: str, *, now: datetime | None = None) -> ActivationToken | None:
    if not raw_token:
        return None
    return (
        ActivationToken.objects.select_related("user")
        .filter(
            token_hash=_digest("activation", raw_token),
            used_at__isnull=True,
            expires_at__gt=now or timezone.now(),
        )
        .first()
    )


@transaction.atomic
def activate_with_token(*, raw_token: str, password: str) -> User:
    now = timezone.now()
    token_hash = _digest("activation", raw_token)
    token = (
        ActivationToken.objects.select_for_update()
        .select_related("user")
        .filter(token_hash=token_hash)
        .first()
    )
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise ActivationError("El enlace venció o ya fue utilizado.")
    user = token.user
    user.set_password(password)
    user.is_active = True
    user.save(update_fields=("password", "is_active"))
    token.used_at = now
    token.save(update_fields=("used_at", "updated_at"))
    invalidate_user_sessions(user)
    return user


def _lock_owner_bootstrap() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1_947_201_001])


@transaction.atomic
def ensure_owner(*, username: str, password: str, email: str = "") -> OwnerBootstrapResult:
    """Create or rotate the configured bootstrap administrator."""
    _lock_owner_bootstrap()
    workspace = get_workspace()
    admins = Membership.objects.select_for_update().filter(
        role=Membership.Role.ADMIN,
        user__is_active=True,
    )
    existing = User.objects.select_for_update().filter(username=username).first()
    if existing is None and admins.exclude(user__username=username).exists():
        raise OwnerConflictError("an active owner with a different username already exists")

    user = existing
    if user is None:
        user = User.objects.create_user(username=username, email=email, password=password)
        membership = user.membership
        if membership.role != Membership.Role.ADMIN:
            membership.role = Membership.Role.ADMIN
            membership.workspace = workspace
            membership.save(update_fields=("role", "workspace", "updated_at"))
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
        user.save(update_fields=changed_fields)
    membership, _ = Membership.objects.get_or_create(
        user=user,
        defaults={"workspace": workspace, "role": Membership.Role.ADMIN},
    )
    if membership.role != Membership.Role.ADMIN or membership.workspace_id != workspace.pk:
        membership.role = Membership.Role.ADMIN
        membership.workspace = workspace
        membership.save(update_fields=("role", "workspace", "updated_at"))
        if not changed_fields:
            changed_fields.append("membership")
    return OwnerBootstrapResult(
        action="updated" if changed_fields else "unchanged",
        user_id=user.pk,
    )
