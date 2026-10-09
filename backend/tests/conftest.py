from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache

from apps.catalogs.storage import private_catalog_storage


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    cache.clear()


@pytest.fixture
def owner(db: None) -> User:
    del db
    return User.objects.create_user(username="owner", password="correct-password")


@pytest.fixture
def private_catalog_dir(tmp_path: Path, settings: Any) -> Iterator[Path]:
    settings.PRIVATE_STORAGE_ROOT = tmp_path
    private_catalog_storage.__dict__.pop("wrapped", None)
    yield tmp_path
    private_catalog_storage.__dict__.pop("wrapped", None)


def _coverage_rows(cov: Any) -> tuple[float, list[tuple[str, float]]]:
    """Total percentage and (file, percentage) pairs, weakest first, from coverage's own report."""
    report = io.StringIO()
    total = float(
        cov.report(file=report, show_missing=False, skip_covered=False, sort="Cover", precision=1)
    )
    rows: list[tuple[str, float]] = []
    for line in report.getvalue().splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0].startswith("src/") and parts[-1].endswith("%"):
            rows.append(("/".join(parts[0].split("/")[-2:]), float(parts[-1][:-1])))
    return total, rows


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    """One glanceable coverage banner instead of a seventy-row table."""
    controller = getattr(config.pluginmanager.getplugin("_cov"), "cov_controller", None)
    cov = getattr(controller, "cov", None)
    if cov is None or not terminalreporter.stats.get("passed"):
        return
    try:
        total, rows = _coverage_rows(cov)
    except Exception:  # coverage data is partial or missing (e.g. a single-file run)
        return
    minimum = float(config.getoption("cov_fail_under") or 0)
    ok = total >= minimum
    terminalreporter.write_sep("=", "coverage")
    terminalreporter.write_line(
        f"  Backend coverage  {total:.1f}%   (minimum {minimum:g}%)   "
        f"{'OK' if ok else 'BELOW MINIMUM'}",
        green=ok,
        red=not ok,
        bold=True,
    )
    weakest = [f"{name} {percent:.0f}%" for name, percent in rows if percent < 80][:5]
    if weakest:
        terminalreporter.write_line("  Weakest files:    " + "   ".join(weakest))
    terminalreporter.write_sep("=")
