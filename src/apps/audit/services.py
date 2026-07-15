from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from django.contrib.auth.models import User

from apps.audit.models import AuditEvent

SENSITIVE_KEY_MARKERS = (
    "password",
    "token",
    "api_key",
    "apikey",
    "secret",
    "authorization",
    "credential",
    "private_key",
    "encryption_key",
    "passphrase",
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).casefold().replace("-", "_").replace(" ", "_")
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def record_event(
    *,
    action: str,
    entity: Any,
    actor: User | None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    correlation_id: uuid.UUID | None = None,
    local_ip: str | None = None,
    entity_type: str | None = None,
) -> AuditEvent:
    return AuditEvent.objects.create(
        actor_type=AuditEvent.ActorType.USER if actor else AuditEvent.ActorType.SYSTEM,
        actor=actor,
        action=action,
        entity_type=entity_type or entity.__class__.__name__,
        entity_id=str(entity.pk),
        before=_redact(before or {}),
        after=_redact(after or {}),
        correlation_id=correlation_id or uuid.uuid4(),
        local_ip=local_ip,
    )
