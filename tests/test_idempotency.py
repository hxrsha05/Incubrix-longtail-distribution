"""Must-have 6: the same idempotency key never creates a second post - even after a timeout."""
import threading
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.connectors.base import BaseConnector
from app.connectors.manifest import Manifest
from app.connectors.registry import ConnectorRegistry
from app.main import create_app
from app.models import PublishResult

OPEN_RULES = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
MANIFEST = Manifest.model_validate(
    {
        "key": "sim", "version": "1.0.0", "tier": "native", "auth": "api_key",
        "surfaces": [{"key": "topic", "content_types": ["text"], "spec": {}}],
        "capabilities": ["publish"],
        "error_map": {"429": "PLATFORM_RATE_LIMIT"},
    }
)


class SimPlatform(BaseConnector):
    """A connector backed by a pretend platform that counts how many posts REALLY exist."""

    key = "sim"
    posts: list[str]
    behaviour = "ok"  # ok | timeout_after_create | timeout_before_create | rate_limited | slow
    can_reconcile = True

    def __init__(self, manifest, http):
        super().__init__(manifest, http)
        self.posts = []

    def publish(self, rendition, destination, connection, idempotency_key):
        if self.behaviour == "slow":
            time.sleep(0.4)
        if self.behaviour == "rate_limited":
            self._call("GET", "http://p.test/limited")
        if self.behaviour == "timeout_before_create":
            self._call("GET", "http://p.test/timeout")
        if self.behaviour == "timeout_after_create":
            self.posts.append(rendition.title)  # the platform DID create it...
            self._call("GET", "http://p.test/timeout")  # ...but we never heard back
        self.posts.append(rendition.title)
        return PublishResult(platform_post_id=str(len(self.posts)), live_url=f"http://p.test/{len(self.posts)}", status="live")

    def reconcile(self, rendition, destination, connection, idempotency_key):
        if not self.can_reconcile:
            raise NotImplementedError
        if rendition.title in self.posts:
            return PublishResult(platform_post_id="found", live_url="http://p.test/found", status="live")
        return None


def platform(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/limited":
        return httpx.Response(429, headers={"Retry-After": "5"})
    raise httpx.ReadTimeout("no answer", request=request)


@pytest.fixture()
def env():
    registry = ConnectorRegistry({"sim": MANIFEST}, {"sim": SimPlatform}, httpx.Client(transport=httpx.MockTransport(platform)))
    client = TestClient(create_app(registry), raise_server_exceptions=False)
    return client, registry.get("sim"), client.app.state


def req(title="post", key=None, rules=None):
    return {
        "rendition": {"title": title},
        "destination": {"name": "d", "connector_key": "sim", "base_url": f"http://{uuid.uuid4().hex}.test", "rules": rules or OPEN_RULES},
        "connection": {"base_url": "http://p.test"},
        "idempotency_key": key or uuid.uuid4().hex,
    }


def publish(client, payload):
    return client.post("/connector/v1/sim/publish", json=payload)


def test_same_key_twice_posts_once_and_replays(env):
    client, sim, _ = env
    payload = req()
    first, second = publish(client, payload), publish(client, payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert "Idempotent-Replayed" not in first.headers and second.headers["Idempotent-Replayed"] == "true"
    assert len(sim.posts) == 1


def test_same_key_different_content_is_refused(env):
    client, sim, _ = env
    payload = req(title="one")
    publish(client, payload)
    r = publish(client, {**payload, "rendition": {"title": "two"}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert sim.posts == ["one"]


def test_header_can_carry_the_key(env):
    client, sim, _ = env
    payload = req()
    key = payload.pop("idempotency_key")
    for _ in range(2):
        assert client.post("/connector/v1/sim/publish", json=payload, headers={"Idempotency-Key": key}).status_code == 200
    assert len(sim.posts) == 1


def test_timeout_after_create_then_retry_does_not_duplicate(env):
    """The forced-timeout test: the platform created the post, but the answer never reached us."""
    client, sim, _ = env
    sim.behaviour = "timeout_after_create"
    payload = req(title="exactly-once")
    first = publish(client, payload)
    assert first.json()["error"]["code"] == "PLATFORM_UNAVAILABLE"
    assert sim.posts == ["exactly-once"]  # it exists on the platform

    sim.behaviour = "ok"  # the platform is healthy again; the caller retries with the same key
    retry = publish(client, payload)
    assert retry.status_code == 200
    assert retry.json()["platform_post_id"] == "found"  # reconciled with the existing post
    assert sim.posts == ["exactly-once"]  # still exactly one


def test_timeout_without_reconcile_refuses_instead_of_guessing(env):
    client, sim, _ = env
    sim.behaviour, sim.can_reconcile = "timeout_after_create", False
    payload = req(title="unsure")
    publish(client, payload)
    sim.behaviour = "ok"
    retry = publish(client, payload)
    assert retry.status_code == 503
    assert retry.json()["error"]["retryable"] is False
    assert sim.posts == ["unsure"]  # never re-posted


def test_timeout_before_create_is_retried_safely_when_verified_absent(env):
    client, sim, _ = env
    sim.behaviour = "timeout_before_create"
    payload = req(title="never-created")
    publish(client, payload)
    assert sim.posts == []
    sim.behaviour = "ok"
    assert publish(client, payload).status_code == 200  # reconcile said "not there", so posting is safe
    assert sim.posts == ["never-created"]


def test_definite_failure_allows_a_clean_retry(env):
    client, sim, _ = env
    sim.behaviour = "rate_limited"
    payload = req(title="after-429")
    first = publish(client, payload)
    assert first.json()["error"]["code"] == "PLATFORM_RATE_LIMIT" and first.json()["error"]["details"]["retry_after_s"] == 5.0
    sim.behaviour = "ok"
    assert publish(client, payload).status_code == 200
    assert sim.posts == ["after-429"]


def test_request_in_flight_is_reported_not_duplicated(env):
    client, sim, state = env
    payload = req()
    from app.idempotency import request_hash
    from app.models import Destination, Rendition

    fingerprint = request_hash(
        {"rendition": Rendition(**payload["rendition"]).model_dump(mode="json"), "destination": Destination(**payload["destination"]).model_dump(mode="json")}
    )
    state.publish_service.store.begin("publish:sim", payload["idempotency_key"], fingerprint)  # someone else is mid-flight
    r = publish(client, payload)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS" and r.json()["error"]["retryable"] is True
    assert sim.posts == []


def test_two_simultaneous_identical_requests_post_once(env):
    client, sim, _ = env
    sim.behaviour = "slow"
    payload = req(title="race")
    results = []
    threads = [threading.Thread(target=lambda: results.append(publish(client, payload))) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sim.posts == ["race"]
    assert sorted(r.status_code for r in results) in ([200, 200], [200, 409])


def test_replay_is_not_blocked_by_the_cap_its_own_post_filled(env):
    client, sim, _ = env
    rules = {**OPEN_RULES, "frequency_cap": {"max_posts": 1, "per_seconds": 3600}}
    payload = req(rules=rules)
    assert publish(client, payload).status_code == 200
    again = publish(client, payload)  # a retry of the SAME post, not a second post
    assert again.status_code == 200 and again.headers["Idempotent-Replayed"] == "true"


def test_second_post_inside_the_cap_is_blocked_with_a_reason(env):
    client, sim, _ = env
    rules = {**OPEN_RULES, "frequency_cap": {"max_posts": 1, "per_seconds": 604800}}
    first = req(title="a", rules=rules)
    second = {**first, "rendition": {"title": "b"}, "idempotency_key": uuid.uuid4().hex}  # same destination, new post
    assert publish(client, first).status_code == 200
    r = publish(client, second)
    err = r.json()["error"]
    assert r.status_code == 429 and err["code"] == "DEST_RULE_FREQUENCY_CAP"
    assert err["details"]["reschedule_at"] and err["details"]["retry_after_s"] > 0
    assert sim.posts == ["a"]  # the second one never reached the platform


def test_promo_to_no_promo_destination_never_reaches_the_platform(env):
    client, sim, _ = env
    payload = req(rules={**OPEN_RULES, "self_promo_allowed": False})
    payload["rendition"]["promotional"] = True
    r = publish(client, payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == "DEST_SPEC_VIOLATION"
    assert r.json()["error"]["details"]["violations"][0]["rule"] == "self_promotion"
    assert sim.posts == []


def test_destination_without_rules_fails_closed(env):
    client, sim, _ = env
    payload = req()
    payload["destination"]["rules"] = {}
    r = publish(client, payload)
    assert r.status_code == 422 and r.json()["error"]["details"]["violations"][0]["rule"] == "rules_missing"
    assert sim.posts == []
