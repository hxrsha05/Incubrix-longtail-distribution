"""Recertification: see what is due, run it now, and read the last run."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1")


@router.get("/recertification")
def recertification_status(request: Request) -> dict[str, Any]:
    """The schedule, what is due right now, and the summary of the last run."""
    r, scheduler = request.app.state.recertifier, request.app.state.scheduler
    return {"scheduler": {"enabled": scheduler.enabled, "interval_s": scheduler.interval_s},
            "policy": {"max_age_s": r.max_age_s, "demoted_cooldown_s": r.demoted_cooldown_s, "attention_retry_s": r.attention_retry_s, "batch_limit": r.batch_limit,
                       "uncertified": "never rechecked automatically: the first certification needs a person's decision"},
            "due_now": r.due(), "last_run": r.last_run}


@router.post("/recertify", status_code=202)
def recertify_now(request: Request, dry_run: bool = False, force: bool = False, idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    """Start a recertification run as a background job (dry_run=true only lists what would be checked; force=true ignores ages and cooldowns, never includes uncertified)."""
    job, created = request.app.state.jobs.submit("recertify", {"dry_run": dry_run, "force": force}, None, idempotency_key)
    return JSONResponse(status_code=202, content=job, headers={} if created else {"Idempotent-Replayed": "true"})
