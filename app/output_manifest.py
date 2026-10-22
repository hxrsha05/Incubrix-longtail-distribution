"""The manifest attached to every result (Integration Standard point 9): what produced this result and how.

Versions, non-secret settings, timings, cost, warnings, licences and whether anything is AI-generated.
"""
from __future__ import annotations

import platform
from typing import Any

APP_VERSION = "0.1.0"

# Components we ship or call, and where their licences are verified (see SOURCES.md).
LICENCES = [
    {"component": "IncuBrix Distribution service (this repo)", "licence": "own code", "reference": "SOURCES.md"},
    {"component": "FastAPI, Pydantic, SQLAlchemy, httpx", "licence": "MIT / BSD (see SOURCES.md)", "reference": "SOURCES.md"},
]


def build_manifest(*, kind: str, started_at: float, finished_at: float, connector_version: str | None = None,
                   settings: dict[str, Any] | None = None, warnings: list[str] | None = None,
                   ai_generated: bool = False, model_calls: int = 0, cost_usd: float = 0.0) -> dict[str, Any]:
    return {
        "kind": kind,
        "versions": {"service": APP_VERSION, "connector": connector_version, "python": platform.python_version()},
        "settings": settings or {},
        "timings": {"started_at": started_at, "finished_at": finished_at, "duration_s": round(finished_at - started_at, 3)},
        "cost": {"model_calls": model_calls, "usd": cost_usd, "note": "no paid API or model is called by certification"},
        "warnings": warnings or [],
        "licences": LICENCES,
        "ai_generated": {"present": ai_generated, "label": "AI-generated content is labelled" if ai_generated else "no AI-generated content in this result"},
    }
