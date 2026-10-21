"""Publishing pipeline: idempotency -> rules -> connector -> record.

The ORDER matters:
  1. idempotency first, so a retry of a post that already succeeded is replayed, not re-judged
     (otherwise it would count against the frequency cap that its own first attempt filled);
  2. rules second, before any request leaves our service;
  3. only then the connector call.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from app.connectors.base import BaseConnector
from app.connectors.registry import ConnectorRegistry
from app.db import post_log
from app.errors import ErrorCode, IncuBrixError
from app.idempotency import IdempotencyStore, request_hash
from app.models import Destination, PublishRequest, PublishResult
from app.rules import RuleDecision, check_rules

log = logging.getLogger("incubrix.publish")


def destination_id(dest: Destination) -> str:
    return f"{dest.connector_key}|{(dest.base_url or '').rstrip('/').lower()}|{dest.community or ''}"


class PostLog:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def add(self, dest_id: str, connector_key: str, platform_post_id: str, posted_at: float) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(post_log).values(destination_id=dest_id, connector_key=connector_key, platform_post_id=platform_post_id, posted_at=posted_at))

    def times_since(self, dest_id: str, since: float) -> list[float]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(post_log.c.posted_at).where(post_log.c.destination_id == dest_id, post_log.c.posted_at > since))
            return [r[0] for r in rows]


class PublishService:
    def __init__(self, registry: ConnectorRegistry, store: IdempotencyStore, posts: PostLog, clock: Callable[[], float] = time.time) -> None:
        self.registry = registry
        self.store = store
        self.posts = posts
        self.clock = clock

    def publish(self, connector_key: str, req: PublishRequest) -> tuple[dict[str, Any], bool]:
        """Returns (result, replayed)."""
        connector = self.registry.get(connector_key)
        scope, key = f"publish:{connector_key}", req.idempotency_key
        # credentials are left out of the hash on purpose: retrying with a refreshed token is still the same request
        fingerprint = request_hash({"rendition": req.rendition.model_dump(mode="json"), "destination": req.destination.model_dump(mode="json")})

        record, created = self.store.begin(scope, key, fingerprint)
        if not created:
            if record.request_hash != fingerprint:
                raise IncuBrixError(ErrorCode.IDEMPOTENCY_KEY_REUSED, "this idempotency key was already used for a different request")
            if record.state == "done":
                return record.response or {}, True
            if record.state == "pending":
                raise IncuBrixError(ErrorCode.IDEMPOTENCY_IN_PROGRESS, "the same request is still being processed")
            found = self._reconcile(connector, req)  # state == "unknown"
            if found is not None:
                self.store.complete(scope, key, found.model_dump(mode="json"))
                return found.model_dump(mode="json"), True
            if not self.store.restart(scope, key):
                raise IncuBrixError(ErrorCode.IDEMPOTENCY_IN_PROGRESS, "the same request is being retried by someone else")

        # we own the key now
        try:
            self._enforce_rules(connector, req)
        except BaseException:
            self.store.release(scope, key)  # nothing was sent, so a retry may start clean
            raise
        return self._execute(connector, connector_key, req, scope, key), False

    # ---------- steps ----------
    def _enforce_rules(self, connector: BaseConnector, req: PublishRequest) -> None:
        dest_id = destination_id(req.destination)
        decision = check_rules(
            req.rendition,
            req.destination.rules,
            now=self.clock(),
            automated=connector.manifest.tier != "assisted",
            post_times_since=lambda since: self.posts.times_since(dest_id, since),
        )
        if decision.allowed:
            return
        details: dict[str, Any] = {"violations": [v.to_dict() for v in decision.violations]}
        if decision.action == "reschedule" and decision.reschedule_at is not None:
            details["reschedule_at"] = datetime.fromtimestamp(decision.reschedule_at, timezone.utc).isoformat()
            details["retry_after_s"] = max(0.0, decision.reschedule_at - self.clock())
            raise IncuBrixError(ErrorCode.DEST_RULE_FREQUENCY_CAP, "post rescheduled: frequency cap reached", details)
        raise IncuBrixError(ErrorCode.DEST_SPEC_VIOLATION, "post blocked by destination rules", details)

    def _execute(self, connector: BaseConnector, connector_key: str, req: PublishRequest, scope: str, key: str) -> dict[str, Any]:
        try:
            result = connector.publish(req.rendition, req.destination, req.connection, key)
        except IncuBrixError as exc:
            if exc.details.get("outcome_unknown"):
                self.store.mark_unknown(scope, key)  # it may have been created: do NOT allow a blind retry
            else:
                self.store.release(scope, key)  # definite failure: nothing exists, a retry is safe
            raise
        except BaseException:
            self.store.mark_unknown(scope, key)
            raise
        body = result.model_dump(mode="json")
        self.store.complete(scope, key, body)
        self.posts.add(destination_id(req.destination), connector_key, result.platform_post_id, self.clock())
        log.info("post published", extra={"connector": connector_key, "platform_post_id": result.platform_post_id})
        return body

    def _reconcile(self, connector: BaseConnector, req: PublishRequest) -> PublishResult | None:
        """An earlier attempt may or may not have created the post. Ask the platform; never guess."""
        try:
            return connector.reconcile(req.rendition, req.destination, req.connection, req.idempotency_key)
        except NotImplementedError:
            raise IncuBrixError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                "an earlier attempt timed out and this connector cannot check whether the post exists; not re-posting to avoid a duplicate",
                {"outcome_unknown": True},
                retryable=False,
            ) from None
