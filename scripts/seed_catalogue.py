"""Fill the destination catalogue with the local test destinations, through the public API (so it is also a demo of
"a new destination is a row, not code"). Safe to re-run: destinations are updated by name.

    python -m scripts.seed_catalogue [http://localhost:8000]
"""
from __future__ import annotations

import sys

import httpx

DESTINATIONS = [
    {"destination": {"name": "Local Lemmy: incubrix_test", "connector_key": "lemmy", "community": "incubrix_test",
                     "rules": {"frequency_cap": {"max_posts": 20, "per_seconds": 86400}}, "extra": {"community_id": 2}},
     "language": "en", "topics": ["ai", "video"]},
    {"destination": {"name": "Local Ghost blog", "connector_key": "ghost", "rules": {"disclosure_required": False}},
     "language": "en", "topics": ["blog"]},
    {"destination": {"name": "Mock Discourse forum", "connector_key": "discourse", "community": "4"},
     "language": "en", "topics": ["ai", "forum"]},
    {"destination": {"name": "Mock Dev.to account", "connector_key": "devto", "rules": {"link_policy": "allowed"}},
     "language": "en", "topics": ["developers"]},
    {"destination": {"name": "Mock partner site (signed webhook)", "connector_key": "webhook", "extra": {"partner": "demo-partner"}},
     "language": "en", "topics": ["partner"]},
    {"destination": {"name": "Hand-posted forum (assisted)", "connector_key": "assisted",
                     "extra": {"submit_url": "https://forum.example/submit"}, "rules": {"self_promo_allowed": False, "disclosure_required": True}},
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
