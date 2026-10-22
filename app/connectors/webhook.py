"""Signed webhook connector, for partner websites.

THE IDEA, IN PLAIN WORDS
A partner has their own website. They agreed to receive our posts at one web address. Anyone could pretend to be us,
so every message carries a SECRET STAMP (a signature). The partner recomputes the stamp with the secret they share
with us. If it matches, the message really came from us and was not changed on the way.

HOW THE STAMP IS MADE
    stamp = "sha256=" + HMAC-SHA256( secret, the exact bytes of the message body ).hexdigest()
It goes in the header  X-IncuBrix-Signature.  The stamp covers the EXACT bytes, so you must turn the data into bytes
once, stamp those bytes, and send those same bytes. For requests with no body (GET, DELETE) the body is empty (b"").

THE CALLS (base_url is the partner's receiver address, for example http://host/partner/hooks/demo-partner)
    publish    POST   {base_url}                    body = JSON, headers = signature + X-IncuBrix-Idempotency-Key
    get_status GET    {base_url}/posts/{post_id}    signed (empty body); 404 means the post is gone
    delete     DELETE {base_url}/posts/{post_id}    signed (empty body); 404 means it is already gone (that is fine)
    reconcile  GET    {base_url}/by-key/{key}       signed (empty body); 404 means "not there"
The partner answers {"id": ..., "url": ...} for publish, and {"id", "url", "status"} for the reads.

THE BODY OF A PUBLISH (JSON)
    {"event": "publish", "idempotency_key": <key>, "rendition": <the rendition as a plain dict>}

HOW IT IS BUILT:
  1. Write a small helper that makes the stamp from the secret and the bytes.            (secret: connection.api_key)
  2. Write publish. Look at publish() in ghost.py for the pattern. Use self._call(...) so errors are mapped for you.
  3. Write get_status and delete.   (How did ghost.py treat a 404?)
  4. Write reconcile.               (Return None when the partner says 404.)
  5. Make a missing secret raise CONNECTION_AUTH_EXPIRED, and never put the secret in an error message.
  6. Add "webhook" to BUILTIN_CONNECTOR_MODULES in app/connectors/registry.py so the HTTP door can find it.

Run:  python -m pytest tests/test_webhook_connector.py -v
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, PublishResult, Rendition, StatusResult

SIGNATURE_HEADER = "X-IncuBrix-Signature"
IDEMPOTENCY_HEADER = "X-IncuBrix-Idempotency-Key"


def sign(secret: str, body: bytes) -> str:
    """The stamp: 'sha256=' + HMAC-SHA256(secret, exact bytes). The partner recomputes it to check us."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@register_connector("webhook")
class WebhookConnector(BaseConnector):
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        payload = {"event": "publish", "idempotency_key": idempotency_key, "rendition": rendition.model_dump(mode="json")}
        body = json.dumps(payload, separators=(",", ":")).encode()  # turned into bytes ONCE: we sign and send these same bytes
        extra = {IDEMPOTENCY_HEADER: idempotency_key, "Content-Type": "application/json"}
        data = self._signed("POST", self.instance_url(connection, destination), connection, body, extra).json()
        return PublishResult(platform_post_id=str(data["id"]), live_url=data["url"], status="live")

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """Did an earlier timed-out attempt create this post? Ask the partner by idempotency key."""
        url = f"{self.instance_url(connection, destination)}/by-key/{idempotency_key}"
        try:
            data = self._signed("GET", url, connection).json()
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return None
            raise
        return PublishResult(platform_post_id=str(data["id"]), live_url=data["url"], status="live")

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        try:
            data = self._signed("GET", f"{self.instance_url(connection)}/posts/{platform_post_id}", connection).json()
        except IncuBrixError as exc:
            if exc.details.get("http_status") == 404:
                return StatusResult(status="not_found", live_url=None)
            raise
        status = data.get("status", "live")
        return StatusResult(status=status, live_url=data.get("url") if status == "live" else None)

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        try:
            self._signed("DELETE", f"{self.instance_url(connection)}/posts/{platform_post_id}", connection)
        except IncuBrixError as exc:
            if exc.details.get("http_status") != 404:
                raise  # 404 means it is already gone, which is the outcome we wanted
        return DeleteResult(deleted=True)

    # ---------- helper ----------
    def _signed(self, method: str, url: str, connection: Connection, body: bytes = b"", extra_headers: dict[str, str] | None = None) -> httpx.Response:
        if not connection.api_key:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no webhook signing secret")
        headers = {SIGNATURE_HEADER: sign(connection.api_key.get_secret_value(), body), **(extra_headers or {})}
        return self._call(method, url, headers=headers, content=body)
