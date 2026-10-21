"""Shared error codes (brief Appendix A) and the JSON error format (Integration Standard point 5).

Every error leaves the service as:
    {"error": {"code": "UPPER_SNAKE_CASE", "message": "...", "retryable": bool, "details": {...}}}
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    # --- connector contract codes (Appendix A) ---
    DEST_SPEC_VIOLATION = "DEST_SPEC_VIOLATION"
    DEST_RULE_FREQUENCY_CAP = "DEST_RULE_FREQUENCY_CAP"
    CONNECTION_AUTH_EXPIRED = "CONNECTION_AUTH_EXPIRED"
    PLATFORM_RATE_LIMIT = "PLATFORM_RATE_LIMIT"
    PLATFORM_QUOTA_EXHAUSTED = "PLATFORM_QUOTA_EXHAUSTED"
    PLATFORM_CONTENT_REJECTED = "PLATFORM_CONTENT_REJECTED"
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    CONNECTOR_DISABLED = "CONNECTOR_DISABLED"
    # --- our own API-level codes ---
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    UNKNOWN_CONNECTOR = "UNKNOWN_CONNECTOR"
    UNKNOWN_METHOD = "UNKNOWN_METHOD"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# code -> (retryable, HTTP status). "Later"/"next window" codes are retryable once rescheduled.
_META: dict[ErrorCode, tuple[bool, int]] = {
    ErrorCode.DEST_SPEC_VIOLATION: (False, 422),
    ErrorCode.DEST_RULE_FREQUENCY_CAP: (True, 429),
    ErrorCode.CONNECTION_AUTH_EXPIRED: (False, 401),
    ErrorCode.PLATFORM_RATE_LIMIT: (True, 429),
    ErrorCode.PLATFORM_QUOTA_EXHAUSTED: (True, 429),
    ErrorCode.PLATFORM_CONTENT_REJECTED: (False, 422),
    ErrorCode.PLATFORM_UNAVAILABLE: (True, 503),
    ErrorCode.CONNECTOR_DISABLED: (False, 503),
    ErrorCode.VALIDATION_ERROR: (False, 422),
    ErrorCode.NOT_FOUND: (False, 404),
    ErrorCode.METHOD_NOT_ALLOWED: (False, 405),
    ErrorCode.IDEMPOTENCY_KEY_REUSED: (False, 409),
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: (True, 409),
    ErrorCode.UNKNOWN_CONNECTOR: (False, 404),
    ErrorCode.UNKNOWN_METHOD: (False, 404),
    ErrorCode.INTERNAL_ERROR: (False, 500),
}


class IncuBrixError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = _META[code][0] if retryable is None else retryable

    @property
    def http_status(self) -> int:
        return _META[self.code][1]

    def to_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            }
        }
