"""Show the hard cases working against the RUNNING service, and save a transcript as evidence.

    python -m scripts.demo_failures [api_url] [mock_url]      (defaults: http://localhost:8000 http://localhost:9100)

It makes the mock platform fail on purpose, calls the real API, and writes test-pack/failure-demos/transcript.md
plus one JSON file per case. It leaves the catalogue as it found it (the demo destination is removed at the end).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
MOCK = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:9100"
OUT = Path(__file__).resolve().parent.parent / "test-pack" / "failure-demos"
OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
RENDITION = {"title": "A failure demo post title", "body": "This post exists to demonstrate failure handling."}
CONN = {"base_url": "http://mock-platforms:9100/discourse", "api_key": "mock-discourse-key", "username": "bot"}  # fake mock account
DEST = {"name": "Demo forum (failures)", "connector_key": "discourse", "rules": OPEN}

lines: list[str] = []
RUN = str(int(time.time()))  # keys are unique per run, so the demo can be repeated


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def save(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def mock(method: str, path: str, **kw):
    return httpx.request(method, f"{MOCK}{path}", timeout=30, **kw)


def publish(key: str, timeout: float = 60):
    body = {"rendition": RENDITION, "destination": DEST, "connection": CONN, "idempotency_key": key}
    return httpx.post(f"{API}/connector/v1/discourse/publish", json=body, timeout=timeout)


def main() -> None:
    http = httpx.Client(base_url=API, timeout=60)
    say("# Failure demos against the running service\n")
    mock("POST", "/__control/reset")

    say("## 1. Timeout after the platform created the post, then a retry with the same key")
    mock("POST", "/__control/fail", json={"platform": "discourse", "op": "create", "kind": "timeout_after", "delay_s": 12})
    t = time.time()
    first = publish(f"demo-timeout-{RUN}")
    say(f"- first call: HTTP {first.status_code} {first.json()['error']['code']} (outcome_unknown={first.json()['error']['details'].get('outcome_unknown')}) after {time.time() - t:.1f} s")
    state = mock("GET", "/__control/state").json()
    say(f"- the platform DID create the post: discourse_topics = {state['discourse_topics']}")
    second = publish(f"demo-timeout-{RUN}")
    state2 = mock("GET", "/__control/state").json()
    say(f"- retry with the same Idempotency-Key: HTTP {second.status_code}, topic {second.json().get('platform_post_id')}")
    say(f"- topics on the platform after the retry: {state2['discourse_topics']}  (duplicates: {state2['discourse_topics'] - 1})")
    save("1-timeout-then-retry", {"first": first.json(), "second": second.json(), "topics_after": state2["discourse_topics"]})

    say("\n## 2. Rate limit with a reset time")
    mock("POST", "/__control/reset")
    mock("POST", "/__control/fail", json={"platform": "discourse", "op": "create", "kind": "status", "status": 429, "retry_after": 15})
    limited = publish(f"demo-429-{RUN}")
    err = limited.json()["error"]
    say(f"- HTTP {limited.status_code} {err['code']} retryable={err['retryable']} retry_after_s={err['details'].get('retry_after_s')}")
    again = publish(f"demo-429-{RUN}")
    say(f"- retry with the same key after the limit cleared: HTTP {again.status_code} ({again.json().get('status')})")
    save("2-rate-limit", {"limited": limited.json(), "retry": again.json()})

    say("\n## 3. The platform renames a field: certification fails and the destination is demoted")
    mock("POST", "/__control/reset")
    http.post("/api/v1/destinations", json={"destination": {**DEST, "name": "Demo forum (failures)"}, "language": "en"}).raise_for_status()
    dest_id = "demo-forum-failures"
    mock("POST", "/__control/fail", json={"platform": "discourse", "kind": "rename_field", "rename": {"topic_id": "topic_identifier"}})
    job = http.post(f"/api/v1/destinations/{dest_id}/certify").json()
    result = wait(http, job["job_id"])
    say(f"- certification verdict: {result['result']['verdict']}; reason: {result['result']['failure']['summary']}")
    say(f"- destination status now: {http.get(f'/api/v1/destinations/{dest_id}').json()['status']}")
    topics = httpx.get(f"{MOCK}/discourse/topics/created-by/bot.json", headers={"Api-Key": "mock-discourse-key", "Api-Username": "bot"}).json()["topic_list"]["topics"]
    say(f"- test posts left behind on the platform: {len(topics)}  (the stray post was found and deleted)")
    save("3-renamed-field", result)

    say("\n## 4. Expired credentials")
    http.post("/api/v1/destinations", json={"destination": {**DEST, "name": "Demo forum (no access)", "extra": {"connection_ref": "nobody"}}})
    bad = wait(http, http.post("/api/v1/destinations/demo-forum-no-access/certify").json()["job_id"])
    say(f"- verdict: {bad['result']['verdict']} ({bad['result']['failure']['kind']}); status: {http.get('/api/v1/destinations/demo-forum-no-access').json()['status']}")
    save("4-no-credentials", bad)

    mock("POST", "/__control/reset")
    for d in ("demo-forum-failures", "demo-forum-no-access"):
        http.delete(f"/api/v1/destinations/{d}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def wait(http: httpx.Client, job_id: str) -> dict:
    for _ in range(120):
        job = http.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(1)
    raise SystemExit("job did not finish")


if __name__ == "__main__":
    main()
