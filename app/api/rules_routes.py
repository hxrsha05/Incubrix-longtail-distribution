"""AI rules capture: propose from a rules page, review, confirm or reject. Only a confirmation changes a destination."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.errors import ErrorCode, IncuBrixError
from app.idempotency import request_hash

router = APIRouter(prefix="/api/v1")


class CaptureRequest(BaseModel):
    url: str | None = None
    text: str | None = None


class ConfirmRequest(BaseModel):
    reviewer: str = Field(min_length=1, description="the person confirming; recorded with the decision")
    overrides: dict[str, Any] = Field(default_factory=dict, description="rule values the reviewer sets themselves instead of the proposal's")
    acknowledge_loosening: bool = False
    note: str | None = None


class RejectRequest(BaseModel):
    reviewer: str = Field(min_length=1)
    note: str | None = None


@router.post("/destinations/{dest_id}/rules/capture", status_code=201)
def capture_rules(dest_id: str, body: CaptureRequest, request: Request, idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    """Read a rules page (by address or pasted text) and create a PENDING proposal. Nothing is applied."""
    service, store = request.app.state.rules_capture, request.app.state.idempotency_store
    scope = f"capture:{dest_id}"
    if idempotency_key:
        fingerprint = request_hash(body.model_dump())
        record, created = store.begin(scope, idempotency_key, fingerprint)
        if not created:
            if record.request_hash != fingerprint:
                raise IncuBrixError(ErrorCode.IDEMPOTENCY_KEY_REUSED, "this idempotency key was already used for a different request")
            if record.state == "done":
                return JSONResponse(content=record.response, headers={"Idempotent-Replayed": "true"})
            raise IncuBrixError(ErrorCode.IDEMPOTENCY_IN_PROGRESS, "the same request is still being processed")
    try:
        proposal = service.capture(dest_id, url=body.url, text=body.text)
    except BaseException:
        if idempotency_key:
            store.release(scope, idempotency_key)
        raise
    if idempotency_key:
        store.complete(scope, idempotency_key, proposal)
    return JSONResponse(status_code=201, content=proposal)


@router.get("/destinations/{dest_id}/rules/proposals")
def list_proposals(dest_id: str, request: Request) -> dict[str, Any]:
    return {"items": request.app.state.rules_capture.list(dest_id)}


@router.get("/rule-proposals/{proposal_id}")
def get_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    return request.app.state.rules_capture.get(proposal_id)


@router.post("/rule-proposals/{proposal_id}/confirm")
def confirm_proposal(proposal_id: str, body: ConfirmRequest, request: Request) -> dict[str, Any]:
    return request.app.state.rules_capture.confirm(proposal_id, body.reviewer, overrides=body.overrides, acknowledge_loosening=body.acknowledge_loosening, note=body.note)


@router.post("/rule-proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, body: RejectRequest, request: Request) -> dict[str, Any]:
    return request.app.state.rules_capture.reject(proposal_id, body.reviewer, body.note)
