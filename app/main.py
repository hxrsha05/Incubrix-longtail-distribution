"""FastAPI entrypoint: correlation ids, JSON errors, health endpoints."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

import httpx
from sqlalchemy import text
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.catalogue_routes import router as catalogue_router
from app.api.connector_routes import router as connector_router
from app.api.job_routes import router as job_router
from app.catalogue import Catalogue
from app.certify_service import CertificationService, register_certification_jobs
from app.config import get_settings
from app.credentials import CredentialProvider
from app.evidence import EvidenceStore, make_s3_client
from app.connectors.registry import ConnectorRegistry, build_registry
from app.db import init_db, make_engine
from app.idempotency import IdempotencyStore
from app.jobs import JobService
from app.publish_service import PostLog, PublishService
from app.errors import ErrorCode, IncuBrixError
from app.logging_setup import configure_logging, correlation_id_var

log = logging.getLogger("incubrix.api")

_HTTP_TO_CODE = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.METHOD_NOT_ALLOWED}


def _error_response(err: IncuBrixError) -> JSONResponse:
    return JSONResponse(status_code=err.http_status, content=err.to_body())


def create_app(
    registry: ConnectorRegistry | None = None,
    callback_http: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="IncuBrix Long-Tail Distribution", version="0.1.0", openapi_url="/api/v1/openapi.json")

    http = httpx.Client(timeout=settings.http_timeout_s)
    engine = make_engine(settings.database_url)
    init_db(engine)
    store = IdempotencyStore(engine, settings.idempotency_pending_timeout_s, clock)
    app.state.engine = engine
    app.state.registry = registry or build_registry(http, settings.disabled_connector_set)
    app.state.publish_service = PublishService(app.state.registry, store, PostLog(engine), clock)
    app.state.jobs = JobService(
        engine, store, callback_http or http,
        callback_secret=settings.callback_hmac_secret.get_secret_value(),
        workers=settings.job_workers,
        callback_attempts=settings.callback_max_attempts,
        callback_backoff_s=settings.callback_backoff_s,
        clock=clock,
    )

    s3 = make_s3_client(settings.s3_endpoint_url, settings.s3_access_key.get_secret_value(), settings.s3_secret_key.get_secret_value()) if settings.s3_enabled else None
    app.state.idempotency_store = store
    app.state.catalogue = Catalogue(engine, clock)
    presigner = make_s3_client(settings.s3_public_url, settings.s3_access_key.get_secret_value(), settings.s3_secret_key.get_secret_value()) if s3 and settings.s3_public_url else None
    app.state.evidence = EvidenceStore(settings.evidence_dir, s3, settings.s3_bucket, presigner=presigner)
    app.state.certification = CertificationService(
        app.state.catalogue, app.state.registry,
        CredentialProvider(settings.connections_file, settings.simulation_mode, settings.mock_platforms_url), app.state.evidence,
        ttl_s=settings.test_post_ttl_seconds, simulation=settings.simulation_mode,
        max_consecutive_failures=settings.certify_max_consecutive_failures, rate_limit_wait_cap_s=settings.certify_rate_limit_wait_cap_s, clock=clock,
    )
    register_certification_jobs(app.state.jobs, app.state.certification, app.state.catalogue)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Correlation-Id"] = cid
        return response

    @app.exception_handler(IncuBrixError)
    async def incubrix_error_handler(request: Request, exc: IncuBrixError):
        log.warning("request failed", extra={"error_code": exc.code.value, "path": request.url.path})
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"]), "reason": e["msg"]} for e in exc.errors()]
        return _error_response(IncuBrixError(ErrorCode.VALIDATION_ERROR, "Request body is invalid", {"fields": fields}))

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        code = _HTTP_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        err = IncuBrixError(code, str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=err.to_body())

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        return _error_response(IncuBrixError(ErrorCode.INTERNAL_ERROR, "Unexpected server error"))

    health = APIRouter()

    @health.get("/health")
    def health_check() -> dict:
        return {"status": "ok"}

    @health.get("/ready")
    def ready_check(request: Request):
        checks = {}
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "checks": checks})

    app.include_router(connector_router)  # POST /connector/v1/{connector_key}/{method}
    app.include_router(catalogue_router)  # /api/v1/destinations, /api/v1/evidence
    app.include_router(job_router)  # POST /api/v1/jobs, GET /api/v1/jobs/{job_id}
    app.include_router(health)  # /health, /ready (as the brief names them)
    app.include_router(health, prefix="/api/v1")  # and under /api/v1 (all routes live there)
    return app


app = create_app()
