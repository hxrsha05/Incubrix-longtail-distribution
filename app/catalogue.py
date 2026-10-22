"""The destination catalogue: one row per forum or site.

A new forum is a new row (a Destination plus a language, topics and a certification status), never new code.
Status moves: uncertified -> certified (a certification run passed) -> demoted (a later run failed because the
platform changed under us) ; needs_attention means the last run was inconclusive (rate limit, expired token, outage).
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from app.db import certification_runs, destinations
from app.errors import ErrorCode, IncuBrixError
from app.models import Destination

STATUSES = ("uncertified", "certified", "demoted", "needs_attention")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100]
    if not slug:
        raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "destination name must contain letters or digits")
    return slug


class Catalogue:
    def __init__(self, engine: Engine, clock: Callable[[], float] = time.time) -> None:
        self.engine, self.clock = engine, clock

    # ---------- rows ----------
    def upsert(self, destination: Destination, language: str | None = None, topics: list[str] | None = None) -> tuple[dict[str, Any], bool]:
        """Create or update by name. Returns (row, created). A changed row keeps its certification status."""
        dest_id, now = slugify(destination.name), self.clock()
        values = {"name": destination.name, "connector_key": destination.connector_key, "destination": destination.model_dump(mode="json"),
                  "language": language, "topics": topics or [], "updated_at": now}
        with self.engine.begin() as conn:
            exists = conn.execute(select(destinations.c.id).where(destinations.c.id == dest_id)).first() is not None
            if exists:
                conn.execute(update(destinations).where(destinations.c.id == dest_id).values(**values))
            else:
                conn.execute(insert(destinations).values(id=dest_id, status="uncertified", created_at=now, **values))
        return self.get(dest_id), not exists

    def get(self, dest_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(select(destinations).where(destinations.c.id == dest_id)).mappings().first()
        if row is None:
            raise IncuBrixError(ErrorCode.NOT_FOUND, f"no destination {dest_id!r}")
        return _public(row)

    def destination(self, dest_id: str) -> Destination:
        return Destination.model_validate(self.get(dest_id)["destination"])

    def list(self, connector_key: str | None = None, status: str | None = None, language: str | None = None,
             q: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        where = []
        if connector_key:
            where.append(destinations.c.connector_key == connector_key)
        if status:
            where.append(destinations.c.status == status)
        if language:
            where.append(destinations.c.language == language)
        if q:
            where.append(destinations.c.name.ilike(f"%{q}%"))
        with self.engine.begin() as conn:
            total = conn.execute(select(func.count()).select_from(destinations).where(*where)).scalar()
            rows = conn.execute(select(destinations).where(*where).order_by(destinations.c.name).limit(limit).offset(offset)).mappings().all()
        return {"total": total, "items": [_public(r) for r in rows]}

    def delete(self, dest_id: str) -> None:
        self.get(dest_id)
        with self.engine.begin() as conn:
            conn.execute(delete(destinations).where(destinations.c.id == dest_id))

    # ---------- certification ----------
    def record_run(self, dest_id: str, record: dict[str, Any]) -> None:
        """Store the run and move the destination's status according to its verdict."""
        verdict = record["verdict"]
        with self.engine.begin() as conn:
            conn.execute(insert(certification_runs).values(run_id=record["run_id"], destination_id=dest_id, verdict=verdict, record=record, started_at=record["started_at"]))
        if verdict == "passed":
            self._set_status(dest_id, "certified", None, certified=True)
        elif verdict == "failed":
            self._set_status(dest_id, "demoted", record.get("failure", {}).get("summary", "certification failed"))
        else:
            self._set_status(dest_id, "needs_attention", record.get("failure", {}).get("summary", "certification inconclusive"))

    def runs(self, dest_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.get(dest_id)
        with self.engine.begin() as conn:
            rows = conn.execute(select(certification_runs.c.record).where(certification_runs.c.destination_id == dest_id)
                                .order_by(certification_runs.c.started_at.desc()).limit(limit)).all()
        return [r[0] for r in rows]

    def _set_status(self, dest_id: str, status: str, reason: str | None, certified: bool = False) -> None:
        values: dict[str, Any] = {"status": status, "status_reason": reason, "updated_at": self.clock()}
        if certified:
            values["last_certified_at"] = self.clock()
        with self.engine.begin() as conn:
            conn.execute(update(destinations).where(destinations.c.id == dest_id).values(**values))


def _public(row: Any) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "name", "connector_key", "destination", "language", "topics", "status", "status_reason", "last_certified_at", "created_at", "updated_at")}
