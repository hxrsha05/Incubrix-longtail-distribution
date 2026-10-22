"""Runs before any test module is imported: every test run gets its own throwaway database file."""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='incubrix-test-')}/test.db"
os.environ["CALLBACK_BACKOFF_S"] = "0.01"

import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import uvicorn  # noqa: E402

from mock_platforms.main import create_app as create_mock_app  # noqa: E402


class RunningMock:
    """The mock platform server, running for real on a local port, with helpers to make it misbehave."""

    def __init__(self, url: str) -> None:
        self.url = url

    def fail(self, **rule) -> None:
        httpx.post(f"{self.url}/__control/fail", json=rule).raise_for_status()

    def reset(self) -> None:
        httpx.post(f"{self.url}/__control/reset").raise_for_status()

    def state(self) -> dict:
        return httpx.get(f"{self.url}/__control/state").json()

    def set_metrics(self, platform: str, item_id: int, **values) -> None:
        httpx.post(f"{self.url}/__control/metrics", json={"platform": platform, "id": item_id, "values": values}).raise_for_status()


@pytest.fixture(scope="session")
def _mock_server_process():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(create_mock_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            httpx.get(f"{url}/__control/state", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("mock server did not start")
    yield RunningMock(url)
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def mock(_mock_server_process):
    """A fresh, empty mock platform server for each test."""
    _mock_server_process.reset()
    return _mock_server_process
