"""owner-recommendation-guard: the ask-gate rule on hosts without ask-gate (pool workers), no model call."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]
ASK_GATE_TESTS = HERE.parent / "ask-gate" / "tests" / "test_owner_recommendation_hold.py"


def _fixtures():
    """INCIDENTS / PASSING (MACOS_LANE, GPU_MONEY, ...) straight from ask-gate's suite, so both plugins are tested on
    the same asks. Executed without its conftest import (only the fixture dicts are used)."""
    src = ASK_GATE_TESTS.read_text().replace("from conftest import llm_returning", "llm_returning = None")
    ns: dict = {"__name__": "ask_gate_owner_rec_fixtures"}
    exec(compile(src, str(ASK_GATE_TESTS), "exec"), ns)
    return SimpleNamespace(**{k: ns[k] for k in ("INCIDENTS", "PASSING", "MACOS_LANE", "GPU_MONEY", "ROOM_IPHONE")})


FX = _fixtures()


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("OWNER_RECOMMENDATION_GUARD_IGNORE_ASK_GATE", raising=False)
    mod = _load("owner_recommendation_guard_t")
    monkeypatch.setattr(mod, "_ask_gate_active", lambda: False)
    return mod


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "owner-recommendation-guard.jsonl"
    return [r for r in map(json.loads, p.read_text().splitlines()) if r["kind"] == kind] if p.exists() else []


def _block(guard, hr, tool="kanban_block", session="s1", **extra):
    return guard.pre_tool_call(tool_name=tool, session_id=session,
                               args={"kind": "needs_input", "reason": "see human_request", "human_request": hr, **extra})


@pytest.mark.parametrize("name", sorted(FX.INCIDENTS))
def test_incident_block_is_held(guard, tmp_path, name):
    r = _block(guard, FX.INCIDENTS[name])
    assert r and r["action"] == "block"
    assert "owner_recommendation_hold" in r["message"]
    assert "apply your recommendation and comment how to reverse" in r["message"]
    assert _events(tmp_path, "owner_recommendation_hold")


def test_macos_lane_held_in_a_worker_and_mcp_prefixed_name(guard, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_01a1d5b9382b24ad")
    r = _block(guard, FX.MACOS_LANE, tool="mcp__kanban_block")
    assert r and "owner_recommendation_hold" in r["message"]


@pytest.mark.parametrize("name", sorted(FX.PASSING))
def test_david_only_limits_pass(guard, tmp_path, name):
    for _ in range(3):
        assert _block(guard, FX.PASSING[name]) is None
    assert not _events(tmp_path, "owner_recommendation_hold")


def test_blocked_create_held_plain_create_not(guard):
    hr = FX.MACOS_LANE
    r = guard.pre_tool_call(tool_name="kanban_create", session_id="s1",
                            args={"title": "Decide", "initial_status": "blocked", "human_request": hr})
    assert r and "owner_recommendation_hold" in r["message"]
    assert guard.pre_tool_call(tool_name="kanban_create", session_id="s2",
                               args={"title": "Decide", "human_request": hr}) is None


def test_other_tools_and_no_request_pass(guard):
    assert guard.pre_tool_call(tool_name="kanban_comment", session_id="s1",
                               args={"human_request": FX.MACOS_LANE}) is None
    assert guard.pre_tool_call(tool_name="kanban_block", session_id="s1", args={"reason": "A) x B) y"}) is None
    assert guard.pre_tool_call(tool_name="kanban_block", session_id="s1", args=None) is None


def test_bounded_per_card_across_rewording(guard, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_01a1cf6f00320b7e")
    for i in range(2):
        hr = dict(FX.ROOM_IPHONE, action=FX.ROOM_IPHONE["action"] + " (rewording %d)" % i)
        assert "owner_recommendation_hold" in _block(guard, hr)["message"]
    assert _block(guard, FX.ROOM_IPHONE) is None
    assert _events(tmp_path, "owner_recommendation_released")
    # another card keeps its own bound
    assert "owner_recommendation_hold" in _block(guard, FX.MACOS_LANE, task_id="t_01a1d5b9382b24ad")["message"]


def test_steps_aside_when_ask_gate_is_active(guard, tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "_ask_gate_active", lambda: True)
    assert _block(guard, FX.MACOS_LANE) is None
    assert not _events(tmp_path, "owner_recommendation_hold")


def test_ask_gate_detection_without_core_is_inactive(monkeypatch):
    fresh = _load("owner_recommendation_guard_t2")
    monkeypatch.setitem(sys.modules, "hermes_cli", None)  # import fails
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", None)
    assert fresh._ask_gate_active() is False


def test_ask_gate_detection_reads_plugin_manager(monkeypatch):
    fresh = _load("owner_recommendation_guard_t3")
    rows = []
    mgr = type("M", (), {"list_plugins": lambda self: rows})()
    pkg, plugins = type(sys)("hermes_cli"), type(sys)("hermes_cli.plugins")
    plugins.get_plugin_manager = lambda: mgr
    pkg.plugins = plugins
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins)
    monkeypatch.delenv("OWNER_RECOMMENDATION_GUARD_IGNORE_ASK_GATE", raising=False)
    assert fresh._ask_gate_active() is False
    rows.append({"name": "ask-gate", "enabled": True, "error": None, "hooks": 4})
    assert fresh._ask_gate_active() is True
    rows[0]["error"] = "ask-gate registration refused"
    assert fresh._ask_gate_active() is False
    rows[0].update(error=None, enabled=False)
    assert fresh._ask_gate_active() is False
    rows[0]["enabled"] = True
    monkeypatch.setenv("OWNER_RECOMMENDATION_GUARD_IGNORE_ASK_GATE", "1")
    assert fresh._ask_gate_active() is False


def test_rule_error_fails_open(guard, tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "_RULE_PATH", tmp_path / "missing.py")
    monkeypatch.delitem(sys.modules, guard._RULE_KEY, raising=False)
    assert _block(guard, FX.MACOS_LANE) is None
    assert _events(tmp_path, "owner_recommendation_error")
    monkeypatch.delitem(sys.modules, guard._RULE_KEY, raising=False)


def test_shares_ask_gate_rule_file(guard):
    assert guard._RULE_PATH == HERE.parent / "ask-gate" / "owner_recommendation.py"
    assert guard._RULE_PATH.is_file()
