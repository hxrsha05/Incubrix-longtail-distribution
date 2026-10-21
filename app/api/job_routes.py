"""POST /api/v1/jobs and GET /api/v1/jobs/{job_id}."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1")


class JobRequest(BaseModel):
    kind: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    callback_url: str | None = None


@router.post("/jobs", status_code=202)
def create_job(body: JobRequest, request: Request, idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    job, created = request.app.state.jobs.submit(body.kind, body.params, body.callback_url, idempotency_key)
    headers = {} if created else {"Idempotent-Replayed": "true"}
    return JSONResponse(status_code=202, content=job, headers=headers)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    return request.app.state.jobs.get(job_id)
