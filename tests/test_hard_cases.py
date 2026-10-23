"""Every hard case in the brief, end to end through the public API (tab 6_Hard_cases of the submission form).

Each test sends a real request to the service, which talks to the mock platforms over real HTTP, and checks what the CALLER sees:
the status, the shared error code, the reason in the message and the details, and that nothing wrong happened on the platform.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
GOOD = {"title": "A long enough title for the forum", "body": "A body that is comfortably longer than twenty characters."}


@pytest.fixture()
def api(mock, tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'hard.db'}")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    monkeypatch.setenv("HTTP_TIMEOUT_S", "0.4")  # so a slow platform really times out in a test
    monkeypatch.setenv("IMPORT_ALLOW_PRIVATE_HOSTS", "true")
    get_settings.cache_clear()
    client = TestClient(create_app())
    client.mock = mock
    yield client
    get_settings.cache_clear()


def add(api, name, connector="discourse", rules=None, language="en", **dest):
    r = api.post("/api/v1/destinations", json={"destination": {"name": name, "connector_key": connector, "rules": rules or OPEN, **dest}, "language": language, "topics": ["video"]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def publish(api, dest_id, key, rendition=None, tailor=False):
    return api.post("/api/v1/publish", json={"destination_id": dest_id, "rendition": rendition or GOOD, "tailor": tailor}, headers={"Idempotency-Key": key})


def wait_job(api, job_id, seconds=30):
    for _ in range(seconds * 10):
        job = api.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def error(response):
    return response.json()["error"]


# 1 ------------------------------------------------------------------------------------------------------------------
def test_a_publish_that_times_out_and_is_retried_with_the_same_key_creates_one_post(api):
    dest = add(api, "Forum")
    api.mock.fail(platform="discourse", op="create", kind="timeout_after", delay_s=1.2)  # the platform creates the post but answers too late
    first = publish(api, dest, "retry-1")
    assert first.status_code == 503 and error(first)["code"] == "PLATFORM_UNAVAILABLE" and error(first)["retryable"] is True and error(first)["details"]["outcome_unknown"] is True
    assert api.mock.state()["discourse_topics"] == 1  # it WAS created
    second = publish(api, dest, "retry-1")
    assert second.status_code == 200 and second.json()["result"]["status"] == "live"
    assert api.mock.state()["discourse_topics"] == 1  # still one: the retry found the existing post instead of posting again
    third = publish(api, dest, "retry-1")
    assert third.headers["Idempotent-Replayed"] == "true" and third.json()["result"] == second.json()["result"]


# 2 ------------------------------------------------------------------------------------------------------------------
def test_a_429_with_a_reset_time_is_reported_with_the_wait_and_a_retry_later_works(api):
    dest = add(api, "Forum")
    api.mock.fail(platform="discourse", op="create", kind="status", status=429, retry_after=15)
    r = publish(api, dest, "rate-1")
    assert r.status_code == 429 and error(r)["code"] == "PLATFORM_RATE_LIMIT" and error(r)["retryable"] is True and error(r)["details"]["retry_after_s"] == 15.0
    assert api.mock.state()["discourse_topics"] == 0
    assert publish(api, dest, "rate-1").status_code == 200  # the same key may be used again: nothing was created, the key was released


def test_an_expired_token_asks_for_a_reconnect_and_is_not_retried(api, mock, tmp_path):
    dest = add(api, "Forum")
    connections = tmp_path / "connections.json"
    connections.write_text(json.dumps({"discourse": {"base_url": f"{mock.url}/discourse", "api_key": "expired-key-123", "username": "bot"}}), encoding="utf-8")
    api.app.state.certification.credentials.entries = json.loads(connections.read_text())
    r = publish(api, dest, "auth-1")
    assert r.status_code == 401 and error(r)["code"] == "CONNECTION_AUTH_EXPIRED" and error(r)["retryable"] is False
    assert "expired-key-123" not in r.text  # the credential never comes back in an error


def test_content_the_platform_refuses_comes_back_with_its_reason(api):
    dest = add(api, "Forum")
    r = publish(api, dest, "reject-1", {"title": "A title that is fine here", "body": "short body but valid"[:19]})  # the platform wants 20 characters
    assert r.status_code == 422 and error(r)["code"] == "PLATFORM_CONTENT_REJECTED" and "too short" in error(r)["details"]["platform_message"]


# 3 ------------------------------------------------------------------------------------------------------------------
def test_a_title_longer_than_the_destination_allows_is_refused_with_field_level_reasons_and_nothing_is_sent(api):
    dest = add(api, "Forum", spec={"title_max_chars": 40})
    r = publish(api, dest, "long-1", {"title": "T" * 60, "body": GOOD["body"]})
    assert r.status_code == 422 and error(r)["code"] == "DEST_SPEC_VIOLATION"
    violation = error(r)["details"]["violations"][0]
    assert violation["field"] == "title" and violation["limit"] == 40 and violation["actual"] == 60
    assert api.mock.state()["discourse_topics"] == 0


def test_with_tailoring_the_same_long_title_is_shortened_at_a_word_and_published_with_a_note(api):
    dest = add(api, "Forum", spec={"title_max_chars": 40})
    r = publish(api, dest, "long-2", {"title": "A title that is far too long for this forum " * 2, "body": GOOD["body"]}, tailor=True)
    assert r.status_code == 200 and any("title shortened" in c for c in r.json()["tailoring"]["changes"])


def test_a_body_longer_than_allowed_is_refused_too(api):
    dest = add(api, "Forum", spec={"body_max_chars": 50})
    r = publish(api, dest, "long-3", {"title": GOOD["title"], "body": "x" * 80})
    assert error(r)["code"] == "DEST_SPEC_VIOLATION" and error(r)["details"]["violations"][0]["field"] == "body"


def test_media_the_destination_does_not_accept_is_refused_and_says_which_item(api):
    dest = add(api, "Forum")
    r = publish(api, dest, "media-1", {**GOOD, "media": [{"url": "https://cdn.example/a.png", "kind": "image"}, {"url": "https://cdn.example/v.mp4", "kind": "video"}]})
    assert error(r)["code"] == "DEST_SPEC_VIOLATION" and error(r)["details"]["violations"][0]["field"] == "media[1]"
    assert "video media is not accepted" in error(r)["details"]["violations"][0]["reason"] and api.mock.state()["discourse_topics"] == 0


# 4 ------------------------------------------------------------------------------------------------------------------
def test_a_second_post_inside_the_weekly_cap_is_blocked_with_the_time_it_may_go_out(api):
    dest = add(api, "Forum", rules={**OPEN, "frequency_cap": {"max_posts": 1, "per_seconds": 604800}})
    assert publish(api, dest, "cap-1").status_code == 200
    second = publish(api, dest, "cap-2", {"title": "Another long enough title here", "body": GOOD["body"]})
    assert second.status_code == 429 and error(second)["code"] == "DEST_RULE_FREQUENCY_CAP" and error(second)["retryable"] is True
    assert "reschedule_at" in error(second)["details"] and error(second)["details"]["retry_after_s"] > 0 and api.mock.state()["discourse_topics"] == 1


def test_a_promotional_post_to_a_community_that_bans_self_promotion_is_blocked_with_the_reason(api):
    dest = add(api, "Forum", rules={**OPEN, "self_promo_allowed": False})
    r = publish(api, dest, "promo-1", {**GOOD, "promotional": True})
    assert r.status_code == 422 and error(r)["code"] == "DEST_SPEC_VIOLATION" and error(r)["details"]["violations"][0]["rule"] == "self_promotion"
    assert api.mock.state()["discourse_topics"] == 0
    assert publish(api, dest, "promo-2", GOOD).status_code == 200  # the same destination takes a post that is not promotional


# 5 ------------------------------------------------------------------------------------------------------------------
def test_a_platform_that_renames_a_field_fails_certification_and_the_destination_is_demoted(api):
    dest = add(api, "Forum")
    api.mock.fail(platform="discourse", kind="rename_field", rename={"topic_id": "topic_identifier"})
    job = wait_job(api, api.post(f"/api/v1/destinations/{dest}/certify").json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["verdict"] == "failed" and job["result"]["failure"]["kind"] == "RESPONSE_SHAPE_CHANGED"
    assert api.get(f"/api/v1/destinations/{dest}").json()["status"] == "demoted"
    blocked = publish(api, dest, "demoted-1")
    assert error(blocked)["code"] == "DEST_SPEC_VIOLATION" and error(blocked)["details"]["violations"][0]["rule"] == "destination_demoted"
    run = api.get(f"/api/v1/destinations/{dest}/certifications").json()["items"][0]
    assert run["test_post"]["deleted"] is True and run["warnings"]  # the post the platform made was found by reconcile and deleted, and the run says so
    assert any("found by reconcile" in w for w in run["warnings"])


# 6 ------------------------------------------------------------------------------------------------------------------
def test_a_csv_with_duplicates_bad_urls_and_mixed_languages_gets_a_row_by_row_report(api, mock):
    site = lambda n: f"{mock.url}/sites/{n}"
    csv_text = "\n".join(["name,url,language", f"Good,{site('plain-1')},English", f"Good again,{site('plain-1')}/,English", "Bad,not a url,English",
                          f"German,{site('lang-de-1')},English", f"Dead,{site('dead-1')},English"]) + "\n"
    job = wait_job(api, api.post("/api/v1/imports?commit=false", content=csv_text, headers={"Content-Type": "text/csv"}).json()["job_id"])
    result = job["result"]
    assert (result["accepted"], result["duplicate"], result["invalid"], result["unreachable"]) == (2, 1, 1, 1)
    report = api.get(result["files"]["report_csv"]).text
    assert "same site as an earlier row" in report and "url contains spaces" in report and "page says 'de'" in report and "HTTP 404" in report


def test_a_partner_who_revokes_permission_is_an_auth_problem_not_a_retry(api):
    dest = add(api, "Partner", connector="webhook")
    api.mock.fail(platform="partner", op="create", kind="status", status=410)
    r = publish(api, dest, "revoked-1")
    assert r.status_code == 401 and error(r)["code"] == "CONNECTION_AUTH_EXPIRED" and error(r)["retryable"] is False


# 7 ------------------------------------------------------------------------------------------------------------------
def test_a_non_english_community_takes_non_english_content_intact_and_warns_about_english_content(api, mock):
    dest = add(api, "Deutsches Forum", language="de")
    german = {"title": "Neue Videobearbeitung für Kreative: Größe, Qualität, Spaß", "body": "Wir haben ein kleines Werkzeug gebaut, das Schnitt und Farbe vereinfacht. 日本語のテスト。", "language": "de"}
    r = publish(api, dest, "de-1", german, tailor=True)
    assert r.status_code == 200 and r.json()["tailoring"]["warnings"] == []
    topic_id = r.json()["result"]["platform_post_id"]
    assert mock.state()["discourse_topics"] == 1 and api.get(f"/api/v1/destinations/{dest}").json()["language"] == "de"
    english = publish(api, dest, "de-2", {"title": "A different long enough title", "body": GOOD["body"]}, tailor=True)
    assert english.status_code == 200 and "not translated" in english.json()["tailoring"]["warnings"][0]
    rec = api.post("/api/v1/recommend", json={"rendition": {"title": "video", "body": "video tools", "tags": ["video"]}}).json()["items"]
    assert dest not in [i["destination_id"] for i in rec]  # English content is not recommended to a German-language destination
    assert topic_id


def test_a_destination_that_requires_ai_disclosure_gets_the_line_added_or_the_post_blocked(api):
    dest = add(api, "Strict forum", rules={**OPEN, "disclosure_required": True})
    ai_post = {**GOOD, "ai_generated": True}
    blocked = publish(api, dest, "ai-1", ai_post, tailor=False)
    assert blocked.status_code == 422 and error(blocked)["details"]["violations"][0]["rule"] == "ai_disclosure"
    fixed = publish(api, dest, "ai-2", ai_post, tailor=True)
    assert fixed.status_code == 200 and any("AI disclosure line added" in c for c in fixed.json()["tailoring"]["changes"])
    assert publish(api, dest, "ai-3", {**GOOD, "title": "A human-written title, no disclosure needed"}).status_code == 200  # human-written content needs no disclosure line
