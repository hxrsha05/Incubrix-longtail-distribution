"""POST /api/v1/publish: publish content to a catalogue destination by its id.

The caller never sends credentials. The service looks the destination up, finds its credentials (never stored in the
catalogue), tailors the content to the destination (optional), and runs the same safe pipeline as the connector endpoint:
idempotency, then the rules, then the platform.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field

from app.errors import ErrorCode, IncuBrixError
from app.models import PublishRequest, Rendition
from app.output_manifest import build_manifest
from app.tailoring import tailor

router = APIRouter(prefix="/api/v1")


class PublishByDestination(BaseModel):
    destination_id: str
    rendition: Rendition
    tailor: bool = Field(default=True, description="fit the content to the destination first (every change is listed in the answer)")
    idempotency_key: str | None = Field(default=None, description="the Idempotency-Key header works too")


@router.post("/publish")
def publish_to_destination(body: PublishByDestination, request: Request, response: Response, idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    key = idempotency_key or body.idempotency_key
    if not key:
        raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "an Idempotency-Key header (or idempotency_key in the body) is required: it is what makes a retry safe")
    state = request.app.state
    started = time.time()
    row = state.catalogue.get(body.destination_id)
    destination = state.catalogue.destination(body.destination_id)
    connector = state.registry.get(destination.connector_key)
    connection = state.certification.credentials.for_destination(destination)
    rendition, tailoring = body.rendition, None
    if body.tailor:
        tailored = tailor(body.rendition, destination, connector.effective_spec(destination), row.get("language"))
        rendition, tailoring = tailored.rendition, {"changes": tailored.changes, "warnings": tailored.warnings}
    result, replayed = state.publish_service.publish(destination.connector_key, PublishRequest(
        rendition=rendition, destination=destination, connection=connection, idempotency_key=key))
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    warnings = (tailoring or {}).get("warnings", [])
    return {"destination_id": body.destination_id, "result": result, "replayed": replayed, "tailoring": tailoring,
            "manifest": build_manifest(kind="publish", started_at=started, finished_at=time.time(), connector_version=connector.manifest.version,
                                       settings={"simulation_mode": state.certification.simulation, "tailored": body.tailor}, warnings=warnings,
                                       ai_generated=body.rendition.ai_generated)}
