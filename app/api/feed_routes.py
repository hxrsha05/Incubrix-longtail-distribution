"""The feed host: GET /feeds/{feed_id}.xml is public; adding and removing items needs the feed token."""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field

from app.errors import ErrorCode, IncuBrixError

router = APIRouter()


class FeedItemIn(BaseModel):
    guid: str = Field(min_length=1, max_length=100)
    title: str
    description: str = ""
    link: str | None = None
    canonical_url: str | None = None
    media: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def _authorize(request: Request, token: str | None) -> None:
    expected = request.app.state.feed_token
    if not token or not hmac.compare_digest(token, expected):
        raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "missing or wrong feed token (header X-Feed-Token)")


def _public(request: Request, feed_id: str, item: dict[str, Any]) -> dict[str, Any]:
    base = request.app.state.service_base_url.rstrip("/")
    return {"guid": item["guid"], "title": item["title"], "url": f"{base}/feeds/{feed_id}.xml#{item['guid']}", "status": "live"}


@router.get("/feeds/{feed_name}.xml")
def read_feed(feed_name: str, request: Request) -> Response:
    xml = request.app.state.feeds.render(feed_name, request.app.state.service_base_url)
    return Response(xml, media_type="application/rss+xml")


@router.post("/api/v1/feeds/{feed_id}/items")
def add_item(feed_id: str, body: FeedItemIn, request: Request, response: Response, x_feed_token: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(request, x_feed_token)
    if not body.title.strip():
        raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "title is required")
    item, created = request.app.state.feeds.add(feed_id, body.model_dump())
    response.status_code = 201 if created else 200
    return _public(request, feed_id, item)


@router.get("/api/v1/feeds/{feed_id}/items/{guid}")
def get_item(feed_id: str, guid: str, request: Request, x_feed_token: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(request, x_feed_token)
    return _public(request, feed_id, request.app.state.feeds.get(feed_id, guid))


@router.delete("/api/v1/feeds/{feed_id}/items/{guid}", status_code=204)
def delete_item(feed_id: str, guid: str, request: Request, x_feed_token: str | None = Header(default=None)) -> Response:
    _authorize(request, x_feed_token)
    request.app.state.feeds.remove(feed_id, guid)
    return Response(status_code=204)
