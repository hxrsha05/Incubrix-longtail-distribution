"""Discourse connector. One connector serves every Discourse forum: the address is connection.base_url.

Authentication: headers Api-Key (connection.api_key) and Api-Username (connection.username).
A post here is a new TOPIC; the platform_post_id we return is the topic id.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.connectors.base import BaseConnector, markdown_images
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, MetricsResult, PublishResult, Rendition, StatusResult


def _raw(rendition: Rendition) -> str:
    raw = rendition.body
    if rendition.link_url and rendition.link_url not in raw:
        raw = f"{raw}\n\n{rendition.link_url}".strip()
    images = markdown_images(rendition)
    return f"{raw}\n\n{images}".strip() if images else raw


@register_connector("discourse")
class DiscourseConnector(BaseConnector):
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        payload: dict[str, Any] = {"title": rendition.title, "raw": _raw(rendition)}
        if destination.community:  # the forum category id
            payload["category"] = int(destination.community) if str(destination.community).isdigit() else destination.community
        if rendition.tags:
            payload["tags"] = rendition.tags
        data = self._api("POST", "/posts.json", connection, destination, json=payload).json()
        return self._result(connection, destination, data["topic_id"], data["topic_slug"])

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """Did an earlier timed-out attempt create the topic? Look through our own topics for the same title.
        (Discourse refuses a duplicate title, so a title match is a reliable sign.)"""
        user = self._username(connection)
        topics = self._api("GET", f"/topics/created-by/{user}.json", connection, destination).json()["topic_list"]["topics"]
        for topic in topics:
            if topic["title"] == rendition.title:
                return self._result(connection, destination, topic["id"], topic["slug"])
        return None

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        try:
            topic = self._api("GET", f"/t/{platform_post_id}.json", connection).json()
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return StatusResult(status="not_found", live_url=None)
            raise
        return StatusResult(status="live", live_url=f"{self.instance_url(connection)}/t/{topic['slug']}/{topic['id']}")

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        try:
            self._api("DELETE", f"/t/{platform_post_id}.json", connection)
        except IncuBrixError as exc:
            if exc.details.get("http_status") != 404:
                raise  # 404 means it is already gone
        return DeleteResult(deleted=True)

    def fetch_metrics(self, platform_post_id: str, connection: Connection, since: str | None) -> MetricsResult:
        topic = self._api("GET", f"/t/{platform_post_id}.json", connection).json()
        return MetricsResult(views=topic.get("views", 0), likes=topic.get("like_count", 0), comments=topic.get("reply_count", 0))

    # ---------- helpers ----------
    def _result(self, connection: Connection, destination: Destination | None, topic_id: int, slug: str) -> PublishResult:
        base = self.instance_url(connection, destination)
        return PublishResult(platform_post_id=str(topic_id), live_url=f"{base}/t/{slug}/{topic_id}", status="live")

    @staticmethod
    def _username(connection: Connection) -> str:
        if not connection.username:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no Discourse username")
        return connection.username

    def _api(self, method: str, path: str, connection: Connection, destination: Destination | None = None, **kwargs: Any) -> httpx.Response:
        if not connection.api_key:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no Discourse API key")
        headers = {"Api-Key": connection.api_key.get_secret_value(), "Api-Username": self._username(connection)}
        return self._call(method, f"{self.instance_url(connection, destination)}{path}", headers=headers, **kwargs)
