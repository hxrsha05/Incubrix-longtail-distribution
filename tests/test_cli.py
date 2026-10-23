"""The batch runner: a folder or file of inputs goes through the API, one folder of results comes out."""
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.cli import certify_all, load_inputs, main, make_client, run_inputs
from app.config import get_settings
from app.models import Destination

RULES = {"self_promo_allowed": False, "frequency_cap": None, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
GOOD = {"destination_id": "forum", "rendition": {"title": "A long enough title here", "body": "A body that is long enough to pass."}}


def build(mock, tmp_path, monkeypatch, **env):
    from app.main import create_app

    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    application = create_app()
    application.state.catalogue.upsert(Destination(name="Forum", connector_key="discourse", rules={**RULES, "self_promo_allowed": True}))
    application.state.catalogue.upsert(Destination(name="NoPromo", connector_key="devto", rules=RULES))
    return application


@pytest.fixture()
def http(mock, tmp_path, monkeypatch):
    yield TestClient(build(mock, tmp_path, monkeypatch))
    get_settings.cache_clear()


def write(folder, name, data):
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def read(folder, name):
    return json.loads((folder / f"{name}.result.json").read_text())


def test_folder_in_results_out_with_failures_recorded(http, tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    write(inp, "a.json", GOOD)
    write(inp, "b.json", {"destination_id": "nopromo", "rendition": {"title": "Buy now", "body": "x", "promotional": True}})
    write(inp, "c.json", {"destination_id": "nowhere", "rendition": {"title": "x"}})
    (inp / "d.json").write_text("{not json", encoding="utf-8")
    write(inp, "e.json", {"hello": "world"})
    summary = run_inputs(http, load_inputs(inp), out)
    assert (summary["inputs"], summary["published"], summary["blocked"], summary["failed"]) == (5, 1, 1, 3)
    assert read(out, "a")["result"]["status"] == "live" and read(out, "a")["manifest"]["versions"]["service"]
    assert read(out, "b")["error"]["code"] == "DEST_SPEC_VIOLATION" and read(out, "b")["status"] == "blocked"
    assert read(out, "c")["error"]["code"] == "NOT_FOUND" and read(out, "c")["http_status"] == 404
    assert read(out, "d")["error"]["code"] == "VALIDATION_ERROR" and "not valid JSON" in read(out, "d")["error"]["message"]
    assert read(out, "e")["error"]["code"] == "VALIDATION_ERROR" and (out / "summary.json").exists()


def test_a_single_file_with_a_list_and_a_jsonl_file_work_too(http, tmp_path):
    many = [{**GOOD, "rendition": {"title": f"A long enough title {i}", "body": "A body that is long enough to pass."}} for i in range(3)]
    (tmp_path / "list.json").write_text(json.dumps(many), encoding="utf-8")
    (tmp_path / "lines.jsonl").write_text("\n".join(json.dumps({**GOOD, "rendition": {"title": f"Another long title {i}", "body": "A body that is long enough to pass."}}) for i in range(2)) + "\n", encoding="utf-8")
    assert [n for n, _ in load_inputs(tmp_path / "list.json")] == ["list-0001", "list-0002", "list-0003"]
    assert run_inputs(http, load_inputs(tmp_path / "list.json"), tmp_path / "o1")["published"] == 3
    assert run_inputs(http, load_inputs(tmp_path / "lines.jsonl"), tmp_path / "o2")["published"] == 2
    with pytest.raises(SystemExit):
        load_inputs(tmp_path / "missing")


def test_rerunning_never_posts_twice(http, tmp_path, mock):
    inp = tmp_path / "in"
    write(inp, "a.json", GOOD)
    first = run_inputs(http, load_inputs(inp), tmp_path / "o1")
    second = run_inputs(http, load_inputs(inp), tmp_path / "o2")
    assert first["published"] == second["published"] == 1
    assert read(tmp_path / "o2", "a")["replayed"] is True and mock.state()["discourse_topics"] == 1


def test_an_idempotency_key_in_the_input_is_used(http, tmp_path, mock):
    inp = tmp_path / "in"
    write(inp, "one.json", {**GOOD, "idempotency_key": "same"})
    write(inp, "two.json", {**GOOD, "idempotency_key": "same"})  # a different file, the same key and request: one post
    run_inputs(http, load_inputs(inp), tmp_path / "o")
    assert mock.state()["discourse_topics"] == 1


def test_editing_an_input_makes_a_new_post_but_an_unchanged_one_is_a_replay(http, tmp_path, mock):
    inp = tmp_path / "in"
    write(inp, "a.json", GOOD)
    run_inputs(http, load_inputs(inp), tmp_path / "o1")
    write(inp, "a.json", {**GOOD, "rendition": {"title": "An edited, still long title", "body": "A body that is long enough to pass."}})
    run_inputs(http, load_inputs(inp), tmp_path / "o2")
    assert read(tmp_path / "o2", "a")["status"] == "published" and read(tmp_path / "o2", "a")["replayed"] is False and mock.state()["discourse_topics"] == 2


def test_tailoring_can_be_switched_off(http, tmp_path):
    for name in ("tailored", "plain"):  # different files, so different keys and different titles
        write(tmp_path / name, "a.json", {"destination_id": "forum", "rendition": {"title": f"A long enough title here, {name}", "body": "hello @alice, a body that is long", "tags": ["AI", "Video Tools"]}})
    assert run_inputs(http, load_inputs(tmp_path / "tailored"), tmp_path / "o1")["published"] == 1
    assert run_inputs(http, load_inputs(tmp_path / "plain"), tmp_path / "o2", tailoring=False)["published"] == 1
    assert read(tmp_path / "o1", "a")["tailoring"] is not None and read(tmp_path / "o2", "a")["tailoring"] is None


def test_certify_through_the_api_writes_one_summary(http, tmp_path):
    out = certify_all(http, tmp_path / "out", None)
    assert out["passed"] == 2 and (tmp_path / "out" / "certification-summary.json").exists()


# ---------------------------------------------------------------- the real command, against a real server
@pytest.fixture()
def server(mock, tmp_path, monkeypatch):
    application = build(mock, tmp_path, monkeypatch, API_TOKEN="batch-token")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{url}/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    srv.should_exit = True
    thread.join(timeout=5)
    get_settings.cache_clear()


def test_the_command_runs_against_a_protected_server(server, tmp_path, capsys):
    write(tmp_path / "in", "a.json", GOOD)
    assert main(["--api", server, "--token", "batch-token", "run", "--input", str(tmp_path / "in"), "--output", str(tmp_path / "out")]) == 0
    assert "1 inputs: 1 published" in capsys.readouterr().out and read(tmp_path / "out", "a")["status"] == "published"


def test_the_wrong_token_fails_every_input_and_the_exit_code_says_so(server, tmp_path, capsys):
    write(tmp_path / "in", "a.json", GOOD)
    assert main(["--api", server, "--token", "wrong", "run", "--input", str(tmp_path / "in"), "--output", str(tmp_path / "out")]) == 1
    assert read(tmp_path / "out", "a")["error"]["code"] == "UNAUTHORIZED"


def test_an_unreachable_api_is_a_clear_message_not_a_stack_trace(tmp_path, capsys):
    write(tmp_path / "in", "a.json", GOOD)
    assert main(["--api", "http://127.0.0.1:9", "run", "--input", str(tmp_path / "in"), "--output", str(tmp_path / "out")]) == 1  # every input fails, reported per input
    assert read(tmp_path / "out", "a")["error"]["code"] == "PLATFORM_UNAVAILABLE"
    assert main(["--api", "http://127.0.0.1:9", "certify", "--output", str(tmp_path / "c")]) == 2
    assert "cannot reach the API" in capsys.readouterr().err


def test_help_works_without_a_database(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0 and "folder or file" in capsys.readouterr().out
    assert make_client("http://x", "t").headers["Authorization"] == "Bearer t"
