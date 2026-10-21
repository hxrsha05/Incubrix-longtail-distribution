"""Lemmy connector against a REAL local Lemmy (the one started by docker compose).

Skipped unless LEMMY_LIVE_URL is set, so the normal test run stays offline:

    LEMMY_LIVE_URL=http://localhost:8536 LEMMY_LIVE_UI=http://localhost:1234 \
    LEMMY_LIVE_USER=incubrix_admin LEMMY_LIVE_PASSWORD=... python -m pytest tests/live -v

Every post these tests create is deleted again, even when a test fails.
"""
import os
import uuid

import httpx
import pytest

from app.connectors.lemmy import LemmyConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.errors import IncuBrixError
from app.models import Connection, Destination, Rendition

pytestmark = pytest.mark.skipif(not os.environ.get("LEMMY_LIVE_URL"), reason="set LEMMY_LIVE_URL to run against a real Lemmy")

BASE = os.environ.get("LEMMY_LIVE_URL", "")
UI = os.environ.get("LEMMY_LIVE_UI", BASE)
COMMUNITY = os.environ.get("LEMMY_LIVE_COMMUNITY", "incubrix_test")


def new_connector() -> LemmyConnector:
    return LemmyConnector(load_manifest(DEFAULT_MANIFEST_DIR / "lemmy.yaml"), httpx.Client(timeout=15))


def conn(password=None) -> Connection:
    return Connection(
        base_url=BASE,
        username=os.environ.get("LEMMY_LIVE_USER", "incubrix_admin"),
        password=password or os.environ.get("LEMMY_LIVE_PASSWORD", ""),
        extra={"ui_base_url": UI},
    )


DEST = Destination(name="live", connector_key="lemmy", community=COMMUNITY)


@pytest.fixture()
def connector():
    return new_connector()


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


def test_publish_confirm_live_status_delete(connector, cleanup):
    rendition = Rendition(title=unique("certify"), body="Automated test post from the IncuBrix connector.", link_url="https://example.com/")
    result = connector.publish(rendition, DEST, conn(), "k-" + uuid.uuid4().hex)
    cleanup.append(result.platform_post_id)
    assert result.status == "live" and result.live_url.startswith(UI)

    page = httpx.get(result.live_url, timeout=15)  # the post is really visible in the Lemmy web UI
    assert page.status_code == 200

    status = connector.get_status(result.platform_post_id, conn())
    assert status.status == "live" and status.live_url == result.live_url

    assert connector.delete(result.platform_post_id, conn()).deleted is True
    assert connector.get_status(result.platform_post_id, conn()).status == "deleted"


def test_reconcile_finds_the_post_we_just_made(connector, cleanup):
    rendition = Rendition(title=unique("reconcile"), body="body")
    assert connector.reconcile(rendition, DEST, conn(), "k") is None  # nothing yet: safe to post
    made = connector.publish(rendition, DEST, conn(), "k")
    cleanup.append(made.platform_post_id)
    found = connector.reconcile(rendition, DEST, conn(), "k")
    assert found is not None and found.platform_post_id == made.platform_post_id


def test_metrics_shape(connector, cleanup):
    made = connector.publish(Rendition(title=unique("metrics")), DEST, conn(), "k")
    cleanup.append(made.platform_post_id)
    m = connector.fetch_metrics(made.platform_post_id, conn(), None)
    assert m.likes >= 0 and m.comments == 0


def test_wrong_password_is_auth_expired(connector):
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(Rendition(title=unique()), DEST, conn(password="definitely-wrong-123"), "k")
    assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED" and exc.value.retryable is False


def test_title_over_the_limit_is_caught_by_validate_and_rejected_by_lemmy(connector):
    too_long = Rendition(title="t" * 201)
    result = connector.validate(too_long, DEST, conn())
    assert result.ok is False and result.violations[0].field == "title" and result.violations[0].limit == 200
    with pytest.raises(IncuBrixError) as exc:  # even if validate were skipped, Lemmy itself refuses
        connector.publish(too_long, DEST, conn(), "k")
    assert exc.value.code.value == "PLATFORM_CONTENT_REJECTED" and exc.value.details["platform_error"] == "invalid_post_title"


def test_body_limit_matches_the_manifest(connector, cleanup):
    ok = connector.publish(Rendition(title=unique("body50k"), body="b" * 50000), DEST, conn(), "k")
    cleanup.append(ok.platform_post_id)
    assert connector.validate(Rendition(title="t", body="b" * 50001), DEST, conn()).ok is False
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(Rendition(title=unique("body50k+1"), body="b" * 50001), DEST, conn(), "k")
    assert exc.value.details["platform_error"] == "invalid_body_field"


def test_unknown_community_is_an_error_not_a_crash(connector):
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(Rendition(title=unique()), Destination(name="x", connector_key="lemmy", community="no_such_community"), conn(), "k")
    assert exc.value.code.value in {"PLATFORM_CONTENT_REJECTED", "PLATFORM_UNAVAILABLE"}


def test_two_connectors_logging_in_within_the_same_second_both_succeed(cleanup):
    """Lemmy 0.19 rejects two logins in one second (duplicate login token); the connector waits and retries."""
    first, second = new_connector(), new_connector()
    for c in (first, second):
        made = c.publish(Rendition(title=unique("samesec")), DEST, conn(), "k")
        cleanup.append(made.platform_post_id)


def test_burst_of_posts_is_not_throttled_on_the_local_instance(connector, cleanup):
    for i in range(10):
        made = connector.publish(Rendition(title=unique(f"burst{i}")), DEST, conn(), "k")
        cleanup.append(made.platform_post_id)
