"""Show the Advanced features working against the RUNNING service and save a transcript as evidence.

    python -m scripts.demo_advanced [api_url] [mock_url]      (defaults: http://localhost:8000 http://localhost:9100)

Saves test-pack/advanced-demos/transcript.md and one JSON file per feature. The demo destination it creates is removed again.
The rules page is served by the mock platform server (/rules/strict), so no real community is contacted.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
MOCK_FROM_API = "http://mock-platforms:9100"  # the address the api container uses for the mock server
OUT = Path(__file__).resolve().parent.parent / "test-pack" / "advanced-demos"
CONSERVATIVE = {"frequency_cap": {"max_posts": 1, "per_seconds": 604800}, "self_promo_allowed": False, "link_policy": "max:1", "disclosure_required": True, "automation_allowed": True}
lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def save(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    http = httpx.Client(base_url=API, timeout=60)
    say("# Advanced features against the running service\n")

    say("## 1. Tailoring: one post, fitted to a destination")
    post = {"title": "Ten practical ways to make your short videos easier to find and share on every platform you use " * 2,
            "body": "We just released our open-source editor. Thanks @alice! See https://example.com/launch and https://example.com/docs for details.",
            "tags": ["AI", "Video Tools", "indie", "open source", "extra"], "link_url": "https://example.com/launch", "ai_generated": True}
    tailored = http.post("/api/v1/tailor", json={"rendition": post, "destination_id": "mock-dev-to-account"}).json()
    say(f"- title: {len(post['title'])} characters -> {len(tailored['rendition']['title'])} (Dev.to limit 128), ends: ...{tailored['rendition']['title'][-25:]}")
    say(f"- tags: {post['tags']} -> {tailored['rendition']['tags']}")
    say(f"- canonical URL: {tailored['rendition']['canonical_url']}")
    for change in tailored["changes"]:
        say(f"- change: {change}")
    save("1-tailoring", tailored)

    say("\n## 2. Relevance score 1-5 with an explanation")
    content = {"title": "New open-source video editor for creators", "body": "A small tool that helps indie creators edit short videos faster.", "tags": ["video", "creators"], "link_url": "https://example.com/launch"}
    ranked = http.post("/api/v1/relevance", json={"rendition": content, "top": 5}).json()
    for item in ranked["items"]:
        say(f"- {item['destination_id']}: {item['explanation']}")
    save("2-relevance", ranked)

    say("\n## 3. AI rules capture: propose, then a person confirms")
    http.post("/api/v1/destinations", json={"destination": {"name": "Demo strict forum", "connector_key": "assisted", "extra": {"submit_url": "https://forum.example/submit"},
                                                            "rules": CONSERVATIVE}, "language": "en"}).raise_for_status()
    job = http.post("/api/v1/destinations/demo-strict-forum/rules/capture", json={"url": f"{MOCK_FROM_API}/rules/strict"}).json()  # a job: 202 and the proposal in the result
    while (state := http.get(f"/api/v1/jobs/{job['job_id']}").json())["status"] not in ("succeeded", "failed"):
        time.sleep(0.5)
    if state["status"] != "succeeded":
        raise SystemExit(f"capture failed: {state['error']}")
    proposal = state["result"]
    say(f"- proposal {proposal['id']} is {proposal['status'].upper()} (reader: {proposal['extractor']})")
    for rule, evidence in proposal["proposed"]["evidence"].items():
        say(f"  - {rule} = {proposal['proposed']['rules'][rule]}   quote: \"{evidence['quote']}\"")
    before = http.get("/api/v1/destinations/demo-strict-forum").json()["destination"]["rules"]
    say(f"- rules BEFORE confirmation (unchanged): link_policy={before['link_policy']}, frequency_cap={before['frequency_cap']}")
    confirmed = http.post(f"/api/v1/rule-proposals/{proposal['id']}/confirm", json={"reviewer": "Demo Reviewer", "acknowledge_loosening": True}).json()
    after = http.get("/api/v1/destinations/demo-strict-forum").json()["destination"]
    say(f"- confirmed by {confirmed['decided_by']}; rules AFTER: link_policy={after['rules']['link_policy']}, automation_allowed={after['rules']['automation_allowed']}, status note: {after['extra']['rules_status']}")
    again = http.post(f"/api/v1/rule-proposals/{proposal['id']}/confirm", json={"reviewer": "Someone"})
    say(f"- confirming the same proposal again: HTTP {again.status_code} {again.json()['error']['code']}")
    save("3-rules-capture", {"proposal": proposal, "confirmed": confirmed, "rules_after": after["rules"]})
    http.delete("/api/v1/destinations/demo-strict-forum")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
