"""Recertification: who is due, automatic demotion with teeth, promotion back, the scheduler thread."""
import threading
import time

import httpx
import pytest
from sqlalchemy import create_engine

from app.catalogue import Catalogue
from app.certify_service import CertificationService
from app.connectors.registry import build_registry
from app.credentials import CredentialProvider
from app.db import init_db
from app.errors import IncuBrixError
from app.evidence import EvidenceStore
from app.idempotency import IdempotencyStore
from app.models import Connection, Destination, PublishRequest, Rendition
from app.publish_service import PostLog, PublishService
from app.recertify import RecertScheduler, Recertifier
from app.recommender import recommend

DAY = 86400
OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture()
def env(mock, tmp_path):
    clock = Clock()
    engine = create_engine(f"sqlite:///{tmp_path / 'recert.db'}")
    init_db(engine)
    catalogue = Catalogue(engine, clock)
    registry = build_registry(httpx.Client(timeout=5.0), set())
    service = CertificationService(catalogue, registry, CredentialProvider(None, True, mock.url), EvidenceStore(tmp_path / "ev"), ttl_s=600, simulation=True,
                                   clock=clock, sleep=lambda s: None)
    recert = Recertifier(catalogue, service, max_age_s=7 * DAY, demoted_cooldown_s=DAY, attention_retry_s=3600, batch_limit=25, clock=clock)
    publisher = PublishService(registry, IdempotencyStore(engine, 120, clock), PostLog(engine), clock, catalogue)

    class Env:
        pass

    e = Env()
    e.clock, e.catalogue, e.service, e.recert, e.publisher, e.mock, e.registry = clock, catalogue, service, recert, publisher, mock, registry
    return e


def add(env, name, key="discourse", **kw):
    row, _ = env.catalogue.upsert(Destination(name=name, connector_key=key, rules=OPEN, **kw), language="en", topics=["video"])
    return row["id"]


def certified(env, name, key="discourse"):
    dest_id = add(env, name, key)
    assert env.service.certify_destination(dest_id)["verdict"] == "passed"
    return dest_id


def publish(env, name, title="A long enough title here"):
    dest = Destination(name=name, connector_key="discourse", rules=OPEN)
    req = PublishRequest(rendition=Rendition(title=title, body="A body that is long enough to pass the checks."), destination=dest,
                         connection=env.service.credentials.for_destination(dest), idempotency_key=f"k-{title}-{env.clock.now}")
    return env.publisher.publish("discourse", req)


# ---------------------------------------------------------------- who is due
def test_a_freshly_certified_destination_is_not_due_until_it_ages(env):
    dest_id = certified(env, "forum")
    assert env.recert.due() == []
    env.clock.advance(7 * DAY - 1)
    assert env.recert.due() == []
    env.clock.advance(2)
    due = env.recert.due()
    assert [d["destination_id"] for d in due] == [dest_id] and "last certified" in due[0]["reason"]


def test_an_uncertified_destination_is_never_rechecked_automatically(env):
    add(env, "imported forum")  # e.g. straight from a CSV import: certification publishes a test post, so a person starts it
    env.clock.advance(365 * DAY)
    assert env.recert.due() == [] and env.recert.run_once()["checked"] == 0


def test_a_passing_recheck_keeps_it_certified_and_resets_the_clock(env):
    dest_id = certified(env, "forum")
    env.clock.advance(8 * DAY)
    out = env.recert.run_once()
    assert (out["checked"], out["still_certified"], out["demoted"]) == (1, 1, 0) and out["transitions"] == []
    assert env.catalogue.get(dest_id)["last_certified_at"] == env.clock.now and env.recert.due() == []


# ---------------------------------------------------------------- automatic demotion, with consequences
def test_a_platform_change_demotes_and_the_demotion_stops_publishing_and_recommendations(env):
    dest_id = certified(env, "forum")
    assert publish(env, "forum")[0]["status"] == "live"  # works while certified
    env.mock.fail(platform="discourse", kind="rename_field", rename={"topic_id": "topic_identifier"})  # the platform changes under us
    env.clock.advance(8 * DAY)
    out = env.recert.run_once()
    assert (out["demoted"], out["still_certified"]) == (1, 0) and out["transitions"][0]["from"] == "certified" and out["transitions"][0]["to"] == "demoted"
    row = env.catalogue.get(dest_id)
    assert row["status"] == "demoted" and "topic_id" in row["status_reason"]
    with pytest.raises(IncuBrixError) as exc:  # teeth 1: nothing is sent to a demoted destination
        publish(env, "forum", title="Another long enough title")
    assert exc.value.code.value == "DEST_SPEC_VIOLATION" and exc.value.details["violations"][0]["rule"] == "destination_demoted"
    rows = env.catalogue.list()["items"]
    assert recommend(Rendition(title="video", body="video", tags=["video"]), rows) == []  # teeth 2: and it is not suggested


def test_a_demoted_destination_waits_for_the_cooldown_then_is_promoted_back_when_fixed(env):
    dest_id = certified(env, "forum")
    env.mock.fail(platform="discourse", kind="rename_field", rename={"topic_id": "topic_identifier"})
    env.clock.advance(8 * DAY)
    env.recert.run_once()
    assert env.catalogue.get(dest_id)["status"] == "demoted"
    env.mock.reset()  # the platform is back to normal
    env.clock.advance(DAY - 60)
    assert env.recert.due() == []  # too soon: do not hammer a platform that just failed
    env.clock.advance(120)
    out = env.recert.run_once()
    assert out["promoted"] == 1 and out["transitions"][0] == {"destination_id": dest_id, "from": "demoted", "to": "certified", "run": out["transitions"][0]["run"]}
    assert env.catalogue.get(dest_id)["status"] == "certified" and publish(env, "forum", title="Back in business here")[0]["status"] == "live"


def test_a_temporary_problem_means_needs_attention_and_a_retry_within_the_hour(env):
    dest_id = certified(env, "forum")
    env.mock.fail(platform="discourse", op="create", kind="status", status=500)
    env.clock.advance(8 * DAY)
    out = env.recert.run_once()
    assert out["needs_attention"] == 1 and env.catalogue.get(dest_id)["status"] == "needs_attention"
    publish(env, "forum")  # needs_attention is not demotion: posting is still allowed
    env.clock.advance(3599)
    assert env.recert.due() == []
    env.clock.advance(2)
    assert [d["destination_id"] for d in env.recert.due()] == [dest_id]
    assert env.recert.run_once()["promoted"] == 1 and env.catalogue.get(dest_id)["status"] == "certified"


# ---------------------------------------------------------------- limits and dry run
def test_oldest_first_and_a_batch_limit(env):
    ids = []
    for i in range(4):
        ids.append(certified(env, f"forum {i}"))
        env.clock.advance(100)
    env.clock.advance(8 * DAY)
    env.recert.batch_limit = 2
    out = env.recert.run_once()
    assert out["due"] == 4 and out["checked"] == 2 and out["would_check"] == ids[:2]
    assert [d["destination_id"] for d in env.recert.due()] == ids[2:]  # the two checked are fresh again; the rest still wait


def test_force_ignores_ages_and_cooldowns_but_never_includes_uncertified(env):
    fresh = certified(env, "fresh forum")
    add(env, "never certified")
    assert env.recert.due() == []
    assert [d["destination_id"] for d in env.recert.due(force=True)] == [fresh]
    out = env.recert.run_once(force=True)
    assert out["checked"] == 1 and out["force"] is True


def test_a_dry_run_only_lists_what_it_would_check(env):
    dest_id = certified(env, "forum")
    env.clock.advance(8 * DAY)
    out = env.recert.run_once(dry_run=True)
    assert out["checked"] == 0 and out["would_check"] == [dest_id] and env.catalogue.get(dest_id)["last_certified_at"] < env.clock.now - 7 * DAY


def test_the_last_run_is_remembered(env):
    assert env.recert.last_run is None
    env.recert.run_once()
    assert env.recert.last_run["checked"] == 0


# ---------------------------------------------------------------- the scheduler thread
def test_the_scheduler_is_off_at_zero_and_runs_and_stops_otherwise():
    calls = []

    class Fake:
        def run_once(self):
            calls.append(time.time())
            return {"checked": 0, "demoted": 0, "promoted": 0}

    off = RecertScheduler(Fake(), 0)
    off.start()
    assert not off.enabled and calls == []
    on = RecertScheduler(Fake(), 0.05)
    on.start()
    on.start()  # starting twice must not start two threads
    time.sleep(0.4)
    on.stop()
    count = len(calls)
    assert count >= 3 and len([t for t in threading.enumerate() if t.name == "recertify"]) <= 1
    time.sleep(0.2)
    assert len(calls) <= count + 1  # it stopped


def test_one_crashing_run_does_not_kill_the_scheduler():
    calls = []

    class Flaky:
        def run_once(self):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return {"checked": 0, "demoted": 0, "promoted": 0}

    s = RecertScheduler(Flaky(), 0.05)
    s.start()
    time.sleep(0.4)
    s.stop()
    assert len(calls) >= 3


# ---------------------------------------------------------------- the HTTP API
def test_recertification_api(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        client.post("/api/v1/destinations", json={"destination": {"name": "Forum", "connector_key": "discourse", "rules": OPEN}})
        status = client.get("/api/v1/recertification").json()
        assert status["scheduler"] == {"enabled": False, "interval_s": 0} and status["due_now"] == [] and "never rechecked" in status["policy"]["uncertified"]
        job = client.post("/api/v1/recertify?dry_run=true")
        assert job.status_code == 202
        for _ in range(50):
            done = client.get(f"/api/v1/jobs/{job.json()['job_id']}").json()
            if done["status"] != "queued" and done["status"] != "running":
                break
            time.sleep(0.1)
        assert done["status"] == "succeeded" and done["result"]["dry_run"] is True and done["result"]["checked"] == 0
        assert client.get("/api/v1/recertification").json()["last_run"]["dry_run"] is True
    finally:
        get_settings.cache_clear()
