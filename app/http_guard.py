"""Two small ASGI middlewares that sit in front of the whole API.

ApiGuard             an access token and a per-client rate limit, so a public demo cannot be abused.
IdempotencyMiddleware  the Idempotency-Key header on EVERY POST under /api/v1/ (Integration Standard point 4): the same key with
                     the same request returns the same answer and never does the work twice; the same key with a different
                     request is refused.

Both are plain ASGI (not BaseHTTPMiddleware) so they can read and replay request bodies safely.
"""
from __future__ import annotations

import hmac
import json
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

import anyio
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import ErrorCode, IncuBrixError
from app.idempotency import IdempotencyStore, request_hash

PUBLIC_EXACT = {"/health", "/ready", "/api/v1/health", "/api/v1/ready", "/docs", "/api/v1/openapi.json"}
RATE_EXEMPT = {"/health", "/ready", "/api/v1/health", "/api/v1/ready"}


def _is_public_feed(path: str) -> bool:
    """Reading a feed needs no token: partners and aggregators poll it."""
    return path.endswith(".xml") and (path.startswith("/feeds/") or path.startswith("/api/v1/feeds/"))


async def _send_json(send: Send, status: int, error: IncuBrixError, extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps(error.to_body()).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *(extra_headers or [])]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class ApiGuard:
    def __init__(self, app: ASGIApp, token: str | None = None, rate_per_minute: int = 0, trust_proxy: bool = False, clock: Callable[[], float] = time.monotonic) -> None:
        self.app, self.token, self.rate, self.trust_proxy, self.clock = app, token, rate_per_minute, trust_proxy, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, headers = scope["path"], {k.lower(): v for k, v in scope["headers"]}
        if self.token and path not in PUBLIC_EXACT and not _is_public_feed(path):
            supplied = headers.get(b"x-api-token", b"").decode()
            auth = headers.get(b"authorization", b"").decode()
            if auth.lower().startswith("bearer "):
                supplied = supplied or auth[7:].strip()
            if not supplied or not hmac.compare_digest(supplied, self.token):
                await _send_json(send, 401, IncuBrixError(ErrorCode.UNAUTHORIZED, "missing or wrong access token (send Authorization: Bearer <token> or X-API-Token)"))
                return
        if self.rate > 0 and path not in RATE_EXEMPT:
            client = scope.get("client") or ("unknown", 0)
            who = client[0]
            if self.trust_proxy and b"x-forwarded-for" in headers:
                who = headers[b"x-forwarded-for"].decode().split(",")[0].strip()
            now = self.clock()
            hits = self._hits[who]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.rate:
                wait = max(1, int(60 - (now - hits[0])) + 1)
                err = IncuBrixError(ErrorCode.RATE_LIMITED, f"too many requests: the limit is {self.rate} per minute", {"retry_after_s": float(wait), "limit_per_minute": self.rate})
                await _send_json(send, 429, err, [(b"retry-after", str(wait).encode())])
                return
            hits.append(now)
        await self.app(scope, receive, send)


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, store: IdempotencyStore) -> None:
        self.app, self.store = app, store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return
        key = dict(scope["headers"]).get(b"idempotency-key", b"").decode().strip()
        if not key:
            await self.app(scope, receive, send)
            return

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        fingerprint = request_hash({"query": scope.get("query_string", b"").decode(), "body": body.decode("utf-8", "replace")})
        name = f"http:{scope['path']}"
        record, created = await anyio.to_thread.run_sync(self.store.begin, name, key, fingerprint)
        if not created and record.state == "unknown":  # the earlier attempt died half way: one retry may take over
            created = await anyio.to_thread.run_sync(self.store.restart, name, key)
        if not created:
            if record.request_hash != fingerprint:
                await _send_json(send, 409, IncuBrixError(ErrorCode.IDEMPOTENCY_KEY_REUSED, "this Idempotency-Key was already used for a different request"))
            elif record.state == "done" and record.response:
                payload = record.response["body"].encode()
                await send({"type": "http.response.start", "status": record.response["status"], "headers": [
                    (b"content-type", record.response["content_type"].encode()), (b"content-length", str(len(payload)).encode()), (b"idempotent-replayed", b"true")]})
                await send({"type": "http.response.body", "body": payload})
            else:
                await _send_json(send, 409, IncuBrixError(ErrorCode.IDEMPOTENCY_IN_PROGRESS, "the same request is still being processed"))
            return

        sent_body = False

        async def replay_receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # disconnects and the like pass through

        captured: dict[str, Any] = {"status": 500, "content_type": "", "chunks": []}

        async def capture_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                captured["content_type"] = dict(message["headers"]).get(b"content-type", b"").decode()
            elif message["type"] == "http.response.body":
                captured["chunks"].append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        except BaseException:
            await anyio.to_thread.run_sync(self.store.release, name, key)
            raise
        if 200 <= captured["status"] < 300 and "json" in captured["content_type"]:
            stored = {"status": captured["status"], "content_type": captured["content_type"], "body": b"".join(captured["chunks"]).decode("utf-8", "replace")}
            await anyio.to_thread.run_sync(self.store.complete, name, key, stored)
        else:  # a failed request created nothing we must protect: the caller may fix it and retry with the same key
            await anyio.to_thread.run_sync(self.store.release, name, key)
