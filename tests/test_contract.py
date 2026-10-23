"""Contract test: every method of the connector contract, on every connector, through the one HTTP door.

For each connector and each of the seven methods:
  * a method the manifest does not list answers UNKNOWN_METHOD (never a crash);
  * a method it lists answers 200 with the documented shape (checked against the mock platforms where one exists).
"""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

METHODS = ["validate", "publish", "get_status", "delete", "fetch_metrics", "refresh_auth", "build_assist_pack"]
POST = {"title": "A long enough title here", "body": "A body that is long enough to pass the checks."}
FULL_RULES = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}

MOCKED = {  # connector -> (destination, connection builder); lemmy and ghost have live tests instead
    "discourse": ({"name": "c-discourse", "connector_key": "discourse"}, lambda u: {"base_url": f"{u}/discourse", "api_key": "mock-discourse-key", "username": "bot"}),
    "devto": ({"name": "c-devto", "connector_key": "devto"}, lambda u: {"base_url": f"{u}/devto", "api_key": "mock-devto-key"}),
    "webhook": ({"name": "c-webhook", "connector_key": "webhook"}, lambda u: {"base_url": f"{u}/partner/hooks/demo-partner", "api_key": "mock-partner-secret"}),
    "hashnode": ({"name": "c-hashnode", "connector_key": "hashnode", "community": "pub-demo"}, lambda u: {"base_url": f"{u}/hashnode/graphql", "api_key": "mock-hashnode-token"}),
    "kit": ({"name": "c-kit", "connector_key": "kit"}, lambda u: {"base_url": f"{u}/kit", "api_key": "mock-kit-key"}),
}


@pytest.fixture()
def client(mock, tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'contract.db'}")
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def call(client, key, method, body):
    return client.post(f"/connector/v1/{key}/{method}", json=body)


def test_every_connector_gates_every_method_by_its_manifest(client):
    registry = client.app.state.registry
    assert {"lemmy", "ghost", "discourse", "devto", "webhook", "assisted", "hashnode", "kit", "feed"} <= registry.keys()
    for key in sorted(registry.keys()):
        manifest = registry.get(key).manifest
        for method in METHODS:
            r = call(client, key, method, {})
            if not manifest.supports(method):
                assert r.status_code == 404 or r.json()["error"]["code"] == "UNKNOWN_METHOD", (key, method)
                assert r.json()["error"]["code"] == "UNKNOWN_METHOD", (key, method)
            else:  # supported, but the body is empty: a clean validation error, not a crash
                assert r.json()["error"]["code"] == "VALIDATION_ERROR" and r.status_code == 422, (key, method, r.text)


@pytest.mark.parametrize("key", sorted(MOCKED))
def test_supported_methods_return_the_documented_shape(client, mock, key):
    dest, make_conn = MOCKED[key]
    dest = {**dest, "rules": FULL_RULES}
    conn = make_conn(mock.url)
    manifest = client.app.state.registry.get(key).manifest

    v = call(client, key, "validate", {"rendition": POST, "destination": dest, "connection": conn})
    assert v.status_code == 200 and v.json()["ok"] is True and v.json()["violations"] == []

    p = call(client, key, "publish", {"rendition": POST, "destination": dest, "connection": conn, "idempotency_key": f"contract-{key}"})
    assert p.status_code == 200 and set(p.json()) == {"platform_post_id", "live_url", "status"}
    post_id = p.json()["platform_post_id"]

    expected = client.app.state.registry.get(key).effective_spec(type("D", (), {"surface": None, "spec": {}})())  .get("certify_status", "live")  # email connectors hold drafts
    s = call(client, key, "get_status", {"platform_post_id": post_id, "connection": conn})
    assert s.status_code == 200 and s.json()["status"] == expected and (s.json()["live_url"] if expected == "live" else s.json()["live_url"] is None)

    if manifest.supports("fetch_metrics"):
        m = call(client, key, "fetch_metrics", {"platform_post_id": post_id, "connection": conn})
        assert m.status_code == 200 and set(m.json()) == {"views", "likes", "comments", "shares", "saves", "watch_time_s", "clicks"}

    d = call(client, key, "delete", {"platform_post_id": post_id, "connection": conn})
    assert d.status_code == 200 and d.json() == {"deleted": True}
    assert call(client, key, "get_status", {"platform_post_id": post_id, "connection": conn}).json()["status"] != "live"


def test_assisted_connector_returns_an_assist_pack(client):
    dest = {"name": "c-assisted", "connector_key": "assisted", "extra": {"submit_url": "https://forum.example/submit"}}
    r = call(client, "assisted", "build_assist_pack", {"rendition": POST, "destination": dest})
    assert r.status_code == 200 and set(r.json()) == {"files", "caption", "deep_link", "checklist"}


def test_every_error_is_the_documented_json_shape(client):
    r = call(client, "nope", "publish", {})
    assert r.status_code == 404 and set(r.json()["error"]) == {"code", "message", "retryable", "details"}
    r = call(client, "discourse", "explode", {})
    assert r.json()["error"]["code"] == "UNKNOWN_METHOD"
