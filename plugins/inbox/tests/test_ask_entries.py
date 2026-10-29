"""Real entry implementations; only the destination Kanban writer is isolated."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
ASK = {"interaction": "approval", "why_human": "Changes the owner's publication policy", "action": "Approve publication"}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


@pytest.mark.parametrize("door", ["tool", "cli", "rest"])
def test_every_posting_door_refuses_and_routes(tmp_path, monkeypatch, door):
    plugin = load("_ask_entry_plugin", "__init__.py")
    st = plugin.store_module()
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "entry-task")
    monkeypatch.setattr(plugin, "_poster_for", lambda sid: ("cli", None))
    if door == "tool":
        result = json.loads(plugin.handle_post({"title": "Review publication", "body": "Evidence", "accept_prompt": "Publish"}))
        assert not result["posted"] and result["task"] == "entry-task"
    elif door == "cli":
        parser = argparse.ArgumentParser()
        plugin._setup_cli(parser)
        assert plugin._run_cli(parser.parse_args(["post", "Review publication", "--prompt", "Publish", "--poster", "cli"])) == 1
    else:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        api = load("_ask_entry_api", "dashboard/plugin_api.py")
        app = FastAPI()
        app.include_router(api.router)
        monkeypatch.setattr(api._store(), "kanban_task_creator", lambda **kw: cards.append(kw) or "entry-task")
        response = TestClient(app).post("/items", json={"title": "Review publication", "body": "Evidence", "accept_prompt": "Publish", "poster": "cli"})
        assert response.status_code == 400
        assert response.json()["detail"]["task"] == "entry-task"
    assert len(cards) == 1


@pytest.mark.parametrize("door", ["tool", "cli", "rest"])
def test_every_posting_door_transmits_valid_ask(tmp_path, monkeypatch, door):
    plugin = load("_ask_valid_entry_plugin", "__init__.py")
    monkeypatch.setattr(plugin, "_poster_for", lambda sid: ("cli", None))
    # Do not touch any live board even if this regression drops the ask.
    monkeypatch.setattr(plugin.store_module(), "kanban_task_creator", lambda **kw: "isolated-task")
    payload = {"title": "Review publication", "body": "Evidence", "accept_prompt": "Publish", "poster": "cli", "ask": ASK}
    if door == "tool":
        result = json.loads(plugin.handle_post(payload))
        assert result.get("posted"), result
    elif door == "cli":
        parser = argparse.ArgumentParser()
        plugin._setup_cli(parser)
        rc = plugin._run_cli(parser.parse_args(["post", payload["title"], "--body", payload["body"], "--prompt", "Publish", "--poster", "cli", "--ask", json.dumps(ASK)]))
        assert rc == 0
    else:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        api = load("_ask_valid_entry_api", "dashboard/plugin_api.py")
        app = FastAPI()
        app.include_router(api.router)
        response = TestClient(app).post("/items", json=payload)
        assert response.status_code == 200, response.text
    box = plugin.store_module().Inbox()
    [item] = box.list()
    assert json.loads(item["ask_json"]) == ASK


@pytest.mark.parametrize("conditional", [False, True])
def test_ask_receipt_survives_refresh(tmp_path, conditional):
    st = load("_ask_receipt", "store.py")
    box = st.Inbox.for_home(tmp_path)
    first = box.post("Review publication", "Evidence", "Publish", key="draft:1", poster="cli", ask=ASK,
                     **({"if_absent": True} if conditional else {}))
    revised = {**ASK, "action": "Approve revised wording"}
    box.post("Review publication", "New evidence", "Publish", key="draft:1", poster="cli", ask=revised,
             **({"expected": first["snapshot"]} if conditional else {}))
    [item] = box.list()
    assert json.loads(item["ask_json"]) == revised
