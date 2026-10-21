"""Background jobs (Integration Standard point 3): anything slow runs as a job.

POST returns 202 + job_id at once; the work runs in a worker thread; GET /api/v1/jobs/{id} reports
status (queued | running | succeeded | failed), progress 0-100 and the result. An optional callback URL is
called when the job ends, signed with HMAC-SHA256 in the X-IncuBrix-Signature header.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from app.db import jobs as t
from app.errors import ErrorCode, IncuBrixError
from app.idempotency import IdempotencyStore, request_hash
from app.logging_setup import correlation_id_var, job_id_var

log = logging.getLogger("incubrix.jobs")


class JobContext:
    """Handed to a job handler so it can report progress."""

    def __init__(self, service: "JobService", job_id: str) -> None:
        self._service, self.job_id = service, job_id

    def progress(self, percent: int) -> None:
        self._service._update(self.job_id, progress=max(0, min(99, int(percent))))


JobHandler = Callable[[JobContext, dict[str, Any]], dict[str, Any]]


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class JobService:
    def __init__(
        self,
        engine: Engine,
        store: IdempotencyStore,
        http: httpx.Client,
        callback_secret: str,
        workers: int = 4,
        callback_attempts: int = 3,
        callback_backoff_s: float = 0.5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.engine, self.store, self.http = engine, store, http
        self.callback_secret = callback_secret
        self.callback_attempts, self.callback_backoff_s = callback_attempts, callback_backoff_s
        self.clock = clock
        self.handlers: dict[str, JobHandler] = {}
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._recover_interrupted()

    def register(self, kind: str, handler: JobHandler) -> None:
        self.handlers[kind] = handler

    # ---------- API ----------
    def submit(self, kind: str, params: dict[str, Any], callback_url: str | None, idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
        """Returns (job, created). With an Idempotency-Key, the same request returns the same job."""
        if kind not in self.handlers:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"unknown job kind {kind!r}", {"kinds": sorted(self.handlers)})
        if callback_url and not callback_url.startswith(("http://", "https://")):
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "callback_url must be http(s)")

        if idempotency_key:
            fingerprint = request_hash({"kind": kind, "params": params, "callback_url": callback_url})
            record, created = self.store.begin("job", idempotency_key, fingerprint)
            if not created:
                if record.request_hash != fingerprint:
                    raise IncuBrixError(ErrorCode.IDEMPOTENCY_KEY_REUSED, "this idempotency key was already used for a different request")
                if record.state != "done":
                    raise IncuBrixError(ErrorCode.IDEMPOTENCY_IN_PROGRESS, "the same job request is still being created")
                return self.get(record.response["job_id"]), False

        job_id = uuid.uuid4().hex
        now = self.clock()
        with self.engine.begin() as conn:
            conn.execute(
                insert(t).values(
                    id=job_id, kind=kind, status="queued", progress=0, params=params,
                    callback_url=callback_url, callback_status="none" if not callback_url else "pending",
                    created_at=now, updated_at=now,
                )
            )
        if idempotency_key:
            self.store.complete("job", idempotency_key, {"job_id": job_id})
        self.pool.submit(self._run, job_id, correlation_id_var.get())
        return self.get(job_id), True

    def get(self, job_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(select(t).where(t.c.id == job_id)).mappings().first()
        if row is None:
            raise IncuBrixError(ErrorCode.NOT_FOUND, f"no job {job_id!r}")
        return {
            "job_id": row["id"], "kind": row["kind"], "status": row["status"], "progress": row["progress"],
            "result": row["result"], "error": row["error"], "callback_status": row["callback_status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    # ---------- worker ----------
    def _run(self, job_id: str, correlation_id: str | None) -> None:
        job_token = job_id_var.set(job_id)
        corr_token = correlation_id_var.set(correlation_id)
        try:
            with self.engine.begin() as conn:
                row = conn.execute(select(t.c.kind, t.c.params).where(t.c.id == job_id)).mappings().first()
            self._update(job_id, status="running")
            try:
                result = self.handlers[row["kind"]](JobContext(self, job_id), row["params"] or {})
                self._update(job_id, status="succeeded", progress=100, result=result)
                log.info("job succeeded")
            except IncuBrixError as exc:
                self._update(job_id, status="failed", error=exc.to_body()["error"])
                log.warning("job failed", extra={"error_code": exc.code.value})
            except Exception:
                log.exception("job crashed")
                self._update(job_id, status="failed", error=IncuBrixError(ErrorCode.INTERNAL_ERROR, "job crashed").to_body()["error"])
            self._send_callback(job_id)
        finally:
            job_id_var.reset(job_token)
            correlation_id_var.reset(corr_token)

    def _send_callback(self, job_id: str) -> None:
        with self.engine.begin() as conn:
            url = conn.execute(select(t.c.callback_url).where(t.c.id == job_id)).scalar()
        if not url:
            return
        job = self.get(job_id)
        body = json.dumps({k: job[k] for k in ("job_id", "kind", "status", "progress", "result", "error")}, sort_keys=True).encode()
        headers = {"Content-Type": "application/json", "X-IncuBrix-Signature": sign(self.callback_secret, body)}
        for attempt in range(1, self.callback_attempts + 1):
            try:
                resp = self.http.post(url, content=body, headers=headers)
                if resp.status_code < 300:
                    self._update(job_id, callback_status="delivered")
                    return
                log.warning("callback rejected", extra={"http_status": resp.status_code, "attempt": attempt})
            except httpx.HTTPError as exc:
                log.warning("callback failed", extra={"reason": type(exc).__name__, "attempt": attempt})
            if attempt < self.callback_attempts:
                time.sleep(self.callback_backoff_s * attempt)
        self._update(job_id, callback_status="failed")

    def _update(self, job_id: str, **values: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.id == job_id).values(updated_at=self.clock(), **values))

    def _recover_interrupted(self) -> None:
        """Jobs still queued/running at startup were cut off by a restart: say so instead of hanging forever."""
        err = IncuBrixError(ErrorCode.INTERNAL_ERROR, "job interrupted by a service restart", retryable=True).to_body()["error"]
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.status.in_(["queued", "running"])).values(status="failed", error=err, updated_at=self.clock()))
