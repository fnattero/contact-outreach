from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

MIGRATION_LOCK_ID = 1_947_201_002


class Command(BaseCommand):
    help = "Apply migrations while holding a PostgreSQL advisory lock."

    def handle(self, *args: Any, **options: Any) -> None:
        if connection.vendor != "postgresql":
            call_command("migrate", interactive=False, verbosity=options["verbosity"])
            return

        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_LOCK_ID])
        try:
            call_command("migrate", interactive=False, verbosity=options["verbosity"])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID])
