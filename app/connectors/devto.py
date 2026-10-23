"""Dev.to (Forem) connector. The address defaults to https://dev.to unless connection.base_url says otherwise.

Authentication: header api-key (connection.api_key).
Dev.to has NO delete for articles. The only removal it offers is unpublishing (published: false), so delete() does that
and a later status check reports "draft". This is documented in the evidence log.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.connectors.base import BaseConnector, markdown_images
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, MetricsResult, PublishResult, Rendition, StatusResult

DEFAULT_BASE = "https://dev.to"
MAX_TAGS = 4


def clean_tags(tags: list[str]) -> str:
    """Dev.to wants one comma-separated string, at most 4 tags, letters and digits only."""
    cleaned = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in tags]
    return ",".join([t for t in cleaned if t][:MAX_TAGS])


def _markdown(rendition: Rendition) -> str:
    body = rendition.body
    if rendition.link_url and rendition.link_url not in body:
        body = f"{body}\n\n{rendition.link_url}".strip()
    images = markdown_images(rendition)
    return f"{body}\n\n{images}".strip() if images else body


def _result(article: dict[str, Any]) -> PublishResult:
    return PublishResult(platform_post_id=str(article["id"]), live_url=article.get("url") or "", status="live" if article.get("published") else "draft")


@register_connector("devto")
class DevToConnector(BaseConnector):
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        article: dict[str, Any] = {"title": rendition.title, "body_markdown": _markdown(rendition), "published": True}
        if rendition.tags:
            article["tags"] = clean_tags(rendition.tags)
        if rendition.canonical_url:
            article["canonical_url"] = rendition.canonical_url
        return _result(self._api("POST", "/api/articles", connection, destination, json={"article": article}).json())

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """Did an earlier timed-out attempt create the article? Look in our own articles for the same title."""
        for article in self._mine(connection, destination):
            if article["title"] == rendition.title:
                return _result(article)
        return None

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        for article in self._mine(connection):
            if str(article["id"]) == str(platform_post_id):
                if article.get("published"):
                    return StatusResult(status="live", live_url=article.get("url"))
                return StatusResult(status="draft", live_url=None)  # unpublished: not visible to the public
        return StatusResult(status="not_found", live_url=None)

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        """Dev.to cannot delete: unpublish instead."""
        try:
            self._api("PUT", f"/api/articles/{platform_post_id}", connection, json={"article": {"published": False}})
        except IncuBrixError as exc:
            if exc.details.get("http_status") != 404:
                raise
        return DeleteResult(deleted=True)

    def fetch_metrics(self, platform_post_id: str, connection: Connection, since: str | None) -> MetricsResult:
        for article in self._mine(connection):
            if str(article["id"]) == str(platform_post_id):
                return MetricsResult(views=article.get("page_views_count", 0), likes=article.get("public_reactions_count", 0), comments=article.get("comments_count", 0))
        raise IncuBrixError(ErrorCode.NOT_FOUND, "article not found")

    # ---------- helpers ----------
    def _mine(self, connection: Connection, destination: Destination | None = None) -> list[dict[str, Any]]:
        return self._api("GET", "/api/articles/me/all", connection, destination, params={"per_page": 1000}).json()

    def _api(self, method: str, path: str, connection: Connection, destination: Destination | None = None, **kwargs: Any) -> httpx.Response:
        if not connection.api_key:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no Dev.to API key")
        base = (connection.base_url or (destination.base_url if destination else None) or DEFAULT_BASE).rstrip("/")
        headers = {"api-key": connection.api_key.get_secret_value(), "Accept": "application/vnd.forem.api-v1+json"}
        return self._call(method, f"{base}{path}", headers=headers, **kwargs)
