"""Fill the destination catalogue with the local test destinations, through the public API (so it is also a demo of
"a new destination is a row, not code"). Safe to re-run: destinations are updated by name.

    python -m scripts.seed_catalogue [http://localhost:8000] [--profile demo]   (token from INCUBRIX_TOKEN if the API is protected)
"""
from __future__ import annotations

import os
import sys
import time

import httpx

# A destination with missing rules is refused (the rules engine fails closed), so every row states all five rules.
OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}

DESTINATIONS = [
    {"destination": {"name": "Local Lemmy: incubrix_test", "connector_key": "lemmy", "community": "incubrix_test",
                     "rules": {**OPEN, "frequency_cap": {"max_posts": 20, "per_seconds": 86400}}, "extra": {"community_id": 2}},
     "language": "en", "topics": ["ai", "video"]},
    {"destination": {"name": "Local Ghost blog", "connector_key": "ghost", "rules": OPEN},
     "language": "en", "topics": ["blog"]},
    {"destination": {"name": "Mock Discourse forum", "connector_key": "discourse", "community": "4", "rules": {**OPEN, "self_promo_allowed": False}},
     "language": "en", "topics": ["ai", "forum"]},
    {"destination": {"name": "Mock Dev.to account", "connector_key": "devto", "rules": OPEN},
     "language": "en", "topics": ["developers"]},
    {"destination": {"name": "Mock partner site (signed webhook)", "connector_key": "webhook", "extra": {"partner": "demo-partner"}, "rules": OPEN},
     "language": "en", "topics": ["partner"]},
    {"destination": {"name": "Mock Hashnode blog", "connector_key": "hashnode", "community": "pub-demo", "rules": OPEN},
     "language": "en", "topics": ["developers", "blog"]},
    {"destination": {"name": "Mock Kit newsletter (drafts only)", "connector_key": "kit", "rules": {**OPEN, "self_promo_allowed": False}},
     "language": "en", "topics": ["newsletter"]},
    {"destination": {"name": "IncuBrix MRSS feed: videos", "connector_key": "feed", "community": "videos", "rules": OPEN},
     "language": "en", "topics": ["video", "feed"]},
    {"destination": {"name": "Hand-posted forum (assisted)", "connector_key": "assisted",
                     "extra": {"submit_url": "https://forum.example/submit"}, "rules": {**OPEN, "self_promo_allowed": False, "disclosure_required": True, "automation_allowed": False}},
     "language": "en", "topics": ["no-api"]},
]


DEMO_SKIP = {"lemmy", "ghost"}  # these need their own server (docker compose); a small public demo has only the fake platforms


def main(base: str, profile: str = "full") -> None:
    token = os.environ.get("INCUBRIX_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    rows = [r for r in DESTINATIONS if not (profile == "demo" and r["destination"]["connector_key"] in DEMO_SKIP)]
    with httpx.Client(base_url=base, timeout=30, headers=headers) as http:
        for _ in range(60):  # the API may still be starting
            try:
                if http.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        for row in rows:
            r = http.post("/api/v1/destinations", json=row)
            r.raise_for_status()
            print(f"{r.status_code} {r.json()['id']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "http://localhost:8000", "demo" if "--profile" in sys.argv and "demo" in sys.argv else "full")
