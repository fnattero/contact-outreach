from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from apps.audit.models import AuditEvent, BackgroundJob
from apps.audit.observability import redact_text

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


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
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
        before=redact(before or {}),
        after=redact(after or {}),
        correlation_id=correlation_id or uuid.uuid4(),
        local_ip=local_ip,
    )


def start_job(
    *,
    idempotency_key: str,
    task_name: str,
    entity_type: str,
    entity_id: object,
    queue: str,
) -> BackgroundJob:
    now = timezone.now()
    job, _ = BackgroundJob.objects.update_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "task_name": task_name,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "queue": queue,
            "state": BackgroundJob.State.RUNNING,
            "heartbeat_at": now,
            "started_at": now,
            "finished_at": None,
            "next_retry_at": None,
            "error": "",
        },
    )
    BackgroundJob.objects.filter(pk=job.pk).update(attempts=models.F("attempts") + 1)
    job.refresh_from_db()
    return job


def finish_job(
    job: BackgroundJob,
    *,
    state: str = BackgroundJob.State.SUCCEEDED,
    error: object = "",
    next_retry_at: datetime | None = None,
) -> None:
    now = timezone.now()
    BackgroundJob.objects.filter(pk=job.pk).update(
        state=state,
        heartbeat_at=now,
        finished_at=(
            now
            if state
            in {
                BackgroundJob.State.SUCCEEDED,
                BackgroundJob.State.FAILED,
                BackgroundJob.State.CANCELLED,
            }
            else None
        ),
        next_retry_at=next_retry_at,
        error=redact_text(error),
    )
