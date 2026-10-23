"""CSV import: validation, normalising, duplicates, reachability, enrichment, the report, the job API."""
import csv
import io
import json
import time

import httpx
import pytest
from sqlalchemy import create_engine

from app.catalogue import Catalogue
from app.csv_import import Importer, dedupe_key, detect_platform, is_private_host, normalize_language, normalize_url, parse_csv, parse_page, report_to_csv
from app.db import init_db
from app.errors import IncuBrixError
from app.models import Destination


# ---------------------------------------------------------------- pure helpers
@pytest.mark.parametrize("raw,expected", [
    ("Example.COM/forum/", "https://example.com/forum"),
    ("http://www.example.com", "http://example.com"),
    ("https://example.com:443/a?utm_source=x&b=2&a=1#top", "https://example.com/a?a=1&b=2"),
    ("https://example.com:8080/", "https://example.com:8080"),
])
def test_urls_are_normalised(raw, expected):
    assert normalize_url(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["", "not a url", "ftp://example.com", "https://", "justaword", "javascript:alert(1)"])
def test_bad_urls_are_refused_with_a_reason(raw):
    url, reason = normalize_url(raw)
    assert url is None and reason


def test_same_site_written_differently_has_the_same_dedupe_key():
    keys = {dedupe_key(normalize_url(u)[0]) for u in ["https://www.Example.com/forum/", "http://example.com/forum", "example.com/forum?utm_campaign=z"]}
    assert len(keys) == 1


@pytest.mark.parametrize("raw,code", [("English", "en"), ("en-US", "en"), ("DE", "de"), ("Deutsch", "de"), ("pt_BR", "pt"), ("", None)])
def test_languages_are_normalised(raw, code):
    assert normalize_language(raw)[0] == code


def test_unknown_language_is_a_warning_not_an_error():
    assert normalize_language("klingon!!") == (None, "language 'klingon!!' not recognised")


def test_page_parsing_and_platform_detection():
    info = parse_page('<html lang="de-DE"><head><title> Mein  Forum </title><meta name="generator" content="Discourse 3.2"><meta name="description" content="Hallo"></head></html>')
    assert info == {"title": "Mein Forum", "lang": "de-DE", "generator": "Discourse 3.2", "description": "Hallo"}
    assert detect_platform("x.example", "Ghost 5.8") == "ghost" and detect_platform("dev.to", None) == "devto" and detect_platform("x.example", None) is None


def test_private_addresses_are_recognised():
    assert is_private_host("127.0.0.1") and is_private_host("10.1.2.3") and is_private_host("169.254.169.254")
    assert not is_private_host("8.8.8.8")


def test_csv_needs_name_and_url_columns():
    with pytest.raises(IncuBrixError) as exc:
        parse_csv("title,link\nA,b\n")
    assert exc.value.code.value == "VALIDATION_ERROR" and "name" in exc.value.message
    assert parse_csv("﻿Name , URL\nA,example.com\n")[0] == {"name": "A", "url": "example.com"}


# ---------------------------------------------------------------- the importer, over real HTTP to the mock sites
@pytest.fixture()
def catalogue(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'import.db'}")
    init_db(engine)
    return Catalogue(engine)


def importer(catalogue, **kw):
    return Importer(catalogue, httpx.Client(), allow_private_hosts=True, fetch_timeout_s=kw.pop("fetch_timeout_s", 3.0), **kw)


def make_csv(rows, header="name,url,language,topics"):
    return header + "\n" + "\n".join(rows) + "\n"


def by_name(report):
    return {r["name"]: r for r in report}


def test_every_kind_of_row_gets_the_right_status_and_reason(mock, catalogue):
    site = lambda n: f"{mock.url}/sites/{n}"
    text = make_csv([
        f"Good Forum,{site('discourse-good')},en,ai;video",
        f"German Blog,{site('lang-de-blog')},English,blog",
        f"A Ghost Site,{site('ghost-news')},,news",
        f"Plain Site,{site('plain')},,",
        f"Dup Of Good,{site('discourse-good/')},en,",           # same site, written differently
        f"good forum,{site('other')},en,",                       # same name, different site
        f"Dead Site,{site('dead-one')},en,",
        f"Gone Site,{site('gone-one')},en,",
        f"Broken Site,{site('boom-one')},en,",
        f"Bad Url,not a url,en,",
        f",{site('noname')},en,",
        "Wrong Scheme,ftp://example.com,en,",
    ])
    out = importer(catalogue).run(text)
    r = by_name(out["report"])
    assert r["Good Forum"]["status"] == "accepted" and r["Good Forum"]["detected_platform"] == "discourse" and r["Good Forum"]["connector_key"] == "discourse"
    assert r["A Ghost Site"]["connector_key"] == "ghost" and r["Plain Site"]["connector_key"] == "assisted"
    assert r["Dup Of Good"]["status"] == "duplicate" and r["Dup Of Good"]["duplicate_of"] == "row 2"
    assert r["good forum"]["status"] == "duplicate" and "same name" in r["good forum"]["reasons"][0]
    assert r["Dead Site"]["status"] == "unreachable" and "404" in r["Dead Site"]["reasons"][0]
    assert "410" in r["Gone Site"]["reasons"][0] and "500" in r["Broken Site"]["reasons"][0]
    assert r["Bad Url"]["status"] == "invalid" and r[""]["status"] == "invalid" and r["Wrong Scheme"]["status"] == "invalid"
    # mixed languages: the file says English, the page says German -> a warning, the file wins
    assert r["German Blog"]["language"] == "en" and r["German Blog"]["detected_language"] == "de"
    assert any("page says 'de'" in w for w in r["German Blog"]["warnings"])
    assert (out["accepted"], out["duplicate"], out["unreachable"], out["invalid"]) == (4, 2, 3, 3)
    assert out["rows"] == 12 and [x["row"] for x in out["report"]][:2] == [2, 3]  # row numbers match the spreadsheet


def test_accepted_rows_become_catalogue_rows_with_conservative_rules(mock, catalogue):
    out = importer(catalogue).run(make_csv([f"Good Forum,{mock.url}/sites/discourse-good,en,ai;video", f"Plain Site,{mock.url}/sites/plain,fr,"]))
    assert out["catalogue_rows_created"] == 2
    forum = catalogue.get("good-forum")
    assert forum["destination"]["connector_key"] == "discourse" and forum["destination"]["base_url"] == mock.url
    assert forum["topics"] == ["ai", "video"] and forum["language"] == "en" and forum["status"] == "uncertified"
    rules = forum["destination"]["rules"]
    assert rules["self_promo_allowed"] is False and rules["link_policy"] == "max:1" and rules["disclosure_required"] is True and rules["frequency_cap"]
    plain = catalogue.get("plain-site")["destination"]
    assert plain["connector_key"] == "assisted" and plain["extra"]["submit_url"].endswith("/sites/plain") and plain["base_url"] is None
    assert plain["extra"]["rules_status"] == "default_conservative_unconfirmed"


def test_dry_run_changes_nothing(mock, catalogue):
    out = importer(catalogue).run(make_csv([f"Good Forum,{mock.url}/sites/discourse-good,en,"]), commit=False)
    assert out["accepted"] == 1 and out["committed"] is False and catalogue.list()["total"] == 0


def test_rows_already_in_the_catalogue_are_duplicates_and_a_second_import_adds_nothing(mock, catalogue):
    text = make_csv([f"Good Forum,{mock.url}/sites/discourse-good,en,", f"Other,{mock.url}/sites/plain,en,"])
    importer(catalogue).run(text)
    again = importer(catalogue).run(text)
    assert again["duplicate"] == 2 and again["accepted"] == 0 and catalogue.list()["total"] == 2
    assert all(r["duplicate_of"].startswith("catalogue:") for r in again["report"])
    catalogue.upsert(Destination(name="Handmade", connector_key="assisted", extra={"submit_url": f"{mock.url}/sites/handmade"}))
    third = importer(catalogue).run(make_csv([f"Another Name,{mock.url}/sites/handmade/,en,"]))
    assert third["report"][0]["status"] == "duplicate" and third["report"][0]["duplicate_of"] == "catalogue: handmade"


def test_a_site_that_blocks_bots_is_kept_with_a_warning(catalogue):
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="no bots"))
    imp = Importer(catalogue, httpx.Client(transport=transport), allow_private_hosts=True)
    out = imp.run(make_csv(["Guarded,https://guarded.example,en,"]))
    assert out["report"][0]["status"] == "accepted" and "403" in out["report"][0]["warnings"][0]


def test_a_timeout_is_retried_once_then_reported(catalogue):
    calls = []

    def handler(request):
        if request.url.path != "/robots.txt":  # only the page itself counts: robots.txt is fetched once per site
            calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    out = Importer(catalogue, httpx.Client(transport=httpx.MockTransport(handler)), allow_private_hosts=True).run(make_csv(["Slow,https://slow.example,en,"]))
    assert out["report"][0]["status"] == "unreachable" and out["report"][0]["reasons"] == ["timed out"] and len(calls) == 2


def test_private_addresses_are_not_fetched_unless_allowed(catalogue):
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, text="ok"))
    imp = Importer(catalogue, httpx.Client(transport=transport), allow_private_hosts=False)
    out = imp.run(make_csv(["Local,http://127.0.0.1:8000/admin,en,", "Metadata,http://169.254.169.254/latest,en,"]))
    assert [r["status"] for r in out["report"]] == ["invalid", "invalid"] and "private" in out["report"][0]["reasons"][0] and seen == []


def test_one_request_per_host_at_a_time(catalogue):
    active, peak = [0], [0]

    def handler(request):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        active[0] -= 1
        return httpx.Response(200, text="<html lang='en'><title>t</title></html>")

    rows = [f"Site {i},https://same-host.example/community-{i},en," for i in range(6)]
    Importer(catalogue, httpx.Client(transport=httpx.MockTransport(handler)), allow_private_hosts=True, workers=6).run(make_csv(rows))
    assert peak[0] == 1


def test_report_csv_has_one_line_per_row_with_reasons(mock, catalogue):
    out = importer(catalogue).run(make_csv([f"Good,{mock.url}/sites/plain,en,", f"Dead,{mock.url}/sites/dead-x,en,", "Bad,not a url,en,"]))
    rows = list(csv.DictReader(io.StringIO(report_to_csv(out["report"]))))
    assert [r["status"] for r in rows] == ["accepted", "unreachable", "invalid"] and rows[1]["reasons"] and rows[0]["row"] == "2"


def test_the_output_manifest_is_attached(mock, catalogue):
    out = importer(catalogue).run(make_csv([f"Good,{mock.url}/sites/plain,en,"]))
    assert out["manifest"]["kind"] == "csv_import" and out["manifest"]["cost"]["usd"] == 0.0 and out["manifest"]["settings"]["workers"] == 32


# ---------------------------------------------------------------- the HTTP API
@pytest.fixture()
def api(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path / "imports"))
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("IMPORT_ALLOW_PRIVATE_HOSTS", "true")
    get_settings.cache_clear()
    try:
        yield TestClient(create_app())
    finally:
        get_settings.cache_clear()


def wait_job(client, job_id, seconds=30):
    for _ in range(seconds * 10):
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_upload_a_csv_get_a_job_then_the_report(api, mock):
    text = make_csv([f"Good Forum,{mock.url}/sites/discourse-good,en,ai", f"Dead,{mock.url}/sites/dead-a,en,", "Bad,not a url,en,"])
    resp = api.post("/api/v1/imports", content=text, headers={"Content-Type": "text/csv", "Idempotency-Key": "imp-1"})
    assert resp.status_code == 202 and resp.json()["rows"] == 3
    replay = api.post("/api/v1/imports", content=text, headers={"Content-Type": "text/csv", "Idempotency-Key": "imp-1"})
    assert replay.headers["Idempotent-Replayed"] == "true" and replay.json()["job_id"] == resp.json()["job_id"]
    done = wait_job(api, resp.json()["job_id"])
    assert done["status"] == "succeeded", done["error"]
    assert done["result"]["accepted"] == 1 and done["result"]["unreachable"] == 1 and done["result"]["invalid"] == 1
    report = api.get(done["result"]["files"]["report_csv"])
    assert report.status_code == 200 and "Dead" in report.text and "404" in report.text
    assert api.get(done["result"]["files"]["report_json"]).json()["summary"]["accepted"] == 1
    assert api.get("/api/v1/destinations/good-forum").json()["destination"]["connector_key"] == "discourse"


def test_a_malformed_upload_is_refused_at_once_with_a_clear_message(api):
    r = api.post("/api/v1/imports", content="title,link\nA,b\n", headers={"Content-Type": "text/csv"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR" and "name" in r.json()["error"]["message"]
    assert api.post("/api/v1/imports", json={"nope": 1}).json()["error"]["code"] == "VALIDATION_ERROR"
    assert api.get("/api/v1/import-files/nothing/here.csv").status_code == 404
    assert api.get("/api/v1/import-files/%2e%2e/api.db").status_code == 404


def test_json_body_works_too(api, mock):
    r = api.post("/api/v1/imports?commit=false", json={"csv": make_csv([f"Good,{mock.url}/sites/plain,en,"])})
    done = wait_job(api, r.json()["job_id"])
    assert done["result"]["accepted"] == 1 and done["result"]["committed"] is False
    assert api.get("/api/v1/destinations").json()["total"] == 0


def test_the_csv_can_come_from_an_address_instead_of_an_upload(api, mock):
    r = api.post("/api/v1/imports?commit=false", json={"csv_url": f"{mock.url}/files/catalogue.csv"})
    assert r.status_code == 202 and r.json()["rows"] == 2
    assert wait_job(api, r.json()["job_id"])["result"]["rows"] == 2
    bad = api.post("/api/v1/imports", json={"csv_url": f"{mock.url}/files/missing.csv"})
    assert bad.status_code == 404 or bad.json()["error"]["code"] in ("PLATFORM_UNAVAILABLE", "NOT_FOUND")
    assert api.post("/api/v1/imports", json={"csv_url": "not a url"}).json()["error"]["code"] == "VALIDATION_ERROR"
    assert api.post("/api/v1/imports", json={"nothing": 1}).json()["error"]["code"] == "VALIDATION_ERROR"


def test_private_addresses_are_refused_for_a_csv_url_unless_allowed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'p.db'}")
    monkeypatch.setenv("IMPORT_ALLOW_PRIVATE_HOSTS", "false")
    get_settings.cache_clear()
    try:
        r = TestClient(create_app()).post("/api/v1/imports", json={"csv_url": "http://127.0.0.1:8000/secret.csv"})
        assert r.status_code == 422 and "private or local" in r.json()["error"]["message"]
    finally:
        get_settings.cache_clear()


def test_the_allowlist_lets_a_public_demo_reach_only_its_own_fake_sites(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'al.db'}")
    monkeypatch.setenv("IMPORT_ALLOW_PRIVATE_HOSTS", "false")
    monkeypatch.setenv("IMPORT_PRIVATE_HOST_ALLOWLIST", "127.0.0.1")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        # the allowlisted host (the mock's) works for the CSV address, the site check and a rules page
        assert client.post("/api/v1/imports?commit=false", json={"csv_url": f"{mock.url}/files/catalogue.csv"}).status_code == 202
        csv_text = f"name,url\nOk,{mock.url}/sites/plain-1\nInternal,http://10.0.0.5/admin\nMetadata,http://169.254.169.254/latest\n"
        job = client.post("/api/v1/imports?commit=false", content=csv_text, headers={"Content-Type": "text/csv"}).json()["job_id"]
        done = wait_job(client, job)
        statuses = {r["name"]: r["status"] for r in client.get(done["result"]["files"]["report_json"]).json()["rows"]}
        assert statuses == {"Ok": "accepted", "Internal": "invalid", "Metadata": "invalid"}
        assert client.post("/api/v1/imports", json={"csv_url": "http://169.254.169.254/latest/meta-data"}).status_code == 422  # not on the list
    finally:
        get_settings.cache_clear()


def test_host_is_blocked_rules():
    from app.csv_import import host_is_blocked

    assert host_is_blocked("127.0.0.1") and host_is_blocked("10.2.3.4") and not host_is_blocked("8.8.8.8")
    assert not host_is_blocked("127.0.0.1", allow_all=True) and not host_is_blocked("127.0.0.1", allowlist={"127.0.0.1"}) and not host_is_blocked("LOCALHOST", allowlist={"localhost"})
    assert host_is_blocked("10.2.3.4", allowlist={"127.0.0.1"})
