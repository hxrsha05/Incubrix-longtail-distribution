"""Alerts: stored, signed webhook, best effort."""
import hashlib
import hmac
import json

import httpx
from sqlalchemy import create_engine

from app.alerts import AlertService
from app.db import init_db


def service(tmp_path, handler=None, url=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    init_db(engine)
    http = httpx.Client(transport=httpx.MockTransport(handler)) if handler else None
    return AlertService(engine, http, url, secret="alert-secret", clock=lambda: 1000.0)


def test_an_alert_is_stored_and_listed_newest_first(tmp_path):
    s = service(tmp_path)
    first = s.raise_alert("destination_demoted", "forum", "first")
    second = s.raise_alert("destination_recovered", "forum", "second", severity="info", details={"run": "r1"})
    assert [a["message"] for a in s.list()] == ["second", "first"] and second["details"] == {"run": "r1"} and first["acknowledged_at"] is None


def test_the_webhook_is_signed_with_hmac_sha256_over_the_exact_body(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"], seen["sig"] = request.content, request.headers["X-IncuBrix-Signature"]
        return httpx.Response(200)

    service(tmp_path, handler, "https://alerts.example/hook").raise_alert("destination_demoted", "forum", "forum demoted")
    assert seen["sig"] == "sha256=" + hmac.new(b"alert-secret", seen["body"], hashlib.sha256).hexdigest()
    payload = json.loads(seen["body"])
    assert payload["kind"] == "destination_demoted" and payload["destination_id"] == "forum" and "acknowledged_at" not in payload


def test_a_webhook_that_is_down_never_breaks_the_work_and_is_retried_once(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("down")

    s = service(tmp_path, handler, "https://alerts.example/hook")
    alert = s.raise_alert("destination_demoted", "forum", "forum demoted")  # does not raise
    assert len(calls) == 2 and s.get(alert["id"])["message"] == "forum demoted"


def test_a_webhook_that_answers_an_error_is_retried_then_given_up_quietly(tmp_path):
    calls = []
    s = service(tmp_path, lambda r: calls.append(1) or httpx.Response(500), "https://alerts.example/hook")
    s.raise_alert("x", None, "m")
    assert len(calls) == 2


def test_no_webhook_configured_means_no_call(tmp_path):
    s = service(tmp_path, lambda r: (_ for _ in ()).throw(AssertionError("must not be called")), None)
    s.raise_alert("x", None, "m")
