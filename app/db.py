"""Database tables. SQLite for tests, Postgres in docker compose (same code, only DATABASE_URL differs)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import JSON, Column, Float, Integer, MetaData, String, Table, Text, create_engine
from sqlalchemy.engine import Engine, make_url

metadata = MetaData()

# One row per (scope, key). state: pending -> done | unknown ; definite failures delete the row.
idempotency = Table(
    "idempotency",
    metadata,
    Column("scope", String(100), primary_key=True),
    Column("key", String(255), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("response", JSON),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

# Successful posts, used by the frequency-cap rule.
post_log = Table(
    "post_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("destination_id", String(500), nullable=False, index=True),
    Column("connector_key", String(100), nullable=False),
    Column("platform_post_id", String(255), nullable=False),
    Column("posted_at", Float, nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("kind", String(100), nullable=False),
    Column("status", String(16), nullable=False),  # queued | running | succeeded | failed
    Column("progress", Integer, nullable=False, default=0),
    Column("params", JSON),
    Column("result", JSON),
    Column("error", JSON),
    Column("callback_url", Text),
    Column("callback_status", String(20)),  # none | delivered | failed
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)


# The destination catalogue: one row per forum / site. Adding a destination is adding a row, never code.
destinations = Table(
    "destinations",
    metadata,
    Column("id", String(120), primary_key=True),  # slug of the name
    Column("name", String(200), nullable=False, unique=True),
    Column("connector_key", String(100), nullable=False, index=True),
    Column("destination", JSON, nullable=False),  # the full Destination (surface, spec, rules, extra ...)
    Column("language", String(16)),
    Column("topics", JSON),
    Column("status", String(20), nullable=False),  # uncertified | certified | demoted | needs_attention
    Column("status_reason", Text),
    Column("last_certified_at", Float),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

# One row per certification run; the full record (steps, evidence, manifest) is in `record`.
certification_runs = Table(
    "certification_runs",
    metadata,
    Column("run_id", String(40), primary_key=True),
    Column("destination_id", String(120), nullable=False, index=True),
    Column("verdict", String(20), nullable=False),  # passed | failed | inconclusive
    Column("record", JSON, nullable=False),
    Column("started_at", Float, nullable=False),
)


def make_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return create_engine(url, **kwargs)


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
