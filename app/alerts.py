"""Alerts: a ticket that a person can see when something needs a human (a destination was demoted, or recovered).

Every alert is stored (GET /api/v1/alerts), written to the log as a WARNING, and, when ALERT_WEBHOOK_URL is set, also sent to that
address signed with HMAC-SHA256 in the X-IncuBrix-Signature header (the same way job callbacks are signed). Sending is best effort:
a webhook that is down never blocks or fails the work that raised the alert; the stored alert is the record.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Callable

import httpx
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from app.db import alerts as t
from app.errors import ErrorCode, IncuBrixError

log = logging.getLogger("incubrix.alerts")


class AlertService:
    def __init__(self, engine: Engine, http: httpx.Client | None = None, webhook_url: str | None = None, secret: str = "",
                 clock: Callable[[], float] = time.time) -> None:
        self.engine, self.http, self.webhook_url, self.secret, self.clock = engine, http, webhook_url, secret, clock

    def raise_alert(self, kind: str, destination_id: str | None, message: str, severity: str = "warning", details: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self.clock()
        with self.engine.begin() as conn:
            alert_id = conn.execute(insert(t).values(created_at=now, kind=kind, severity=severity, destination_id=destination_id, message=message, details=details or {})).inserted_primary_key[0]
        alert = self.get(alert_id)
        (log.warning if severity != "info" else log.info)("alert: %s", message, extra={"alert_kind": kind, "destination": destination_id})
        self._send_webhook(alert)
        return alert

    def get(self, alert_id: int) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(select(t).where(t.c.id == alert_id)).mappings().first()
        if row is None:
            raise IncuBrixError(ErrorCode.NOT_FOUND, f"no alert {alert_id}")
        return dict(row)

    def list(self, unacknowledged_only: bool = False, destination_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = select(t).order_by(t.c.created_at.desc(), t.c.id.desc()).limit(limit)
        if unacknowledged_only:
            query = query.where(t.c.acknowledged_at.is_(None))
        if destination_id:
            query = query.where(t.c.destination_id == destination_id)
        with self.engine.begin() as conn:
            return [dict(r) for r in conn.execute(query).mappings()]

    def acknowledge(self, alert_id: int, by: str) -> dict[str, Any]:
        if not (by or "").strip():
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "acknowledged_by (who read it) is required")
        self.get(alert_id)
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.id == alert_id, t.c.acknowledged_at.is_(None)).values(acknowledged_at=self.clock(), acknowledged_by=by.strip()))
        return self.get(alert_id)

    def _send_webhook(self, alert: dict[str, Any]) -> None:
        if not self.webhook_url or self.http is None:
            return
        body = json.dumps({k: alert[k] for k in ("id", "kind", "severity", "destination_id", "message", "details", "created_at")}, sort_keys=True).encode()
        signature = "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        for attempt in (1, 2):
            try:
                if self.http.post(self.webhook_url, content=body, headers={"Content-Type": "application/json", "X-IncuBrix-Signature": signature}, timeout=5).status_code < 300:
                    return
            except httpx.HTTPError:
                pass
        log.warning("alert webhook could not be delivered (the alert itself is stored)", extra={"alert_id": alert["id"]})
