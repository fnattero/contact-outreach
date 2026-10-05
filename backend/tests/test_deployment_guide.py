"""The Railway guide must only name variables the code reads, and set the ones it requires."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "RAILWAY_DEPLOYMENT.md"
SOURCES = (
    *(ROOT / "backend" / "src").rglob("*.py"),
    *(ROOT / "backend" / "scripts").glob("*"),
    *(ROOT / "frontend" / "app").rglob("*.ts"),
    *(ROOT / "frontend" / "app").rglob("*.tsx"),
    ROOT / "frontend" / "proxy.ts",
    ROOT / "frontend" / "next.config.ts",
)


def _guide_variables() -> dict[str, str]:
    """Variables assigned in the guide's ```text blocks, with their example values."""
    variables: dict[str, str] = {}
    for block in re.findall(r"```text\n(.*?)```", GUIDE.read_text(), flags=re.S):
        for line in block.splitlines():
            match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
            if match:
                variables[match.group(1)] = match.group(2)
    return variables


def test_every_variable_in_the_guide_is_read_by_the_code() -> None:
    corpus = "\n".join(path.read_text(errors="ignore") for path in SOURCES if path.is_file())
    unread = sorted(name for name in _guide_variables() if not re.search(rf"\b{name}\b", corpus))

    assert unread == [], f"the guide sets variables that nothing reads: {unread}"


def test_the_guide_sets_the_production_safeguards_the_settings_require() -> None:
    variables = _guide_variables()

    assert variables["APP_ENV"] == "production"
    assert variables["DJANGO_DEBUG"] == "false"
    assert variables["DJANGO_SSL_REDIRECT"] == "true"
    assert variables["DJANGO_SECURE_COOKIES"] == "true"
    assert variables["DJANGO_PROXY_HTTPS"] == "true"
    assert variables["DJANGO_RAILWAY_PROXY"] == "true"
    assert variables["SEND_MODE"] == "dry-run"
    for kill_switch in ("SEND_KILL_SWITCH", "AUTO_REPLY_KILL_SWITCH", "RELATIONSHIP_KILL_SWITCH"):
        assert variables[kill_switch] == "true"
    # The backend only accepts the public host, which the frontend proxy presents.
    assert variables["DJANGO_ALLOWED_HOSTS"] == variables["PUBLIC_APP_ORIGIN"].removeprefix(
        "https://"
    )
