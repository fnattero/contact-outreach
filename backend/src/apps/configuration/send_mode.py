"""Whether real email may leave the app.

Two keys must both be turned: the server allows it (`SEND_MODE=live` and the send kill switch off,
a deployment decision) and an administrator turns it on inside the app. Either one alone keeps the
app in simulation. Every place that decides to call Gmail asks `live_sending_allowed`.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import Capability, require_user_capability
from apps.accounts.signals import get_workspace
from apps.audit.services import record_event
from apps.configuration.models import SendModeSetting


@dataclass(frozen=True, slots=True)
class SendModeState:
    server_allows_live: bool
    app_enabled: bool
    effective_live: bool
    enabled_by: str | None


def server_allows_live() -> bool:
    return settings.SEND_MODE == "live" and not settings.SEND_KILL_SWITCH


def _setting() -> SendModeSetting | None:
    return (
        SendModeSetting.objects.filter(workspace=get_workspace())
        .select_related("live_enabled_by")
        .first()
    )


def app_enabled_live() -> bool:
    setting = _setting()
    return bool(setting and setting.live_enabled)


def live_sending_allowed() -> bool:
    if not server_allows_live():
        return False
    return app_enabled_live() or not settings.SEND_REQUIRES_APP_ENABLE


def send_mode_state() -> SendModeState:
    setting = _setting()
    enabled = bool(setting and setting.live_enabled)
    return SendModeState(
        server_allows_live=server_allows_live(),
        app_enabled=enabled,
        effective_live=live_sending_allowed(),
        enabled_by=(
            setting.live_enabled_by.get_username()
            if setting and setting.live_enabled and setting.live_enabled_by
            else None
        ),
    )


@transaction.atomic
def enable_live_sending(*, actor: User, reauthenticated: bool) -> SendModeSetting:
    membership = require_user_capability(actor, Capability.MANAGE_INTEGRATIONS)
    if not reauthenticated:
        raise PermissionDenied("Volvé a ingresar tu contraseña antes de activar los envíos reales.")
    if not server_allows_live():
        raise ValidationError(
            "El servidor no permite envíos reales. Pedile a quien lo administra que los habilite."
        )
    setting, _ = SendModeSetting.objects.select_for_update().get_or_create(
        workspace=membership.workspace
    )
    before = {"live_enabled": setting.live_enabled}
    setting.live_enabled = True
    setting.live_enabled_at = timezone.now()
    setting.live_enabled_by = actor
    setting.save()
    record_event(
        action="send_mode.live_enabled",
        entity=setting,
        actor=actor,
        before=before,
        after={"live_enabled": True},
    )
    return setting


@transaction.atomic
def disable_live_sending(*, actor: User) -> SendModeSetting:
    membership = require_user_capability(actor, Capability.MANAGE_INTEGRATIONS)
    setting, _ = SendModeSetting.objects.select_for_update().get_or_create(
        workspace=membership.workspace
    )
    before = {"live_enabled": setting.live_enabled}
    setting.live_enabled = False
    setting.live_enabled_at = None
    setting.live_enabled_by = None
    setting.save()
    record_event(
        action="send_mode.simulation_enabled",
        entity=setting,
        actor=actor,
        before=before,
        after={"live_enabled": False},
    )
    return setting
