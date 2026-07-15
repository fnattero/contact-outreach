from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def runtime_safety(request: HttpRequest) -> dict[str, Any]:
    return {
        "runtime_send_mode": settings.SEND_MODE,
        "runtime_kill_switch": settings.SEND_KILL_SWITCH,
    }
