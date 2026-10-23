"""The access token, the rate limit, and the Idempotency-Key header on every POST."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.http_guard import ApiGuard


def make_client(tmp_path, monkeypatch, **env):
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'g.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clear_settings():
    yield
    get_settings.cache_clear()


POST = {"rendition": {"title": "A long title " * 30, "body": "hello"}, "destination_id": "x"}
TAILOR_DEST = {"destination": {"name": "Blog", "connector_key": "devto"}, "language": "en"}


# ---------------------------------------------------------------- the access token
def test_without_the_token_everything_but_the_public_paths_is_refused(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, API_TOKEN="s3cret-demo-token")
    r = c.get("/api/v1/destinations")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED" and r.json()["error"]["retryable"] is False
    assert c.post("/connector/v1/devto/validate", json={}).status_code == 401
    for public in ("/health", "/ready", "/api/v1/health", "/api/v1/openapi.json", "/docs"):
        assert c.get(public).status_code == 200, public


def test_the_token_works_as_a_bearer_header_or_x_api_token_and_a_wrong_one_does_not(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, API_TOKEN="s3cret-demo-token")
    assert c.get("/api/v1/destinations", headers={"Authorization": "Bearer s3cret-demo-token"}).status_code == 200
    assert c.get("/api/v1/destinations", headers={"X-API-Token": "s3cret-demo-token"}).status_code == 200
    bad = c.get("/api/v1/destinations", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and "s3cret-demo-token" not in bad.text and "nope" not in bad.text  # the message never echoes either token
    assert c.get("/api/v1/destinations", headers={"Authorization": "Bearer "}).status_code == 401


def test_public_feeds_can_be_read_without_a_token_but_not_written(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, API_TOKEN="s3cret-demo-token")
    assert c.get("/feeds/videos.xml").status_code == 200
    assert c.post("/api/v1/feeds/videos/items", json={"guid": "g", "title": "t"}, headers={"X-Feed-Token": "local-dev-feed-token"}).status_code == 401  # the API token comes first


def test_no_token_configured_means_open_as_before(tmp_path, monkeypatch):
    assert make_client(tmp_path, monkeypatch).get("/api/v1/destinations").status_code == 200


# ---------------------------------------------------------------- the rate limit
class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def tiny_app(clock, rate=3, trust_proxy=False):
    inner = FastAPI()

    @inner.get("/ping")
    def ping():
        return {"ok": True}

    @inner.get("/health")
    def health():
        return {"status": "ok"}

    inner.add_middleware(ApiGuard, rate_per_minute=rate, trust_proxy=trust_proxy, clock=clock)
    return TestClient(inner)


def test_over_the_limit_gets_429_with_a_retry_after_and_recovers_after_a_minute():
    clock = Clock()
    c = tiny_app(clock)
    assert [c.get("/ping").status_code for _ in range(3)] == [200, 200, 200]
    blocked = c.get("/ping")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "RATE_LIMITED" and blocked.json()["error"]["retryable"] is True
    assert int(blocked.headers["Retry-After"]) >= 1 and blocked.json()["error"]["details"]["limit_per_minute"] == 3
    clock.now = 30
    assert c.get("/ping").status_code == 429  # still inside the window
    clock.now = 61
    assert c.get("/ping").status_code == 200


def test_health_is_never_rate_limited():
    c = tiny_app(Clock(), rate=1)
    assert all(c.get("/health").status_code == 200 for _ in range(10))


def test_each_client_has_its_own_allowance_behind_a_trusted_proxy():
    c = tiny_app(Clock(), rate=1, trust_proxy=True)
    assert c.get("/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert c.get("/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert c.get("/ping", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    # without trust_proxy the header is ignored (it could be forged)
    d = tiny_app(Clock(), rate=1)
    assert d.get("/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert d.get("/ping", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


# ---------------------------------------------------------------- Idempotency-Key on every POST
def test_the_same_key_and_request_returns_the_same_answer_and_marks_the_replay(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.post("/api/v1/destinations", json=TAILOR_DEST)
    body = {"rendition": {"title": "x " * 200, "body": "b"}, "destination_id": "blog"}
    first = c.post("/api/v1/tailor", json=body, headers={"Idempotency-Key": "t-1"})
    second = c.post("/api/v1/tailor", json=body, headers={"Idempotency-Key": "t-1"})
    assert first.status_code == second.status_code == 200 and "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true" and second.json() == first.json()


def test_the_same_key_with_a_different_request_is_refused(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.post("/api/v1/destinations", json=TAILOR_DEST)
    c.post("/api/v1/tailor", json={"rendition": {"title": "one"}, "destination_id": "blog"}, headers={"Idempotency-Key": "t-2"})
    r = c.post("/api/v1/tailor", json={"rendition": {"title": "two"}, "destination_id": "blog"}, headers={"Idempotency-Key": "t-2"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_two_different_routes_may_use_the_same_key(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.post("/api/v1/destinations", json=TAILOR_DEST)
    a = c.post("/api/v1/tailor", json={"rendition": {"title": "one"}, "destination_id": "blog"}, headers={"Idempotency-Key": "shared"})
    b = c.post("/api/v1/relevance", json={"rendition": {"title": "one"}}, headers={"Idempotency-Key": "shared"})
    assert a.status_code == b.status_code == 200 and "Idempotent-Replayed" not in b.headers


def test_a_failed_request_does_not_burn_the_key(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    bad = c.post("/api/v1/tailor", json={"rendition": {"title": "x"}, "destination_id": "blog"}, headers={"Idempotency-Key": "t-3"})
    assert bad.status_code == 404  # no such destination yet
    c.post("/api/v1/destinations", json=TAILOR_DEST)
    good = c.post("/api/v1/tailor", json={"rendition": {"title": "x"}, "destination_id": "blog"}, headers={"Idempotency-Key": "t-3"})
    assert good.status_code == 200 and "Idempotent-Replayed" not in good.headers  # a real run, not a replayed error


def test_without_a_key_nothing_is_stored_or_replayed(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.post("/api/v1/destinations", json=TAILOR_DEST)
    body = {"rendition": {"title": "x"}, "destination_id": "blog"}
    assert "Idempotent-Replayed" not in c.post("/api/v1/tailor", json=body).headers and "Idempotent-Replayed" not in c.post("/api/v1/tailor", json=body).headers


def test_a_job_post_with_the_same_key_returns_the_same_job(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    one = c.post("/api/v1/jobs", json={"kind": "recertify", "params": {"dry_run": True}}, headers={"Idempotency-Key": "j-1"})
    two = c.post("/api/v1/jobs", json={"kind": "recertify", "params": {"dry_run": True}}, headers={"Idempotency-Key": "j-1"})
    assert one.status_code == two.status_code == 202 and one.json()["job_id"] == two.json()["job_id"] and two.headers["Idempotent-Replayed"] == "true"


def test_every_post_route_under_api_v1_honours_the_key(tmp_path, monkeypatch):
    """The point of the standard: not just the routes we thought of. Walk the OpenAPI document."""
    c = make_client(tmp_path, monkeypatch)
    paths = c.get("/api/v1/openapi.json").json()["paths"]
    post_routes = sorted(p for p, ops in paths.items() if "post" in ops and p.startswith("/api/v1/"))
    assert len(post_routes) >= 10
    for route in post_routes:
        url = route.replace("{dest_id}", "nope").replace("{feed_id}", "f").replace("{proposal_id}", "nope")
        a = c.post(url, json={}, headers={"Idempotency-Key": f"walk-{route}"})
        b = c.post(url, json={"different": True}, headers={"Idempotency-Key": f"walk-{route}"})
        # a request that fails (the empty body here is invalid or the thing does not exist) must not leave the key stuck:
        # the second call, with a different body, is a fresh request and never the 409 "key reused" answer
        assert b.json().get("error", {}).get("code") != "IDEMPOTENCY_KEY_REUSED" or a.status_code < 300, route
