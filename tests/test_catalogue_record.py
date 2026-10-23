"""The destination record (Appendix A), the levels L0-L4, the audience relevance score, the export, and the importer's enrichment."""
import csv
import io

import httpx
import pytest
from sqlalchemy import create_engine

from app.audience import score_audience
from app.catalogue import Catalogue
from app.connectors.registry import build_registry
from app.csv_import import Importer, find_rules_url
from app.db import init_db
from app.models import Destination
from app.records import RECORD_FIELDS, destination_record, level_of, records_to_csv

REQUIRED = ["name", "url", "platform_key", "surface", "category", "audience_tags", "language", "geo", "est_audience", "rules_url", "frequency_cap",
            "self_promo_allowed", "link_policy", "disclosure_required", "automation_allowed", "connector_key", "relevance_score", "relevance_reason",
            "level", "last_certified_at"]


def row(name="Forum", topics=(), connector="discourse", status="uncertified", site_title=None, description=None, extra=None, rules=None, language="en"):
    return {"id": name.lower(), "name": name, "connector_key": connector, "status": status, "topics": list(topics), "language": language, "last_certified_at": None,
            "destination": {"name": name, "connector_key": connector, "base_url": "https://forum.example", "rules": rules or {}, "surface": None,
                            "extra": {"site_title": site_title, "site_description": description, **(extra or {})}}}


@pytest.fixture(scope="module")
def registry():
    return build_registry(httpx.Client(), set())


# ---------------------------------------------------------------- the audience score
def test_a_destination_with_nothing_to_do_with_the_audience_scores_one():
    s = score_audience(row("Garden Club", ["gardening", "recipes"]))
    assert s.score == 1 and "nothing in its description" in s.reason and s.segments == {}


def test_more_segments_means_a_higher_score_and_the_reason_names_them():
    one = score_audience(row("Video Corner", ["film"]))
    two = score_audience(row("Video Corner", ["film", "ai"]))
    three = score_audience(row("Corner", ["film", "ai", "saas"]))
    five = score_audience(row("Corner", ["film", "ai", "saas", "seo", "creators"]))
    assert one.score < two.score <= three.score < five.score and five.score == 5
    assert set(two.segments) == {"AI", "video"} and "AI (ai" in two.reason and "No signal for" in two.reason


def test_a_strong_single_segment_beats_a_weak_one():
    weak = score_audience(row("Chat", ["video"]))
    strong = score_audience(row("Chat", ["video", "youtube", "editing", "footage"]))
    assert weak.score == 2 and strong.score == 3


def test_the_site_description_and_platform_count_and_hyphenated_words_are_split():
    s = score_audience(row("Corner", [], description="A place for open-source machine-learning models", extra={"platform_key": "discourse"}))
    assert "AI" in s.segments and 1 <= s.score <= 5


def test_the_score_is_always_one_to_five_and_repeatable():
    for topics in ([], ["ai"], ["ai", "video", "marketing", "saas", "creators", "founders", "startup", "youtube"]):
        a, b = score_audience(row("X", topics)), score_audience(row("X", topics))
        assert 1 <= a.score <= 5 and a == b


# ---------------------------------------------------------------- the record
def test_the_record_has_exactly_the_fields_the_brief_requires(registry):
    rec = destination_record(row("AI Video Forum", ["ai", "video"], rules={"frequency_cap": {"max_posts": 1, "per_seconds": 604800}, "self_promo_allowed": False,
                                                                         "link_policy": "max:1", "disclosure_required": True, "automation_allowed": True},
                                  extra={"member_count": 1234, "rules_url": "https://forum.example/rules", "geo": "DE", "category": "forum", "platform_key": "discourse"}), registry)
    assert list(rec) == REQUIRED == RECORD_FIELDS
    assert rec["est_audience"] == 1234 and rec["rules_url"] == "https://forum.example/rules" and rec["geo"] == "DE" and rec["self_promo_allowed"] is False
    assert rec["link_policy"] == "max:1" and rec["url"] == "https://forum.example" and rec["surface"] == "topic" and rec["relevance_score"] >= 3 and rec["relevance_reason"]


def test_levels_follow_the_connector_tier_and_only_count_once_certified(registry):
    cases = {"discourse": "L4", "lemmy": "L4", "ghost": "L3", "feed": "L2", "webhook": "L2", "assisted": "L1", "kit": "L4", "hashnode": "L4"}
    for connector, expected in cases.items():
        assert level_of(row(connector=connector, status="certified"), registry) == expected, connector
        for status in ("uncertified", "demoted", "needs_attention"):
            assert level_of(row(connector=connector, status=status), registry) == "L0", (connector, status)
    assert level_of(row(connector="nonexistent", status="certified"), registry) == "L0"


def test_csv_export_has_the_required_header_and_flat_values(registry):
    rec = destination_record(row("AI Forum", ["ai", "video"], rules={"frequency_cap": {"max_posts": 2, "per_seconds": 604800}}), registry)
    text = records_to_csv([rec])
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert next(csv.reader(io.StringIO(text))) == REQUIRED
    assert parsed[0]["audience_tags"] == "ai; video" and parsed[0]["frequency_cap"] == "2 per 604800 s" and parsed[0]["geo"] == ""


# ---------------------------------------------------------------- the API: record on every row, export
@pytest.fixture()
def api(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rec.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    get_settings.cache_clear()
    try:
        yield TestClient(create_app())
    finally:
        get_settings.cache_clear()


def test_every_catalogue_row_carries_its_record_and_the_level_rises_with_certification(api):
    api.post("/api/v1/destinations", json={"destination": {"name": "AI Forum", "connector_key": "discourse", "rules": {"self_promo_allowed": True}}, "language": "en", "topics": ["ai", "video"]})
    assert api.get("/api/v1/destinations/ai-forum").json()["record"]["level"] == "L0"
    job = api.post("/api/v1/destinations/ai-forum/certify").json()["job_id"]
    import time

    for _ in range(100):
        if api.get(f"/api/v1/jobs/{job}").json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    rec = api.get("/api/v1/destinations/ai-forum").json()["record"]
    assert rec["level"] == "L4" and rec["last_certified_at"] and rec["relevance_score"] >= 3
    assert api.get("/api/v1/destinations").json()["items"][0]["record"]["level"] == "L4"


def test_the_export_endpoint(api):
    api.post("/api/v1/destinations", json={"destination": {"name": "AI Forum", "connector_key": "discourse"}, "language": "en", "topics": ["ai"]})
    api.post("/api/v1/destinations", json={"destination": {"name": "Blog", "connector_key": "devto"}, "language": "en", "topics": ["developers"]})
    r = api.get("/api/v1/catalogue/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and next(csv.reader(io.StringIO(r.text))) == REQUIRED
    assert len(list(csv.DictReader(io.StringIO(r.text)))) == 2
    assert api.get("/api/v1/catalogue/export?format=json&connector_key=devto").json()["count"] == 1
    assert api.get("/api/v1/catalogue/export?format=xml").json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- the importer's enrichment
@pytest.fixture()
def catalogue(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'imp.db'}")
    init_db(engine)
    return Catalogue(engine)


def test_the_rules_link_is_found_on_a_page():
    html = '<a href="/about">About</a> <a href="/community-guidelines">Read this</a>'
    assert find_rules_url(html, "https://forum.example") == "https://forum.example/community-guidelines"
    assert find_rules_url('<a href="/faq">Rules of the road</a>', "https://forum.example/") == "https://forum.example/faq"
    assert find_rules_url('<a href="/pricing">Pricing</a>', "https://forum.example") is None


def test_the_importer_adds_member_count_rules_link_platform_category_and_relevance(mock, catalogue):
    text = f"name,url,language,topics\nAI Lemmy,{mock.url}/sites/lemmy-ai,en,ai;video\nVideo Discourse,{mock.url}/sites/discourse-vid,en,video\nPlain,{mock.url}/sites/plain-x,en,\n"
    out = Importer(catalogue, httpx.Client(), allow_private_hosts=True).run(text)
    by = {r["name"]: r for r in out["report"]}
    assert by["AI Lemmy"]["member_count"] and by["Video Discourse"]["member_count"] and by["Plain"]["member_count"] is None  # only platforms with a public stats endpoint
    assert by["AI Lemmy"]["rules_url"].endswith("/sites/lemmy-ai/rules") and by["AI Lemmy"]["category"] == "forum" and by["Plain"]["category"] == "community (posted by hand)"
    assert by["AI Lemmy"]["relevance_score"] >= 3 and "AI (" in by["AI Lemmy"]["relevance_reason"] and by["Plain"]["relevance_score"] == 1
    saved = catalogue.get("ai-lemmy")["destination"]["extra"]
    assert saved["member_count"] == by["AI Lemmy"]["member_count"] and saved["platform_key"] == "lemmy" and saved["rules_url"] == by["AI Lemmy"]["rules_url"]


def test_two_addresses_for_the_same_site_after_a_redirect_are_one_destination(catalogue):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "old.example":
            return httpx.Response(301, headers={"location": "https://new.example/"})
        return httpx.Response(200, text="<html lang='en'><title>New home</title></html>")

    text = "name,url,language,topics\nNew,https://new.example,en,\nOld address,https://old.example,en,\n"
    out = Importer(catalogue, httpx.Client(transport=httpx.MockTransport(handler)), allow_private_hosts=True).run(text)
    assert [r["status"] for r in out["report"]] == ["accepted", "duplicate"] and "once redirects are followed" in out["report"][1]["reasons"][0]
    assert out["report"][1]["duplicate_of"] == "row 2" and catalogue.list()["total"] == 1


def test_the_report_csv_has_the_new_columns(mock, catalogue):
    from app.csv_import import REPORT_COLUMNS, report_to_csv

    out = Importer(catalogue, httpx.Client(), allow_private_hosts=True).run(f"name,url\nA,{mock.url}/sites/plain-1\n")
    header = next(csv.reader(io.StringIO(report_to_csv(out["report"]))))
    assert header == REPORT_COLUMNS and {"member_count", "rules_url", "category", "relevance_score", "relevance_reason"} <= set(header)
