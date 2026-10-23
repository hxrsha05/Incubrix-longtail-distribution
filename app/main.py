"""FastAPI entrypoint: correlation ids, JSON errors, health endpoints."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
import time
import uuid
from typing import Callable

import httpx
from sqlalchemy import text
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.advice_routes import router as advice_router
from app.api.catalogue_routes import router as catalogue_router
from app.api.connector_routes import router as connector_router
from app.api.feed_routes import router as feed_router
from app.api.import_routes import router as import_router
from app.api.publish_routes import router as publish_router
from app.api.alert_routes import router as alert_router
from app.api.recert_routes import router as recert_router
from app.api.rules_routes import router as rules_router
from app.api.simulation_routes import router as simulation_router
from app.api.job_routes import router as job_router
from app.catalogue import Catalogue
from app.csv_import import Importer
from app.import_service import register_import_job
from app.recertify import RecertScheduler, Recertifier
from app.rules_capture import AnthropicExtractor, MockExtractor, RulesCapture
from app.certify_service import CertificationService, register_certification_jobs
from app.config import get_settings
from app.credentials import CredentialProvider
from app.evidence import EvidenceStore, make_s3_client
from app.feeds import FeedStore
from app.connectors.registry import ConnectorRegistry, build_registry
from app.db import init_db, make_engine
from app.alerts import AlertService
from app.http_guard import ApiGuard, IdempotencyMiddleware
from app.idempotency import IdempotencyStore
from app.jobs import JobService
from app.publish_service import PostLog, PublishService
from app.errors import ErrorCode, IncuBrixError
from app.logging_setup import configure_logging, correlation_id_var

log = logging.getLogger("incubrix.api")

_HTTP_TO_CODE = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.METHOD_NOT_ALLOWED}


def _error_response(err: IncuBrixError) -> JSONResponse:
    return JSONResponse(status_code=err.http_status, content=err.to_body())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the recertification scheduler with the server and stop it with the server (the modern replacement for startup/shutdown handlers)."""
    app.state.scheduler.start()
    try:
        yield
    finally:
        app.state.scheduler.stop()


def create_app(
    registry: ConnectorRegistry | None = None,
    callback_http: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="IncuBrix Long-Tail Distribution", version="0.1.0", openapi_url="/api/v1/openapi.json", lifespan=lifespan)

    http = httpx.Client(timeout=settings.http_timeout_s)
    engine = make_engine(settings.database_url)
    init_db(engine)
    store = IdempotencyStore(engine, settings.idempotency_pending_timeout_s, clock)
    app.state.engine = engine
    app.state.registry = registry or build_registry(http, settings.disabled_connector_set)
    app.state.settings = settings
    app.state.simulation_http = httpx.Client()
    app.state.catalogue = Catalogue(engine, clock)
    app.state.publish_service = PublishService(app.state.registry, store, PostLog(engine), clock, app.state.catalogue)
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
    app.state.feeds = FeedStore(engine, settings.feed_max_items, clock)
    app.state.feed_token = settings.feed_token.get_secret_value()
    app.state.service_base_url = settings.service_base_url
    presigner = make_s3_client(settings.s3_public_url, settings.s3_access_key.get_secret_value(), settings.s3_secret_key.get_secret_value()) if s3 and settings.s3_public_url else None
    app.state.evidence = EvidenceStore(settings.evidence_dir, s3, settings.s3_bucket, presigner=presigner)
    app.state.certification = CertificationService(
        app.state.catalogue, app.state.registry,
        CredentialProvider(settings.connections_file, settings.simulation_mode, settings.mock_platforms_url, settings.service_base_url, settings.feed_token.get_secret_value()), app.state.evidence,
        ttl_s=settings.test_post_ttl_seconds, simulation=settings.simulation_mode,
        max_consecutive_failures=settings.certify_max_consecutive_failures, rate_limit_wait_cap_s=settings.certify_rate_limit_wait_cap_s, clock=clock,
    )
    app.state.imports = EvidenceStore(settings.import_dir, s3, settings.s3_bucket, presigner=presigner, s3_prefix="imports", url_prefix="/api/v1/import-files")
    importer = Importer(app.state.catalogue, httpx.Client(timeout=settings.import_fetch_timeout_s, limits=httpx.Limits(max_connections=settings.import_workers * 2),
                                                           headers={"User-Agent": "IncuBrix-Catalogue-Importer/0.1 (+catalogue research; respects robots.txt)"}),
                        workers=settings.import_workers, allow_private_hosts=settings.import_allow_private_hosts, private_host_allowlist=settings.private_host_allowlist, fetch_timeout_s=settings.import_fetch_timeout_s, clock=clock)
    app.state.importer = importer
    register_import_job(app.state.jobs, importer, app.state.imports)
    if settings.rules_extractor == "anthropic":
        if settings.anthropic_api_key is None:
            raise RuntimeError("RULES_EXTRACTOR=anthropic needs ANTHROPIC_API_KEY")
        extractor = AnthropicExtractor(httpx.Client(), settings.anthropic_api_key.get_secret_value(), settings.rules_model,
                                       settings.rules_price_per_mtok_in, settings.rules_price_per_mtok_out)
    else:
        extractor = MockExtractor()
    app.state.rules_capture = RulesCapture(engine, app.state.catalogue, extractor, http, allow_private_hosts=settings.import_allow_private_hosts, private_host_allowlist=settings.private_host_allowlist, clock=clock)
    app.state.jobs.register("rules_capture", lambda ctx, params: app.state.rules_capture.capture(params["destination_id"], url=params.get("url"), text=params.get("text")))
    register_certification_jobs(app.state.jobs, app.state.certification, app.state.catalogue)
    app.state.alerts = AlertService(engine, http, settings.alert_webhook_url, settings.callback_hmac_secret.get_secret_value(), clock)
    app.state.recertifier = Recertifier(app.state.catalogue, app.state.certification, max_age_s=settings.recert_max_age_s, demoted_cooldown_s=settings.recert_demoted_cooldown_s,
                                        attention_retry_s=settings.recert_attention_retry_s, batch_limit=settings.recert_batch_limit, clock=clock, alerts=app.state.alerts)
    app.state.jobs.register("recertify", lambda ctx, params: app.state.recertifier.run_once(dry_run=bool(params.get("dry_run", False)), force=bool(params.get("force", False))))
    app.state.scheduler = RecertScheduler(app.state.recertifier, settings.recert_interval_s)


    app.add_middleware(IdempotencyMiddleware, store=store)  # Idempotency-Key on every POST under /api/v1/
    app.add_middleware(ApiGuard, token=settings.api_token.get_secret_value() if settings.api_token else None,
                       rate_per_minute=settings.rate_limit_per_minute, trust_proxy=settings.trust_proxy)  # outermost: refused requests do no work

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
    app.include_router(advice_router)  # POST /api/v1/tailor, /api/v1/relevance
    app.include_router(publish_router)  # POST /api/v1/publish
    app.include_router(simulation_router)  # /api/v1/simulation/*: failure switches for the fake platforms (simulation mode only)
    app.include_router(alert_router)  # GET /api/v1/alerts, POST /api/v1/alerts/{id}/acknowledge
    app.include_router(recert_router)  # POST /api/v1/recertify, GET /api/v1/recertification
    app.include_router(rules_router)  # AI rules capture: propose, review, confirm
    app.include_router(feed_router)  # GET /feeds/{id}.xml (public), /api/v1/feeds/... (token)
    app.include_router(import_router)  # POST /api/v1/imports, /api/v1/import-files
    app.include_router(job_router)  # POST /api/v1/jobs, GET /api/v1/jobs/{job_id}
    app.include_router(health)  # /health, /ready (as the brief names them)
    app.include_router(health, prefix="/api/v1")  # and under /api/v1 (all routes live there)
    return app


app = create_app()
