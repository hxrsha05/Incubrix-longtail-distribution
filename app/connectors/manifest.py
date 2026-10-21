"""One YAML manifest per connector (brief Appendix A). Loaded and validated at startup."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from app.errors import ErrorCode

# contract method -> capability that must be listed in the manifest
METHOD_CAPABILITY = {
    "validate": None,  # every connector can validate
    "publish": "publish",
    "get_status": "status",
    "delete": "delete",
    "fetch_metrics": "metrics",
    "refresh_auth": "refresh_auth",
    "build_assist_pack": "assist",
}


class Surface(BaseModel):
    key: str
    content_types: list[str]
    spec: dict[str, Any] = Field(default_factory=dict)


class Manifest(BaseModel):
    key: str
    version: str
    tier: Literal["native", "protocol", "bridge", "feed", "assisted"]
    auth: Literal["oauth2", "api_key", "bot_token", "none"]
    surfaces: list[Surface]
    rate_limits: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    error_map: dict[str, str] = Field(default_factory=dict)
    instance_param: str | None = None

    @field_validator("error_map")
    @classmethod
    def _codes_are_known(cls, v: dict[str, str]) -> dict[str, str]:
        for status, code in v.items():
            if code not in {c.value for c in ErrorCode}:
                raise ValueError(f"error_map[{status!r}] -> unknown error code {code!r}")
        return v

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_known(cls, v: list[str]) -> list[str]:
        known = {c for c in METHOD_CAPABILITY.values() if c}
        bad = [c for c in v if c not in known]
        if bad:
            raise ValueError(f"unknown capabilities {bad}; allowed: {sorted(known)}")
        return v

    def supports(self, method: str) -> bool:
        cap = METHOD_CAPABILITY.get(method, "__none__")
        return cap is None or cap in self.capabilities

    def surface(self, key: str) -> Surface | None:
        return next((s for s in self.surfaces if s.key == key), None)


def load_manifest(path: Path) -> Manifest:
    with path.open(encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))


def load_manifests(directory: Path) -> dict[str, Manifest]:
    manifests: dict[str, Manifest] = {}
    for path in sorted(directory.glob("*.yaml")):
        m = load_manifest(path)
        if m.key in manifests:
            raise ValueError(f"duplicate connector key {m.key!r} in {path.name}")
        manifests[m.key] = m
    return manifests
