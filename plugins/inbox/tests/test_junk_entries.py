"""Junk action reaches the CLI and REST doors (tool-less: owner-only), mutes the family and files a five-whys card."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from ask_fixtures import ASK, register_test_posters  # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, HERE / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    register_test_posters(tmp_path)


def _post(st):
    return st.Inbox().post("Choose wording", "evidence", "Choose", key="draft:1", poster="cron · editorial", ask=ASK)["session_id"]


def test_cli_junk_mutes_and_files_card(monkeypatch, capsys):
    plugin = load("_junk_cli_plugin", "__init__.py")
    st = plugin.store_module()
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "junk-task")
    sid = _post(st)
    parser = argparse.ArgumentParser()
    plugin._setup_cli(parser)
    assert plugin._run_cli(parser.parse_args(["junk", sid])) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["task"] == "junk-task" and out["family"] == "cron"
    assert "five-whys" in cards[0]["body"]
    assert plugin._run_cli(parser.parse_args(["junk", "no-such-id"])) == 1


def test_rest_junk_mutes_and_files_card(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load("_junk_rest_api", "dashboard/plugin_api.py")
    st = api._store()
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "junk-task")
    monkeypatch.setattr(api, "_profile", lambda: "default")
    sid = _post(st)
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    r = c.post(f"/items/{sid}/junk")
    assert r.status_code == 200, r.text
    assert r.json()["task"] == "junk-task" and r.json()["family"] == "cron"
    assert c.post("/items/nope/junk").status_code == 404
