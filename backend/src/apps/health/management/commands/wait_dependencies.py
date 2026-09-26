from __future__ import annotations

import time
from typing import Any

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection

from apps.catalogs.storage import private_catalog_storage


class Command(BaseCommand):
    help = "Wait a bounded time for PostgreSQL, Redis, and private object storage."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--timeout", type=int, default=60)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        timeout = int(options["timeout"])
        if timeout < 1 or timeout > 300:
            raise CommandError("timeout must be between 1 and 300 seconds")
        deadline = time.monotonic() + timeout
        last_unavailable: tuple[str, ...] = ()
        while time.monotonic() < deadline:
            unavailable: list[str] = []
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            except Exception:
                unavailable.append("database")

            try:
                cache_key = "startup:dependency-check"
                cache.set(cache_key, "ok", timeout=5)
                if cache.get(cache_key) != "ok":
                    unavailable.append("redis")
                cache.delete(cache_key)
            except Exception:
                unavailable.append("redis")

            try:
                private_catalog_storage.exists("health/dependency-check")
            except Exception:
                unavailable.append("storage")

            if not unavailable:
                self.stdout.write("Required dependencies are available.")
                return
            last_unavailable = tuple(unavailable)
            time.sleep(1)
        names = ", ".join(last_unavailable) or "unknown"
        raise CommandError(f"Required dependencies unavailable: {names}")
