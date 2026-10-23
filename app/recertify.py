"""Recertification (Outstanding level): destinations are re-checked on a schedule, and a failure demotes them automatically.

Who is re-checked, and when:
  certified         when its last pass is older than RECERT_MAX_AGE_S (a platform can change under us at any time)
  demoted           after RECERT_DEMOTED_COOLDOWN_S, because the cause may be fixed: a pass promotes it back automatically
  needs_attention   after RECERT_ATTENTION_RETRY_S (rate limit, expired token or outage are usually temporary)
  uncertified       NEVER automatically. Certification publishes a test post, so the first one needs a person's decision.
A demotion has teeth: a demoted destination is refused at publish time (DEST_SPEC_VIOLATION, rule "destination_demoted") and
is left out of recommendations until a later run passes. A run checks at most RECERT_BATCH_LIMIT destinations (oldest first),
and the batch stops hitting a connector after repeated failures (it does not hammer a platform that is down).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from app.catalogue import Catalogue
from app.certify_service import CertificationService

log = logging.getLogger("incubrix.recertify")


class Recertifier:
    def __init__(self, catalogue: Catalogue, service: CertificationService, *, max_age_s: float = 7 * 86400, demoted_cooldown_s: float = 86400,
                 attention_retry_s: float = 3600, batch_limit: int = 25, clock: Callable[[], float] = time.time) -> None:
        self.catalogue, self.service, self.clock = catalogue, service, clock
        self.max_age_s, self.demoted_cooldown_s, self.attention_retry_s, self.batch_limit = max_age_s, demoted_cooldown_s, attention_retry_s, batch_limit
        self.last_run: dict[str, Any] | None = None
        self._lock = threading.Lock()  # two runs at once would double the test posts

    def due(self, force: bool = False) -> list[dict[str, Any]]:
        """force=True ignores the ages and cooldowns (for a demo or an urgent check); uncertified destinations are still never included."""
        now = self.clock()
        max_age, cooldown, retry = (0, 0, 0) if force else (self.max_age_s, self.demoted_cooldown_s, self.attention_retry_s)
        attempts = self.catalogue.last_attempts()
        out = []
        for row in self.catalogue.list(limit=100000)["items"]:
            status, last_try = row["status"], attempts.get(row["id"])
            if status == "certified" and now - (row["last_certified_at"] or 0) >= max_age:
                out.append({"destination_id": row["id"], "status": status, "reason": f"last certified more than {self.max_age_s:.0f} s ago", "since": row["last_certified_at"]})
            elif status == "demoted" and last_try is not None and now - last_try >= cooldown:
                out.append({"destination_id": row["id"], "status": status, "reason": "cooldown over: it may have been fixed", "since": last_try})
            elif status == "needs_attention" and last_try is not None and now - last_try >= retry:
                out.append({"destination_id": row["id"], "status": status, "reason": "retrying after a temporary problem", "since": last_try})
        out.sort(key=lambda d: d["since"] or 0)  # the longest-waiting first
        return out

    def run_once(self, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
        with self._lock:
            started = self.clock()
            due = self.due(force)
            chosen = due[: self.batch_limit]
            summary: dict[str, Any] = {"started_at": started, "due": len(due), "checked": 0, "dry_run": dry_run, "force": force, "would_check": [d["destination_id"] for d in chosen],
                                       "transitions": [], "skipped": [], "still_certified": 0, "demoted": 0, "promoted": 0, "needs_attention": 0}
            if dry_run or not chosen:
                summary["finished_at"] = self.clock()
                self.last_run = summary
                return summary
            before = {d["destination_id"]: d["status"] for d in chosen}
            batch = self.service.certify_many([d["destination_id"] for d in chosen])
            summary["skipped"] = batch["skipped"]
            for result in batch["results"]:
                dest_id = result["destination_id"]
                after = self.catalogue.get(dest_id)["status"]
                summary["checked"] += 1
                if after != before[dest_id]:
                    summary["transitions"].append({"destination_id": dest_id, "from": before[dest_id], "to": after, "run": result["run_id"]})
                if after == "certified" and before[dest_id] == "certified":
                    summary["still_certified"] += 1
                elif after == "certified":
                    summary["promoted"] += 1
                elif after == "demoted":
                    summary["demoted"] += 1
                    log.warning("destination demoted by recertification", extra={"destination": dest_id})
                else:
                    summary["needs_attention"] += 1
            summary["finished_at"] = self.clock()
            self.last_run = summary
            return summary


class RecertScheduler:
    """Runs Recertifier.run_once every `interval_s` seconds on a background thread. interval_s <= 0 means off."""

    def __init__(self, recertifier: Recertifier, interval_s: float) -> None:
        self.recertifier, self.interval_s = recertifier, interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.interval_s > 0

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="recertify", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                summary = self.recertifier.run_once()
                log.info("recertification run finished", extra={"checked": summary["checked"], "demoted": summary["demoted"], "promoted": summary["promoted"]})
            except Exception:  # noqa: BLE001 - the scheduler must survive anything one run throws
                log.exception("recertification run crashed")
