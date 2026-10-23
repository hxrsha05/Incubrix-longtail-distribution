"""Tailoring and relevance: POST /api/v1/tailor, POST /api/v1/relevance."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.errors import ErrorCode, IncuBrixError
from app.models import Rendition
from app.recommender import Recommendation, recommend
from app.relevance import rank
from app.tailoring import TailoredResult, tailor

router = APIRouter(prefix="/api/v1")


class TailorRequest(BaseModel):
    rendition: Rendition
    destination_id: str


class RelevanceRequest(BaseModel):
    rendition: Rendition
    destination_ids: list[str] | None = None  # default: the whole catalogue
    top: int | None = Field(default=None, ge=1, le=1000)
    connector_key: str | None = None
    status: str | None = None


@router.post("/tailor")
def tailor_for_destination(body: TailorRequest, request: Request) -> TailoredResult:
    """Fit the content to one catalogue destination and list every change made."""
    row = request.app.state.catalogue.get(body.destination_id)
    destination = request.app.state.catalogue.destination(body.destination_id)
    connector = request.app.state.registry.get(destination.connector_key)
    return tailor(body.rendition, destination, connector.effective_spec(destination), row.get("language"))


@router.post("/relevance")
def relevance(body: RelevanceRequest, request: Request) -> dict[str, Any]:
    """Score 1-5 with an explanation for each destination, best first."""
    catalogue = request.app.state.catalogue
    if body.destination_ids is not None:
        rows = [catalogue.get(i) for i in body.destination_ids]
    else:
        rows = catalogue.list(connector_key=body.connector_key, status=body.status, limit=5000)["items"]
    if not rows:
        raise IncuBrixError(ErrorCode.NOT_FOUND, "no destinations to score")
    return {"items": [r.model_dump() for r in rank(body.rendition, rows, body.top)], "scored": len(rows)}


class RecommendRequest(BaseModel):
    rendition: Rendition
    top: int = Field(default=10, ge=1, le=100)
    status: str | None = None  # e.g. "certified" to suggest only proven destinations
    connector_key: str | None = None


@router.post("/recommend")
def recommend_destinations(body: RecommendRequest, request: Request) -> dict[str, Any]:
    """Which destinations should this content go to? Best first; destinations that would refuse it are left out."""
    rows = request.app.state.catalogue.list(connector_key=body.connector_key, status=body.status, limit=5000)["items"]
    items: list[Recommendation] = recommend(body.rendition, rows, body.top, history=request.app.state.catalogue.run_stats())
    return {"items": [i.model_dump() for i in items], "considered": len(rows)}
