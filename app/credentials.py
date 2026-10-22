"""Where a destination's credentials come from. They are never stored in the catalogue or the database.

Order: (1) the CONNECTIONS_FILE JSON, keyed by the destination's connection_ref (default: its connector key);
(2) in simulation mode, the fake accounts of the mock platform server. Anything else is an auth error.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import SecretStr

from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, Destination


class CredentialProvider:
    def __init__(self, connections_file: str | None, simulation_mode: bool, mock_url: str) -> None:
        self.simulation_mode, self.mock_url = simulation_mode, mock_url.rstrip("/")
        self.entries: dict[str, dict] = {}
        if connections_file and Path(connections_file).is_file():
            self.entries = json.loads(Path(connections_file).read_text(encoding="utf-8"))

    def for_destination(self, destination: Destination) -> Connection:
        ref = destination.extra.get("connection_ref") or destination.connector_key
        if ref in self.entries:
            return Connection.model_validate(self.entries[ref])
        explicit = "connection_ref" in destination.extra  # a named connection must exist; only the default falls back to the mocks
        if self.simulation_mode and not explicit and destination.connector_key in _MOCK_ACCOUNTS:
            return _MOCK_ACCOUNTS[destination.connector_key](self.mock_url, destination)
        raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, f"no credentials for {ref!r}; add it to the connections file")


def _mock_discourse(url: str, d: Destination) -> Connection:
    return Connection(base_url=f"{url}/discourse", api_key=SecretStr("mock-discourse-key"), username="bot")


def _mock_devto(url: str, d: Destination) -> Connection:
    return Connection(base_url=f"{url}/devto", api_key=SecretStr("mock-devto-key"))


def _mock_webhook(url: str, d: Destination) -> Connection:
    partner = d.extra.get("partner", "demo-partner")
    return Connection(base_url=f"{url}/partner/hooks/{partner}", api_key=SecretStr("mock-partner-secret"))


def _no_credentials(url: str, d: Destination) -> Connection:
    return Connection(base_url=d.base_url or url)


_MOCK_ACCOUNTS = {"discourse": _mock_discourse, "devto": _mock_devto, "webhook": _mock_webhook, "assisted": _no_credentials}
