"""Agent-owned posters (registry owner kanban/coach) cannot self-attest an ask: it must come from the card's
human_request or the key must be a known owner-ask family. Tool and CLI doors."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
ASK = {"interaction": "approval", "why_human": "Only David can approve", "action": "Approve the thing"}
HR = {"interaction": "approval", "why_human": "Only David can approve", "action": "Approve the thing"}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for k in ("HERMES_KANBAN_TASK", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(k, raising=False)
    plugin = load("_aoa_plugin", "__init__.py")
    st = plugin.store_module()
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "t-x")
    return plugin, st, cards, monkeypatch


def post(door, plugin, title, key, poster, ask):
    """Returns True when posted."""
    if door == "tool":
        return bool(json.loads(plugin.handle_post({"title": title, "body": "b", "accept_prompt": "go", "poster": poster,
                                                    "key": key, "ask": ask})).get("posted"))
    parser = argparse.ArgumentParser()
    plugin._setup_cli(parser)
    argv = ["post", title, "--body", "b", "--prompt", "go", "--poster", poster, "--key", key, "--ask", json.dumps(ask)]
    return plugin._run_cli(parser.parse_args(argv)) == 0


@pytest.mark.parametrize("door", ["tool", "cli"])
@pytest.mark.parametrize("poster", ["kanban · x", "coach · y"])
def test_self_attested_ask_on_unlisted_key_is_refused(env, door, poster):
    plugin, st, cards, _ = env
    assert not post(door, plugin, "Failure report", "worker-failed:1", poster, ASK)
    assert not st.Inbox().list()
    assert "ask_self_attested" in cards[0]["body"] and "kanban_block(human_request" in cards[0]["body"]


@pytest.mark.parametrize("door", ["tool", "cli"])
@pytest.mark.parametrize("key,poster", [
    ("engine-budget:2026-10", "coach · engine budget"), ("hub-claude-login:1", "kanban · a"),
    ("hub-claude-login", "kanban · a"), ("bsky-signin:daveey", "kanban · a"), ("codex-move-to-hub:2", "kanban · a"),
    ("hub-broker-enroll:v", "kanban · a"), ("gh-quota-app:t_1", "kanban · a"), ("phone-calls-p2-board", "kanban · a")])
def test_allowlisted_owner_ask_keys_are_accepted(env, door, key, poster):
    plugin, st, _, _ = env
    assert post(door, plugin, "Sign in", key, poster, ASK)


@pytest.mark.parametrize("door", ["tool", "cli"])
def test_ask_derived_from_card_human_request_is_accepted(env, door):
    plugin, st, _, mp = env
    mp.setenv("HERMES_KANBAN_TASK", "t_card")
    mp.setattr(st, "card_human_request", lambda tid: HR)
    assert post(door, plugin, "Needs David", "anything:1", "kanban · a", ASK)


@pytest.mark.parametrize("door", ["tool", "cli"])
def test_ask_differing_from_human_request_is_still_refused(env, door):
    plugin, st, _, mp = env
    mp.setenv("HERMES_KANBAN_TASK", "t_card")
    mp.setattr(st, "card_human_request", lambda tid: HR)
    assert not post(door, plugin, "Needs David", "anything:1", "kanban · a", {**ASK, "action": "Something else"})


def test_non_agent_owner_still_accepts_any_ask(env):
    plugin, st, _, _ = env
    assert post("tool", plugin, "Review publication", "draft:1", "cli", ASK)
