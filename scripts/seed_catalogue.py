"""Fill the destination catalogue with the local test destinations, through the public API (so it is also a demo of
"a new destination is a row, not code"). Safe to re-run: destinations are updated by name.

    python -m scripts.seed_catalogue [http://localhost:8000]
"""
from __future__ import annotations

import sys

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


def main(base: str) -> None:
    with httpx.Client(base_url=base, timeout=30) as http:
        for row in DESTINATIONS:
            r = http.post("/api/v1/destinations", json=row)
            r.raise_for_status()
            print(f"{r.status_code} {r.json()['id']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")
