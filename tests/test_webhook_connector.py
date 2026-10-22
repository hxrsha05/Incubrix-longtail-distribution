"""What the signed webhook connector must do. Run against the mock partner site, over a real connection.

Run just these tests:  python -m pytest tests/test_webhook_connector.py -v
"""
import json

import httpx
import pytest
from pydantic import SecretStr

from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.connectors.webhook import WebhookConnector
from app.errors import IncuBrixError
from app.models import Connection, Destination, Rendition

SECRET = "mock-partner-secret"
DEST = Destination(name="partner", connector_key="webhook")
POST = Rendition(title="Hello partner", body="Some body text.", link_url="https://example.com/a", tags=["ai"], canonical_url="https://example.com/orig")


def make(mock, timeout=5.0):
    connector = WebhookConnector(load_manifest(DEFAULT_MANIFEST_DIR / "webhook.yaml"), httpx.Client(timeout=timeout))
    conn = Connection(base_url=f"{mock.url}/partner/hooks/demo-partner", api_key=SecretStr(SECRET))
    return connector, conn


def with_secret(conn, secret):
    return Connection(base_url=conn.base_url, api_key=SecretStr(secret))


# ---------- the happy path ----------
def test_publish_then_status_then_delete(mock):
    connector, conn = make(mock)
    result = connector.publish(POST, DEST, conn, "key-1")
    assert result.status == "live" and result.platform_post_id and result.live_url.startswith("http://partner.mock/")
    assert connector.get_status(result.platform_post_id, conn).status == "live"
    assert connector.delete(result.platform_post_id, conn).deleted is True
    assert connector.get_status(result.platform_post_id, conn).status == "not_found"


def test_delete_of_a_post_that_is_already_gone_is_fine(mock):
    connector, conn = make(mock)
    assert connector.delete("p999999", conn).deleted is True


def test_the_partner_receives_the_post_and_the_idempotency_key(mock):
    connector, conn = make(mock)
    connector.publish(POST, DEST, conn, "key-42")
    received = httpx.get(f"{mock.url}/__control/partner_posts").json()
    assert len(received) == 1
    assert received[0]["key"] == "key-42"  # sent in the X-IncuBrix-Idempotency-Key header
    payload = received[0]["payload"]
    assert payload["event"] == "publish" and payload["idempotency_key"] == "key-42"
    assert payload["rendition"]["title"] == "Hello partner" and payload["rendition"]["canonical_url"] == "https://example.com/orig"


# ---------- the signature ----------
def test_a_wrong_secret_is_rejected_on_every_kind_of_call(mock):
    connector, conn = make(mock)
    made = connector.publish(POST, DEST, conn, "key-1")
    wrong = with_secret(conn, "wrong-secret-xyz")
    for call in (
        lambda: connector.publish(POST, DEST, wrong, "key-2"),
        lambda: connector.get_status(made.platform_post_id, wrong),
        lambda: connector.delete(made.platform_post_id, wrong),
        lambda: connector.reconcile(POST, DEST, wrong, "key-1"),
    ):
        with pytest.raises(IncuBrixError) as exc:
            call()
        assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED" and exc.value.retryable is False
        assert "wrong-secret-xyz" not in json.dumps(exc.value.to_body())  # the secret never appears in an error


def test_a_connection_without_a_secret_is_an_auth_error(mock):
    connector, conn = make(mock)
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(POST, DEST, Connection(base_url=conn.base_url), "k")
    assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED"


# ---------- reconcile: after a timeout, did it happen? ----------
def test_reconcile_finds_our_post_by_idempotency_key(mock):
    connector, conn = make(mock)
    assert connector.reconcile(POST, DEST, conn, "key-7") is None  # nothing yet: safe to post
    made = connector.publish(POST, DEST, conn, "key-7")
    found = connector.reconcile(POST, DEST, conn, "key-7")
    assert found is not None and found.platform_post_id == made.platform_post_id
    assert connector.reconcile(POST, DEST, conn, "another-key") is None


def test_after_a_real_timeout_reconcile_finds_the_post_that_was_created(mock):
    mock.fail(platform="partner", op="create", kind="timeout_after", delay_s=1.0)
    connector, conn = make(mock, timeout=0.3)
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(POST, DEST, conn, "key-9")
    assert exc.value.code.value == "PLATFORM_UNAVAILABLE" and exc.value.details["outcome_unknown"] is True
    assert mock.state()["partner_posts"] == 1  # the partner DID create it
    patient, conn = make(mock, timeout=5.0)
    assert patient.reconcile(POST, DEST, conn, "key-9") is not None  # so we find it instead of posting again


# ---------- errors ----------
def test_rate_limit_carries_the_reset_time(mock):
    mock.fail(platform="partner", op="create", kind="status", status=429, retry_after=15)
    connector, conn = make(mock)
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(POST, DEST, conn, "k")
    assert exc.value.code.value == "PLATFORM_RATE_LIMIT" and exc.value.retryable is True
    assert exc.value.details["retry_after_s"] == 15.0


def test_a_partner_who_revokes_permission_is_an_auth_error_not_a_retry(mock):
    mock.fail(platform="partner", op="create", kind="status", status=410)
    connector, conn = make(mock)
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(POST, DEST, conn, "k")
    assert exc.value.code.value == "CONNECTION_AUTH_EXPIRED" and exc.value.retryable is False


def test_a_server_error_means_we_do_not_know_what_happened(mock):
    mock.fail(platform="partner", op="create", kind="status", status=500)
    connector, conn = make(mock)
    with pytest.raises(IncuBrixError) as exc:
        connector.publish(POST, DEST, conn, "k")
    assert exc.value.code.value == "PLATFORM_UNAVAILABLE" and exc.value.details["outcome_unknown"] is True


# ---------- checking content before posting ----------
def test_validate_uses_the_manifest_limits(mock):
    connector, conn = make(mock)
    assert connector.validate(Rendition(title="t" * 301), DEST, conn).violations[0].limit == 300
    assert connector.validate(POST, DEST, conn).ok is True
