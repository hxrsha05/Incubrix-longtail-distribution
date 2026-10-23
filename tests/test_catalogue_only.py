"""A catalogue-only destination is a real community we only LIST. Nothing is ever certified or posted there, by any route."""
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.errors import IncuBrixError
from app.models import Destination


@pytest.fixture()
def api(mock, tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'co.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    monkeypatch.setenv("IMPORT_ALLOW_PRIVATE_HOSTS", "true")
    get_settings.cache_clear()
    client = TestClient(create_app())
    client.mock = mock
    yield client
    get_settings.cache_clear()


def wait(api, job_id):
    for _ in range(200):
        job = api.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def import_catalogue_only(api, mock):
    text = f"name,url,language,topics\nReal Forum,{mock.url}/sites/discourse-real,en,ai\nReal Blog,{mock.url}/sites/plain-real,en,video\n"
    job = api.post("/api/v1/imports?catalogue_only=true", content=text, headers={"Content-Type": "text/csv"}).json()["job_id"]
    result = wait(api, job)["result"]
    assert result["accepted"] == 2 and result["manifest"]["settings"]["catalogue_only"] is True
    return ["real-forum", "real-blog"]


def test_the_importer_marks_what_it_adds_and_a_normal_import_does_not(api, mock):
    ids = import_catalogue_only(api, mock)
    for i in ids:
        assert api.get(f"/api/v1/destinations/{i}").json()["destination"]["extra"]["catalogue_only"] is True
    normal = api.post("/api/v1/imports", content=f"name,url\nNormal,{mock.url}/sites/plain-normal\n", headers={"Content-Type": "text/csv"}).json()["job_id"]
    wait(api, normal)
    assert "catalogue_only" not in api.get("/api/v1/destinations/normal").json()["destination"]["extra"]


def test_certification_of_one_is_refused_with_a_clear_reason_and_nothing_is_posted(api, mock):
    ids = import_catalogue_only(api, mock)
    r = api.post(f"/api/v1/destinations/{ids[0]}/certify")
    job = wait(api, r.json()["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "DEST_SPEC_VIOLATION" and "catalogue only" in job["error"]["message"]
    assert mock.state()["discourse_topics"] == 0 and api.get(f"/api/v1/destinations/{ids[0]}/certifications").json()["items"] == []
    assert api.get(f"/api/v1/destinations/{ids[0]}").json()["status"] == "uncertified"


def test_a_batch_certification_skips_them_and_says_so(api, mock):
    import_catalogue_only(api, mock)
    api.post("/api/v1/destinations", json={"destination": {"name": "Ours", "connector_key": "discourse", "rules": {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}}})
    job = wait(api, api.post("/api/v1/jobs", json={"kind": "certify_batch", "params": {}}).json()["job_id"])
    result = job["result"]
    assert result["passed"] == 1 and len(result["results"]) == 1 and result["results"][0]["destination_id"] == "ours"
    assert sorted(s["destination_id"] for s in result["skipped"]) == ["real-blog", "real-forum"] and all("catalogue only" in s["reason"] for s in result["skipped"])
    assert mock.state()["discourse_topics"] == 1  # only our own test post was made, and deleted


def test_publishing_to_one_is_blocked_whatever_the_route(api, mock):
    ids = import_catalogue_only(api, mock)
    body = {"destination_id": ids[0], "rendition": {"title": "A long enough title for this", "body": "A body that is comfortably longer than twenty characters."}}
    r = api.post("/api/v1/publish", json=body, headers={"Idempotency-Key": "co-1"})
    assert r.status_code == 422 and r.json()["error"]["details"]["violations"][0]["rule"] == "catalogue_only" and mock.state()["discourse_topics"] == 0
    contract = api.post("/connector/v1/discourse/publish", json={"rendition": body["rendition"], "idempotency_key": "co-2", "connection": {"base_url": f"{mock.url}/discourse", "api_key": "mock-discourse-key", "username": "bot"},
                                                                "destination": {"name": "Real Forum", "connector_key": "discourse", "rules": {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}}})
    assert contract.status_code == 422 and contract.json()["error"]["details"]["violations"][0]["rule"] == "catalogue_only" and mock.state()["discourse_topics"] == 0


def test_they_stay_level_zero_never_recertify_and_are_still_recommendable(api, mock):
    ids = import_catalogue_only(api, mock)
    assert api.get(f"/api/v1/destinations/{ids[0]}").json()["record"]["level"] == "L0"
    assert api.get("/api/v1/recertification").json()["due_now"] == []
    items = api.post("/api/v1/recommend", json={"rendition": {"title": "AI video", "body": "ai", "tags": ["ai"]}}).json()["items"]
    assert "real-forum" in [i["destination_id"] for i in items]  # a recommendation target: it is only that nothing is ever sent to it automatically
