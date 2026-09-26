from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.health.management.commands import wait_dependencies


@pytest.mark.django_db
def test_wait_dependencies_accepts_available_services(
    private_catalog_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del private_catalog_dir

    call_command("wait_dependencies", timeout=1)

    assert "Required dependencies are available." in capsys.readouterr().out


@pytest.mark.parametrize("timeout", [0, 301])
def test_wait_dependencies_rejects_unbounded_timeout(timeout: int) -> None:
    with pytest.raises(CommandError, match="between 1 and 300"):
        call_command("wait_dependencies", timeout=timeout)


def test_wait_dependencies_reports_each_unavailable_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic_values = iter((0.0, 0.0, 2.0))

    def unavailable_cursor() -> NoReturn:
        raise RuntimeError("database unavailable")

    def unavailable_cache(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise RuntimeError("cache unavailable")

    def unavailable_storage(name: str) -> NoReturn:
        del name
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(wait_dependencies.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(wait_dependencies.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(wait_dependencies.connection, "cursor", unavailable_cursor)
    monkeypatch.setattr(wait_dependencies.cache, "set", unavailable_cache)
    monkeypatch.setattr(wait_dependencies.private_catalog_storage, "exists", unavailable_storage)

    with pytest.raises(
        CommandError,
        match="Required dependencies unavailable: database, redis, storage",
    ):
        call_command("wait_dependencies", timeout=1)
