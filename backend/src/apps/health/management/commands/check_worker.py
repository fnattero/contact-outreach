from __future__ import annotations

from typing import Any, cast

from celery.result import AsyncResult
from django.core.management.base import BaseCommand, CommandError, CommandParser

from contact_outreach.tasks import HealthcheckResult, healthcheck


class Command(BaseCommand):
    help = "Submit a deterministic task and wait for a worker result."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--timeout", type=float, default=10.0)

    def handle(self, *args: Any, **options: Any) -> None:
        result = cast(AsyncResult, healthcheck.apply_async())
        payload = cast(HealthcheckResult, result.get(timeout=options["timeout"]))
        if payload.get("status") != "ok":
            raise CommandError("worker returned an invalid healthcheck result")
        self.stdout.write(self.style.SUCCESS("Worker processed the test task."))
