"""AI rules capture: propose from a page, verify the quotes, a person confirms, loosening needs acknowledgement."""
import json

import httpx
import pytest
from sqlalchemy import create_engine

from app.catalogue import Catalogue
from app.csv_import import CONSERVATIVE_RULES
from app.db import init_db
from app.errors import IncuBrixError
from app.models import Destination
from app.rules_capture import AnthropicExtractor, MockExtractor, RulesCapture, html_to_text, make_diff, verify

STRICT = ("Community rules. Self-promotion is not allowed. You may post one post per week. Do not post any links in the body. "
          "AI-generated content must be disclosed with the [AI] tag. Bots are not allowed on this forum.")
RELAXED = "Be kind. Self-promotion is welcome if you take part in discussions. Up to 2 links per post. Post as often as you like."


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rules.db'}")
    init_db(engine)
    catalogue = Catalogue(engine)
    catalogue.upsert(Destination(name="Forum", connector_key="discourse", rules=dict(CONSERVATIVE_RULES)))

    class Env:
        pass

    e = Env()
    e.engine, e.catalogue = engine, catalogue
    e.service = RulesCapture(engine, catalogue, MockExtractor(), httpx.Client(), allow_private_hosts=True)
    return e


# ---------------------------------------------------------------- reading a page
def test_html_becomes_plain_text_without_scripts():
    text = html_to_text("<html><script>var x=1;</script><body><h1>Rules</h1><p>No bots &amp; no spam.</p></body></html>")
    assert "var x" not in text and "No bots & no spam." in text


def test_the_mock_reader_finds_all_five_rules_with_quotes():
    out = MockExtractor().extract(STRICT, None)["rules"]
    got = {r["rule"]: r for r in out}
    assert got["self_promo_allowed"]["value"] is False and got["frequency_cap"]["value"] == {"max_posts": 1, "per_seconds": 604800}
    assert got["link_policy"]["value"] == "none" and got["disclosure_required"]["value"] is True and got["automation_allowed"]["value"] is False
    assert all(r["quote"] in STRICT for r in out)


def test_the_mock_reader_understands_a_relaxed_page():
    got = {r["rule"]: r["value"] for r in MockExtractor().extract(RELAXED, None)["rules"]}
    assert got["self_promo_allowed"] is True and got["link_policy"] == "max:2" and "frequency_cap" not in got


def test_a_page_that_says_nothing_proposes_nothing():
    assert MockExtractor().extract("This is a friendly place to talk about video editing and creative tools.", None)["rules"] == []


# ---------------------------------------------------------------- verification
def test_verification_drops_invented_malformed_and_unknown_rules():
    page = "Self-promotion is not allowed here. Posting more than once a week is discouraged."
    proposals = [
        {"rule": "self_promo_allowed", "value": False, "quote": "Self-promotion is not allowed here.", "confidence": 0.9},   # fine
        {"rule": "link_policy", "value": "none", "quote": "Links are banned everywhere.", "confidence": 0.9},                  # quote not on the page
        {"rule": "frequency_cap", "value": {"max_posts": "one"}, "quote": "once a week", "confidence": 0.9},                 # wrong shape
        {"rule": "color_of_logo", "value": "blue", "quote": "Self-promotion is not allowed here.", "confidence": 1},         # unknown rule
        {"rule": "automation_allowed", "value": False, "quote": "x", "confidence": 1},                                        # quote too short
        "garbage",
    ]
    rules, evidence, dropped = verify(proposals, page)
    assert rules == {"self_promo_allowed": False} and evidence["self_promo_allowed"]["quote"].startswith("Self-promotion")
    reasons = " | ".join(d["why"] for d in dropped)
    assert "possible invention" in reasons and "wrong shape" in reasons and "unknown rule" in reasons and "not an object" in reasons and len(dropped) == 5


def test_quotes_match_even_when_spacing_and_case_differ():
    rules, _, dropped = verify([{"rule": "self_promo_allowed", "value": False, "quote": "SELF-PROMOTION   is not\nallowed", "confidence": 1}], "Self-promotion is not allowed.")
    assert rules == {"self_promo_allowed": False} and not dropped


# ---------------------------------------------------------------- the workflow
def test_capture_creates_a_pending_proposal_and_changes_nothing(env):
    before = env.catalogue.get("forum")["destination"]["rules"]
    p = env.service.capture("forum", text=STRICT)
    assert p["status"] == "pending" and p["proposed"]["rules"]["self_promo_allowed"] is False and p["extractor"] == "mock-keyword-reader"
    assert env.catalogue.get("forum")["destination"]["rules"] == before  # nothing applied
    assert p["manifest"]["kind"] == "rules_capture" and p["manifest"]["cost"]["usd"] == 0.0 and p["manifest"]["ai_generated"]["present"] is False
    assert {d["rule"]: d["change"] for d in p["diff"]}["link_policy"] == "tightens"


def test_a_person_confirms_and_only_then_the_rules_change(env):
    p = env.service.capture("forum", text=STRICT)
    done = env.service.confirm(p["id"], "Asha Rao", note="checked the page")
    assert done["status"] == "confirmed" and done["decided_by"] == "Asha Rao" and done["decided_at"]
    row = env.catalogue.get("forum")["destination"]
    assert row["rules"]["link_policy"] == "none" and row["rules"]["frequency_cap"] == {"max_posts": 1, "per_seconds": 604800}
    assert row["extra"]["rules_status"] == "confirmed_by:Asha Rao" and row["extra"]["rules_proposal"] == p["id"]
    assert env.catalogue.get("forum")["status"] == "uncertified"  # confirming rules does not touch certification


def test_confirming_needs_a_name_and_works_only_once(env):
    p = env.service.capture("forum", text=STRICT)
    with pytest.raises(IncuBrixError) as exc:
        env.service.confirm(p["id"], "  ")
    assert exc.value.code.value == "VALIDATION_ERROR"
    env.service.confirm(p["id"], "Asha")
    with pytest.raises(IncuBrixError) as exc:
        env.service.confirm(p["id"], "Asha")
    assert "already confirmed" in exc.value.message
    with pytest.raises(IncuBrixError):
        env.service.reject(p["id"], "Asha")


def test_a_rejected_proposal_changes_nothing(env):
    before = env.catalogue.get("forum")["destination"]["rules"]
    p = env.service.capture("forum", text=STRICT)
    assert env.service.reject(p["id"], "Asha", "page is outdated")["status"] == "rejected"
    assert env.catalogue.get("forum")["destination"]["rules"] == before


def test_loosening_needs_an_explicit_acknowledgement(env):
    p = env.service.capture("forum", text=RELAXED)  # promo welcome (was forbidden), 2 links (was max 1)
    changes = {d["rule"]: d["change"] for d in p["diff"]}
    assert changes["self_promo_allowed"] == "loosens" and changes["link_policy"] == "loosens"
    with pytest.raises(IncuBrixError) as exc:
        env.service.confirm(p["id"], "Asha")
    assert exc.value.code.value == "VALIDATION_ERROR" and set(exc.value.details["loosening"]) == {"self_promo_allowed", "link_policy"}
    assert env.catalogue.get("forum")["destination"]["rules"]["self_promo_allowed"] is False  # still strict
    env.service.confirm(p["id"], "Asha", acknowledge_loosening=True)
    assert env.catalogue.get("forum")["destination"]["rules"]["self_promo_allowed"] is True


def test_the_reviewer_can_edit_the_proposal_before_confirming(env):
    p = env.service.capture("forum", text=STRICT)
    env.service.confirm(p["id"], "Asha", overrides={"frequency_cap": {"max_posts": 2, "per_seconds": 604800}}, acknowledge_loosening=True)  # 2 a week is looser than the current 1
    assert env.catalogue.get("forum")["destination"]["rules"]["frequency_cap"]["max_posts"] == 2
    p2 = env.service.capture("forum", text=STRICT)
    with pytest.raises(IncuBrixError):
        env.service.confirm(p2["id"], "Asha", overrides={"link_policy": "plenty"})


def test_rules_the_page_does_not_mention_keep_their_current_value(env):
    p = env.service.capture("forum", text="Self-promotion is not allowed on this forum at all.")
    env.service.confirm(p["id"], "Asha")
    rules = env.catalogue.get("forum")["destination"]["rules"]
    assert rules["frequency_cap"] == CONSERVATIVE_RULES["frequency_cap"] and rules["disclosure_required"] is True


def test_diff_classification():
    cur = {"frequency_cap": {"max_posts": 1, "per_seconds": 604800}, "link_policy": "max:1", "disclosure_required": True}
    d = {x["rule"]: x["change"] for x in make_diff(cur, {"frequency_cap": {"max_posts": 5, "per_seconds": 604800}, "link_policy": "none", "disclosure_required": False})}
    assert d == {"frequency_cap": "loosens", "link_policy": "tightens", "disclosure_required": "loosens"}
    assert make_diff({"link_policy": "none"}, {"link_policy": "none"})[0]["change"] == "same"


def test_capture_needs_a_page_and_unknown_destinations_are_404(env):
    with pytest.raises(IncuBrixError) as exc:
        env.service.capture("forum")
    assert exc.value.code.value == "VALIDATION_ERROR"
    with pytest.raises(IncuBrixError) as exc:
        env.service.capture("nope", text=STRICT)
    assert exc.value.code.value == "NOT_FOUND"
    with pytest.raises(IncuBrixError):
        env.service.capture("forum", text="too short")


# ---------------------------------------------------------------- the AI model adapter, with a fake API
def fake_anthropic(rules, status=200, usage=None):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"], seen["body"] = dict(request.headers), json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": {"type": "x"}}, headers={"retry-after": "7"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Here you go: " + json.dumps({"rules": rules})}], "usage": usage or {"input_tokens": 1000, "output_tokens": 200}})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_the_ai_adapter_sends_the_page_and_the_key_and_a_hallucinated_quote_is_dropped(env):
    http, seen = fake_anthropic([
        {"rule": "self_promo_allowed", "value": False, "quote": "Self-promotion is not allowed.", "confidence": 0.95},
        {"rule": "link_policy", "value": "none", "quote": "Links are never allowed anywhere.", "confidence": 0.9},  # invented
    ])
    extractor = AnthropicExtractor(http, "sk-test-123", "claude-haiku-5-5", price_in=1.0, price_out=5.0)
    service = RulesCapture(env.engine, env.catalogue, extractor, httpx.Client(), allow_private_hosts=True)
    p = service.capture("forum", text=STRICT)
    assert seen["headers"]["x-api-key"] == "sk-test-123" and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["model"] == "claude-haiku-5-5" and "Self-promotion" in seen["body"]["messages"][0]["content"] and seen["body"]["system"]
    assert list(p["proposed"]["rules"]) == ["self_promo_allowed"] and "possible invention" in p["proposed"]["dropped"][0]["why"]
    assert p["extractor"] == "anthropic:claude-haiku-5-5" and p["cost"]["input_tokens"] == 1000 and p["cost"]["usd"] == 0.002
    assert p["manifest"]["ai_generated"]["present"] is True and p["manifest"]["cost"]["model_calls"] == 1
    assert "sk-test-123" not in json.dumps(p)  # the key never appears in anything we return


@pytest.mark.parametrize("status,code,retryable", [(429, "PLATFORM_RATE_LIMIT", True), (401, "CONNECTION_AUTH_EXPIRED", False), (500, "PLATFORM_UNAVAILABLE", True)])
def test_ai_model_errors_become_the_shared_error_codes(env, status, code, retryable):
    http, _ = fake_anthropic([], status=status)
    service = RulesCapture(env.engine, env.catalogue, AnthropicExtractor(http, "k", "m"), httpx.Client(), allow_private_hosts=True)
    with pytest.raises(IncuBrixError) as exc:
        service.capture("forum", text=STRICT)
    assert exc.value.code.value == code and exc.value.retryable is retryable
    assert env.service.list("forum") == []  # a failed call leaves no proposal behind


def test_ai_model_answering_without_json_proposes_nothing_and_says_so(env):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": "Sorry, I cannot help."}], "usage": {}})))
    p = RulesCapture(env.engine, env.catalogue, AnthropicExtractor(http, "k", "m"), httpx.Client(), allow_private_hosts=True).capture("forum", text=STRICT)
    assert p["proposed"]["rules"] == {}


# ---------------------------------------------------------------- the HTTP API
@pytest.fixture()
def api(mock, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("IMPORT_ALLOW_PRIVATE_HOSTS", "true")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        client.post("/api/v1/destinations", json={"destination": {"name": "Forum", "connector_key": "discourse", "rules": CONSERVATIVE_RULES}})
        yield client
    finally:
        get_settings.cache_clear()


def test_api_flow_capture_from_a_url_review_and_confirm(api, mock):
    r = api.post("/api/v1/destinations/forum/rules/capture", json={"url": f"{mock.url}/rules/strict"}, headers={"Idempotency-Key": "c1"})
    assert r.status_code == 201
    proposal = r.json()
    assert proposal["status"] == "pending" and proposal["proposed"]["rules"]["automation_allowed"] is False
    assert api.post("/api/v1/destinations/forum/rules/capture", json={"url": f"{mock.url}/rules/strict"}, headers={"Idempotency-Key": "c1"}).headers["Idempotent-Replayed"] == "true"
    assert api.get(f"/api/v1/rule-proposals/{proposal['id']}").json()["status"] == "pending"
    assert len(api.get("/api/v1/destinations/forum/rules/proposals").json()["items"]) == 1
    assert api.get("/api/v1/destinations/forum").json()["destination"]["rules"]["link_policy"] == "max:1"  # untouched so far
    assert api.post(f"/api/v1/rule-proposals/{proposal['id']}/confirm", json={}).status_code == 422  # a reviewer name is required
    done = api.post(f"/api/v1/rule-proposals/{proposal['id']}/confirm", json={"reviewer": "Asha Rao"})
    assert done.status_code == 200 and done.json()["status"] == "confirmed"
    after = api.get("/api/v1/destinations/forum").json()["destination"]
    assert after["rules"]["link_policy"] == "none" and after["extra"]["rules_status"] == "confirmed_by:Asha Rao"
    assert api.post(f"/api/v1/rule-proposals/{proposal['id']}/confirm", json={"reviewer": "Asha"}).status_code == 409


def test_api_page_that_is_missing_or_silent(api, mock):
    assert api.post("/api/v1/destinations/forum/rules/capture", json={"url": f"{mock.url}/rules/nope"}).json()["error"]["code"] == "PLATFORM_UNAVAILABLE"
    silent = api.post("/api/v1/destinations/forum/rules/capture", json={"url": f"{mock.url}/rules/silent"}).json()
    assert silent["proposed"]["rules"] == {} and silent["diff"] == []
    assert api.post("/api/v1/destinations/forum/rules/capture", json={}).json()["error"]["code"] == "VALIDATION_ERROR"
    assert api.post("/api/v1/rule-proposals/nope/reject", json={"reviewer": "a"}).json()["error"]["code"] == "NOT_FOUND"
