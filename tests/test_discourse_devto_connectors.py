"""Discourse and Dev.to connectors against the mock platform server, over a real connection."""
import json

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.devto import DevToConnector, clean_tags
from app.connectors.discourse import DiscourseConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.errors import IncuBrixError
from app.models import Connection, Destination, Rendition

TITLE = "A long enough title for Discourse"
BODY = "A body that is comfortably longer than twenty characters."
POST = Rendition(title=TITLE, body=BODY, link_url="https://example.com/a", tags=["AI", "Video Tools"], canonical_url="https://example.com/orig")


def discourse(mock, timeout=5.0, key="mock-discourse-key"):
    connector = DiscourseConnector(load_manifest(DEFAULT_MANIFEST_DIR / "discourse.yaml"), httpx.Client(timeout=timeout))
    conn = Connection(base_url=f"{mock.url}/discourse", api_key=SecretStr(key), username="bot")
    return connector, conn, Destination(name="forum", connector_key="discourse")


def devto(mock, timeout=5.0, key="mock-devto-key"):
    connector = DevToConnector(load_manifest(DEFAULT_MANIFEST_DIR / "devto.yaml"), httpx.Client(timeout=timeout))
    conn = Connection(base_url=f"{mock.url}/devto", api_key=SecretStr(key))
    return connector, conn, Destination(name="devto", connector_key="devto")


def code(exc):
    return exc.value.code.value


# ------------------------------------------------------------------ Discourse
def test_discourse_publish_status_metrics_delete(mock):
    c, conn, dest = discourse(mock)
    made = c.publish(POST, dest, conn, "k1")
    assert made.status == "live" and made.live_url.endswith(f"/{made.platform_post_id}")
    assert c.get_status(made.platform_post_id, conn).status == "live"
    mock.set_metrics("discourse", int(made.platform_post_id), views=42, like_count=7)
    metrics = c.fetch_metrics(made.platform_post_id, conn, None)
    assert (metrics.views, metrics.likes) == (42, 7)
    assert c.delete(made.platform_post_id, conn).deleted is True
    assert c.get_status(made.platform_post_id, conn).status == "not_found"
    assert c.delete(made.platform_post_id, conn).deleted is True  # already gone is fine


def test_discourse_content_rules_become_a_rejection(mock):
    c, conn, dest = discourse(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(Rendition(title="short", body=BODY), dest, conn, "k")
    assert code(exc) == "PLATFORM_CONTENT_REJECTED" and exc.value.retryable is False


def test_discourse_wrong_key_is_an_auth_error_and_not_leaked(mock):
    c, conn, dest = discourse(mock, key="wrong-key-xyz")
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED" and "wrong-key-xyz" not in json.dumps(exc.value.to_body())


def test_discourse_missing_key_or_username(mock):
    c, conn, dest = discourse(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, Connection(base_url=conn.base_url, username="bot"), "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED"
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, Connection(base_url=conn.base_url, api_key=SecretStr("mock-discourse-key")), "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED"


def test_discourse_rate_limit_carries_reset_time(mock):
    mock.fail(platform="discourse", op="create", kind="status", status=429, retry_after=15)
    c, conn, dest = discourse(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "PLATFORM_RATE_LIMIT" and exc.value.retryable and exc.value.details["retry_after_s"] == 15.0


def test_discourse_timeout_after_create_is_found_by_reconcile(mock):
    mock.fail(platform="discourse", op="create", kind="timeout_after", delay_s=1.0)
    c, conn, dest = discourse(mock, timeout=0.3)
    assert c.reconcile(POST, dest, conn, "k9") is None  # nothing yet: safe to post
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k9")
    assert code(exc) == "PLATFORM_UNAVAILABLE" and exc.value.details["outcome_unknown"] is True
    patient, conn, dest = discourse(mock)
    assert patient.reconcile(POST, dest, conn, "k9") is not None  # found, so we do not post twice
    assert mock.state()["discourse_topics"] == 1


def test_discourse_validate_uses_manifest_limits(mock):
    c, conn, dest = discourse(mock)
    assert c.validate(Rendition(title="t" * 256), dest, conn).violations[0].limit == 255


# ------------------------------------------------------------------ Dev.to
def test_devto_publish_status_metrics_and_delete_is_unpublish(mock):
    c, conn, dest = devto(mock)
    made = c.publish(POST, dest, conn, "k1")
    assert made.status == "live" and made.live_url.startswith("http://devto.mock/")
    assert c.get_status(made.platform_post_id, conn).status == "live"
    mock.set_metrics("devto", int(made.platform_post_id), views=10, likes=3, comments=1)
    metrics = c.fetch_metrics(made.platform_post_id, conn, None)
    assert (metrics.views, metrics.likes, metrics.comments) == (10, 3, 1)
    assert c.delete(made.platform_post_id, conn).deleted is True
    after = c.get_status(made.platform_post_id, conn)
    assert after.status == "draft" and after.live_url is None  # unpublished, not erased: Dev.to has no delete
    assert c.get_status("999999", conn).status == "not_found"


def test_devto_tags_are_cleaned_and_limited():
    assert clean_tags(["AI", "Video Tools", "x-y", "d", "e"]) == "ai,videotools,xy,d"


def test_devto_five_tags_never_reach_the_platform(mock):
    c, conn, dest = devto(mock)
    made = c.publish(Rendition(title="T", body="b", tags=list("abcdef")), dest, conn, "k")
    assert made.status == "live"  # the mock would answer 422 for five tags


def test_devto_wrong_key_and_missing_key(mock):
    c, conn, dest = devto(mock, key="wrong-devto-xyz")
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED" and "wrong-devto-xyz" not in json.dumps(exc.value.to_body())
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, Connection(base_url=conn.base_url), "k")
    assert code(exc) == "CONNECTION_AUTH_EXPIRED"


def test_devto_blank_title_is_rejected_by_the_platform(mock):
    c, conn, dest = devto(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(Rendition(title=" ", body="b"), dest, conn, "k")
    assert code(exc) == "PLATFORM_CONTENT_REJECTED"


def test_devto_timeout_after_create_is_found_by_reconcile(mock):
    mock.fail(platform="devto", op="create", kind="timeout_after", delay_s=1.0)
    c, conn, dest = devto(mock, timeout=0.3)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k9")
    assert exc.value.details["outcome_unknown"] is True
    patient, conn, dest = devto(mock)
    found = patient.reconcile(POST, dest, conn, "k9")
    assert found is not None and mock.state()["devto_articles"] == 1
    assert patient.reconcile(Rendition(title="Another"), dest, conn, "k") is None


def test_devto_server_error_means_outcome_unknown(mock):
    mock.fail(platform="devto", op="create", kind="status", status=500)
    c, conn, dest = devto(mock)
    with pytest.raises(IncuBrixError) as exc:
        c.publish(POST, dest, conn, "k")
    assert code(exc) == "PLATFORM_UNAVAILABLE" and exc.value.details["outcome_unknown"] is True
