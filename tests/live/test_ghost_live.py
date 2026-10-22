"""Ghost connector against a REAL local Ghost (the one started by docker compose).

Skipped unless GHOST_LIVE_URL is set:

    GHOST_LIVE_URL=http://127.0.0.1:2368 GHOST_LIVE_HOST=ghost.localhost:2368 GHOST_LIVE_KEY=<id:secret> \
    python -m pytest tests/live/test_ghost_live.py -v

Ghost redirects any request whose Host differs from its configured url (http://ghost.localhost:2368). Browsers and
our containers resolve that name; Windows Python does not, so these tests connect to 127.0.0.1 and send the Host
header explicitly. Every post created here is deleted again, even when a test fails.
"""
import os
import uuid

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.ghost import GhostConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.errors import IncuBrixError
from app.models import Connection, Destination, Rendition

pytestmark = pytest.mark.skipif(not os.environ.get("GHOST_LIVE_URL"), reason="set GHOST_LIVE_URL to run against a real Ghost")

BASE = os.environ.get("GHOST_LIVE_URL", "")
HOST = os.environ.get("GHOST_LIVE_HOST", "")
KEY = os.environ.get("GHOST_LIVE_KEY", "")


def _http() -> httpx.Client:
    hooks = {"request": [lambda r: r.headers.__setitem__("Host", HOST)]} if HOST else {}
    return httpx.Client(timeout=30, event_hooks=hooks)


def conn(key: str | None = None) -> Connection:
    return Connection(base_url=BASE, api_key=SecretStr(KEY if key is None else key))


DEST = Destination(name="live", connector_key="ghost")


@pytest.fixture()
def connector():
    return GhostConnector(load_manifest(DEFAULT_MANIFEST_DIR / "ghost.yaml"), _http())


@pytest.fixture()
def cleanup(connector):
    created: list[str] = []
    yield created
    for post_id in created:
        try:
            connector.delete(post_id, conn())
        except Exception:
            pass


def unique(prefix="live"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def public_page(url: str) -> httpx.Response:
    path = "/" + url.split("//", 1)[1].split("/", 1)[1]
    return _http().get(BASE + path, follow_redirects=False)


def test_publish_confirm_live_status_delete(connector, cleanup):
    title = unique("certify")
    rendition = Rendition(title=title, body="Automated test post from the IncuBrix connector.", canonical_url="https://example.com/original", tags=["incubrix-test"])
    result = connector.publish(rendition, DEST, conn(), "k-" + uuid.uuid4().hex)
    cleanup.append(result.platform_post_id)
    assert result.status == "live" and result.live_url.startswith("http://ghost.localhost:2368/")

    page = public_page(result.live_url)  # the post is really visible on the public blog
    assert page.status_code == 200 and title in page.text

    status = connector.get_status(result.platform_post_id, conn())
    assert status.status == "live" and status.live_url == result.live_url

    assert connector.delete(result.platform_post_id, conn()).deleted is True
    assert connector.get_status(result.platform_post_id, conn()).status == "not_found"
    assert public_page(result.live_url).status_code == 404


def test_reconcile_finds_the_post_by_idempotency_key(connector, cleanup):
    rendition, key = Rendition(title=unique("reconcile"), body="body"), "k-" + uuid.uuid4().hex
    assert connector.reconcile(rendition, DEST, conn(), key) is None  # nothing yet: safe to post
    made = connector.publish(rendition, DEST, conn(), key)
    cleanup.append(made.platform_post_id)
    found = connector.reconcile(rendition, DEST, conn(), key)
    assert found is not None and found.platform_post_id == made.platform_post_id
    assert connector.reconcile(rendition, DEST, conn(), "k-" + uuid.uuid4().hex) is None


def test_draft_is_not_listed_publicly(connector, cleanup):
    """A Ghost draft gets an unlisted preview link (/p/<random-id>/) that works for anyone who has the URL, but the
    draft must not appear on the public site or in its feed."""
    title = unique("draft")
    made = connector.publish(Rendition(title=title), Destination(name="d", connector_key="ghost", extra={"post_status": "draft"}), conn(), "k-" + uuid.uuid4().hex)
    cleanup.append(made.platform_post_id)
    assert made.status == "draft" and "/p/" in made.live_url
    assert connector.get_status(made.platform_post_id, conn()).status == "draft"
    assert connector.get_status(made.platform_post_id, conn()).live_url is None  # a draft has no live URL
    for listing in ("/", "/rss/"):
        assert title not in _http().get(BASE + listing, follow_redirects=True).text


def test_markup_in_the_body_is_escaped_on_the_real_site(connector, cleanup):
    made = connector.publish(Rendition(title=unique("xss"), body='<script>alert("pwned")</script>'), DEST, conn(), "k-" + uuid.uuid4().hex)
    cleanup.append(made.platform_post_id)
    page = public_page(made.live_url).text
    assert "<script>alert(" not in page  # never rendered as a real script tag
    assert "&lt;script&gt;" in page      # shown as visible text instead


def test_wrong_secret_and_garbage_key_are_auth_errors(connector):
    key_id = KEY.split(":")[0]
    for bad in (f"{key_id}:{'00' * 32}", "garbage"):
        with pytest.raises(IncuBrixError) as exc:
            connector.publish(Rendition(title=unique()), DEST, conn(bad), "k")
        assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED" and exc.value.retryable is False


def test_title_over_the_limit(connector):
    too_long = Rendition(title="t" * 256)
    assert connector.validate(too_long, DEST, conn()).violations[0].limit == 255
    with pytest.raises(IncuBrixError) as exc:  # even if validate were skipped, Ghost itself refuses
        connector.publish(too_long, DEST, conn(), "k-" + uuid.uuid4().hex)
    assert exc.value.code.value == "PLATFORM_CONTENT_REJECTED" and "255" in exc.value.details["platform_reason"]


def test_empty_title_is_caught_before_ghost_posts_untitled(connector):
    result = connector.validate(Rendition(title=""), DEST, conn())
    assert result.ok is False and result.violations[0].reason == "title is required"


def test_large_body_is_accepted(connector, cleanup):
    made = connector.publish(Rendition(title=unique("big"), body="word " * 100_000), DEST, conn(), "k-" + uuid.uuid4().hex)
    cleanup.append(made.platform_post_id)
    assert connector.get_status(made.platform_post_id, conn()).status == "live"


def test_burst_of_posts(connector, cleanup):
    for i in range(10):
        made = connector.publish(Rendition(title=unique(f"burst{i}")), DEST, conn(), "k-" + uuid.uuid4().hex)
        cleanup.append(made.platform_post_id)
