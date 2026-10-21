"""Structured JSON logs with job_id + correlation id, and secret redaction (must-have 7).

Redaction happens in the formatter, so a token can't reach the log output no matter
which code path logged it.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

REDACTED = "***REDACTED***"
_SENSITIVE_KEY = re.compile(r"(token|secret|password|passwd|api[_-]?key|authorization|cookie|jwt|signature)", re.I)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(bearer\s+|basic\s+|token=|api[_-]?key=|secret=|password=)[A-Za-z0-9._~+/=\-]{6,}"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\b")

_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def redact_text(text: str) -> str:
    text = _SENSITIVE_VALUE.sub(lambda m: m.group(1) + REDACTED, text)
    return _JWT.sub(REDACTED, text)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "job_id": job_id_var.get(),
            "correlation_id": correlation_id_var.get(),
        }
        for key, val in record.__dict__.items():
            if key not in _STD_ATTRS and key not in entry:
                entry[key] = REDACTED if _SENSITIVE_KEY.search(key) else redact(val)
        if record.exc_info:
            entry["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
