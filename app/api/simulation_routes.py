"""Simulation mode controls: make the fake platforms fail on purpose, through the same API (and token) as everything else.

Only available when SIMULATION_MODE=true. They forward to the mock platform server's failure switches, so a reviewer of a public demo can
force a timeout, a 429 with a reset time or a renamed field and watch how the service handles it, without access to the machine.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.errors import ErrorCode, IncuBrixError

router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])


class FailureSwitch(BaseModel):
    platform: str = Field(description="discourse | devto | partner | hashnode | kit")
    op: str = Field(default="any", description="create | read | update | delete | any")
    kind: str = Field(default="status", description="status | timeout_before | timeout_after | rename_field")
    status: int = 500
    retry_after: float | None = Field(default=None, description="seconds the platform says to wait (status 429)")
    delay_s: float = Field(default=3.0, description="how late the answer comes (timeout_*)")
    count: int | None = Field(default=None, description="how many times it fires (default once; a renamed field stays until reset)")
    rename: dict[str, str] = Field(default_factory=dict, description='for kind rename_field, for example {"topic_id": "topic_identifier"}')


def _mock(request: Request, method: str, path: str, **kwargs: Any) -> Any:
    settings = request.app.state.settings
    if not settings.simulation_mode:
        raise IncuBrixError(ErrorCode.NOT_FOUND, "simulation mode is off, so there are no fake platforms to control")
    headers = {"X-Mock-Token": settings.mock_control_token.get_secret_value()} if settings.mock_control_token else {}
    try:
        resp = request.app.state.simulation_http.request(method, f"{settings.mock_platforms_url.rstrip('/')}{path}", headers=headers, timeout=10, **kwargs)
    except httpx.HTTPError as exc:
        raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the fake platforms are not reachable ({type(exc).__name__})") from None
    if resp.status_code >= 400:
        raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the fake platforms refused the request (HTTP {resp.status_code})")
    return resp.json()


@router.post("/fail")
def add_failure(body: FailureSwitch, request: Request) -> dict[str, Any]:
    """Arm one failure on a fake platform. It fires on that platform's next matching call, then clears (unless count says more)."""
    payload = {k: v for k, v in body.model_dump().items() if v is not None and v != {}}
    return _mock(request, "POST", "/__control/fail", json=payload)


@router.delete("/fail")
def clear_failures(request: Request) -> dict[str, Any]:
    return _mock(request, "DELETE", "/__control/fail")


@router.post("/reset")
def reset_platforms(request: Request) -> dict[str, Any]:
    """Wipe the fake platforms (posts, failure switches) back to empty."""
    return _mock(request, "POST", "/__control/reset")


@router.get("/state")
def platform_state(request: Request) -> dict[str, Any]:
    """What the fake platforms hold right now (for example how many topics exist: the proof that a retry created no duplicate)."""
    return _mock(request, "GET", "/__control/state")
