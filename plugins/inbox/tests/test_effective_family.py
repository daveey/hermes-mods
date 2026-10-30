"""Poster identity is bound to the runtime context: inside a Kanban worker or a cron run, a declared
`cli`/`desktop`/`oneshot` label cannot shed the agent-owned rules. Tool, CLI and REST doors."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
ASK = {"interaction": "approval", "why_human": "Only David can approve", "action": "Approve the thing"}
TYPES = HERE.parents[1] / "skills" / "productivity" / "hermes-coach" / "inbox-types" / "engine-budget.yaml"
CRON_ENV = ("HERMES_CRON_SESSION", "1")
KANBAN_ENV = ("HERMES_KANBAN_TASK", "t_card")
SHELL_ENV = ("HERMES_KANBAN_WORKER_TASK", "t_card")  # what a worker's shell child sees


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, HERE / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for k in ("HERMES_KANBAN_TASK", "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_CRON_SESSION", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(k, raising=False)
    plugin = _load("_ef_plugin", "__init__.py")
    st = plugin.store_module()
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "t-x")
    types = tmp_path / "inbox" / "types"
    types.mkdir(parents=True)
    (types / "engine-budget.yaml").write_text(TYPES.read_text())
    return plugin, st, cards, monkeypatch


def post(door, plugin, title, poster, *, ask=None, type=None):
    args = {"title": title, "body": "b", "accept_prompt": "go", "poster": poster, "key": "k:1"}
    if type:
        args.update(type=type, fields={"day": "2026-10-30"})
    if ask:
        args["ask"] = ask
    if door == "tool":
        return bool(json.loads(plugin.handle_post(args)).get("posted"))
    parser = argparse.ArgumentParser()
    plugin._setup_cli(parser)
    argv = ["post", title, "--body", "b", "--prompt", "go", "--poster", poster, "--key", "k:1"]
    if type:
        argv += ["--type", type, "--field", "day=2026-10-30"]
    if ask:
        argv += ["--ask", json.dumps(ask)]
    return plugin._run_cli(parser.parse_args(argv)) == 0


@pytest.mark.parametrize("door", ["tool", "cli"])
@pytest.mark.parametrize("marker", [KANBAN_ENV, CRON_ENV, SHELL_ENV])
@pytest.mark.parametrize("poster", ["cli", "desktop", "oneshot"])
def test_declared_inbox_family_cannot_post_defect_typed_card_from_agent_run(env, door, marker, poster):
    plugin, st, cards, mp = env
    mp.setenv(*marker)
    assert not post(door, plugin, "Detector misses replies", poster, type="engine-budget")
    assert not st.Inbox().list()
    assert "poster_type_forbidden" in cards[0]["body"]
    assert poster in cards[0]["body"] and ("kanban" in cards[0]["body"] or "cron" in cards[0]["body"])


@pytest.mark.parametrize("door", ["tool", "cli"])
@pytest.mark.parametrize("marker", [KANBAN_ENV, CRON_ENV, SHELL_ENV])
@pytest.mark.parametrize("poster", ["cli", "desktop", "oneshot"])
def test_declared_inbox_family_cannot_self_attest_an_ask_from_agent_run(env, door, marker, poster):
    plugin, st, cards, mp = env
    mp.setenv(*marker)
    assert not post(door, plugin, "Needs David", poster, ask=ASK)
    assert not st.Inbox().list()
    assert "ask_self_attested" in cards[0]["body"]


@pytest.mark.parametrize("door", ["tool", "cli"])
@pytest.mark.parametrize("poster", ["cli", "desktop", "oneshot"])
def test_without_agent_markers_inbox_families_still_post(env, door, poster):
    plugin, st, _, _ = env
    assert post(door, plugin, "Review publication", poster, ask=ASK)
    assert len(st.Inbox().list()) == 1


@pytest.mark.parametrize("door", ["tool", "cli"])
def test_kanban_worker_with_human_request_posting_as_cli_gets_derived_ask(env, door):
    plugin, st, _, mp = env
    mp.setenv(*KANBAN_ENV)
    mp.setattr(st, "card_human_request", lambda tid: ASK)
    assert post(door, plugin, "Needs David", "cli", ask=ASK)
    assert len(st.Inbox().list()) == 1


def test_rest_post_route_binds_kanban_env(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    api = _load("_ef_api", "dashboard/plugin_api.py")
    monkeypatch.setattr(api, "_profile", lambda: "default")
    st = api._store()
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "t-x")
    app = fastapi.FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    body = {"title": "Needs David", "body": "b", "accept_prompt": "go", "poster": "cli", "key": "k:2", "ask": ASK}
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert c.post("/items", json=body).status_code == 200
    monkeypatch.setenv(*KANBAN_ENV)
    body["key"] = "k:3"
    r = c.post("/items", json=body)
    assert r.status_code >= 400
    assert "ask_self_attested" in cards[0]["body"]
