"""Ghost connector against a fake Ghost that really verifies the signed token. The real Ghost is tested in tests/live/."""
import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.ghost import GhostConnector, make_admin_token, make_slug, to_html
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.errors import IncuBrixError
from app.models import Connection, Destination, Rendition

KEY_ID = "a" * 24
SECRET_HEX = "0123456789abcdef" * 4  # 64 hex chars
ADMIN_KEY = f"{KEY_ID}:{SECRET_HEX}"


def _unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify_token(token: str) -> dict:
    """What Ghost does: check the signature, the audience and the lifetime. Returns the payload or raises."""
    header_b64, payload_b64, sig_b64 = token.split(".")
    header, payload = json.loads(_unb64(header_b64)), json.loads(_unb64(payload_b64))
    expected = hmac.new(bytes.fromhex(SECRET_HEX), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    assert hmac.compare_digest(expected, _unb64(sig_b64)), "bad signature"
    assert header == {"alg": "HS256", "typ": "JWT", "kid": KEY_ID}
    assert payload["aud"] == "/admin/"
    assert 0 < payload["exp"] - payload["iat"] <= 300
    assert payload["exp"] > time.time()
    return payload


class FakeGhost:
    def __init__(self):
        self.posts: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.fail = None  # (status, body) for the next POST /posts/

    def _err(self, status, type_, message, code=None, context=None):
        return httpx.Response(status, json={"errors": [{"type": type_, "message": message, "code": code, "context": context}]})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Ghost "):
            return self._err(403, "NoPermissionError", "Authorization failed")
        try:
            verify_token(auth[6:])
        except Exception:
            return self._err(401, "UnauthorizedError", "Invalid token: invalid signature", "INVALID_JWT")
        path, method = request.url.path.removeprefix("/ghost/api/admin"), request.method
        if path == "/posts/" and method == "POST":
            if self.fail:
                status, body = self.fail
                return httpx.Response(status, json=body, headers={"Retry-After": "20"} if status == 429 else {})
            p = json.loads(request.content)["posts"][0]
            slug = p["slug"]
            while any(x["slug"] == slug for x in self.posts.values()):
                slug += "-2"  # real Ghost silently renames a duplicate slug
            pid = f"post{len(self.posts) + 1}"
            self.posts[pid] = {**p, "id": pid, "slug": slug, "url": f"http://ghost.test/{slug}/"}
            return httpx.Response(201, json={"posts": [self.posts[pid]]})
        if path.startswith("/posts/slug/") and method == "GET":
            slug = path.split("/")[3]
            hit = next((x for x in self.posts.values() if x["slug"] == slug), None)
            return httpx.Response(200, json={"posts": [hit]}) if hit else self._err(404, "NotFoundError", "Post not found.")
        if path.startswith("/posts/") and method in ("GET", "DELETE"):
            pid = path.split("/")[2]
            if pid not in self.posts:
                return self._err(404, "NotFoundError", "Post not found.")
            if method == "DELETE":
                del self.posts[pid]
                return httpx.Response(204)
            return httpx.Response(200, json={"posts": [self.posts[pid]]})
        return httpx.Response(404)


@pytest.fixture()
def ghost():
    return FakeGhost()


@pytest.fixture()
def connector(ghost):
    return GhostConnector(load_manifest(DEFAULT_MANIFEST_DIR / "ghost.yaml"), httpx.Client(transport=httpx.MockTransport(ghost)))


CONN = Connection(base_url="http://ghost.test", api_key=SecretStr(ADMIN_KEY))
DEST = Destination(name="g", connector_key="ghost")
R = Rendition(title="Hello Ghost", body="First paragraph.\n\nSecond paragraph.", link_url="https://example.com/a", tags=["ai"], canonical_url="https://example.com/orig")


# ---------- token ----------
def test_token_is_a_valid_signed_admin_token():
    payload = verify_token(make_admin_token(ADMIN_KEY))
    assert payload["aud"] == "/admin/"


@pytest.mark.parametrize("bad", ["", "nocolon", "a:b:c", f"{KEY_ID}:not-hex"])
def test_malformed_key_is_rejected_without_echoing_it(bad, connector):
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, DEST, Connection(base_url="http://ghost.test", api_key=SecretStr(bad)), "k")
    assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED"
    if bad:
        assert bad not in json.dumps(exc.value.to_body())


def test_missing_key(connector):
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, DEST, Connection(base_url="http://ghost.test"), "k")
    assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED"


def test_secret_never_appears_in_any_error(connector, ghost):
    ghost.fail = (422, {"errors": [{"type": "ValidationError", "message": "bad", "context": "no"}]})
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, DEST, CONN, "k")
    assert SECRET_HEX not in json.dumps(exc.value.to_body())


# ---------- happy path ----------
def test_publish_status_delete_roundtrip(connector, ghost):
    result = connector.publish(R, DEST, CONN, "key-1")
    assert result.status == "live" and result.live_url.startswith("http://ghost.test/hello-ghost-")
    sent = ghost.posts[result.platform_post_id]
    assert sent["status"] == "published" and sent["canonical_url"] == "https://example.com/orig" and sent["tags"] == [{"name": "ai"}]
    assert connector.get_status(result.platform_post_id, CONN).status == "live"
    assert connector.delete(result.platform_post_id, CONN).deleted is True
    assert connector.get_status(result.platform_post_id, CONN).status == "not_found"


def test_delete_of_an_already_deleted_post_is_fine(connector):
    assert connector.delete("never-existed", CONN).deleted is True


def test_draft_is_a_destination_setting_not_code(connector, ghost):
    result = connector.publish(R, Destination(name="g", connector_key="ghost", extra={"post_status": "draft"}), CONN, "k")
    assert result.status == "draft" or ghost.posts[result.platform_post_id]["status"] == "draft"


def test_invalid_post_status_in_destination_is_refused(connector):
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, Destination(name="g", connector_key="ghost", extra={"post_status": "deleted"}), CONN, "k")
    assert exc.value.code.value == "VALIDATION_ERROR"


# ---------- html / slug ----------
def test_body_is_escaped_so_markup_cannot_be_injected():
    html = to_html(Rendition(title="t", body='<script>alert("x")</script>\n\nline1\nline2', link_url='https://e.com/?a="b"'))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "line1<br>line2" in html and "&quot;" in html


def test_slug_is_deterministic_and_unique_per_key():
    assert make_slug("My Title!", "k1") == make_slug("My Title!", "k1")
    assert make_slug("My Title!", "k1") != make_slug("My Title!", "k2")
    assert make_slug("My Title!", "k1").startswith("my-title-")
    assert make_slug("日本語", "k1").startswith("post-")  # nothing usable in the title: still a valid slug
    assert len(make_slug("x" * 500, "k")) <= 70


# ---------- reconcile (idempotency after a timeout) ----------
def test_reconcile_finds_our_post_by_slug_and_reports_absence(connector):
    assert connector.reconcile(R, DEST, CONN, "key-1") is None
    made = connector.publish(R, DEST, CONN, "key-1")
    found = connector.reconcile(R, DEST, CONN, "key-1")
    assert found is not None and found.platform_post_id == made.platform_post_id
    assert connector.reconcile(R, DEST, CONN, "another-key") is None  # a different request is not "ours"


# ---------- errors ----------
@pytest.mark.parametrize(
    "status,body,code,retryable",
    [
        (401, {"errors": [{"type": "UnauthorizedError", "message": "Invalid token: jwt expired", "code": "INVALID_JWT"}]}, "CONNECTION_AUTH_EXPIRED", False),
        (400, {"errors": [{"type": "BadRequestError", "message": "Invalid token", "code": "INVALID_JWT"}]}, "CONNECTION_AUTH_EXPIRED", False),
        (403, {"errors": [{"type": "NoPermissionError", "message": "Authorization failed"}]}, "CONNECTION_AUTH_EXPIRED", False),
        (422, {"errors": [{"type": "ValidationError", "message": "x", "context": "title exceeds maximum length"}]}, "PLATFORM_CONTENT_REJECTED", False),
        (429, {"errors": [{"type": "TooManyRequestsError", "message": "slow down"}]}, "PLATFORM_RATE_LIMIT", True),
        (500, {"errors": [{"type": "InternalServerError", "message": "boom"}]}, "PLATFORM_UNAVAILABLE", True),
    ],
)
def test_error_mapping(connector, ghost, status, body, code, retryable):
    ghost.fail = (status, body)
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, DEST, CONN, "k")
    assert exc.value.code.value == code and exc.value.retryable is retryable


def test_429_carries_the_reset_time_and_422_the_reason(connector, ghost):
    ghost.fail = (429, {"errors": [{"type": "TooManyRequestsError", "message": "slow"}]})
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, DEST, CONN, "k")
    assert exc.value.details["retry_after_s"] == 20.0
    ghost.fail = (422, {"errors": [{"type": "ValidationError", "message": "x", "context": "title exceeds maximum length"}]})
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, DEST, CONN, "k")
    assert "maximum length" in exc.value.details["platform_reason"] and exc.value.details["platform_error"] == "ValidationError"


def test_server_error_means_outcome_unknown(connector, ghost):
    ghost.fail = (502, {"errors": []})
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(R, DEST, CONN, "k")
    assert exc.value.details["outcome_unknown"] is True


# ---------- validate ----------
def test_validate_limits_and_required_title(connector):
    assert connector.validate(Rendition(title="x" * 256), DEST, CONN).violations[0].limit == 255
    assert connector.validate(Rendition(title="   "), DEST, CONN).violations[0].reason == "title is required"
    assert connector.validate(Rendition(title="ok", body="b" * 1_000_000), DEST, CONN).ok is True
    assert connector.validate(Rendition(title="ok", content_type="video_link"), DEST, CONN).ok is False
