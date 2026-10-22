"""Prepare the LOCAL test instances started by docker compose. Safe to run again and again.

  Lemmy: log in as the admin created by lemmy.hjson, create a test community.
  Ghost: create the owner account on first run, then a custom integration to get an Admin API key.

Writes the credentials to SEED_OUTPUT (a docker volume, never the repository), where the connectors read them.
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

LEMMY_URL = os.environ["LEMMY_URL"].rstrip("/")
LEMMY_USER = os.environ["LEMMY_ADMIN_USER"]
LEMMY_PASSWORD = os.environ["LEMMY_ADMIN_PASSWORD"]
LEMMY_COMMUNITY = os.environ.get("LEMMY_COMMUNITY", "incubrix_test")

GHOST_URL = os.environ["GHOST_URL"].rstrip("/")
GHOST_EMAIL = os.environ["GHOST_ADMIN_EMAIL"]
GHOST_PASSWORD = os.environ["GHOST_ADMIN_PASSWORD"]
GHOST_INTEGRATION = "IncuBrix Local"

OUTPUT = os.environ.get("SEED_OUTPUT", "connections.json")


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def wait_for(name: str, probe, timeout: float = 420.0) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            if probe():
                log(f"{name} is up")
                return
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(3)
    sys.exit(f"[seed] gave up waiting for {name} ({last})")


# ------------------------------------------------------------------ Lemmy
def seed_lemmy() -> dict:
    wait_for("lemmy", lambda: httpx.get(f"{LEMMY_URL}/api/v3/site", timeout=5).status_code == 200)
    login = None
    for _ in range(20):  # the admin account is created by the setup block just after the first start
        login = httpx.post(f"{LEMMY_URL}/api/v3/user/login", json={"username_or_email": LEMMY_USER, "password": LEMMY_PASSWORD}, timeout=10)
        if login.status_code == 200:
            break
        time.sleep(3)
    if login is None or login.status_code != 200:
        sys.exit(f"[seed] lemmy login failed: {login.status_code if login is not None else '?'} {login.text[:200] if login is not None else ''}")
    jwt = login.json()["jwt"]
    auth = {"Authorization": f"Bearer {jwt}"}

    # Lemmy allows only ~6 posts per 10 minutes by default. That is right for real communities but would throttle
    # our own certification runs, so relax it on THIS local test instance only.
    relaxed = {f"rate_limit_{k}": 100000 for k in ("message", "post", "register", "image", "comment", "search")}
    relaxed.update({f"rate_limit_{k}_per_second": 60 for k in ("message", "post", "register", "image", "comment", "search")})
    edit = httpx.put(f"{LEMMY_URL}/api/v3/site", json=relaxed, headers=auth, timeout=15)
    log("lemmy rate limits relaxed for local testing" if edit.status_code == 200 else f"could not relax lemmy rate limits: {edit.status_code} {edit.text[:200]}")

    found = httpx.get(f"{LEMMY_URL}/api/v3/community", params={"name": LEMMY_COMMUNITY}, headers=auth, timeout=10)
    if found.status_code == 200:
        community_id = found.json()["community_view"]["community"]["id"]
        log(f"lemmy community '{LEMMY_COMMUNITY}' already exists (id {community_id})")
    else:
        made = httpx.post(
            f"{LEMMY_URL}/api/v3/community",
            json={"name": LEMMY_COMMUNITY, "title": "IncuBrix Test", "description": "Throwaway community for automated certification posts."},
            headers=auth,
            timeout=15,
        )
        if made.status_code != 200:
            sys.exit(f"[seed] lemmy community creation failed: {made.status_code} {made.text[:300]}")
        community_id = made.json()["community_view"]["community"]["id"]
        log(f"lemmy community '{LEMMY_COMMUNITY}' created (id {community_id})")
    return {"base_url": LEMMY_URL, "username": LEMMY_USER, "password": LEMMY_PASSWORD, "community": LEMMY_COMMUNITY, "community_id": community_id}


# ------------------------------------------------------------------ Ghost
def seed_ghost() -> dict:
    admin = f"{GHOST_URL}/ghost/api/admin"
    headers = {"Origin": GHOST_URL, "Accept-Version": "v5.0", "Content-Type": "application/json"}
    wait_for("ghost", lambda: httpx.get(f"{admin}/site/", timeout=5).status_code == 200)

    with httpx.Client(timeout=20, headers=headers) as c:
        status = c.get(f"{admin}/authentication/setup/").json()["setup"][0]["status"]
        if not status:
            r = c.post(
                f"{admin}/authentication/setup/",
                json={"setup": [{"name": "IncuBrix Admin", "email": GHOST_EMAIL, "password": GHOST_PASSWORD, "blogTitle": "IncuBrix Local Ghost"}]},
            )
            if r.status_code >= 300:
                sys.exit(f"[seed] ghost setup failed: {r.status_code} {r.text[:300]}")
            log("ghost owner account created")
        else:
            log("ghost already set up")

        r = c.post(f"{admin}/session/", json={"username": GHOST_EMAIL, "password": GHOST_PASSWORD})
        if r.status_code >= 300:
            sys.exit(f"[seed] ghost login failed: {r.status_code} {r.text[:300]}")

        listing = c.get(f"{admin}/integrations/", params={"include": "api_keys", "limit": "all"})
        integration = next((i for i in listing.json().get("integrations", []) if i["name"] == GHOST_INTEGRATION), None)
        if integration is None:
            r = c.post(f"{admin}/integrations/", params={"include": "api_keys"}, json={"integrations": [{"name": GHOST_INTEGRATION}]})
            if r.status_code >= 300:
                sys.exit(f"[seed] ghost integration failed: {r.status_code} {r.text[:300]}")
            integration = r.json()["integrations"][0]
            log("ghost integration created")
        key = next(k for k in integration["api_keys"] if k["type"] == "admin")
    # Ghost 6 already returns the full "id:secret" in the secret field; older versions return only the secret.
    admin_key = key["secret"] if ":" in key["secret"] else f"{key['id']}:{key['secret']}"
    return {"base_url": GHOST_URL, "admin_api_key": admin_key}


def main() -> None:
    result = {"lemmy": seed_lemmy(), "ghost": seed_ghost()}
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    log(f"wrote {OUTPUT} (lemmy community id {result['lemmy']['community_id']}, ghost key ...{result['ghost']['admin_api_key'][-4:]})")


if __name__ == "__main__":
    main()
