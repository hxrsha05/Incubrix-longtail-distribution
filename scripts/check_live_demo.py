"""Check a running demo from outside:   python -m scripts.check_live_demo https://incubrix-demo.onrender.com
The access token is read from the environment variable INCUBRIX_TOKEN (never from the command line, so it stays out of shell history).
Checks: health, protection (401 without a token), the catalogue, a publish, the same publish again (replayed, same post), a failure switch
(timeout after the platform created the post, then a retry that must not duplicate), and the catalogue export. Prints one line per check.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import httpx


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
    token = os.environ.get("INCUBRIX_TOKEN")
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    http = httpx.Client(base_url=base, timeout=90)
    started = time.time()
    r = http.get("/health")
    check("health answers 200 without a token", r.status_code == 200, f"({time.time() - started:.2f} s)")
    check("ready", http.get("/ready").status_code == 200)
    check("protected call without a token is refused (401 UNAUTHORIZED)", http.get("/api/v1/destinations").status_code == 401)
    if not token:
        print("Set INCUBRIX_TOKEN to run the rest of the checks.")
        return 1
    r = http.get("/api/v1/destinations", headers=auth)
    check("catalogue lists the demo destinations", r.status_code == 200 and r.json().get("total", 0) >= 5, f"({r.json().get('total') if r.status_code == 200 else r.status_code})")

    key, title = f"live-check-{uuid.uuid4().hex[:8]}", f"Live demo check {uuid.uuid4().hex[:6]}"
    body = {"destination_id": "mock-discourse-forum", "tailor": False, "rendition": {"title": title, "body": "A body that is comfortably longer than twenty characters."}}
    first = http.post("/api/v1/publish", json=body, headers={**auth, "Idempotency-Key": key})
    check("publish goes live", first.status_code == 200, first.json().get("result", {}).get("live_url", "") if first.status_code == 200 else first.text[:200])
    again = http.post("/api/v1/publish", json=body, headers={**auth, "Idempotency-Key": key})
    same = first.status_code == 200 and again.status_code == 200 and again.json()["result"]["platform_post_id"] == first.json()["result"]["platform_post_id"]
    check("same key again returns the same post (replayed)", same and again.headers.get("Idempotent-Replayed") == "true")

    http.post("/api/v1/simulation/reset", headers=auth)
    http.post("/api/v1/simulation/fail", headers=auth, json={"platform": "discourse", "op": "create", "kind": "timeout_after", "delay_s": 15})
    key2, title2 = f"live-check-{uuid.uuid4().hex[:8]}", f"Late answer check {uuid.uuid4().hex[:6]}"
    body2 = {**body, "rendition": {"title": title2, "body": "A body that is comfortably longer than twenty characters."}}
    late = http.post("/api/v1/publish", json=body2, headers={**auth, "Idempotency-Key": key2})
    check("platform answers too late: caller sees outcome unknown (503)", late.status_code == 503, late.json().get("error", {}).get("code", "") if late.status_code == 503 else str(late.status_code))
    retry = http.post("/api/v1/publish", json=body2, headers={**auth, "Idempotency-Key": key2})
    check("retry with the same key succeeds", retry.status_code == 200)
    state = http.get("/api/v1/simulation/state", headers=auth)
    created = state.json().get("discourse_topics") if state.status_code == 200 else None
    check("exactly one post exists after the timeout and the retry (no duplicate)", created == 1, f"({created} posts on the platform)")
    http.post("/api/v1/simulation/reset", headers=auth)
    exp = http.get("/api/v1/catalogue/export?format=csv", headers=auth)
    check("catalogue export (destination records) works", exp.status_code == 200 and exp.text.startswith("name,"))
    print(f"\n{'ALL CHECKS PASSED' if not failed else str(failed) + ' CHECK(S) FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
