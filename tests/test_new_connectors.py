"""Hashnode, Kit and MRSS feed connectors (the three extra connectors of the Advanced level)."""
import json
import xml.etree.ElementTree as ET

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.feed import FeedConnector, make_guid
from app.connectors.hashnode import HashnodeConnector
from app.connectors.kit import KitConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.errors import IncuBrixError
from app.models import Connection, Destination, Media, Rendition

POST = Rendition(title="Launching our open-source video editor", body="We built a small tool for editing short videos.\n\nTry it today.",
                 link_url="https://example.com/launch", tags=["Video Tools", "AI"], canonical_url="https://example.com/original")
MEDIA = "http://search.yahoo.com/mrss/"


def code(exc):
    return exc.value.code.value


def hashnode(mock, timeout=5.0, token="mock-hashnode-token"):
    c = HashnodeConnector(load_manifest(DEFAULT_MANIFEST_DIR / "hashnode.yaml"), httpx.Client(timeout=timeout))
    return c, Connection(base_url=f"{mock.url}/hashnode/graphql", api_key=SecretStr(token)), Destination(name="blog", connector_key="hashnode", community="pub-demo")


def kit(mock, timeout=5.0, key="mock-kit-key", **extra):
    c = KitConnector(load_manifest(DEFAULT_MANIFEST_DIR / "kit.yaml"), httpx.Client(timeout=timeout))
    return c, Connection(base_url=f"{mock.url}/kit", api_key=SecretStr(key)), Destination(name="newsletter", connector_key="kit", extra=extra)


# ================================================================== Hashnode
def test_hashnode_publish_status_metrics_delete(mock):
    c, conn, dest = hashnode(mock)
    made = c.publish(POST, dest, conn, "k1")
    assert made.status == "live" and made.live_url.startswith("http://hashnode.mock/")
    assert c.get_status(made.platform_post_id, conn).status == "live"
    mock.set_metrics("hashnode", made.platform_post_id, views=40, reactionCount=6, responseCount=2)
    m = c.fetch_metrics(made.platform_post_id, conn, None)
    assert (m.views, m.likes, m.comments) == (40, 6, 2)
    assert c.delete(made.platform_post_id, conn).deleted is True
    assert c.get_status(made.platform_post_id, conn).status == "not_found"
    assert c.delete(made.platform_post_id, conn).deleted is True  # already gone is fine
    assert mock.state()["hashnode_posts"] == 0


def test_hashnode_reads_errors_from_the_body_not_the_http_status(mock):
    c, conn, dest = hashnode(mock, token="wrong-token-xyz")  # the mock answers HTTP 200 with an errors list, like Hashnode
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED" and exc.value.retryable is False and "wrong-token-xyz" not in json.dumps(exc.value.to_body())


def test_hashnode_content_problems_are_rejections(mock):
    c, conn, dest = hashnode(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(Rendition(title="Hi", body="b"), dest, conn, "k")
    assert code(exc) == "PLATFORM_CONTENT_REJECTED" and "6 characters" in exc.value.details["platform_message"]
    with pytest.raises(IncuBrixError) as exc:
        c.publish(Rendition(title="A decent title", body="b", tags=list("abcdefg")), dest, conn, "k")
    assert code(exc) == "PLATFORM_CONTENT_REJECTED"


def test_hashnode_needs_a_publication_and_a_token(mock):
    c, conn, _ = hashnode(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, Destination(name="x", connector_key="hashnode"), conn, "k")
    assert code(exc) == "VALIDATION_ERROR"
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, Destination(name="x", connector_key="hashnode", community="pub-demo"), Connection(base_url=conn.base_url), "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED"
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, Destination(name="x", connector_key="hashnode", community="no-such-publication"), conn, "k")
    assert code(exc) == "NOT_FOUND"


def test_hashnode_timeout_after_create_is_found_by_reconcile(mock):
    mock.fail(platform="hashnode", op="any", kind="timeout_after", delay_s=1.0)
    c, conn, dest = hashnode(mock, timeout=0.3)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k9")
    assert code(exc) == "PLATFORM_UNAVAILABLE" and exc.value.details["outcome_unknown"] is True and mock.state()["hashnode_posts"] == 1
    patient, conn, dest = hashnode(mock)
    assert patient.reconcile(POST, dest, conn, "k9") is not None
    assert patient.reconcile(Rendition(title="Something else entirely"), dest, conn, "k") is None


def test_hashnode_rate_limit_and_server_error(mock):
    c, conn, dest = hashnode(mock)
    mock.fail(platform="hashnode", op="any", kind="status", status=429, retry_after=12)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "PLATFORM_RATE_LIMIT" and exc.value.details["retry_after_s"] == 12.0
    mock.fail(platform="hashnode", op="any", kind="status", status=500)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "PLATFORM_UNAVAILABLE" and exc.value.details["outcome_unknown"] is True


def test_hashnode_renamed_field_surfaces_as_a_key_error_for_certification_to_catch(mock):
    mock.fail(platform="hashnode", kind="rename_field", rename={"publishPost": "createPost"})
    c, conn, dest = hashnode(mock)
    with pytest.raises(KeyError):
        c.publish(POST, dest, conn, "k")


# ================================================================== Kit
def test_kit_publishes_a_draft_and_never_sends(mock):
    c, conn, dest = kit(mock)
    made = c.publish(POST, dest, conn, "k1")
    assert made.status == "draft"
    assert c.get_status(made.platform_post_id, conn).status == "draft"
    assert c.get_status(made.platform_post_id, conn).live_url is None
    mock.set_metrics("kit", int(made.platform_post_id), emails_opened=30, total_clicks=4)
    m = c.fetch_metrics(made.platform_post_id, conn, None)
    assert (m.views, m.clicks) == (30, 4)
    assert c.delete(made.platform_post_id, conn).deleted is True
    assert c.get_status(made.platform_post_id, conn).status == "not_found" and c.delete(made.platform_post_id, conn).deleted is True


def test_kit_html_body_is_escaped(mock):
    c, conn, dest = kit(mock)
    c.publish(Rendition(title="Escaping check", body="<script>alert(1)</script> & more"), dest, conn, "k")
    sent = httpx.get(f"{mock.url}/kit/v4/broadcasts", headers={"X-Kit-Api-Key": "mock-kit-key"}).json()["broadcasts"][0]["content"]
    assert "<script>" not in sent and "&lt;script&gt;" in sent


def test_kit_refuses_to_send_immediately_and_nothing_reaches_the_platform(mock):
    c, conn, _ = kit(mock)
    for mode in ("send_now", "immediately", "publish"):
        with pytest.raises(IncuBrixError) as exc:
            c.publish(POST, Destination(name="n", connector_key="kit", extra={"send_mode": mode}), conn, "k")
        assert code(exc) == "VALIDATION_ERROR"
    assert mock.state()["kit_broadcasts"] == 0


def test_kit_scheduled_send_needs_a_time_and_reports_scheduled(mock):
    c, conn, _ = kit(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, Destination(name="n", connector_key="kit", extra={"send_mode": "schedule"}), conn, "k")
    assert code(exc) == "VALIDATION_ERROR"
    made = c.publish(POST, Destination(name="n", connector_key="kit", extra={"send_mode": "schedule", "send_at": "2099-01-01T09:00:00Z"}), conn, "k")
    assert made.status == "scheduled" and c.get_status(made.platform_post_id, conn).status == "scheduled"
    assert c.delete(made.platform_post_id, conn).deleted is True  # deleting cancels the send


def test_kit_errors(mock):
    c, conn, dest = kit(mock, key="wrong-kit-key")
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED" and "wrong-kit-key" not in json.dumps(exc.value.to_body())
    c, conn, dest = kit(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(Rendition(title=" ", body="b"), dest, conn, "k")
    assert code(exc) == "PLATFORM_CONTENT_REJECTED"
    mock.fail(platform="kit", op="create", kind="status", status=429, retry_after=9)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "PLATFORM_RATE_LIMIT" and exc.value.details["retry_after_s"] == 9.0
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, Connection(base_url=conn.base_url), "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED"


def test_kit_timeout_after_create_is_found_by_reconcile_without_a_duplicate(mock):
    mock.fail(platform="kit", op="create", kind="timeout_after", delay_s=1.0)
    c, conn, dest = kit(mock, timeout=0.3)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k9")
    assert exc.value.details["outcome_unknown"] is True
    patient, conn, dest = kit(mock)
    found = patient.reconcile(POST, dest, conn, "k9")
    assert found is not None and mock.state()["kit_broadcasts"] == 1


# ================================================================== MRSS feed (against our own feed host)
@pytest.fixture()
def feed_env(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'feed.db'}")
    monkeypatch.setenv("SERVICE_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    app = create_app()
    http = TestClient(app)  # a TestClient IS an httpx client: the connector talks to the real routes, in process
    connector = FeedConnector(load_manifest(DEFAULT_MANIFEST_DIR / "feed.yaml"), http)
    conn = Connection(base_url="http://testserver", api_key=SecretStr("local-dev-feed-token"), extra={"feed_id": "videos"})
    try:
        yield connector, conn, Destination(name="feed", connector_key="feed", community="videos"), http
    finally:
        get_settings.cache_clear()


def test_feed_publish_shows_up_in_valid_mrss_and_delete_removes_it(feed_env):
    c, conn, dest, http = feed_env
    post = POST.model_copy(update={"media": [Media(url="https://cdn.example/a.jpg", kind="image", mime_type="image/jpeg"), Media(url="https://cdn.example/v.mp4", kind="video", mime_type="video/mp4")]})
    made = c.publish(post, dest, conn, "k1")
    assert made.status == "live" and made.live_url == f"http://testserver/feeds/videos.xml#{make_guid('k1')}"
    xml = http.get("/feeds/videos.xml")
    assert xml.status_code == 200 and xml.headers["content-type"].startswith("application/rss+xml")
    root = ET.fromstring(xml.text)  # it parses, so it is well-formed XML
    item = root.find("channel/item")
    assert item.find("title").text == POST.title and item.find("guid").text == make_guid("k1") and item.find("link").text == "https://example.com/original"
    contents = item.findall(f"{{{MEDIA}}}content")
    assert [(m.get("url"), m.get("medium"), m.get("type")) for m in contents] == [("https://cdn.example/a.jpg", "image", "image/jpeg"), ("https://cdn.example/v.mp4", "video", "video/mp4")]
    assert item.find(f"{{{MEDIA}}}thumbnail").get("url") == "https://cdn.example/a.jpg" and item.find(f"{{{MEDIA}}}title").text == POST.title
    assert [c_.text for c_ in item.findall("category")] == ["Video Tools", "AI"]
    assert c.get_status(made.platform_post_id, conn).status == "live"
    assert c.delete(made.platform_post_id, conn).deleted is True
    assert c.get_status(made.platform_post_id, conn).status == "not_found" and ET.fromstring(http.get("/feeds/videos.xml").text).find("channel/item") is None
    assert c.delete(made.platform_post_id, conn).deleted is True


def test_feed_same_idempotency_key_is_one_item(feed_env):
    c, conn, dest, http = feed_env
    first, second = c.publish(POST, dest, conn, "same"), c.publish(POST, dest, conn, "same")
    assert first.platform_post_id == second.platform_post_id
    assert len(ET.fromstring(http.get("/feeds/videos.xml").text).findall("channel/item")) == 1


def test_feed_reconcile_is_exact_and_special_characters_are_escaped(feed_env):
    c, conn, dest, http = feed_env
    assert c.reconcile(POST, dest, conn, "k7") is None
    c.publish(Rendition(title="Tom & Jerry <live>", body="1 < 2 & \"quotes\" </description>"), dest, conn, "k7")
    assert c.reconcile(POST, dest, conn, "k7") is not None and c.reconcile(POST, dest, conn, "other") is None
    item = ET.fromstring(http.get("/feeds/videos.xml").text).find("channel/item")
    assert item.find("title").text == "Tom & Jerry <live>" and item.find("description").text == "1 < 2 & \"quotes\" </description>"


def test_feed_writes_need_the_token_but_the_feed_is_public(feed_env):
    c, conn, dest, http = feed_env
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, Connection(base_url=conn.base_url, api_key=SecretStr("nope-nope")), "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED" and "nope-nope" not in json.dumps(exc.value.to_body())
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, Connection(base_url=conn.base_url), "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED"
    assert http.get("/feeds/videos.xml").status_code == 200  # reading needs no token


def test_feed_keeps_only_the_newest_items(feed_env):
    c, conn, dest, http = feed_env
    http.app.state.feeds.max_items = 3
    for i in range(5):
        c.publish(Rendition(title=f"Post number {i}", body="b"), dest, conn, f"k{i}")
    titles = [i.find("title").text for i in ET.fromstring(http.get("/feeds/videos.xml").text).findall("channel/item")]
    assert len(titles) == 3 and "Post number 4" in titles and "Post number 0" not in titles


def test_feeds_are_separate(feed_env):
    c, conn, dest, http = feed_env
    c.publish(POST, dest, conn, "k1")
    assert ET.fromstring(http.get("/feeds/other.xml").text).find("channel/item") is None


def test_kit_sends_every_field_kits_api_reference_marks_as_required(mock):
    c, conn, dest = kit(mock)
    c.publish(POST, dest, conn, "k1")
    sent = httpx.get(f"{mock.url}/kit/v4/broadcasts", headers={"X-Kit-Api-Key": "mock-kit-key"}).json()["broadcasts"][0]
    assert sent["preview_text"].startswith("We built a small tool") and len(sent["preview_text"]) <= 140 and sent["public"] is False and sent["send_at"] is None
    # a request without the required fields is refused by the (modelled) platform, as the real one does
    r = httpx.post(f"{mock.url}/kit/v4/broadcasts", headers={"X-Kit-Api-Key": "mock-kit-key"}, json={"subject": "s", "content": "c"})
    assert r.status_code == 422 and any("preview_text" in e for e in r.json()["errors"])


def test_kit_a_destination_can_choose_the_audience_filter(mock):
    c, conn, _ = kit(mock)
    c.publish(POST, Destination(name="n", connector_key="kit", extra={"subscriber_filter": [{"all": [], "any": [{"type": "tag", "ids": [7]}], "none": []}]}), conn, "k2")
    assert mock.state()["kit_broadcasts"] == 1
