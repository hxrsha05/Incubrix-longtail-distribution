"""Relevance score 1-5 with an explanation, and the tailor / relevance API."""
import time

import pytest

from app.models import Rendition
from app.relevance import keywords, rank, score_destination


def row(dest_id="d", topics=(), language="en", status="uncertified", name="A forum", rules=None, site_title=None):
    return {"id": dest_id, "topics": list(topics), "language": language, "status": status,
            "destination": {"name": name, "rules": rules or {}, "extra": {"site_title": site_title}}}


POST = Rendition(title="New open-source video editor for creators", body="We built a small tool that helps indie creators edit short videos faster.", tags=["video", "creators", "open-source"])


def test_keywords_ignore_filler_words_and_fold_plurals():
    assert keywords("The creators and the Video tools!") == {"creator", "video", "tool"} | set()


def test_an_on_topic_destination_scores_high_with_a_reason():
    s = score_destination(POST, row(topics=["video", "creators", "open-source"], status="certified"))
    assert s.score == 5 and "shared topics" in s.explanation and "certified" in s.explanation


def test_an_off_topic_destination_scores_low():
    s = score_destination(POST, row(topics=["gardening", "recipes"], name="Home cooks"))
    assert s.score <= 2 and "no shared topics" in s.explanation


def test_no_shared_topic_never_scores_above_two_even_when_everything_else_is_perfect():
    s = score_destination(POST, row(topics=["gardening"], language="en", status="certified", name="Garden"))
    assert s.score == 2 and "no shared topics" in s.explanation


def test_more_overlap_never_scores_lower_than_less():
    scores = [score_destination(POST, row(topics=t)).score for t in ([], ["video"], ["video", "creators"], ["video", "creators", "open-source"])]
    assert scores == sorted(scores)


def test_a_different_language_costs_a_point_and_is_explained():
    same = score_destination(POST, row(topics=["video", "creators"], language="en"))
    other = score_destination(POST, row(topics=["video", "creators"], language="de"))
    assert other.score < same.score and "language differs" in other.explanation


def test_promotion_to_a_no_promo_destination_is_capped_at_one_whatever_the_topic():
    promo = POST.model_copy(update={"promotional": True})
    s = score_destination(promo, row(topics=["video", "creators", "open-source"], status="certified", rules={"self_promo_allowed": False}))
    assert s.score == 1 and "promotion is not allowed" in s.explanation and s.signals["vetoes"]


def test_a_link_to_a_no_link_destination_is_a_veto():
    s = score_destination(POST.model_copy(update={"link_url": "https://x.example"}), row(topics=["video"], rules={"link_policy": "none"}))
    assert s.score == 1 and "links are not allowed" in s.explanation


def test_the_site_description_counts_as_a_topic_source():
    s = score_destination(POST, row(topics=[], name="Corner", site_title="A community for open-source video makers"))
    assert s.score >= 3 and "video" in s.signals["shared_words"]


def test_the_score_is_always_a_whole_number_from_1_to_5():
    for t in ([], ["video"], ["video", "creators", "open-source", "indie", "tool", "edit"]):
        for lang in ("en", "de"):
            assert score_destination(POST, row(topics=t, language=lang)).score in (1, 2, 3, 4, 5)


def test_ranking_is_best_first_and_stable():
    rows = [row("b", topics=["gardening"]), row("a", topics=["video", "creators"]), row("c", topics=["video"])]
    ranked = rank(POST, rows)
    assert [r.destination_id for r in ranked] == ["a", "c", "b"] and [r.destination_id for r in rank(POST, rows, top=2)] == ["a", "c"]
    assert [r.destination_id for r in rank(POST, list(reversed(rows)))] == ["a", "c", "b"]  # input order does not matter


# ---------------------------------------------------------------- the HTTP API
@pytest.fixture()
def api(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        client.post("/api/v1/destinations", json={"destination": {"name": "Video Makers", "connector_key": "devto"}, "language": "en", "topics": ["video", "creators"]})
        client.post("/api/v1/destinations", json={"destination": {"name": "Home Cooks", "connector_key": "discourse"}, "language": "en", "topics": ["recipes"]})
        yield client
    finally:
        get_settings.cache_clear()


def test_relevance_api_ranks_the_catalogue(api):
    r = api.post("/api/v1/relevance", json={"rendition": POST.model_dump()})
    assert r.status_code == 200 and r.json()["scored"] == 2
    items = r.json()["items"]
    assert items[0]["destination_id"] == "video-makers" and items[0]["score"] > items[1]["score"] and items[0]["explanation"]
    assert [i["destination_id"] for i in api.post("/api/v1/relevance", json={"rendition": POST.model_dump(), "top": 1}).json()["items"]] == ["video-makers"]
    assert api.post("/api/v1/relevance", json={"rendition": POST.model_dump(), "connector_key": "nope"}).json()["error"]["code"] == "NOT_FOUND"


def test_tailor_api_fits_the_content_to_the_destination(api):
    long = Rendition(title="Word " * 80, body="Hello @alice", tags=["AI", "Video Tools", "a", "b", "c", "d"], link_url="https://x.example/orig")
    r = api.post("/api/v1/tailor", json={"rendition": long.model_dump(), "destination_id": "video-makers"})
    assert r.status_code == 200
    out = r.json()
    assert len(out["rendition"]["title"]) <= 128 and len(out["rendition"]["tags"]) == 4 and out["rendition"]["canonical_url"] == "https://x.example/orig"
    assert any("title shortened" in c for c in out["changes"])
    assert api.post("/api/v1/tailor", json={"rendition": long.model_dump(), "destination_id": "nope"}).json()["error"]["code"] == "NOT_FOUND"
