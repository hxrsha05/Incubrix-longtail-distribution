"""Lemmy connector (API v3, Lemmy 0.19.x). One connector serves every Lemmy instance: the address comes from
connection.base_url and the community from destination.community, so a new community is a catalogue row."""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, MetricsResult, PublishResult, RefreshResult, Rendition, StatusResult

# Lemmy reports most failures as HTTP 400 with {"error": "<code>"}; the code tells us what really went wrong.
_AUTH_ERRORS = {"not_logged_in", "incorrect_login", "unauthorized", "login_required", "password_incorrect"}
_RATE_LIMIT_ERRORS = {"rate_limit_error"}


@register_connector("lemmy")
class LemmyConnector(BaseConnector):
    LOGIN_ATTEMPTS = 4
    LOGIN_RETRY_DELAY_S = 1.1  # slightly over one second, see _login

    def __init__(self, manifest, http) -> None:
        super().__init__(manifest, http)
        self._jwts: dict[tuple[str, str], str] = {}
        self._community_ids: dict[tuple[str, str], int] = {}

    # ---------- contract methods ----------
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        base = self.instance_url(connection, destination)
        payload: dict[str, Any] = {
            "name": rendition.title,
            "community_id": self._community_id(base, destination, connection),
            "body": rendition.body or None,
            "url": rendition.link_url or rendition.canonical_url,
        }
        data = self._api("POST", "/post", connection, destination, json={k: v for k, v in payload.items() if v is not None}).json()
        return self._to_publish_result(data["post_view"]["post"], connection, base)

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """Did an earlier timed-out attempt create this post? Look at the community's newest posts by our account."""
        base = self.instance_url(connection, destination)
        name = destination.community
        if not name:
            raise NotImplementedError  # cannot look without knowing where
        data = self._api("GET", "/post/list", connection, destination, params={"community_name": name, "sort": "New", "limit": 50}).json()
        me = self._whoami(connection, base)
        for view in data.get("posts", []):
            post = view["post"]
            if view["creator"]["name"] == me and not post["deleted"] and post["name"] == rendition.title and (post.get("body") or "") == (rendition.body or ""):
                return self._to_publish_result(post, connection, base)
        return None

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        base = self.instance_url(connection)
        try:
            post = self._api("GET", "/post", connection, params={"id": platform_post_id}).json()["post_view"]["post"]
        except IncuBrixError as exc:
            if exc.details.get("platform_error") == "couldnt_find_post":
                return StatusResult(status="not_found", live_url=None)
            raise
        status = "deleted" if post["deleted"] else "removed" if post["removed"] else "live"
        return StatusResult(status=status, live_url=self._live_url(connection, base, post["id"]) if status == "live" else None)

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        data = self._api("POST", "/post/delete", connection, json={"post_id": int(platform_post_id), "deleted": True}).json()
        return DeleteResult(deleted=bool(data["post_view"]["post"]["deleted"]))

    def fetch_metrics(self, platform_post_id: str, connection: Connection, since: str | None) -> MetricsResult:
        counts = self._api("GET", "/post", connection, params={"id": platform_post_id}).json()["post_view"]["counts"]
        return MetricsResult(likes=counts.get("upvotes", 0), comments=counts.get("comments", 0))  # Lemmy exposes no views/shares

    def refresh_auth(self, connection: Connection) -> RefreshResult:
        base = self.instance_url(connection)
        self._jwts.pop(self._cache_key(base, connection), None)
        return RefreshResult(expires_at=_jwt_expiry(self._jwt(base, connection)))

    # ---------- helpers ----------
    def _map_error(self, resp: httpx.Response) -> IncuBrixError:
        error = _platform_error(resp)
        if error in _AUTH_ERRORS:
            err = IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "Lemmy rejected the login", {"http_status": resp.status_code})
        elif error in _RATE_LIMIT_ERRORS:
            err = super()._map_error(httpx.Response(429, headers=resp.headers, text=resp.text, request=resp.request))
        else:
            err = super()._map_error(resp)
            if resp.status_code == 400:
                err = IncuBrixError(ErrorCode.PLATFORM_CONTENT_REJECTED, f"Lemmy refused the request: {error or 'bad request'}", err.details)
        if error:
            err.details["platform_error"] = error
        return err

    def _api(self, method: str, path: str, connection: Connection, destination: Destination | None = None, **kwargs: Any) -> httpx.Response:
        base = self.instance_url(connection, destination)
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._jwt(base, connection)}"}
            try:
                return self._call(method, f"{base}/api/v3{path}", headers=headers, **kwargs)
            except IncuBrixError as exc:
                if exc.code == ErrorCode.CONNECTION_AUTH_EXPIRED and attempt == 1 and not connection.token:
                    self._jwts.pop(self._cache_key(base, connection), None)  # cached token went stale: log in again once
                    continue
                raise
        raise AssertionError("unreachable")

    def _jwt(self, base: str, connection: Connection) -> str:
        if connection.token:
            return connection.token.get_secret_value()
        key = self._cache_key(base, connection)
        if key not in self._jwts:
            if not (connection.username and connection.password):
                raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has neither a token nor a username/password")
            self._jwts[key] = self._login(base, connection)
        return self._jwts[key]

    def _login(self, base: str, connection: Connection) -> str:
        """Lemmy 0.19 builds the login token from the current second, so two logins within the same second collide
        ('duplicate key ... login_token_pkey', HTTP 400). Wait out the second and try again."""
        for attempt in range(1, self.LOGIN_ATTEMPTS + 1):
            try:
                resp = self._call(
                    "POST", f"{base}/api/v3/user/login",
                    json={"username_or_email": connection.username, "password": connection.password.get_secret_value()},
                )
                return resp.json()["jwt"]
            except IncuBrixError as exc:
                duplicate_token = "login_token_pkey" in str(exc.details.get("platform_message", ""))
                if not duplicate_token or attempt == self.LOGIN_ATTEMPTS:
                    raise
                time.sleep(self.LOGIN_RETRY_DELAY_S)
        raise AssertionError("unreachable")

    def _cache_key(self, base: str, connection: Connection) -> tuple[str, str]:
        return base, connection.username or "token"

    def _community_id(self, base: str, destination: Destination, connection: Connection) -> int:
        if destination.extra.get("community_id"):
            return int(destination.extra["community_id"])
        if not destination.community:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "lemmy destination needs a 'community' name")
        key = (base, destination.community)
        if key not in self._community_ids:
            data = self._api("GET", "/community", connection, destination, params={"name": destination.community}).json()
            self._community_ids[key] = data["community_view"]["community"]["id"]
        return self._community_ids[key]

    def _whoami(self, connection: Connection, base: str) -> str:
        if connection.username:
            return connection.username
        return self._api("GET", "/site", connection).json()["my_user"]["local_user_view"]["person"]["name"]

    def _live_url(self, connection: Connection, base: str, post_id: int) -> str:
        ui = str(connection.extra.get("ui_base_url") or base).rstrip("/")
        return f"{ui}/post/{post_id}"

    def _to_publish_result(self, post: dict[str, Any], connection: Connection, base: str) -> PublishResult:
        return PublishResult(platform_post_id=str(post["id"]), live_url=self._live_url(connection, base, post["id"]), status="live")


def _platform_error(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
        return body.get("error") if isinstance(body, dict) else None
    except ValueError:
        return None


def _jwt_expiry(token: str) -> str | None:
    """Best effort: read the 'exp' claim without verifying (we only report it)."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if "exp" in claims:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat()
    except Exception:
        pass
    return None
