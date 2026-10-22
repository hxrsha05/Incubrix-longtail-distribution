"""The destination catalogue API and certification evidence downloads."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.errors import ErrorCode, IncuBrixError
from app.idempotency import request_hash
from app.models import Destination

router = APIRouter(prefix="/api/v1")


class DestinationIn(BaseModel):
    destination: Destination
    language: str | None = None
    topics: list[str] = Field(default_factory=list)


@router.post("/destinations")
def upsert_destination(body: DestinationIn, request: Request, idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    """Add a destination (201) or update the one with the same name (200). Same Idempotency-Key = same answer."""
    catalogue = request.app.state.catalogue
    store = request.app.state.idempotency_store
    if idempotency_key:
        fingerprint = request_hash(body.model_dump(mode="json"))
        record, created = store.begin("destination", idempotency_key, fingerprint)
        if not created:
            if record.request_hash != fingerprint:
                raise IncuBrixError(ErrorCode.IDEMPOTENCY_KEY_REUSED, "this idempotency key was already used for a different request")
            if record.state == "done":
                return JSONResponse(content=record.response, headers={"Idempotent-Replayed": "true"})
            raise IncuBrixError(ErrorCode.IDEMPOTENCY_IN_PROGRESS, "the same request is still being processed")
    try:
        row, created_row = catalogue.upsert(body.destination, body.language, body.topics)
    except BaseException:
        if idempotency_key:
            store.release("destination", idempotency_key)
        raise
    if idempotency_key:
        store.complete("destination", idempotency_key, row)
    return JSONResponse(status_code=201 if created_row else 200, content=row)


@router.get("/destinations")
def list_destinations(request: Request, connector_key: str | None = None, status: str | None = None, language: str | None = None,
                      q: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    return request.app.state.catalogue.list(connector_key, status, language, q, limit, max(offset, 0))


@router.get("/destinations/{dest_id}")
def get_destination(dest_id: str, request: Request) -> dict[str, Any]:
    return request.app.state.catalogue.get(dest_id)


@router.delete("/destinations/{dest_id}", status_code=204)
def delete_destination(dest_id: str, request: Request) -> JSONResponse:
    request.app.state.catalogue.delete(dest_id)
    return JSONResponse(status_code=204, content=None)


@router.post("/destinations/{dest_id}/certify", status_code=202)
def certify_destination(dest_id: str, request: Request, idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    request.app.state.catalogue.get(dest_id)  # 404 now, not inside the job
    job, created = request.app.state.jobs.submit("certify", {"destination_id": dest_id}, None, idempotency_key)
    return JSONResponse(status_code=202, content=job, headers={} if created else {"Idempotent-Replayed": "true"})


@router.get("/destinations/{dest_id}/certifications")
def list_certifications(dest_id: str, request: Request) -> dict[str, Any]:
    return {"items": request.app.state.catalogue.runs(dest_id)}


@router.get("/evidence/{path:path}")
def download_evidence(path: str, request: Request) -> FileResponse:
    file = request.app.state.evidence.read(path)
    if file is None:
        raise IncuBrixError(ErrorCode.NOT_FOUND, "no such evidence file")
    return FileResponse(file)
