"""Show recertification and automatic demotion on the RUNNING service, and save a transcript as evidence.

    python -m scripts.demo_recertification [api_url] [mock_url]      (defaults: http://localhost:8000 http://localhost:9100)

Uses the force option so a destination does not have to wait a week. Creates one demo destination and removes it again.
Saves test-pack/recertification/transcript.md.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
MOCK = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:9100"
OUT = Path(__file__).resolve().parent.parent / "test-pack" / "recertification"
OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
DEST = {"name": "Recertification demo forum", "connector_key": "discourse", "rules": OPEN}
DEST_ID = "recertification-demo-forum"
lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def wait(http: httpx.Client, job_id: str) -> dict:
    for _ in range(120):
        job = http.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(1)
    raise SystemExit("job did not finish")


def status(http: httpx.Client) -> str:
    return http.get(f"/api/v1/destinations/{DEST_ID}").json()["status"]


def publish(http: httpx.Client, key: str):
    body = {"rendition": {"title": "A recertification demo post", "body": "This post is made by the recertification demo script."}, "destination": DEST,
            "connection": {"base_url": "http://mock-platforms:9100/discourse", "api_key": "mock-discourse-key", "username": "bot"}, "idempotency_key": key}
    return http.post("/connector/v1/discourse/publish", json=body)


def main() -> None:
    http = httpx.Client(base_url=API, timeout=60)
    run = str(int(time.time()))
    httpx.post(f"{MOCK}/__control/reset")
    say("# Recertification and automatic demotion on the running service\n")
    http.post("/api/v1/destinations", json={"destination": DEST, "language": "en", "topics": ["demo"]}).raise_for_status()
    say("## 1. First certification (a person starts it: uncertified destinations are never rechecked automatically)")
    wait(http, http.post(f"/api/v1/destinations/{DEST_ID}/certify").json()["job_id"])
    say(f"- status: {status(http)}")
    first = publish(http, f"recert-demo-a-{run}")
    say(f"- publishing while certified: HTTP {first.status_code} ({first.json().get('status')})")
    say("\n## 2. What is due right now")
    due = http.get("/api/v1/recertification").json()
    say(f"- due now: {[d['destination_id'] for d in due['due_now'] if d['destination_id'] == DEST_ID] or 'nothing for this destination (certified a moment ago)'}")
    say(f"- policy: re-check certified destinations after {due['policy']['max_age_s'] / 86400:g} days, demoted after {due['policy']['demoted_cooldown_s'] / 86400:g} day, needs_attention after {due['policy']['attention_retry_s'] / 3600:g} hour")

    say("\n## 3. The platform changes its answer (a field is renamed), then a recertification run (force = do not wait a week)")
    httpx.post(f"{MOCK}/__control/fail", json={"platform": "discourse", "kind": "rename_field", "rename": {"topic_id": "topic_identifier"}})
    job = wait(http, http.post("/api/v1/recertify?force=true").json()["job_id"])
    result = job["result"]
    say(f"- checked {result['checked']}, demoted {result['demoted']}, transitions: {[(t['destination_id'], t['from'], t['to']) for t in result['transitions'] if t['destination_id'] == DEST_ID]}")
    say(f"- status: {status(http)} ({http.get(f'/api/v1/destinations/{DEST_ID}').json()['status_reason']})")
    blocked = publish(http, f"recert-demo-b-{run}")
    err = blocked.json()["error"]
    say(f"- publishing now: HTTP {blocked.status_code} {err['code']} rule={err['details']['violations'][0]['rule']}")
    rec = http.post("/api/v1/recommend", json={"rendition": {"title": "demo video", "body": "demo", "tags": ["demo"]}, "top": 50}).json()
    say(f"- suggested by the recommender: {'NO (left out)' if DEST_ID not in [i['destination_id'] for i in rec['items']] else 'yes'}")

    say("\n## 4. The platform is fixed, the next recertification promotes the destination back")
    httpx.post(f"{MOCK}/__control/reset")
    job = wait(http, http.post("/api/v1/recertify?force=true").json()["job_id"])
    say(f"- transitions: {[(t['destination_id'], t['from'], t['to']) for t in job['result']['transitions'] if t['destination_id'] == DEST_ID]}")
    say(f"- status: {status(http)}")
    again = publish(http, f"recert-demo-c-{run}")
    say(f"- publishing again: HTTP {again.status_code} ({again.json().get('status')})")

    http.delete(f"/api/v1/destinations/{DEST_ID}")
    httpx.post(f"{MOCK}/__control/reset")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
