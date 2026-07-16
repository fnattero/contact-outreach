from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(password|token|api[_-]?key|secret|authorization|credential)"
        r"([\s:=]+)([^\s,;]+)"
    ),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
)


def redact_text(value: object) -> str:
    text = " ".join(str(value).split())
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1\2[REDACTED]", text)
        else:
            text = pattern.sub("Bearer [REDACTED]", text)
    return text[:2000]


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": redact_text(record.getMessage()),
            "correlation_id": getattr(record, "correlation_id", "") or correlation_id_var.get(),
        }
        for field in (
            "campaign_id",
            "job_id",
            "provider",
            "duration_ms",
            "status_code",
            "error_code",
            "method",
            "path",
        ):
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, default=str)
