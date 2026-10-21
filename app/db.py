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
