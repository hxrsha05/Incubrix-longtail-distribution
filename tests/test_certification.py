"""Certification harness: runs against the mock platforms over real HTTP, with evidence and demotion."""
import json
import time

import httpx
import pytest
from sqlalchemy import create_engine

from app.catalogue import Catalogue
from app.certification import certify
from app.certify_service import CertificationService
from app.connectors.base import BaseConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR, build_registry
from app.credentials import CredentialProvider
from app.db import init_db
from app.errors import IncuBrixError
from app.evidence import EvidenceStore
from app.models import Connection, DeleteResult, Destination, PublishResult, StatusResult


@pytest.fixture()
def env(mock, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cert.db'}")
    init_db(engine)
    catalogue = Catalogue(engine)
    registry = build_registry(httpx.Client(timeout=5.0), set())
    sleeps: list[float] = []
    service = CertificationService(
        catalogue, registry, CredentialProvider(None, True, mock.url), EvidenceStore(tmp_path / "evidence"),
        ttl_s=600, simulation=True, max_consecutive_failures=3, sleep=sleeps.append,
    )

    class Env:
        pass

    e = Env()
    e.catalogue, e.service, e.sleeps, e.mock, e.tmp = catalogue, service, sleeps, mock, tmp_path
    return e


def add(env, name, connector_key, **kw):
    if connector_key == "hashnode":
        kw.setdefault("community", "pub-demo")
    row, _ = env.catalogue.upsert(Destination(name=name, connector_key=connector_key, **kw), language="en")
    return row["id"]


def live_topics(mock):
    r = httpx.get(f"{mock.url}/discourse/topics/created-by/bot.json", headers={"Api-Key": "mock-discourse-key", "Api-Username": "bot"})
    return r.json()["topic_list"]["topics"]


def step_names(record):
    return [s["name"] for s in record["steps"]]


# ---------------------------------------------------------------- passes
@pytest.mark.parametrize("connector_key", ["discourse", "devto", "webhook", "hashnode", "kit"])
def test_full_certification_passes_and_stores_evidence(env, connector_key):
    dest_id = add(env, f"{connector_key} test", connector_key)
    record = env.service.certify_destination(dest_id)
    assert record["verdict"] == "passed", record.get("failure")
    names = step_names(record)
    assert names[:3] == ["validate", "publish", "confirm_live"] and "status" in names and names[-2:] == ["delete", "confirm_deleted"]
    assert record["test_post"]["deleted"] is True and record["test_post"]["deleted_after_s"] <= record["test_post"]["limit_s"]
    assert env.catalogue.get(dest_id)["status"] == "certified" and env.catalogue.get(dest_id)["last_certified_at"]
    files = {p.name for p in (env.tmp / "evidence" / dest_id).iterdir()}
    assert {f"run-{record['run_id']}.json", f"run-{record['run_id']}.txt"} <= files
    saved = json.loads((env.tmp / "evidence" / dest_id / f"run-{record['run_id']}.json").read_text())
    assert saved["manifest"]["versions"]["connector"] and saved["manifest"]["cost"]["usd"] == 0.0 and saved["manifest"]["licences"]


def test_devto_confirms_unpublished_not_erased(env):
    record = env.service.certify_destination(add(env, "dev to", "devto"))
    gone = next(s for s in record["steps"] if s["name"] == "confirm_deleted")
    assert gone["detail"]["status_after_delete"] == "draft" and "no delete" in gone["detail"]["note"]


def test_assisted_destination_certifies_without_posting(env):
    record = env.service.certify_destination(add(env, "by hand", "assisted", extra={"submit_url": "https://forum.example/submit"}))
    assert record["verdict"] == "passed" and step_names(record) == ["build_assist_pack"] and "test_post" not in record


# ---------------------------------------------------------------- the hard cases
def test_a_renamed_field_fails_certification_demotes_and_leaves_no_stray_post(env):
    dest_id = add(env, "forum", "discourse")
    env.mock.fail(platform="discourse", kind="rename_field", rename={"topic_id": "topic_identifier"})
    record = env.service.certify_destination(dest_id)
    assert record["verdict"] == "failed" and record["failure"]["kind"] == "RESPONSE_SHAPE_CHANGED"
    assert record["failure"]["details"]["missing_field"] == "topic_id"
    row = env.catalogue.get(dest_id)
    assert row["status"] == "demoted" and "topic_id" in row["status_reason"]
    assert live_topics(env.mock) == []  # the post the platform did create was found by reconcile and deleted
    assert any("found by reconcile" in w for w in record["warnings"])
    assert record["test_post"]["deleted"] is True


def test_timeout_after_create_still_cleans_up_and_is_inconclusive(env):
    dest_id = add(env, "slow forum", "discourse")
    env.mock.fail(platform="discourse", op="create", kind="timeout_after", delay_s=1.0)
    env.service.registry.http.timeout = httpx.Timeout(0.3)
    record = env.service.certify_destination(dest_id)
    assert record["verdict"] == "inconclusive" and record["failure"]["kind"] == "PLATFORM_UNAVAILABLE"
    assert live_topics(env.mock) == [] and record["test_post"]["deleted"] is True
    assert env.catalogue.get(dest_id)["status"] == "needs_attention"  # an outage is not proof the connector is broken
    env.service.registry.http.timeout = httpx.Timeout(5.0)


def test_rate_limit_is_waited_out_then_retried(env):
    dest_id = add(env, "busy forum", "discourse")
    env.mock.fail(platform="discourse", op="create", kind="status", status=429, retry_after=2)
    record = env.service.certify_destination(dest_id)
    assert record["verdict"] == "passed" and env.sleeps == [2.0]
    assert next(s for s in record["steps"] if s["name"] == "publish")["detail"]["attempts"] == 2


def test_a_long_rate_limit_wait_is_capped(env):
    dest_id = add(env, "very busy forum", "discourse")
    env.mock.fail(platform="discourse", op="create", kind="status", status=429, retry_after=3600)
    env.service.certify_destination(dest_id)
    assert env.sleeps == [30.0]


def test_wrong_credentials_are_inconclusive_not_a_demotion(env):
    dest_id = add(env, "no access", "discourse", extra={"connection_ref": "missing"})
    record = env.service.certify_destination(dest_id)
    assert record["verdict"] == "inconclusive" and record["failure"]["kind"] == "CONNECTION_AUTH_EXPIRED"
    assert env.catalogue.get(dest_id)["status"] == "needs_attention"


def test_a_platform_that_rejects_our_valid_post_is_demoted(env):
    dest_id = add(env, "picky", "discourse")
    env.mock.fail(platform="discourse", op="create", kind="status", status=422)
    record = env.service.certify_destination(dest_id)
    assert record["verdict"] == "failed" and record["failure"]["kind"] == "PLATFORM_CONTENT_REJECTED"
    assert env.catalogue.get(dest_id)["status"] == "demoted"


def test_a_batch_stops_after_consecutive_failures_on_one_connector(env):
    ids = [add(env, f"forum {i}", "discourse", extra={"connection_ref": "missing"}) for i in range(5)]
    ids.append(add(env, "fine devto", "devto"))
    out = env.service.certify_many(ids)
    assert len(out["results"]) == 4 and len(out["skipped"]) == 2  # 3 failures, then discourse is left alone
    assert out["results"][-1]["verdict"] == "passed"  # another connector is unaffected
    assert out["inconclusive"] == 3 and out["passed"] == 1


# ---------------------------------------------------------------- cleanup deadline (fake connector, fake clock)
class StuckDelete(BaseConnector):
    """Publishes fine, but the platform refuses every delete."""

    def validate(self, rendition, destination, connection):
        return super().validate(rendition, destination, connection)

    def publish(self, rendition, destination, connection, idempotency_key):
        return PublishResult(platform_post_id="p1", live_url="http://x/p1", status="live")

    def get_status(self, platform_post_id, connection):
        return StatusResult(status="live", live_url="http://x/p1")

    def delete(self, platform_post_id, connection):
        self.deletes = getattr(self, "deletes", 0) + 1
        raise IncuBrixError(__import__("app.errors", fromlist=["ErrorCode"]).ErrorCode.PLATFORM_UNAVAILABLE, "down")


def test_cleanup_keeps_trying_until_the_time_limit_then_reports_failure():
    now = [1000.0]
    manifest = load_manifest(DEFAULT_MANIFEST_DIR / "webhook.yaml")
    connector = StuckDelete(manifest, httpx.Client())
    record = certify(connector, Destination(name="d", connector_key="webhook"), Connection(), destination_id="d",
                     clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s), ttl_s=100)
    assert record["verdict"] == "failed" and record["test_post"]["deleted"] is False
    assert record["failure"]["kind"] in ("PLATFORM_UNAVAILABLE", "CLEANUP_FAILED")
    assert 3 <= connector.deletes <= 10 and now[0] - 1000.0 <= 100  # it gave up inside the limit, it did not run forever


# ---------------------------------------------------------------- the HTTP API
@pytest.fixture()
def api(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    get_settings.cache_clear()
    try:
        yield TestClient(create_app())
    finally:
        get_settings.cache_clear()


def wait_job(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_catalogue_api_add_filter_certify_and_download_evidence(api):
    body = {"destination": {"name": "My Forum", "connector_key": "discourse"}, "language": "en", "topics": ["video"]}
    created = api.post("/api/v1/destinations", json=body, headers={"Idempotency-Key": "k1"})
    assert created.status_code == 201 and created.json()["id"] == "my-forum" and created.json()["status"] == "uncertified"
    replay = api.post("/api/v1/destinations", json=body, headers={"Idempotency-Key": "k1"})
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert api.post("/api/v1/destinations", json={**body, "language": "de"}, headers={"Idempotency-Key": "k1"}).json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert api.post("/api/v1/destinations", json={**body, "language": "de"}).status_code == 200  # same name = update
    api.post("/api/v1/destinations", json={"destination": {"name": "Other", "connector_key": "devto"}, "language": "en"})
    assert api.get("/api/v1/destinations").json()["total"] == 2
    assert [d["id"] for d in api.get("/api/v1/destinations?connector_key=devto").json()["items"]] == ["other"]
    assert [d["id"] for d in api.get("/api/v1/destinations?language=de").json()["items"]] == ["my-forum"]
    assert api.get("/api/v1/destinations/nope").json()["error"]["code"] == "NOT_FOUND"

    job = api.post("/api/v1/destinations/my-forum/certify")
    assert job.status_code == 202
    done = wait_job(api, job.json()["job_id"])
    assert done["status"] == "succeeded" and done["result"]["verdict"] == "passed"
    assert api.get("/api/v1/destinations/my-forum").json()["status"] == "certified"
    runs = api.get("/api/v1/destinations/my-forum/certifications").json()["items"]
    assert len(runs) == 1 and runs[0]["verdict"] == "passed"
    link = done["result"]["evidence"]["summary"]["download_url"]
    download = api.get(link)
    assert download.status_code == 200 and "verdict: PASSED" in download.text
    assert api.get("/api/v1/evidence/../app.db").status_code in (404, 422)
    assert api.get("/api/v1/evidence/%2e%2e/api.db").status_code == 404
    assert api.delete("/api/v1/destinations/other").status_code == 204
    assert api.get("/api/v1/destinations").json()["total"] == 1
