"""The mock platform server itself must behave, or every test built on it is meaningless."""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from mock_platforms.main import create_app

DISCOURSE = {"Api-Key": "mock-discourse-key", "Api-Username": "bot"}
DEVTO = {"api-key": "mock-devto-key"}
TITLE = "A long enough title"
RAW = "A body that is longer than twenty characters."


@pytest.fixture()
def client():
    return TestClient(create_app())


def fail(client, **rule):
    assert client.post("/__control/fail", json=rule).status_code == 200


# ---------------------------------------------------------------- Discourse
def test_discourse_create_read_list_delete(client):
    made = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW}).json()
    assert {"id", "topic_id", "topic_slug"} <= set(made)
    assert client.get(f"/discourse/t/{made['topic_id']}.json").json()["title"] == TITLE
    listing = client.get("/discourse/topics/created-by/bot.json", headers=DISCOURSE).json()["topic_list"]["topics"]
    assert [t["id"] for t in listing] == [made["topic_id"]]
    assert client.delete(f"/discourse/t/{made['topic_id']}.json", headers=DISCOURSE).status_code == 200
    assert client.get(f"/discourse/t/{made['topic_id']}.json").status_code == 404


def test_discourse_rejects_bad_key_short_title_and_duplicate_title(client):
    assert client.post("/discourse/posts.json", headers={"Api-Key": "wrong", "Api-Username": "bot"}, json={"title": TITLE, "raw": RAW}).status_code == 403
    short = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": "short", "raw": RAW})
    assert short.status_code == 422 and "too short" in short.json()["errors"][0]
    client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW})
    dup = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW})
    assert dup.status_code == 422 and "already been used" in dup.json()["errors"][0]


# ---------------------------------------------------------------- Dev.to
def test_devto_draft_publish_unpublish(client):
    draft = client.post("/devto/api/articles", headers=DEVTO, json={"article": {"title": "T", "body_markdown": "b", "tags": "ai,video"}}).json()
    assert draft["published"] is False and draft["url"] is None
    assert client.get(f"/devto/api/articles/{draft['id']}").status_code == 404  # a draft is not public
    live = client.put(f"/devto/api/articles/{draft['id']}", headers=DEVTO, json={"article": {"published": True}}).json()
    assert live["published"] is True and live["url"]
    assert client.get(f"/devto/api/articles/{draft['id']}").status_code == 200
    back = client.put(f"/devto/api/articles/{draft['id']}", headers=DEVTO, json={"article": {"published": False}}).json()
    assert back["published"] is False  # the only "delete" Dev.to offers is unpublishing
    assert [a["id"] for a in client.get("/devto/api/articles/me/all", headers=DEVTO).json()] == [draft["id"]]


def test_devto_validation_and_auth(client):
    assert client.post("/devto/api/articles", headers={"api-key": "x"}, json={"article": {"title": "T"}}).status_code == 401
    assert client.post("/devto/api/articles", headers=DEVTO, json={"article": {"title": " "}}).status_code == 422
    five = client.post("/devto/api/articles", headers=DEVTO, json={"article": {"title": "T", "tags": "a,b,c,d,e"}})
    assert five.status_code == 422 and "4 tags" in five.json()["error"]


# ---------------------------------------------------------------- partner webhook
def sign(body: bytes, secret="mock-partner-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_partner_accepts_only_correctly_signed_bodies(client):
    body = json.dumps({"event": "publish", "title": "hi"}).encode()
    ok = client.post("/partner/hooks/demo-partner", content=body, headers={"X-IncuBrix-Signature": sign(body), "X-IncuBrix-Idempotency-Key": "k1"})
    assert ok.status_code == 201
    post_id = ok.json()["id"]
    empty = {"X-IncuBrix-Signature": sign(b"")}  # GET and DELETE sign an empty body
    assert client.get("/partner/hooks/demo-partner/by-key/k1", headers=empty).json()["id"] == post_id
    assert client.get("/partner/hooks/demo-partner/by-key/k1").status_code == 401  # unsigned reads are refused too
    assert client.post("/partner/hooks/demo-partner", content=body, headers={"X-IncuBrix-Signature": sign(body, "wrong")}).status_code == 401
    assert client.post("/partner/hooks/demo-partner", content=body + b" ", headers={"X-IncuBrix-Signature": sign(body)}).status_code == 401  # tampered
    assert client.post("/partner/hooks/demo-partner", content=body).status_code == 401  # unsigned
    assert client.delete(f"/partner/hooks/demo-partner/posts/{post_id}").status_code == 401  # unsigned delete refused
    assert client.delete(f"/partner/hooks/demo-partner/posts/{post_id}", headers=empty).status_code == 204
    assert client.get(f"/partner/hooks/demo-partner/posts/{post_id}", headers=empty).status_code == 404


# ---------------------------------------------------------------- failure switches
def test_status_failure_fires_once_with_a_reset_time_then_clears(client):
    fail(client, platform="discourse", op="create", kind="status", status=429, retry_after=15)
    first = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW})
    assert first.status_code == 429 and first.headers["Retry-After"] == "15" and first.json()["extras"]["wait_seconds"] == 15
    assert client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW}).status_code == 200


def test_failure_only_hits_the_chosen_platform_and_operation(client):
    fail(client, platform="devto", op="create", kind="status", status=500)
    assert client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW}).status_code == 200  # other platform
    assert client.get("/devto/api/articles/me/all", headers=DEVTO).status_code == 200  # other operation
    assert client.post("/devto/api/articles", headers=DEVTO, json={"article": {"title": "T"}}).status_code == 500


def test_timeout_after_creates_the_post_but_answers_late(client):
    fail(client, platform="discourse", op="create", kind="timeout_after", delay_s=0.3)
    t = time.time()
    client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW})
    assert time.time() - t >= 0.25
    assert client.get("/__control/state").json()["discourse_topics"] == 1  # it was created


def test_timeout_before_answers_late_and_creates_nothing(client):
    fail(client, platform="discourse", op="create", kind="timeout_before", delay_s=0.3)
    t = time.time()
    r = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW})
    assert time.time() - t >= 0.25 and r.status_code == 504
    assert client.get("/__control/state").json()["discourse_topics"] == 0


def test_renamed_field_stays_renamed_until_reset(client):
    fail(client, platform="discourse", kind="rename_field", rename={"topic_id": "topic_identifier"})
    for i in range(2):  # a counter, not the clock: Windows clocks can return the same time twice in a row
        made = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": f"{TITLE} {i}", "raw": RAW}).json()
        assert "topic_identifier" in made and "topic_id" not in made
    client.post("/__control/reset")
    again = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW}).json()
    assert "topic_id" in again


def test_metrics_can_be_set_for_tests(client):
    made = client.post("/discourse/posts.json", headers=DISCOURSE, json={"title": TITLE, "raw": RAW}).json()
    client.post("/__control/metrics", json={"platform": "discourse", "id": made["topic_id"], "values": {"views": 42, "like_count": 7}})
    topic = client.get(f"/discourse/t/{made['topic_id']}.json").json()
    assert (topic["views"], topic["like_count"]) == (42, 7)
