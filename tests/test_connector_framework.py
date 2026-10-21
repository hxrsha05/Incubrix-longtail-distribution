import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.connectors.base import BaseConnector
from app.connectors.manifest import Manifest, load_manifests
from app.connectors.registry import DEFAULT_MANIFEST_DIR, ConnectorRegistry
from app.main import create_app
from app.models import Connection, Destination, PublishResult, Rendition


# ---------- a tiny fake platform + connector, so the framework is tested on its own ----------
def fake_platform(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/ok":
        return httpx.Response(200, json={"id": 7})
    if path == "/limited":
        return httpx.Response(429, headers={"Retry-After": "30"}, text="slow down")
    if path == "/expired":
        return httpx.Response(401, text="token expired")
    if path == "/rejected":
        return httpx.Response(422, text="title already used")
    if path == "/boom":
        return httpx.Response(500, text="oops")
    if path == "/timeout":
        raise httpx.ReadTimeout("too slow", request=request)
    return httpx.Response(404)


class FakeConnector(BaseConnector):
    key = "fake"

    def publish(self, rendition, destination, connection, idempotency_key):
        self._call("GET", f"{self.instance_url(connection, destination)}{destination.extra.get('path', '/ok')}")
        return PublishResult(platform_post_id="7", live_url="http://x/7", status="live")


MANIFEST = Manifest.model_validate(
    {
        "key": "fake",
        "version": "1.0.0",
        "tier": "native",
        "auth": "api_key",
        "surfaces": [
            {
                "key": "topic",
                "content_types": ["text"],
                "spec": {"title_max_chars": 20, "body_max_chars": 50, "links_allowed": False},
            }
        ],
        "capabilities": ["publish"],
        "error_map": {"429": "PLATFORM_RATE_LIMIT", "401": "CONNECTION_AUTH_EXPIRED", "422": "PLATFORM_CONTENT_REJECTED"},
        "instance_param": "base_url",
    }
)


@pytest.fixture()
def http():
    return httpx.Client(transport=httpx.MockTransport(fake_platform))


@pytest.fixture()
def client(http):
    registry = ConnectorRegistry({"fake": MANIFEST}, {"fake": FakeConnector}, http, disabled={"off"})
    registry.manifests["off"] = MANIFEST
    registry.classes["off"] = FakeConnector
    return TestClient(create_app(registry), raise_server_exceptions=False)


OPEN_RULES = {
    "frequency_cap": None,
    "self_promo_allowed": True,
    "link_policy": "allowed",
    "disclosure_required": False,
    "automation_allowed": True,
}


def body(path="/ok", **rendition):
    return {
        "rendition": {"title": "Hello", **rendition},
        "destination": {"name": "d", "connector_key": "fake", "extra": {"path": path}, "rules": OPEN_RULES},
        "connection": {"base_url": "http://platform.test", "api_key": "k"},
        "idempotency_key": uuid.uuid4().hex,  # unique per call: the database is shared across tests
    }


# ---------- manifests ----------
def test_shipped_manifests_load():
    manifests = load_manifests(DEFAULT_MANIFEST_DIR)
    assert "discourse" in manifests
    assert manifests["discourse"].instance_param == "base_url"


def test_manifest_rejects_unknown_error_code():
    bad = MANIFEST.model_dump() | {"error_map": {"429": "NOT_A_REAL_CODE"}}
    with pytest.raises(ValueError):
        Manifest.model_validate(bad)


def test_manifest_rejects_unknown_capability():
    with pytest.raises(ValueError):
        Manifest.model_validate(MANIFEST.model_dump() | {"capabilities": ["teleport"]})


# ---------- validate() ----------
def test_validate_reports_field_level_violations(http):
    c = FakeConnector(MANIFEST, http)
    r = Rendition(title="x" * 25, body="y" * 60 + " http://a.b", content_type="video_link")
    result = c.validate(r, Destination(name="d", connector_key="fake"), Connection())
    assert result.ok is False
    fields = {v.field for v in result.violations}
    assert fields == {"content_type", "title", "body", "link_url"}
    title = next(v for v in result.violations if v.field == "title")
    assert (title.limit, title.actual) == (20, 25)


def test_destination_spec_overrides_manifest_default(http):
    c = FakeConnector(MANIFEST, http)
    r = Rendition(title="x" * 25)
    assert not c.validate(r, Destination(name="d", connector_key="fake"), Connection()).ok
    assert c.validate(r, Destination(name="d", connector_key="fake", spec={"title_max_chars": 100}), Connection()).ok


# ---------- the HTTP door ----------
def test_validate_over_http(client):
    r = client.post("/connector/v1/fake/validate", json=body(title="x" * 30) | {"rendition": {"title": "x" * 30}})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_publish_over_http(client):
    r = client.post("/connector/v1/fake/publish", json=body())
    assert r.status_code == 200
    assert r.json() == {"platform_post_id": "7", "live_url": "http://x/7", "status": "live"}


def test_unknown_connector_and_method(client):
    assert client.post("/connector/v1/nope/publish", json={}).json()["error"]["code"] == "UNKNOWN_CONNECTOR"
    assert client.post("/connector/v1/fake/explode", json={}).json()["error"]["code"] == "UNKNOWN_METHOD"


def test_method_without_capability_is_refused(client):
    r = client.post("/connector/v1/fake/fetch_metrics", json={"platform_post_id": "1", "connection": {}})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "UNKNOWN_METHOD"


def test_kill_switch(client):
    r = client.post("/connector/v1/off/publish", json=body())
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "CONNECTOR_DISABLED"
    assert r.json()["error"]["retryable"] is False


def test_publish_requires_idempotency_key(client):
    payload = body()
    del payload["idempotency_key"]
    r = client.post("/connector/v1/fake/publish", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert r.json()["error"]["details"]["fields"][0]["field"] == "idempotency_key"


# ---------- platform errors -> shared codes ----------
@pytest.mark.parametrize(
    "path,code,retryable",
    [
        ("/limited", "PLATFORM_RATE_LIMIT", True),
        ("/expired", "CONNECTION_AUTH_EXPIRED", False),
        ("/rejected", "PLATFORM_CONTENT_REJECTED", False),
        ("/boom", "PLATFORM_UNAVAILABLE", True),
        ("/timeout", "PLATFORM_UNAVAILABLE", True),
    ],
)
def test_platform_errors_are_mapped(client, path, code, retryable):
    err = client.post("/connector/v1/fake/publish", json=body(path)).json()["error"]
    assert err["code"] == code
    assert err["retryable"] is retryable


def test_429_carries_reset_time(client):
    err = client.post("/connector/v1/fake/publish", json=body("/limited")).json()["error"]
    assert err["details"]["retry_after_s"] == 30.0
