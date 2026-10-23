"""Recommender: weighting, synonyms, vetoes, and the measured precision at 10 on the labelled set."""
import json
from pathlib import Path

import pytest

from app.models import Rendition
from app.recommender import recommend, stem
from scripts.eval_recommender import evaluate

LABELLED = Path(__file__).resolve().parent.parent / "samples" / "recommender" / "labelled_set.json"


def row(dest_id, topics=(), name=None, language="en", rules=None, status="uncertified"):
    return {"id": dest_id, "name": name or dest_id, "topics": list(topics), "language": language, "status": status,
            "destination": {"name": name or dest_id, "rules": rules or {}, "extra": {"site_title": None}}}


def ids(items):
    return [i.destination_id for i in items]


def test_stemming_and_synonyms():
    assert stem("creators") == stem("creator") and stem("editing") == stem("editor")
    assert stem("film") == stem("video") == stem("footage") and stem("film", synonyms=False) == "film"


def test_a_rare_term_outweighs_a_common_one():
    rows = [row("rare", ["kubernetes"]), row("common1", ["community"]), row("common2", ["community"]), row("common3", ["community"])]
    post = Rendition(title="Kubernetes community news", body="x", tags=[])
    assert ids(recommend(post, rows))[0] == "rare"


def test_synonyms_connect_different_words_for_the_same_thing():
    rows = [row("films", ["film", "movie"]), row("cooking", ["recipe"])]
    post = Rendition(title="New video editor", body="Edit your video clips quickly", tags=["video"])
    assert ids(recommend(post, rows))[0] == "films"
    without = recommend(post, rows, synonyms=False)
    assert without[0].similarity == 0 or ids(without)[0] != "films" or without[0].similarity < recommend(post, rows)[0].similarity


def test_destinations_that_would_refuse_the_content_are_left_out_entirely():
    rows = [row("ok", ["video"]), row("nopromo", ["video"], rules={"self_promo_allowed": False}), row("nolinks", ["video"], rules={"link_policy": "none"})]
    post = Rendition(title="Video tool", body="video", tags=["video"], promotional=True, link_url="https://x.example")
    assert ids(recommend(post, rows)) == ["ok"]


def test_other_languages_are_not_suggested():
    rows = [row("en", ["video"]), row("de", ["video"], language="de")]
    assert ids(recommend(Rendition(title="Video", body="video", tags=["video"]), rows)) == ["en"]
    assert ids(recommend(Rendition(title="Video", body="video", tags=["video"], language="de"), rows)) == ["de"]


def test_top_limits_the_list_and_ties_are_broken_the_same_way_every_time():
    rows = [row(f"d{i}", ["video"]) for i in range(20)]
    post = Rendition(title="Video", body="video", tags=["video"])
    first = ids(recommend(post, rows, top=5))
    assert len(first) == 5 and first == ids(recommend(post, list(reversed(rows)), top=5))


def test_a_certified_destination_wins_a_tie():
    rows = [row("a", ["video"]), row("b", ["video"], status="certified")]
    assert ids(recommend(Rendition(title="Video", body="video", tags=["video"]), rows))[0] == "b"


def test_every_item_carries_the_score_and_the_reason():
    item = recommend(Rendition(title="Video tool", body="video editing", tags=["video"]), [row("v", ["video"])])[0]
    assert 1 <= item.score <= 5 and "shared topics" in item.explanation and "video" in item.matched_terms


def test_nothing_to_recommend_is_an_empty_list():
    assert recommend(Rendition(title="x", body="y"), []) == []


# ---------------------------------------------------------------- the measured quality
@pytest.fixture(scope="module")
def result():
    return evaluate(json.loads(LABELLED.read_text(encoding="utf-8")))


def test_measured_precision_at_10_on_the_labelled_set(result):
    p = result["precision_at_10"]
    assert result["queries"] == 30 and result["destinations"] == 300
    assert p["random"] < 0.2  # the floor
    assert p["recommender"] >= 0.9 and p["recommender"] > p["relevance"] > p["random"]
    assert p["no_synonyms"] >= 0.85  # most of the result does not depend on the synonym table


def test_the_labelled_set_is_fair_to_precision_at_10():
    data = json.loads(LABELLED.read_text(encoding="utf-8"))
    assert all(len(q["relevant"]) >= 10 for q in data["queries"])  # 100% is reachable for every post
    assert "SYNTHETIC" in data["description"]  # the file says what it is


# ---------------------------------------------------------------- the HTTP API
def test_recommend_api(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        client.post("/api/v1/destinations", json={"destination": {"name": "Video Makers", "connector_key": "devto", "rules": {"self_promo_allowed": True}}, "language": "en", "topics": ["video", "creators"]})
        client.post("/api/v1/destinations", json={"destination": {"name": "Home Cooks", "connector_key": "discourse"}, "language": "en", "topics": ["recipes"]})
        r = client.post("/api/v1/recommend", json={"rendition": {"title": "A video editor", "body": "for creators", "tags": ["video"]}, "top": 5})
        assert r.status_code == 200 and r.json()["considered"] == 2
        assert [i["destination_id"] for i in r.json()["items"]][0] == "video-makers" and r.json()["items"][0]["explanation"]
        assert client.post("/api/v1/recommend", json={"rendition": {"title": "x"}, "top": 0}).status_code == 422
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- audience fit and past results
def test_equal_content_fit_is_broken_by_audience_relevance_to_incubrix():
    rows = [row("plain", ["updates"], name="Weekly updates"), row("audience", ["updates"], name="Weekly updates")]  # the same words, so the same content fit
    rows[1]["destination"]["extra"]["site_description"] = "a place for SaaS founders and AI builders"  # only the audience score sees this
    post = Rendition(title="Weekly updates", body="our weekly updates", tags=["updates"])
    ranked = recommend(post, rows)
    assert ids(ranked)[0] == "audience" and ranked[0].audience_score > ranked[1].audience_score and "Audience fit" in ranked[0].explanation


def test_a_clean_track_record_helps_and_is_explained():
    rows = [row("a", ["video"]), row("b", ["video"])]
    post = Rendition(title="Video", body="video", tags=["video"])
    ranked = recommend(post, rows, history={"b": {"runs": 4, "passes": 4}, "a": {"runs": 4, "passes": 1}})
    assert ids(ranked) == ["b", "a"] and ranked[0].track_record == "4 of 4 certification runs passed" and "Track record: 4 of 4" in ranked[0].explanation


def test_the_bonuses_never_beat_a_clearly_better_content_fit():
    rows = [row("fits", ["kubernetes"]), row("popular", ["ai", "saas", "founders", "video", "marketing", "creators"])]
    post = Rendition(title="Kubernetes news", body="kubernetes", tags=["kubernetes"])
    assert ids(recommend(post, rows, history={"popular": {"runs": 9, "passes": 9}}))[0] == "fits"


def test_the_api_uses_the_track_record(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api2.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    get_settings.cache_clear()
    try:
        import time

        client = TestClient(create_app())
        client.post("/api/v1/destinations", json={"destination": {"name": "Video Forum", "connector_key": "discourse", "rules": {"self_promo_allowed": True}}, "language": "en", "topics": ["video"]})
        job = client.post("/api/v1/destinations/video-forum/certify").json()["job_id"]
        for _ in range(100):
            if client.get(f"/api/v1/jobs/{job}").json()["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.1)
        item = client.post("/api/v1/recommend", json={"rendition": {"title": "Video", "body": "video", "tags": ["video"]}}).json()["items"][0]
        assert item["track_record"] == "1 of 1 certification runs passed" and item["audience_score"] >= 2
    finally:
        get_settings.cache_clear()
