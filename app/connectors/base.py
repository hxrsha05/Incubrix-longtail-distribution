"""Base class every connector extends.

What it gives a connector for free (so adding a platform is small, and adding a *destination* is just a row):
  * validate()   - checks content against the limits in the manifest (+ per-destination overrides)
  * _call()      - one HTTP helper that turns platform failures into the shared error codes
"""
from __future__ import annotations

import time
from typing import Any, ClassVar

import httpx

from app.connectors.manifest import Manifest
from app.errors import ErrorCode, IncuBrixError
from app.logging_setup import redact_text
from app.models import (
    AssistPack,
    Connection,
    DeleteResult,
    Destination,
    MetricsResult,
    PublishResult,
    RefreshResult,
    Rendition,
    StatusResult,
    ValidateResult,
    Violation,
)


class BaseConnector:
    key: ClassVar[str]

    def __init__(self, manifest: Manifest, http: httpx.Client) -> None:
        self.manifest = manifest
        self.http = http

    # ---------- contract methods (Appendix A) ----------
    def validate(self, rendition: Rendition, destination: Destination, connection: Connection) -> ValidateResult:
        surface = self.manifest.surface(destination.surface) if destination.surface else self.manifest.surfaces[0]
        if surface is None:
            return ValidateResult(
                ok=False,
                violations=[Violation(field="surface", reason=f"connector {self.key!r} has no surface {destination.surface!r}")],
            )
        spec = {**surface.spec, **destination.spec}  # destination row wins over the manifest default
        violations: list[Violation] = []

        if rendition.content_type not in surface.content_types:
            violations.append(
                Violation(field="content_type", reason=f"{rendition.content_type!r} not accepted; allowed: {surface.content_types}")
            )
        title_max = spec.get("title_max_chars")
        if title_max is not None and len(rendition.title) > title_max:
            violations.append(Violation(field="title", reason="title too long", limit=title_max, actual=len(rendition.title)))
        body_max = spec.get("body_max_chars")
        if body_max is not None and len(rendition.body) > body_max:
            violations.append(Violation(field="body", reason="body too long", limit=body_max, actual=len(rendition.body)))
        if spec.get("links_allowed") is False and (rendition.link_url or "http" in rendition.body):
            violations.append(Violation(field="link_url", reason="this destination does not allow links"))
        return ValidateResult(ok=not violations, violations=violations)

    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        raise NotImplementedError

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        raise NotImplementedError

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        raise NotImplementedError

    def fetch_metrics(self, platform_post_id: str, connection: Connection, since: str | None) -> MetricsResult:
        raise NotImplementedError

    def refresh_auth(self, connection: Connection) -> RefreshResult:
        raise NotImplementedError

    def build_assist_pack(self, rendition: Rendition, destination: Destination) -> AssistPack:
        raise NotImplementedError

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """After a timeout: did the earlier attempt create the post? Return it if so, None if it is verifiably absent.
        Connectors that cannot check leave this unimplemented, and the service then refuses to re-post."""
        raise NotImplementedError

    # ---------- helpers for subclasses ----------
    def instance_url(self, connection: Connection, destination: Destination | None = None) -> str:
        url = connection.base_url or (destination.base_url if destination else None)
        if not url:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"{self.key}: no base_url on the connection or destination")
        return url.rstrip("/")

    def _call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Call the platform. Network trouble and error statuses become shared error codes."""
        try:
            resp = self.http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            # connect/pool timeouts mean the request never left us; read/write timeouts mean it MAY have been processed
            details: dict[str, Any] = {"reason": "timeout", "url": url}
            if not isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)):
                details["outcome_unknown"] = True
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, "platform timed out", details) from exc
        except httpx.TransportError as exc:
            details = {"reason": type(exc).__name__, "url": url}
            if isinstance(exc, (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)):
                details["outcome_unknown"] = True
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, "platform unreachable", details) from exc
        if resp.status_code >= 400:
            raise self._map_error(resp)
        return resp

    def _map_error(self, resp: httpx.Response) -> IncuBrixError:
        status = resp.status_code
        code_name = self.manifest.error_map.get(str(status))
        if code_name:
            code = ErrorCode(code_name)
        elif status >= 500:
            code = ErrorCode.PLATFORM_UNAVAILABLE
        else:
            code = ErrorCode.PLATFORM_CONTENT_REJECTED
        details: dict[str, Any] = {"http_status": status, "platform_message": redact_text(resp.text[:300])}
        if status >= 500:
            details["outcome_unknown"] = True  # a failing gateway may still have created the post
        if status == 429:
            retry_after = _reset_seconds(resp)
            if retry_after is not None:
                details["retry_after_s"] = retry_after
        return IncuBrixError(code, f"platform returned HTTP {status}", details)


def _reset_seconds(resp: httpx.Response) -> float | None:
    """How long to back off. Understands Retry-After (seconds) and X-RateLimit-Reset (seconds or epoch)."""
    raw = resp.headers.get("Retry-After") or resp.headers.get("X-RateLimit-Reset")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(0.0, value - time.time()) if value > 1_000_000_000 else value
