"""Media: a destination only takes the kinds it can really show, and images reach the post."""
import json

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.devto import DevToConnector
from app.connectors.discourse import DiscourseConnector
from app.connectors.ghost import to_html
from app.connectors.hashnode import HashnodeConnector
from app.connectors.lemmy import LemmyConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR, build_registry
from app.models import Connection, Destination, Media, Rendition
from test_lemmy_connector import FakeLemmy

IMG = Media(url="https://cdn.example/a.png", kind="image")
IMG2 = Media(url="https://cdn.example/b.png", kind="image")
VIDEO = Media(url="https://cdn.example/v.mp4", kind="video")
FILE = Media(url="https://cdn.example/f.pdf", kind="file")


@pytest.fixture(scope="module")
def registry():
    return build_registry(httpx.Client(), set())


def validate(registry, key, media):
    connector = registry.get(key)
    dest = Destination(name="d", connector_key=key)
    return connector.validate(Rendition(title="A fine title", body="b", media=media), dest, Connection())


# ---------------------------------------------------------------- what each destination accepts
@pytest.mark.parametrize("key", ["lemmy", "discourse", "devto", "hashnode", "ghost", "kit"])
def test_image_destinations_take_images_and_refuse_video_and_files(registry, key):
    assert validate(registry, key, [IMG]).ok
    v = validate(registry, key, [IMG, VIDEO, FILE])
    assert not v.ok and [x.field for x in v.violations] == ["media[1]", "media[2]"]
    assert "video media is not accepted here" in v.violations[0].reason and "share it as a link" in v.violations[0].reason


@pytest.mark.parametrize("key", ["feed", "webhook", "assisted"])
def test_structured_media_destinations_take_all_kinds(registry, key):
    assert validate(registry, key, [IMG, VIDEO, FILE]).ok


def test_too_many_media_items_is_a_field_level_violation_with_the_limit(registry):
    v = validate(registry, "lemmy", [IMG] * 11)
    assert not v.ok and v.violations[0].field == "media" and v.violations[0].limit == 10 and v.violations[0].actual == 11


def test_no_media_is_always_fine(registry):
    assert all(validate(registry, k, []).ok for k in registry.keys())


def test_a_destination_row_can_override_the_media_rules(registry):
    d = Destination(name="d", connector_key="lemmy", spec={"media_kinds": ["image", "video"], "max_media": 2})
    assert registry.get("lemmy").validate(Rendition(title="A fine title", media=[IMG, VIDEO]), d, Connection()).ok
    assert not registry.get("lemmy").validate(Rendition(title="A fine title", media=[IMG, IMG, IMG]), d, Connection()).ok


# ---------------------------------------------------------------- images reach the platform
def capture(connector_cls, manifest, answer):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    return connector_cls(load_manifest(DEFAULT_MANIFEST_DIR / manifest), httpx.Client(transport=httpx.MockTransport(handler))), seen


POST = Rendition(title="A long enough title here", body="A body that is long enough.", media=[IMG, IMG2, VIDEO])


def test_discourse_embeds_the_images_in_the_post():
    c, seen = capture(DiscourseConnector, "discourse.yaml", lambda r: httpx.Response(200, json={"id": 1, "topic_id": 7, "topic_slug": "s"}))
    c.publish(POST, Destination(name="d", connector_key="discourse"), Connection(base_url="http://f", api_key=SecretStr("k"), username="bot"), "k")
    raw = json.loads(seen[0].content)["raw"]
    assert "![](https://cdn.example/a.png)" in raw and "![](https://cdn.example/b.png)" in raw and "v.mp4" not in raw


def test_devto_and_hashnode_embed_the_images():
    c, seen = capture(DevToConnector, "devto.yaml", lambda r: httpx.Response(201, json={"id": 1, "url": "http://x", "published": True}))
    c.publish(POST, Destination(name="d", connector_key="devto"), Connection(base_url="http://f", api_key=SecretStr("k")), "k")
    assert "![](https://cdn.example/a.png)" in json.loads(seen[0].content)["article"]["body_markdown"]
    h, seen2 = capture(HashnodeConnector, "hashnode.yaml", lambda r: httpx.Response(200, json={"data": {"publishPost": {"post": {"id": "1", "title": "t", "slug": "s", "url": "http://x"}}}}))
    h.publish(POST, Destination(name="d", connector_key="hashnode", community="pub"), Connection(base_url="http://f", api_key=SecretStr("k")), "k")
    assert "![](https://cdn.example/b.png)" in json.loads(seen2[0].content)["variables"]["input"]["contentMarkdown"]


def test_lemmy_uses_the_first_image_as_the_post_address_when_there_is_no_link_and_embeds_the_rest():
    lemmy = FakeLemmy()
    c = LemmyConnector(load_manifest(DEFAULT_MANIFEST_DIR / "lemmy.yaml"), httpx.Client(transport=httpx.MockTransport(lemmy)))
    c.LOGIN_RETRY_DELAY_S = 0
    conn, dest = Connection(base_url="http://lemmy", username="bot", password=SecretStr("right")), Destination(name="d", connector_key="lemmy", community="c")
    c.publish(POST, dest, conn, "k1")
    stored = lemmy.posts[1]
    assert stored["url"] == "https://cdn.example/a.png" and "![](https://cdn.example/b.png)" in stored["body"] and "![](https://cdn.example/a.png)" not in stored["body"]
    c.publish(POST.model_copy(update={"link_url": "https://example.com/story", "title": "Another long title"}), dest, conn, "k2")
    assert lemmy.posts[2]["url"] == "https://example.com/story" and "![](https://cdn.example/a.png)" in lemmy.posts[2]["body"]


def test_ghost_html_embeds_images_safely():
    html = to_html(Rendition(title="t", body="hi", media=[IMG, Media(url='https://x.example/a.png" onerror="alert(1)', kind="image"), Media(url="javascript:alert(1)", kind="image")]))
    assert '<img src="https://cdn.example/a.png" alt="">' in html
    assert "onerror=\"alert" not in html and "javascript:" not in html  # the quote is escaped and only http(s) addresses are used
    assert html.count("<img") == 2  # the javascript: address is dropped altogether


def test_markdown_platforms_ignore_non_http_image_addresses():
    c, seen = capture(DiscourseConnector, "discourse.yaml", lambda r: httpx.Response(200, json={"id": 1, "topic_id": 7, "topic_slug": "s"}))
    bad = Rendition(title="A long enough title here", body="A body that is long enough.", media=[Media(url="javascript:alert(1)", kind="image"), Media(url="data:text/html,x", kind="image")])
    c.publish(bad, Destination(name="d", connector_key="discourse"), Connection(base_url="http://f", api_key=SecretStr("k"), username="bot"), "k")
    assert "![]" not in json.loads(seen[0].content)["raw"]
