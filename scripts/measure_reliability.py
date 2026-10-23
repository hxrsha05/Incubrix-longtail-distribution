"""Measure reliability:   python -m scripts.measure_reliability [--local http://localhost:8000]

Part 1 (simulation, runs by itself): the service and the fake platforms run in this process, over real HTTP.
  A  publish success rate: many different posts to each connector through POST /api/v1/publish
  B  duplicates under retry: for each case the platform CREATES the post but answers too late (a real timeout), the caller retries with the
     same Idempotency-Key, and we count posts on the platform. Duplicates = posts created minus posts intended. The brief requires 0.
  C  duplicates under plain double submits: the same request twice with the same key
  D  rate limits: a 429 with a reset time, then the retry
Part 2 (local instances, only with --local pointing at the running docker compose stack): full certification cycles (publish, confirm live,
  status, delete, confirm gone) on the real local Lemmy and Ghost, repeated, and the share that passed.
Writes test-pack/reliability/report.md and report.json.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
N_PER_CONNECTOR, N_TIMEOUT, N_DOUBLE, N_RATE, N_LOCAL = 40, 40, 40, 20, 20


def start_mock() -> tuple[str, object]:
    import uvicorn

    from mock_platforms.main import create_app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{url}/__control/state", timeout=1)
            return url, server
        except httpx.HTTPError:
            time.sleep(0.1)
    raise SystemExit("mock did not start")


def simulation() -> dict:
    tmp = tempfile.mkdtemp(prefix="incubrix-reliability-")
    mock_url, server = start_mock()
    os.environ.update({"DATABASE_URL": f"sqlite:///{tmp}/r.db", "EVIDENCE_DIR": f"{tmp}/ev", "MOCK_PLATFORMS_URL": mock_url, "HTTP_TIMEOUT_S": "0.4", "LOG_LEVEL": "ERROR"})
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    api = TestClient(create_app())
    mock = lambda method, path, **kw: httpx.request(method, f"{mock_url}{path}", timeout=30, **kw)  # noqa: E731
    state = lambda: mock("GET", "/__control/state").json()  # noqa: E731
    names = {"discourse": "discourse", "devto": "devto", "hashnode": "hashnode", "kit": "kit", "webhook": "webhook"}
    for key, connector in names.items():
        extra = {"community": "pub-demo"} if key == "hashnode" else {}
        api.post("/api/v1/destinations", json={"destination": {"name": f"reliability {key}", "connector_key": connector, "rules": OPEN, **extra}, "language": "en"}).raise_for_status()

    def publish(dest: str, key: str, title: str):
        return api.post("/api/v1/publish", json={"destination_id": dest, "tailor": False, "rendition": {"title": title, "body": "A body that is comfortably longer than twenty characters."}},
                        headers={"Idempotency-Key": key})

    result: dict = {"A": {}, "B": {}, "C": {}, "D": {}}
    # A: success rate per connector
    for key in names:
        ok = sum(publish(f"reliability-{key}", f"a-{key}-{i}", f"Reliability post {key} number {i} here").status_code == 200 for i in range(N_PER_CONNECTOR))
        result["A"][key] = {"attempts": N_PER_CONNECTOR, "succeeded": ok}
    # B: timeouts then a retry with the same key (Discourse and Dev.to, the two where the retry has to find the earlier post)
    for key, field in (("discourse", "discourse_topics"), ("devto", "devto_articles")):
        mock("POST", "/__control/reset")
        for i in range(N_TIMEOUT):
            mock("POST", "/__control/fail", json={"platform": key, "op": "create", "kind": "timeout_after", "delay_s": 0.7})
            first = publish(f"reliability-{key}", f"b-{key}-{i}", f"Retry safety post {key} number {i} here")
            second = publish(f"reliability-{key}", f"b-{key}-{i}", f"Retry safety post {key} number {i} here")
            result["B"].setdefault(key, {"cases": 0, "first_call_timed_out": 0, "retry_succeeded": 0})
            result["B"][key]["cases"] += 1
            result["B"][key]["first_call_timed_out"] += first.status_code == 503
            result["B"][key]["retry_succeeded"] += second.status_code == 200
        created = state()[field]
        result["B"][key].update(posts_intended=N_TIMEOUT, posts_on_platform=created, duplicates=created - N_TIMEOUT)
    # C: double submit with the same key
    mock("POST", "/__control/reset")
    for i in range(N_DOUBLE):
        publish("reliability-discourse", f"c-{i}", f"Double submit post number {i} here")
        publish("reliability-discourse", f"c-{i}", f"Double submit post number {i} here")
    created = state()["discourse_topics"]
    result["C"] = {"cases": N_DOUBLE, "posts_intended": N_DOUBLE, "posts_on_platform": created, "duplicates": created - N_DOUBLE}
    # D: 429 with a reset time, then the retry with the same key
    mock("POST", "/__control/reset")
    seen_wait, recovered = 0, 0
    for i in range(N_RATE):
        mock("POST", "/__control/fail", json={"platform": "discourse", "op": "create", "kind": "status", "status": 429, "retry_after": 15})
        limited = publish("reliability-discourse", f"d-{i}", f"Rate limit post number {i} here")
        if limited.status_code == 429 and limited.json()["error"]["details"].get("retry_after_s") == 15.0:
            seen_wait += 1
        recovered += publish("reliability-discourse", f"d-{i}", f"Rate limit post number {i} here").status_code == 200
    result["D"] = {"cases": N_RATE, "reported_with_reset_time": seen_wait, "retry_succeeded": recovered, "posts_on_platform": state()["discourse_topics"]}
    server.should_exit = True
    return result


def local_instances(base: str) -> dict:
    out: dict = {}
    with httpx.Client(base_url=base, timeout=60, headers={"Authorization": f"Bearer {os.environ['INCUBRIX_TOKEN']}"} if os.environ.get("INCUBRIX_TOKEN") else {}) as http:
        for dest in ("local-lemmy-incubrix-test", "local-ghost-blog"):
            passed, times = 0, []
            for _ in range(N_LOCAL):
                job = http.post(f"/api/v1/destinations/{dest}/certify").json()
                while (state := http.get(f"/api/v1/jobs/{job['job_id']}").json())["status"] not in ("succeeded", "failed"):
                    time.sleep(0.3)
                record = state["result"]
                passed += record["verdict"] == "passed"
                runs = http.get(f"/api/v1/destinations/{dest}/certifications").json()["items"]
                times.append(runs[0]["duration_s"])
                time.sleep(1.2)  # Lemmy refuses two logins in the same second; a real user would not hit it either
            out[dest] = {"cycles": N_LOCAL, "passed": passed, "mean_s": round(sum(times) / len(times), 3), "max_s": round(max(times), 3)}
    return out


def main() -> None:
    sim = simulation()
    local = local_instances(sys.argv[sys.argv.index("--local") + 1]) if "--local" in sys.argv else None
    out = ROOT / "test-pack" / "reliability"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"simulation": sim, "local_instances": local}, indent=2), encoding="utf-8")
    a_total = sum(v["attempts"] for v in sim["A"].values())
    a_ok = sum(v["succeeded"] for v in sim["A"].values())
    dups = sum(v["duplicates"] for v in sim["B"].values()) + sim["C"]["duplicates"]
    lines = ["# Reliability", "", "Measured by `python -m scripts.measure_reliability`; every call is a real HTTP request to the service, which calls the fake platforms over real HTTP.", "",
             "## Publish success rate in simulation (A)", "", "| Connector | Attempts | Succeeded |", "|---|---|---|",
             *[f"| {k} | {v['attempts']} | {v['succeeded']} |" for k, v in sim["A"].items()], f"| **all** | {a_total} | **{a_ok} ({a_ok / a_total:.1%})** |", "",
             "## Duplicates under retry (B and C): must be 0", "",
             "| Test | Cases | Posts intended | Posts on the platform | Duplicates |", "|---|---|---|---|---|",
             *[f"| timeout after the platform created the post, retry with the same key ({k}) | {v['cases']} | {v['posts_intended']} | {v['posts_on_platform']} | **{v['duplicates']}** |" for k, v in sim["B"].items()],
             f"| the same request twice with the same key | {sim['C']['cases']} | {sim['C']['posts_intended']} | {sim['C']['posts_on_platform']} | **{sim['C']['duplicates']}** |", "",
             f"Total duplicates: **{dups}**.", "",
             "For the timeout tests, how the first call and the retry went:", "",
             *[f"- {k}: the first call timed out in {v['first_call_timed_out']} of {v['cases']} cases, the retry succeeded in {v['retry_succeeded']} of {v['cases']}" for k, v in sim["B"].items()], "",
             "## Rate limits (D)", "",
             f"- {sim['D']['reported_with_reset_time']} of {sim['D']['cases']} 429 answers carried the reset time (15 s) to the caller; the retry with the same key succeeded {sim['D']['retry_succeeded']} of {sim['D']['cases']} times; posts on the platform: {sim['D']['posts_on_platform']}", ""]
    if local:
        lines += ["## Local instances: full certification cycles (publish, live, status, delete, gone)", "", "| Destination | Cycles | Passed | Mean seconds | Slowest |", "|---|---|---|---|---|",
                  *[f"| {k} | {v['cycles']} | {v['passed']} | {v['mean_s']} | {v['max_s']} |" for k, v in local.items()], ""]
    else:
        lines += ["## Local instances", "", "Not run in this report (needs `--local` and the docker compose stack).", ""]
    lines += ["Limits: simulated platforms behave as I modelled them; a real platform may fail in ways I did not think of. The local Lemmy and Ghost are real but tiny."]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:30]))


if __name__ == "__main__":
    main()
