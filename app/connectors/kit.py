"""Kit (email newsletter) connector, API v4 broadcasts.

SAFETY: an email goes to real people, so this connector NEVER sends immediately. The default is a DRAFT in the Kit account
(a person reviews and sends it there). A destination may ask for a scheduled send with extra.send_mode = "schedule" and
extra.send_at (a future time, ISO 8601); deleting a scheduled broadcast cancels it. Certification uses drafts only, so it
can never email a subscriber.

  endpoint  connection.base_url (default https://api.kit.com)      auth  header X-Kit-Api-Key = connection.api_key
HONESTY NOTE: built against Kit's documented v4 API and a mock; it has not been run against a real Kit account.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.connectors.base import BaseConnector
from app.connectors.ghost import to_html
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, MetricsResult, PublishResult, Rendition, StatusResult

DEFAULT_BASE = "https://api.kit.com"


def _preview(rendition: Rendition) -> str:
    text = " ".join((rendition.body or rendition.title).split())
    return text[:140]  # the inbox preview line


@register_connector("kit")
class KitConnector(BaseConnector):
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        # every field below is marked required in Kit's API reference for creating a broadcast (checked against developers.kit.com)
        body: dict[str, Any] = {"subject": rendition.title, "content": to_html(rendition), "public": False, "send_at": None,
                                "description": f"IncuBrix {idempotency_key}"[:120], "preview_text": _preview(rendition),
                                "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "subscriber_filter": destination.extra.get("subscriber_filter") or [{"all": [], "any": [], "none": []}]}
        mode = destination.extra.get("send_mode", "draft")
        if mode == "schedule":
            if not destination.extra.get("send_at"):
                raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "send_mode 'schedule' needs extra.send_at (a future time)")
            body["send_at"] = destination.extra["send_at"]
        elif mode != "draft":
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"send_mode must be 'draft' or 'schedule', got {mode!r}; this connector never sends immediately")
        data = self._api("POST", "/v4/broadcasts", connection, destination, json=body).json()["broadcast"]
        return self._result(connection, destination, data)

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """Did an earlier timed-out attempt create the broadcast? Kit has no idempotency, so look for the same subject."""
        listing = self._api("GET", "/v4/broadcasts", connection, destination, params={"per_page": 100}).json()["broadcasts"]
        for item in listing:
            if item["subject"] == rendition.title:
                return self._result(connection, destination, item)
        return None

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        try:
            data = self._api("GET", f"/v4/broadcasts/{platform_post_id}", connection).json()["broadcast"]
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return StatusResult(status="not_found", live_url=None)
            raise
        status = self._status(data)
        return StatusResult(status=status, live_url=self._url(connection, None, data["id"], data.get("public_url")) if status == "live" else None)

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        try:
            self._api("DELETE", f"/v4/broadcasts/{platform_post_id}", connection)
        except IncuBrixError as exc:
            if exc.details.get("http_status") != 404:
                raise
        return DeleteResult(deleted=True)

    def fetch_metrics(self, platform_post_id: str, connection: Connection, since: str | None) -> MetricsResult:
        stats = self._api("GET", f"/v4/broadcasts/{platform_post_id}/stats", connection).json()["broadcast"]["stats"]
        return MetricsResult(views=stats.get("emails_opened", 0), clicks=stats.get("total_clicks", 0))  # an email "view" is an open

    # ---------- helpers ----------
    @staticmethod
    def _status(data: dict[str, Any]) -> str:
        if data.get("published_at"):
            return "live"
        return "scheduled" if data.get("send_at") else "draft"

    def _url(self, connection: Connection, destination: Destination | None, broadcast_id: Any, public_url: str | None = None) -> str:
        if public_url:
            return public_url  # Kit gives a public page once a broadcast is public
        base = (connection.base_url or (destination.base_url if destination else None) or DEFAULT_BASE).rstrip("/")
        return f"{base}/v4/broadcasts/{broadcast_id}"  # no public page for a draft: the API address of the broadcast

    def _result(self, connection: Connection, destination: Destination | None, data: dict[str, Any]) -> PublishResult:
        return PublishResult(platform_post_id=str(data["id"]), live_url=self._url(connection, destination, data["id"], data.get("public_url")), status=self._status(data))

    def _api(self, method: str, path: str, connection: Connection, destination: Destination | None = None, **kwargs: Any) -> httpx.Response:
        if not connection.api_key:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no Kit API key")
        base = (connection.base_url or (destination.base_url if destination else None) or DEFAULT_BASE).rstrip("/")
        return self._call(method, f"{base}{path}", headers={"X-Kit-Api-Key": connection.api_key.get_secret_value()}, **kwargs)
