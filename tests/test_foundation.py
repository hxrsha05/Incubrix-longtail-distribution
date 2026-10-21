import json
import logging

from fastapi.testclient import TestClient

from app.errors import ErrorCode, IncuBrixError
from app.logging_setup import JsonFormatter, correlation_id_var, redact
from app.main import create_app

client = TestClient(create_app(), raise_server_exceptions=False)


def test_health_and_ready_both_paths():
    for path in ("/health", "/ready", "/api/v1/health", "/api/v1/ready"):
        assert client.get(path).status_code == 200


def test_correlation_id_is_echoed_or_generated():
    assert client.get("/health", headers={"X-Correlation-Id": "abc-123"}).headers["X-Correlation-Id"] == "abc-123"
    assert client.get("/health").headers["X-Correlation-Id"]


def test_unknown_route_uses_error_format():
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    err = r.json()["error"]
    assert set(err) == {"code", "message", "retryable", "details"}
    assert err["code"] == "NOT_FOUND"


def test_error_codes_are_upper_snake_case():
    for code in ErrorCode:
        assert code.value == code.value.upper() and " " not in code.value


def test_retryable_flags_match_the_contract():
    assert IncuBrixError(ErrorCode.PLATFORM_RATE_LIMIT, "x").retryable is True
    assert IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, "x").retryable is True
    assert IncuBrixError(ErrorCode.DEST_SPEC_VIOLATION, "x").retryable is False
    assert IncuBrixError(ErrorCode.PLATFORM_CONTENT_REJECTED, "x").retryable is False
    assert IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "x").retryable is False


def test_redaction_masks_keys_and_token_shapes():
    out = redact({"api_key": "sk-123456", "nested": {"Authorization": "Bearer abcdef123456"}, "note": "ok"})
    assert out["api_key"] == "***REDACTED***"
    assert out["nested"]["Authorization"] == "***REDACTED***"
    assert out["note"] == "ok"
    assert "abcdef123456" not in redact("header was Bearer abcdef123456 here")


def test_log_line_is_json_with_ids_and_no_secrets():
    token = correlation_id_var.set("cid-1")
    try:
        record = logging.LogRecord("t", logging.INFO, "", 0, "calling with token=supersecret123", (), None)
        record.api_token = "supersecret123"
        line = json.loads(JsonFormatter().format(record))
    finally:
        correlation_id_var.reset(token)
    assert line["correlation_id"] == "cid-1"
    assert {"timestamp", "level", "job_id", "message"} <= set(line)
    assert "supersecret123" not in json.dumps(line)
