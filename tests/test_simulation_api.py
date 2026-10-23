"""Simulation mode controls: a reviewer of a public demo can make the fake platforms fail, through the same API and token."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
GOOD = {"title": "A long enough title for the forum", "body": "A body that is comfortably longer than twenty characters."}


def make(mock, tmp_path, monkeypatch, **env):
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'sim.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    monkeypatch.setenv("HTTP_TIMEOUT_S", "0.4")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clear():
    yield
    get_settings.cache_clear()


def publish(c, key):
    return c.post("/api/v1/publish", json={"destination_id": "forum", "rendition": GOOD, "tailor": False}, headers={"Idempotency-Key": key})


def test_a_reviewer_can_force_a_429_a_timeout_and_a_reset_without_touching_the_machine(mock, tmp_path, monkeypatch):
    c = make(mock, tmp_path, monkeypatch)
    c.post("/api/v1/destinations", json={"destination": {"name": "Forum", "connector_key": "discourse", "rules": OPEN}, "language": "en"})
    assert c.post("/api/v1/simulation/fail", json={"platform": "discourse", "op": "create", "kind": "status", "status": 429, "retry_after": 20}).status_code == 200
    limited = publish(c, "s1")
    assert limited.status_code == 429 and limited.json()["error"]["details"]["retry_after_s"] == 20.0
    c.post("/api/v1/simulation/fail", json={"platform": "discourse", "op": "create", "kind": "timeout_after", "delay_s": 1.2})
    assert publish(c, "s2").status_code == 503
    assert c.get("/api/v1/simulation/state").json()["discourse_topics"] == 1  # the platform made the post: the proof a retry must not duplicate it
    assert publish(c, "s2").status_code == 200 and c.get("/api/v1/simulation/state").json()["discourse_topics"] == 1
    c.post("/api/v1/simulation/fail", json={"platform": "discourse", "kind": "rename_field", "rename": {"topic_id": "topic_identifier"}})
    assert c.get("/api/v1/simulation/state").json()["rules"]
    assert c.delete("/api/v1/simulation/fail").json() == {"rules": 0}
    assert c.post("/api/v1/simulation/reset").json() == {"status": "reset"} and c.get("/api/v1/simulation/state").json()["discourse_topics"] == 0


def test_the_controls_are_protected_by_the_same_token(mock, tmp_path, monkeypatch):
    c = make(mock, tmp_path, monkeypatch, API_TOKEN="demo-token-1")
    assert c.post("/api/v1/simulation/reset").status_code == 401
    assert c.post("/api/v1/simulation/reset", headers={"Authorization": "Bearer demo-token-1"}).status_code == 200


def test_outside_simulation_mode_there_is_nothing_to_control(mock, tmp_path, monkeypatch):
    c = make(mock, tmp_path, monkeypatch, SIMULATION_MODE="false")
    r = c.post("/api/v1/simulation/reset")
    assert r.status_code == 404 and "simulation mode is off" in r.json()["error"]["message"]


def test_a_protected_mock_gets_its_token_and_an_unreachable_mock_is_a_clear_error(mock, tmp_path, monkeypatch):
    c = make(mock, tmp_path, monkeypatch, MOCK_PLATFORMS_URL="http://127.0.0.1:9")
    r = c.get("/api/v1/simulation/state")
    assert r.status_code == 503 and r.json()["error"]["code"] == "PLATFORM_UNAVAILABLE" and "not reachable" in r.json()["error"]["message"]


def test_the_mock_control_token_is_enforced_when_set(mock, monkeypatch):
    import os

    import httpx

    monkeypatch.setenv("MOCK_CONTROL_TOKEN", "mock-secret-1")
    assert httpx.post(f"{mock.url}/__control/reset").status_code == 401
    assert httpx.post(f"{mock.url}/__control/reset", headers={"X-Mock-Token": "mock-secret-1"}).status_code == 200
    assert httpx.get(f"{mock.url}/sites/plain-1").status_code == 200  # the fake platforms themselves stay open
    monkeypatch.delenv("MOCK_CONTROL_TOKEN")
    assert os.environ.get("MOCK_CONTROL_TOKEN") is None
