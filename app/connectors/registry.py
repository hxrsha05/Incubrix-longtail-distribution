"""Finds a connector by key. Manifests come from YAML files; code comes from @register_connector classes."""
from __future__ import annotations

import importlib
from pathlib import Path

import httpx

from app.connectors.base import BaseConnector
from app.connectors.manifest import Manifest, load_manifests
from app.errors import ErrorCode, IncuBrixError

CONNECTOR_CLASSES: dict[str, type[BaseConnector]] = {}
DEFAULT_MANIFEST_DIR = Path(__file__).parent / "manifests"


def register_connector(key: str):
    def decorator(cls: type[BaseConnector]) -> type[BaseConnector]:
        cls.key = key
        CONNECTOR_CLASSES[key] = cls
        return cls

    return decorator


class ConnectorRegistry:
    def __init__(
        self,
        manifests: dict[str, Manifest],
        classes: dict[str, type[BaseConnector]],
        http: httpx.Client,
        disabled: set[str] | None = None,
    ) -> None:
        self.manifests = manifests
        self.classes = classes
        self.http = http
        self.disabled = disabled or set()
        self._instances: dict[str, BaseConnector] = {}

    def get(self, key: str) -> BaseConnector:
        if key not in self.manifests or key not in self.classes:
            raise IncuBrixError(ErrorCode.UNKNOWN_CONNECTOR, f"no connector {key!r}", {"available": sorted(self.keys())})
        if key in self.disabled:
            raise IncuBrixError(ErrorCode.CONNECTOR_DISABLED, f"connector {key!r} is switched off (kill switch)")
        if key not in self._instances:
            self._instances[key] = self.classes[key](self.manifests[key], self.http)
        return self._instances[key]

    def keys(self) -> set[str]:
        return set(self.manifests) & set(self.classes)


BUILTIN_CONNECTOR_MODULES = ["lemmy", "ghost", "webhook"]  # importing a module runs its @register_connector


def build_registry(http: httpx.Client, disabled: set[str], manifest_dir: Path = DEFAULT_MANIFEST_DIR) -> ConnectorRegistry:
    for name in BUILTIN_CONNECTOR_MODULES:
        importlib.import_module(f"app.connectors.{name}")
    return ConnectorRegistry(load_manifests(manifest_dir), CONNECTOR_CLASSES, http, disabled)
