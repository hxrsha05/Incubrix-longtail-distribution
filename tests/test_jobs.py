import json
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.connectors.registry import ConnectorRegistry
from app.errors import ErrorCode, IncuBrixError
from app.jobs import sign
from app.main import create_app

received: list[httpx.Request] = []


def callback_receiver(request: httpx.Request) -> httpx.Response:
    received.append(request)
    return httpx.Response(200)


@pytest.fixture()
def client():
    received.clear()
    app = create_app(ConnectorRegistry({}, {}, httpx.Client()), callback_http=httpx.Client(transport=httpx.MockTransport(callback_receiver)))
    jobs = app.state.jobs
    calls = {"count": 0}

    def echo(ctx, params):
        calls["count"] += 1
        ctx.progress(50)
        return {"echo": params, "links": {"report": "http://files.test/report.json"}}

    def failing(ctx, params):
        raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, "platform down")

    def crashing(ctx, params):
        raise RuntimeError("bug")

    jobs.register("echo", echo)
    jobs.register("failing", failing)
    jobs.register("crashing", crashing)
    c = TestClient(app, raise_server_exceptions=False)
    c.calls = calls
    return c


def wait_done(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_post_returns_202_and_job_finishes(client):
    r = client.post("/api/v1/jobs", json={"kind": "echo", "params": {"a": 1}})
    assert r.status_code == 202
    job = r.json()
    assert job["status"] in ("queued", "running", "succeeded") and "job_id" in job
    done = wait_done(client, job["job_id"])
    assert done["status"] == "succeeded" and done["progress"] == 100
    assert done["result"]["echo"] == {"a": 1} and done["result"]["links"]["report"]


def test_failed_job_reports_the_shared_error_format(client):
    done = wait_done(client, client.post("/api/v1/jobs", json={"kind": "failing"}).json()["job_id"])
    assert done["status"] == "failed"
    assert done["error"]["code"] == "PLATFORM_UNAVAILABLE" and done["error"]["retryable"] is True


def test_crashing_job_fails_cleanly(client):
    done = wait_done(client, client.post("/api/v1/jobs", json={"kind": "crashing"}).json()["job_id"])
    assert done["status"] == "failed" and done["error"]["code"] == "INTERNAL_ERROR"


def test_unknown_job_kind_and_unknown_job_id(client):
    assert client.post("/api/v1/jobs", json={"kind": "nope"}).json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.get("/api/v1/jobs/doesnotexist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_idempotency_key_returns_the_same_job_and_runs_once(client):
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    body = {"kind": "echo", "params": {"x": 1}}
    first = client.post("/api/v1/jobs", json=body, headers=headers)
    second = client.post("/api/v1/jobs", json=body, headers=headers)
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    wait_done(client, first.json()["job_id"])
    assert client.calls["count"] == 1


def test_idempotency_key_with_different_body_is_refused(client):
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    client.post("/api/v1/jobs", json={"kind": "echo", "params": {"x": 1}}, headers=headers)
    r = client.post("/api/v1/jobs", json={"kind": "echo", "params": {"x": 2}}, headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_callback_is_signed_with_hmac_sha256(client):
    job = client.post("/api/v1/jobs", json={"kind": "echo", "callback_url": "http://hook.test/done"}).json()
    done = wait_done(client, job["job_id"])
    deadline = time.time() + 5
    while not received and time.time() < deadline:
        time.sleep(0.02)
    assert received, "callback was not delivered"
    request = received[0]
    assert request.url.path == "/done"
    assert request.headers["X-IncuBrix-Signature"] == sign("change-me", request.content)  # default test secret
    payload = json.loads(request.content)
    assert payload["job_id"] == done["job_id"] and payload["status"] == "succeeded"


def test_ready_checks_the_database(client):
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
