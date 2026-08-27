from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, call, patch

import pytest
from django.core.management import CommandError, call_command

from apps.health.management.commands.check_worker import Command as CheckWorkerCommand
from apps.health.management.commands.migrate_safe import (
    MIGRATION_LOCK_ID,
)
from apps.health.management.commands.migrate_safe import (
    Command as MigrateSafeCommand,
)
from contact_outreach.celery import app
from contact_outreach.tasks import healthcheck


def test_celery_application_is_importable() -> None:
    assert app.main == "contact_outreach"


def test_healthcheck_task_runs_in_eager_mode() -> None:
    result = healthcheck.apply()
    assert result.successful()
    assert result.get()["status"] == "ok"


@pytest.mark.django_db
def test_check_worker_command_accepts_valid_result() -> None:
    output = StringIO()
    call_command("check_worker", stdout=output)
    assert "processed the test task" in output.getvalue()


def test_check_worker_rejects_an_invalid_result() -> None:
    async_result = MagicMock()
    async_result.get.return_value = {"status": "unexpected"}
    with (
        patch(
            "apps.health.management.commands.check_worker.healthcheck.apply_async",
            return_value=async_result,
        ),
        pytest.raises(CommandError, match="invalid healthcheck result"),
    ):
        CheckWorkerCommand().handle(timeout=1.0)


def test_migrate_safe_uses_regular_migrate_outside_postgresql() -> None:
    with (
        patch("apps.health.management.commands.migrate_safe.connection.vendor", "sqlite"),
        patch("apps.health.management.commands.migrate_safe.call_command") as migrate,
    ):
        MigrateSafeCommand().handle(verbosity=1)
    migrate.assert_called_once_with("migrate", interactive=False, verbosity=1)


def test_migrate_safe_holds_and_releases_postgresql_advisory_lock() -> None:
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    with (
        patch("apps.health.management.commands.migrate_safe.connection.vendor", "postgresql"),
        patch(
            "apps.health.management.commands.migrate_safe.connection.cursor",
            return_value=cursor_context,
        ),
        patch("apps.health.management.commands.migrate_safe.call_command") as migrate,
    ):
        MigrateSafeCommand().handle(verbosity=1)

    migrate.assert_called_once_with("migrate", interactive=False, verbosity=1)
    assert cursor.execute.call_args_list == [
        call("SELECT pg_advisory_lock(%s)", [MIGRATION_LOCK_ID]),
        call("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID]),
    ]
