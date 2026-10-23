"""MRSS feed connector (tier "feed"): publish into a Media RSS feed that partners and aggregators poll.

The feed host is this service itself (app/feeds.py, app/api/feed_routes.py). connection.base_url is that service's address
and connection.api_key is the feed token. The feed is destination.community or destination.extra["feed_id"] (default "main").
The item's guid comes from the idempotency key, so a retry is the same item and reconcile() can check exactly.
"""
from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, PublishResult, Rendition, StatusResult


def make_guid(idempotency_key: str) -> str:
    return "ik-" + hashlib.sha1(idempotency_key.encode()).hexdigest()[:20]


def feed_of(destination: Destination | None) -> str:
    if destination is None:
        return "main"
    return str(destination.community or destination.extra.get("feed_id") or "main")


@register_connector("feed")
class FeedConnector(BaseConnector):
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        item = {"guid": make_guid(idempotency_key), "title": rendition.title, "description": rendition.body, "link": rendition.link_url,
                "canonical_url": rendition.canonical_url, "media": [m.model_dump() for m in rendition.media], "tags": rendition.tags}
        data = self._api("POST", f"/api/v1/feeds/{feed_of(destination)}/items", connection, destination, json=item).json()
        return PublishResult(platform_post_id=data["guid"], live_url=data["url"], status="live")

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        try:
            data = self._api("GET", f"/api/v1/feeds/{feed_of(destination)}/items/{make_guid(idempotency_key)}", connection, destination).json()
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return None
            raise
        return PublishResult(platform_post_id=data["guid"], live_url=data["url"], status="live")

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        feed = connection.extra.get("feed_id", "main")
        try:
            data = self._api("GET", f"/api/v1/feeds/{feed}/items/{platform_post_id}", connection).json()
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return StatusResult(status="not_found", live_url=None)
            raise
        return StatusResult(status="live", live_url=data["url"])

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        feed = connection.extra.get("feed_id", "main")
        try:
            self._api("DELETE", f"/api/v1/feeds/{feed}/items/{platform_post_id}", connection)
        except IncuBrixError as exc:
            if exc.details.get("http_status") != 404:
                raise
        return DeleteResult(deleted=True)

    def _api(self, method: str, path: str, connection: Connection, destination: Destination | None = None, **kwargs: Any) -> httpx.Response:
        if not connection.api_key:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no feed token")
        return self._call(method, f"{self.instance_url(connection, destination)}{path}", headers={"X-Feed-Token": connection.api_key.get_secret_value()}, **kwargs)
