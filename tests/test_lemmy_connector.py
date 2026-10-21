"""Lemmy connector against a fake Lemmy (no network). The real instance is tested in tests/live/."""
import json

import httpx
import pytest

from app.connectors.lemmy import LemmyConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.errors import IncuBrixError
from app.models import Connection, Destination, Rendition


class FakeLemmy:
    def __init__(self):
        self.posts: dict[int, dict] = {}
        self.logins = 0
        self.valid_jwt = "jwt-1"
        self.fail = None  # (status, body) returned by the next /post call
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/api/v3/user/login":
            body = json.loads(request.content)
            if body["password"] != "right":
                return httpx.Response(400, json={"error": "incorrect_login"})
            self.logins += 1
            return httpx.Response(200, json={"jwt": self.valid_jwt})
        if request.headers.get("Authorization") != f"Bearer {self.valid_jwt}":
            return httpx.Response(400, json={"error": "not_logged_in"})
        if path == "/api/v3/community":
            return httpx.Response(200, json={"community_view": {"community": {"id": 5, "name": request.url.params["name"]}}})
        if path == "/api/v3/post" and method == "POST":
            if self.fail:
                status, body = self.fail
                return httpx.Response(status, json=body, headers={"Retry-After": "12"} if status == 429 else {})
            body = json.loads(request.content)
            pid = len(self.posts) + 1
            self.posts[pid] = {"id": pid, "name": body["name"], "body": body.get("body"), "deleted": False, "removed": False, "url": body.get("url")}
            return httpx.Response(200, json={"post_view": {"post": self.posts[pid]}})
        if path == "/api/v3/post" and method == "GET":
            pid = int(request.url.params["id"])
            if pid not in self.posts:
                return httpx.Response(400, json={"error": "couldnt_find_post"})
            return httpx.Response(200, json={"post_view": {"post": self.posts[pid], "counts": {"upvotes": 3, "comments": 2}}})
        if path == "/api/v3/post/delete":
            body = json.loads(request.content)
            self.posts[body["post_id"]]["deleted"] = True
            return httpx.Response(200, json={"post_view": {"post": self.posts[body["post_id"]]}})
        if path == "/api/v3/post/list":
            return httpx.Response(200, json={"posts": [{"post": p, "creator": {"name": "bot"}} for p in self.posts.values()]})
        return httpx.Response(404)


@pytest.fixture()
def lemmy():
    return FakeLemmy()


@pytest.fixture()
def connector(lemmy):
    manifest = load_manifest(DEFAULT_MANIFEST_DIR / "lemmy.yaml")
    return LemmyConnector(manifest, httpx.Client(transport=httpx.MockTransport(lemmy)))


CONN = Connection(base_url="http://lemmy.test", username="bot", password="right", extra={"ui_base_url": "http://ui.test"})
DEST = Destination(name="d", connector_key="lemmy", community="incubrix_test")
RENDITION = Rendition(title="Hello Lemmy", body="body text", link_url="https://example.com/a")


def test_publish_status_delete_roundtrip(connector, lemmy):
    result = connector.publish(RENDITION, DEST, CONN, "k1")
    assert (result.platform_post_id, result.status, result.live_url) == ("1", "live", "http://ui.test/post/1")
    assert lemmy.posts[1]["url"] == "https://example.com/a"

    status = connector.get_status("1", CONN)
    assert status.status == "live" and status.live_url == "http://ui.test/post/1"

    assert connector.delete("1", CONN).deleted is True
    assert connector.get_status("1", CONN).status == "deleted"


def test_status_of_missing_post(connector):
    assert connector.get_status("999", CONN).status == "not_found"


def test_logs_in_once_and_reuses_the_token(connector, lemmy):
    connector.publish(RENDITION, DEST, CONN, "k1")
    connector.publish(Rendition(title="second"), DEST, CONN, "k2")
    assert lemmy.logins == 1


def test_stale_token_triggers_one_relogin(connector, lemmy):
    connector.publish(RENDITION, DEST, CONN, "k1")
    lemmy.valid_jwt = "jwt-2"  # server rotated its keys: the cached token is now stale
    connector.publish(Rendition(title="again"), DEST, CONN, "k2")
    assert lemmy.logins == 2


def test_wrong_password_is_auth_expired(connector):
    bad = Connection(base_url="http://lemmy.test", username="bot", password="wrong")
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(RENDITION, DEST, bad, "k")
    assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED" and exc.value.retryable is False


def test_rate_limit_carries_reset_time(connector, lemmy):
    lemmy.fail = (400, {"error": "rate_limit_error"})
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(RENDITION, DEST, CONN, "k")
    assert exc.value.code.value == "PLATFORM_RATE_LIMIT" and exc.value.retryable is True


def test_http_429_with_retry_after(connector, lemmy):
    lemmy.fail = (429, {})
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(RENDITION, DEST, CONN, "k")
    assert exc.value.code.value == "PLATFORM_RATE_LIMIT" and exc.value.details["retry_after_s"] == 12.0


def test_platform_refusal_is_content_rejected(connector, lemmy):
    lemmy.fail = (400, {"error": "language_not_allowed"})
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(RENDITION, DEST, CONN, "k")
    assert exc.value.code.value == "PLATFORM_CONTENT_REJECTED" and exc.value.details["platform_error"] == "language_not_allowed"
    assert exc.value.retryable is False


def test_reconcile_finds_an_existing_post_and_reports_absence(connector, lemmy):
    assert connector.reconcile(RENDITION, DEST, CONN, "k") is None  # nothing there yet: safe to post
    connector.publish(RENDITION, DEST, CONN, "k")
    found = connector.reconcile(RENDITION, DEST, CONN, "k")
    assert found is not None and found.platform_post_id == "1"
    connector.delete("1", CONN)
    assert connector.reconcile(RENDITION, DEST, CONN, "k") is None  # a deleted post doesn't count


def test_metrics(connector):
    connector.publish(RENDITION, DEST, CONN, "k")
    m = connector.fetch_metrics("1", CONN, None)
    assert (m.likes, m.comments) == (3, 2)


def test_token_connection_skips_login(connector, lemmy):
    from pydantic import SecretStr

    lemmy.valid_jwt = "stored"
    connector.publish(RENDITION, DEST, Connection(base_url="http://lemmy.test", token=SecretStr("stored")), "k")
    assert lemmy.logins == 0


def test_password_never_appears_in_error_details(connector):
    bad = Connection(base_url="http://lemmy.test", username="bot", password="wrong-secret-pw")
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(RENDITION, DEST, bad, "k")
    assert "wrong-secret-pw" not in json.dumps(exc.value.to_body())
