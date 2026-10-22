"""The batch runner: a folder of inputs in, one folder of results out."""
import json

import pytest

from app.cli import certify_all, main, run_folder
from app.config import get_settings
from app.models import Destination

RULES = {"self_promo_allowed": False, "frequency_cap": None, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}


@pytest.fixture()
def app(mock, tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("MOCK_PLATFORMS_URL", mock.url)
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    get_settings.cache_clear()
    application = create_app()
    application.state.catalogue.upsert(Destination(name="Forum", connector_key="discourse", rules={**RULES, "self_promo_allowed": True}))
    application.state.catalogue.upsert(Destination(name="NoPromo", connector_key="devto", rules=RULES))
    yield application
    get_settings.cache_clear()


def write(folder, name, data):
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


GOOD = {"destination_id": "forum", "rendition": {"title": "A long enough title here", "body": "A body that is long enough to pass."}}


def test_folder_in_results_out_with_failures_recorded(app, tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    write(inp, "a.json", GOOD)
    write(inp, "b.json", {"destination_id": "nopromo", "rendition": {"title": "Buy now", "body": "x", "promotional": True}})
    write(inp, "c.json", {"destination_id": "nowhere", "rendition": {"title": "x"}})
    (inp / "d.json").write_text("{not json", encoding="utf-8")
    summary = run_folder(app, inp, out)
    assert (summary["inputs"], summary["published"], summary["blocked"], summary["failed"]) == (4, 1, 1, 2)
    assert json.loads((out / "a.result.json").read_text())["result"]["status"] == "live"
    assert json.loads((out / "b.result.json").read_text())["error"]["code"] == "DEST_SPEC_VIOLATION"
    assert json.loads((out / "c.result.json").read_text())["error"]["code"] == "NOT_FOUND"
    assert json.loads((out / "d.result.json").read_text())["error"]["code"] == "VALIDATION_ERROR"
    assert json.loads((out / "a.result.json").read_text())["manifest"]["versions"]["service"] and (out / "summary.json").exists()


def test_rerunning_a_folder_never_posts_twice(app, tmp_path, mock):
    inp = tmp_path / "in"
    write(inp, "a.json", GOOD)
    first = run_folder(app, inp, tmp_path / "o1")
    second = run_folder(app, inp, tmp_path / "o2")
    assert first["published"] == second["published"] == 1
    assert json.loads((tmp_path / "o2" / "a.result.json").read_text())["replayed"] is True
    assert mock.state()["discourse_topics"] == 1


def test_certify_all_writes_one_summary(app, tmp_path):
    out = certify_all(app, tmp_path / "out", None)
    assert out["passed"] == 2 and (tmp_path / "out" / "certification-summary.json").exists()


def test_help_works_without_a_database(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0 and "run" in capsys.readouterr().out
