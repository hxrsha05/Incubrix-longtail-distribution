"""Alerts raised when something needs a person (a destination demoted or recovered)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1")


class Acknowledge(BaseModel):
    acknowledged_by: str = Field(min_length=1)


@router.get("/alerts")
def list_alerts(request: Request, unacknowledged: bool = False, destination_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    items = request.app.state.alerts.list(unacknowledged, destination_id, max(1, min(limit, 500)))
    return {"items": items, "count": len(items)}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, body: Acknowledge, request: Request) -> dict[str, Any]:
    return request.app.state.alerts.acknowledge(alert_id, body.acknowledged_by)
