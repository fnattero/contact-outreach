from __future__ import annotations

import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.services import OwnerConflictError, ensure_owner


def _enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Command(BaseCommand):
    help = "Load development-only demonstration data; never runs automatically."

    def handle(self, *args: Any, **options: Any) -> None:
        if os.getenv("APP_ENV", "development") != "development":
            raise CommandError("demo data is allowed only when APP_ENV=development")
        if not _enabled(os.getenv("ALLOW_DEMO_DATA", "false")):
            raise CommandError("set ALLOW_DEMO_DATA=true explicitly to load demo data")

        username = os.getenv("DEMO_OWNER_USERNAME", "demo").strip()
        password = os.getenv("DEMO_OWNER_PASSWORD", "")
        email = os.getenv("DEMO_OWNER_EMAIL", "").strip()
        if not username or not password:
            raise CommandError("DEMO_OWNER_USERNAME and DEMO_OWNER_PASSWORD must be configured")
        try:
            result = ensure_owner(username=username, password=password, email=email)
        except OwnerConflictError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Demo data load {result.action}."))
