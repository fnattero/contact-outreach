"""backup.sh and restore.sh must drive the services the compose file really defines."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
BACKUP = BACKEND / "scripts" / "backup.sh"
RESTORE = BACKEND / "scripts" / "restore.sh"

# Records every invocation (and stdin for restores) and fakes the outputs the scripts redirect.
DOCKER_SHIM = """#!/bin/sh
echo "docker $*" >> "$SHIM_LOG"
case "$*" in
  *pg_dump*) printf 'DUMP' ;;
  *"config --services"*) printf '%s\n' ${SHIM_SERVICES:-postgres redis minio minio-init backend} ;;
  *"private_objects export"*) tar -cf - -C "$SHIM_OBJECTS" manifest.tsv ;;
  *pg_restore*|*"private_objects import"*) cat > /dev/null ;;
  *"shell -c"*)
    [ "${SHIM_KILL_SWITCH:-true}" = true ] || { echo "Activá SEND_KILL_SWITCH" >&2; exit 1; } ;;
esac
[ "$*" != "${SHIM_FAIL_ON:-}" ] || exit 1
"""


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    # The shim must be executable and the container's /tmp is mounted noexec.
    scratch = BACKEND / ".test-private"
    scratch.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="backup-scripts-", dir=scratch))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "docker"
    shim.write_text(DOCKER_SHIM)
    shim.chmod(0o755)
    objects = tmp_path / "objects"
    objects.mkdir(exist_ok=True)
    # A real archive needs a manifest.tsv member, like the management command writes.
    (objects / "manifest.tsv").write_text("abc\t3\tcatalogs/a.pdf\n")
    log = tmp_path / "shim.log"
    log.touch()
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SHIM_LOG": str(log),
        "SHIM_OBJECTS": str(objects),
        "HOME": str(tmp_path),
        **extra,
    }


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "shim.log").read_text().splitlines()


def _backup(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(BACKUP), str(tmp_path / "backups")],
        capture_output=True,
        text=True,
        env=_env(tmp_path, **extra),
        check=False,
        timeout=30,
    )


def _make_backup(tmp_path: Path) -> Path:
    result = _backup(tmp_path)
    assert result.returncode == 0, result.stderr
    (backup_dir,) = (tmp_path / "backups").iterdir()
    return backup_dir


def _restore(tmp_path: Path, backup_dir: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "shim.log").write_text("")
    return subprocess.run(
        ["sh", str(RESTORE), "--confirm", str(backup_dir)],
        capture_output=True,
        text=True,
        env=_env(tmp_path, **extra),
        check=False,
        timeout=30,
    )


def test_backup_uses_only_services_and_project_options_of_the_current_compose_file(
    tmp_path: Path,
) -> None:
    _make_backup(tmp_path)

    calls = _calls(tmp_path)
    compose = (
        f"--project-directory {REPO} --file {REPO}/infra/docker-compose.yml --profile storage-s3"
    )
    assert all(call.startswith(f"docker compose {compose} ") for call in calls)
    services = {
        match.group(1)
        for call in calls
        if (match := re.search(r"(?:exec -T|run --rm --no-deps(?: -T)?) (\w+)", call))
    }
    assert services <= {"backend", "postgres", "minio-init"}
    declared = (REPO / "infra" / "docker-compose.yml").read_text()
    assert all(re.search(rf"^  {name}:$", declared, re.M) for name in services)


def test_backup_verifies_then_dumps_the_database_before_the_objects(tmp_path: Path) -> None:
    _make_backup(tmp_path)

    steps = [
        next(index for index, call in enumerate(_calls(tmp_path)) if marker in call)
        for marker in ("verify_restore", "pg_dump", "private_objects export")
    ]
    assert steps == sorted(steps)


def test_backup_writes_a_private_checksummed_directory_without_the_encryption_key(
    tmp_path: Path,
) -> None:
    result = _backup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "FIELD_ENCRYPTION_KEY no está incluido" in result.stdout
    (backup_dir,) = (tmp_path / "backups").iterdir()
    assert sorted(path.name for path in backup_dir.iterdir()) == [
        "checksums.sha256",
        "database.dump",
        "manifest.txt",
        "objects.manifest.tsv",
        "objects.tar",
    ]
    assert (backup_dir / "database.dump").read_bytes() == b"DUMP"
    assert (backup_dir / "objects.manifest.tsv").read_text() == "abc\t3\tcatalogs/a.pdf\n"
    manifest = (backup_dir / "manifest.txt").read_text()
    assert "contains=postgresql,private_objects" in manifest
    assert "object_count=1" in manifest
    assert "encryption_key_included=false" in manifest
    assert backup_dir.stat().st_mode & 0o077 == 0
    assert (backup_dir / "database.dump").stat().st_mode & 0o077 == 0
    verify = subprocess.run(
        ["sha256sum", "-c", "checksums.sha256"], cwd=backup_dir, capture_output=True, check=False
    )
    assert verify.returncode == 0


def test_a_failed_backup_leaves_no_partial_directory_and_keeps_the_previous_one(
    tmp_path: Path,
) -> None:
    previous = tmp_path / "backups" / "20200101T000000Z"
    previous.mkdir(parents=True)
    (previous / "database.dump").write_bytes(b"old")

    result = _backup(
        tmp_path,
        SHIM_FAIL_ON=(
            f"compose --project-directory {REPO} --file {REPO}/infra/docker-compose.yml "
            "--profile storage-s3 exec -T backend python src/manage.py private_objects export"
        ),
    )

    assert result.returncode != 0
    assert [path.name for path in (tmp_path / "backups").iterdir()] == [previous.name]
    assert (previous / "database.dump").read_bytes() == b"old"


def test_a_custom_compose_command_is_honoured(tmp_path: Path) -> None:
    result = _backup(tmp_path, COMPOSE="docker compose --file a.yml --file b.yml")

    assert result.returncode == 0, result.stderr
    prefix = "docker compose --file a.yml --file b.yml "
    assert all(call.startswith(prefix) for call in _calls(tmp_path))


def test_restore_requires_confirmation(tmp_path: Path) -> None:
    result = subprocess.run(
        ["sh", str(RESTORE), str(tmp_path)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 2
    assert "Uso:" in result.stderr


def test_restore_runs_the_documented_sequence_and_starts_the_stack_last(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)

    result = _restore(tmp_path, backup_dir)

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    markers = [
        "shell -c",
        "stop backend",
        "up --detach --wait postgres redis",
        "up --detach --wait minio",
        "run --rm --no-deps minio-init",
        "pg_restore",
        "private_objects import",
        "migrate_safe",
        "verify_restore --require-kill-switch",
    ]
    positions = [next(i for i, call in enumerate(calls) if marker in call) for marker in markers]
    assert positions == sorted(positions)
    assert calls[-1].endswith(" up --detach --wait")
    assert len(calls) - 1 > positions[-1]
    assert "Restore verificado" in result.stdout


def test_restore_with_filesystem_storage_never_starts_minio(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)

    result = _restore(tmp_path, backup_dir, SHIM_SERVICES="postgres redis backend")

    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert not any("minio" in call for call in calls)
    assert any("private_objects import" in call for call in calls)


def test_restore_refuses_when_the_kill_switch_is_off_before_touching_anything(
    tmp_path: Path,
) -> None:
    backup_dir = _make_backup(tmp_path)

    result = _restore(tmp_path, backup_dir, SHIM_KILL_SWITCH="false")

    assert result.returncode != 0
    calls = _calls(tmp_path)
    assert len(calls) == 1 and "shell -c" in calls[0]


def test_restore_refuses_a_backup_whose_checksums_do_not_match(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)
    (backup_dir / "objects.tar").write_bytes(b"corrupted")

    result = _restore(tmp_path, backup_dir)

    assert result.returncode != 0
    assert _calls(tmp_path) == []


def test_restore_refuses_a_backup_from_the_old_layout(tmp_path: Path) -> None:
    backup_dir = _make_backup(tmp_path)
    (backup_dir / "objects.tar").unlink()

    result = _restore(tmp_path, backup_dir)

    assert result.returncode != 0
    assert _calls(tmp_path) == []
