from __future__ import annotations

from typing import TypedDict

from celery import shared_task
from django.utils import timezone


class HealthcheckResult(TypedDict):
    status: str
    processed_at: str


@shared_task(name="contact_outreach.healthcheck")  # type: ignore[untyped-decorator]
def healthcheck() -> HealthcheckResult:
    """Small deterministic task used to prove that a worker can process jobs."""
    return {"status": "ok", "processed_at": timezone.now().isoformat()}
