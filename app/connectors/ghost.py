"""Ghost connector (Admin API, Ghost 5/6). One connector serves every Ghost site: the address comes from
connection.base_url, and per-site choices (for example draft instead of published) come from the destination row.

Authentication: the Admin API key is "id:secret". For every call we sign a short-lived HS256 token with it:
header {kid: id}, payload {iat, exp (5 min), aud: "/admin/"}, sent as "Authorization: Ghost <token>".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import time
from typing import Any

import httpx

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, PublishResult, Rendition, StatusResult

API_VERSION = "v5.0"
TOKEN_LIFETIME_S = 300  # Ghost documents a 5 minute maximum
_STATUS_MAP = {"published": "live", "draft": "draft", "scheduled": "scheduled", "sent": "live"}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_admin_token(admin_key: str, now: float | None = None) -> str:
    """Sign a Ghost Admin API token. Raises ValueError (never echoing the key) if the key is malformed."""
    try:
        key_id, secret_hex = admin_key.split(":")
        secret = bytes.fromhex(secret_hex)
    except ValueError:
        raise ValueError("malformed Ghost admin API key") from None
    iat = int(time.time() if now is None else now)
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"iat": iat, "exp": iat + TOKEN_LIFETIME_S, "aud": "/admin/"}, separators=(",", ":")).encode())
    signature = _b64url(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def make_slug(title: str, idempotency_key: str) -> str:
    """Readable and unique per idempotency key: 'my-title-3f2a1b9c'. Lets reconcile() find our post exactly,
    because Ghost silently renames a duplicate slug to 'slug-2' instead of refusing it."""
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-") or "post"
    return f"{base}-{hashlib.sha1(idempotency_key.encode()).hexdigest()[:8]}"


def to_html(rendition: Rendition) -> str:
    """Plain text -> safe HTML. Everything is escaped, so a post body can never inject markup into the blog."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", rendition.body or "") if p.strip()]
    parts = [f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs]
    if rendition.link_url:
        url = html.escape(rendition.link_url, quote=True)
        parts.append(f'<p><a href="{url}" rel="noopener">{url}</a></p>')
    for m in rendition.media:  # images from their address; the address is escaped and must be http(s), so it cannot inject anything
        if m.kind == "image" and m.url.startswith(("http://", "https://")):
            parts.append(f'<figure><img src="{html.escape(m.url, quote=True)}" alt=""></figure>')
    return "".join(parts)


@register_connector("ghost")
class GhostConnector(BaseConnector):
    # ---------- contract methods ----------
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        post: dict[str, Any] = {
            "title": rendition.title,
            "html": to_html(rendition),
            "status": self._post_status(destination),
            "slug": make_slug(rendition.title, idempotency_key),
        }
        if rendition.canonical_url:
            post["canonical_url"] = rendition.canonical_url
        if rendition.tags:
            post["tags"] = [{"name": t} for t in rendition.tags]
        data = self._api("POST", "/posts/", connection, destination, params={"source": "html"}, json={"posts": [post]}).json()
        return self._to_result(data["posts"][0])

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """Did an earlier timed-out attempt create this post? Its slug is derived from the idempotency key."""
        slug = make_slug(rendition.title, idempotency_key)
        try:
            data = self._api("GET", f"/posts/slug/{slug}/", connection, destination).json()
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return None
            raise
        return self._to_result(data["posts"][0])

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        try:
            post = self._api("GET", f"/posts/{platform_post_id}/", connection).json()["posts"][0]
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return StatusResult(status="not_found", live_url=None)
            raise
        status = _STATUS_MAP.get(post["status"], post["status"])
        return StatusResult(status=status, live_url=post.get("url") if status == "live" else None)

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        try:
            self._api("DELETE", f"/posts/{platform_post_id}/", connection)
        except IncuBrixError as exc:
            if exc.details.get("http_status") != 404:
                raise  # 404 means it is already gone, which is the outcome we wanted
        return DeleteResult(deleted=True)

    # ---------- helpers ----------
    def _api(self, method: str, path: str, connection: Connection, destination: Destination | None = None, **kwargs: Any) -> httpx.Response:
        base = self.instance_url(connection, destination)
        if not connection.api_key:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no Ghost admin API key")
        try:
            token = make_admin_token(connection.api_key.get_secret_value())
        except ValueError as exc:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, str(exc)) from None
        headers = {"Authorization": f"Ghost {token}", "Accept-Version": API_VERSION}
        return self._call(method, f"{base}/ghost/api/admin{path}", headers=headers, **kwargs)

    def _map_error(self, resp: httpx.Response) -> IncuBrixError:
        error = _first_error(resp)
        if error.get("code") == "INVALID_JWT":  # a garbage token comes back as HTTP 400, not 401
            err = IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "Ghost rejected the API token", {"http_status": resp.status_code})
        else:
            err = super()._map_error(resp)
        if error.get("type"):
            err.details["platform_error"] = error["type"]
        if error.get("context") or error.get("message"):
            err.details["platform_reason"] = str(error.get("context") or error.get("message"))[:300]
        return err

    @staticmethod
    def _post_status(destination: Destination) -> str:
        status = destination.extra.get("post_status", "published")
        if status not in ("published", "draft"):
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"destination.extra.post_status must be published or draft, got {status!r}")
        return status

    @staticmethod
    def _to_result(post: dict[str, Any]) -> PublishResult:
        return PublishResult(platform_post_id=post["id"], live_url=post["url"], status=_STATUS_MAP.get(post["status"], post["status"]))


def _first_error(resp: httpx.Response) -> dict[str, Any]:
    try:
        errors = resp.json().get("errors") or [{}]
        return errors[0] if isinstance(errors[0], dict) else {}
    except (ValueError, AttributeError):
        return {}
