"""The backend entrypoint decides which credentials the long-running processes inherit."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "start-backend.sh"

PYTHON_SHIM = """#!/bin/sh
# Records each manage.py invocation with the database URL it ran under.
echo "manage $* DATABASE_URL=${DATABASE_URL:-}" >> "$SHIM_LOG"
"""
SUPERVISORD_SHIM = """#!/bin/sh
# Records the environment the long-running processes would inherit.
{
  echo "supervisord DATABASE_URL=${DATABASE_URL:-}"
  echo "supervisord MIGRATION_DATABASE_URL=${MIGRATION_DATABASE_URL:-}"
  echo "supervisord OWNER_PASSWORD=${OWNER_PASSWORD:-}"
  echo "supervisord OWNER_USERNAME=${OWNER_USERNAME:-}"
} >> "$SHIM_LOG"
"""


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    # The shims must be executable, and the container's /tmp is mounted noexec, so they live in the
    # git-ignored scratch directory inside the checkout instead.
    scratch = BACKEND / ".test-private"
    scratch.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="start-backend-", dir=scratch))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _run(tmp_path: Path, **environment: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("python", PYTHON_SHIM), ("supervisord", SUPERVISORD_SHIM)):
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(0o755)
    log = tmp_path / "shim.log"
    log.touch()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SHIM_LOG": str(log),
        "HOME": str(tmp_path),
    }
    env.update(environment)
    result = subprocess.run(
        ["sh", str(SCRIPT)], capture_output=True, text=True, env=env, check=False, timeout=30
    )
    return result, log.read_text().splitlines()


def test_production_refuses_to_bootstrap_the_owner_on_startup(tmp_path: Path) -> None:
    # Bootstrapping rewrites the owner's password on every restart, so in production the first
    # administrator is created once, by hand, and never by the container entrypoint.
    result, calls = _run(
        tmp_path,
        APP_ENV="production",
        RUN_OWNER_BOOTSTRAP_ON_STARTUP="true",
        OWNER_USERNAME="owner",
        OWNER_PASSWORD="initial-password",
    )

    assert result.returncode != 0
    assert "RUN_OWNER_BOOTSTRAP_ON_STARTUP" in result.stderr
    assert calls == []


def test_the_owner_bootstrap_is_still_available_for_local_development(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        APP_ENV="development",
        RUN_OWNER_BOOTSTRAP_ON_STARTUP="true",
        OWNER_USERNAME="owner",
        OWNER_PASSWORD="initial-password",
    )

    assert result.returncode == 0, result.stderr
    assert any(call.startswith("manage src/manage.py bootstrap_owner") for call in calls)


@pytest.mark.parametrize("migrations", ["true", "false"])
def test_long_running_processes_never_inherit_migration_or_bootstrap_credentials(
    tmp_path: Path, migrations: str
) -> None:
    result, calls = _run(
        tmp_path,
        APP_ENV="production",
        RUN_MIGRATIONS_ON_STARTUP=migrations,
        RUN_OWNER_BOOTSTRAP_ON_STARTUP="false",
        DATABASE_URL="postgresql://runtime:pw@db/app",
        MIGRATION_DATABASE_URL="postgresql://ddl:pw@db/app",
        OWNER_USERNAME="owner",
        OWNER_PASSWORD="initial-password",
    )

    assert result.returncode == 0, result.stderr
    assert "supervisord DATABASE_URL=postgresql://runtime:pw@db/app" in calls
    assert "supervisord MIGRATION_DATABASE_URL=" in calls
    assert "supervisord OWNER_PASSWORD=" in calls
    assert "supervisord OWNER_USERNAME=" in calls
    migrate = [call for call in calls if "migrate_safe" in call]
    if migrations == "true":
        # DDL runs under the migration role, not the runtime one.
        assert migrate == [
            "manage src/manage.py migrate_safe DATABASE_URL=postgresql://ddl:pw@db/app"
        ]
    else:
        assert migrate == []
