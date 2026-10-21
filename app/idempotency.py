"""Idempotency store: the same key never causes the work to happen twice.

States of one (scope, key):
  pending  - someone is doing the work right now
  done     - finished; the stored response is replayed on every retry
  unknown  - the work MAY have happened (timeout, crash) - never blindly redo it
A definite failure (we know nothing was created) deletes the row, so a retry can try again safely.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db import idempotency as t


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class IdempotencyRecord:
    scope: str
    key: str
    request_hash: str
    state: str
    response: dict[str, Any] | None


class IdempotencyStore:
    def __init__(self, engine: Engine, pending_timeout_s: float = 120.0, clock: Callable[[], float] = time.time) -> None:
        self.engine = engine
        self.pending_timeout_s = pending_timeout_s
        self.clock = clock

    def begin(self, scope: str, key: str, req_hash: str) -> tuple[IdempotencyRecord, bool]:
        """Try to claim (scope, key). Returns (record, created). created=True means the caller owns the work."""
        now = self.clock()
        try:
            with self.engine.begin() as conn:  # the primary key makes this claim atomic
                conn.execute(
                    insert(t).values(scope=scope, key=key, request_hash=req_hash, state="pending", created_at=now, updated_at=now)
                )
            return IdempotencyRecord(scope, key, req_hash, "pending", None), True
        except IntegrityError:
            pass

        with self.engine.begin() as conn:
            row = conn.execute(select(t).where(t.c.scope == scope, t.c.key == key)).mappings().first()
            if row is None:  # released between our insert and our select: try again
                return self.begin(scope, key, req_hash)
            state = row["state"]
            if state == "pending" and now - row["updated_at"] > self.pending_timeout_s:
                # the worker died mid-job: we can no longer assume it did or didn't finish
                conn.execute(update(t).where(t.c.scope == scope, t.c.key == key, t.c.state == "pending").values(state="unknown"))
                state = "unknown"
        return IdempotencyRecord(scope, key, row["request_hash"], state, row["response"]), False

    def complete(self, scope: str, key: str, response: dict[str, Any]) -> None:
        self._set(scope, key, state="done", response=response)

    def mark_unknown(self, scope: str, key: str) -> None:
        self._set(scope, key, state="unknown")

    def restart(self, scope: str, key: str) -> bool:
        """unknown -> pending. Only one caller wins, so two retries can't both redo the work."""
        with self.engine.begin() as conn:
            res = conn.execute(
                update(t).where(t.c.scope == scope, t.c.key == key, t.c.state == "unknown").values(state="pending", updated_at=self.clock())
            )
        return res.rowcount == 1

    def release(self, scope: str, key: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(t).where(t.c.scope == scope, t.c.key == key))

    def _set(self, scope: str, key: str, **values: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.scope == scope, t.c.key == key).values(updated_at=self.clock(), **values))
