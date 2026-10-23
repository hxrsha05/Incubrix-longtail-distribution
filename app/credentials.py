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
    def __init__(self, connections_file: str | None, simulation_mode: bool, mock_url: str, self_url: str = "http://localhost:8000", feed_token: str = "local-dev-feed-token") -> None:
        self.simulation_mode, self.mock_url = simulation_mode, mock_url.rstrip("/")
        self.self_url, self.feed_token = self_url.rstrip("/"), feed_token
        self.connections_file = connections_file
        self.entries: dict[str, dict] = {}
        self._loaded_stamp: tuple[int, int] | None = None
        self._reload_if_changed()

    def _reload_if_changed(self) -> None:
        """The file may appear (or change) after the service starts: on a fresh `docker compose up` the seed step writes it a few seconds later."""
        if not self.connections_file:
            return
        path = Path(self.connections_file)
        try:
            info = path.stat()
        except OSError:
            self.entries, self._loaded_stamp = {}, None
            return
        stamp = (info.st_mtime_ns, info.st_size)
        if stamp != self._loaded_stamp:
            self.entries = json.loads(path.read_text(encoding="utf-8"))
            self._loaded_stamp = stamp

    def for_destination(self, destination: Destination) -> Connection:
        self._reload_if_changed()
        ref = destination.extra.get("connection_ref") or destination.connector_key
        if ref in self.entries:
            return _to_connection(self.entries[ref])
        explicit = "connection_ref" in destination.extra  # a named connection must exist; only the default falls back to the mocks
        if self.simulation_mode and not explicit and destination.connector_key in _MOCK_ACCOUNTS:
            if destination.connector_key == "feed":  # the feed host is this very service, not the mock server
                return Connection(base_url=self.self_url, api_key=SecretStr(self.feed_token), extra={"feed_id": destination.community or destination.extra.get("feed_id") or "main"})
            return _MOCK_ACCOUNTS[destination.connector_key](self.mock_url, destination)
        raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, f"no credentials for {ref!r}; add it to the connections file")


def _to_connection(entry: dict) -> Connection:
    """The seed file uses each platform's own words (admin_api_key ...); map them onto Connection, keep the rest in extra."""
    entry = dict(entry)
    if "admin_api_key" in entry:
        entry["api_key"] = entry.pop("admin_api_key")
    known = set(Connection.model_fields)
    extra = {k: entry.pop(k) for k in list(entry) if k not in known}
    return Connection.model_validate({**entry, "extra": {**entry.get("extra", {}), **extra}})


def _mock_discourse(url: str, d: Destination) -> Connection:
    return Connection(base_url=f"{url}/discourse", api_key=SecretStr("mock-discourse-key"), username="bot")


def _mock_devto(url: str, d: Destination) -> Connection:
    return Connection(base_url=f"{url}/devto", api_key=SecretStr("mock-devto-key"))


def _mock_webhook(url: str, d: Destination) -> Connection:
    partner = d.extra.get("partner", "demo-partner")
    return Connection(base_url=f"{url}/partner/hooks/{partner}", api_key=SecretStr("mock-partner-secret"))


def _mock_hashnode(url: str, d: Destination) -> Connection:
    return Connection(base_url=f"{url}/hashnode/graphql", api_key=SecretStr("mock-hashnode-token"))


def _mock_kit(url: str, d: Destination) -> Connection:
    return Connection(base_url=f"{url}/kit", api_key=SecretStr("mock-kit-key"))


def _no_credentials(url: str, d: Destination) -> Connection:
    return Connection(base_url=d.base_url or url)


_MOCK_ACCOUNTS = {"discourse": _mock_discourse, "devto": _mock_devto, "webhook": _mock_webhook, "assisted": _no_credentials, "hashnode": _mock_hashnode, "kit": _mock_kit, "feed": _no_credentials}
