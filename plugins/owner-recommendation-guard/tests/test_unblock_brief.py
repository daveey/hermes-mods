"""owner-recommendation-guard also runs ask-gate's unblock_card_quality rule on hosts without ask-gate (workers)."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
INCIDENT = dict(
    action="Reply A or B in this chat. A = authorize one bounded repair run (PID 5198). B = close acceptance.",
    why_human="The proven app process is gone and no inspector is available.",
    interaction="approval", scope="PR1697 Mac delivery acceptance", evidence="ps: no process 5198")
GOOD = dict(
    action="Choose how to finish verifying the fix. Unblock = A; archive = B.",
    why_human="Tried: attaching to the closed app; a restart is outside approval. Only you can: allow a restart.",
    interaction="approval",
    scope=("Situation: The Mac app fix is installed; one of four checks still needs a debug run.\n"
           "Options:\nA) One supervised run — ~1 min restart, reversible.\nB) Accept with the gap — lowers the card's acceptance criteria; nothing restarts.\n"
           "Recommend: A — a one minute restart covers the bug you actually hit."),
    choices=[{"label": "A: supervised run (recommended)", "value": "A"}, {"label": "B: accept gap", "value": "B"}],
    evidence="ps: no process 5198")


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ASK_GATE_UNBLOCK_BRIEF", "1")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    spec = importlib.util.spec_from_file_location("orguard_brief_t", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_ask_gate_active", lambda: False)
    return mod


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "owner-recommendation-guard.jsonl"
    return [r for r in map(json.loads, p.read_text().splitlines()) if r["kind"] == kind] if p.exists() else []


def _block(guard, hr, task="t_x"):
    return guard.pre_tool_call(tool_name="kanban_block", session_id="w1",
                               args={"task_id": task, "kind": "needs_input", "human_request": hr})


def test_incident_is_held_on_a_worker(guard, tmp_path):
    r = _block(guard, INCIDENT)
    assert r and r["action"] == "block" and "unblock_card_hold" in r["message"]
    assert _events(tmp_path, "unblock_card_hold")


def test_good_brief_passes(guard, tmp_path):
    assert _block(guard, GOOD) is None
    assert not _events(tmp_path, "unblock_card_hold")


def test_bounded_then_released(guard, tmp_path):
    assert _block(guard, INCIDENT, "t_b") and _block(guard, INCIDENT, "t_b")
    assert _block(guard, INCIDENT, "t_b") is None
    assert _events(tmp_path, "unblock_card_released")


def test_steps_aside_when_ask_gate_active(guard, monkeypatch):
    monkeypatch.setattr(guard, "_ask_gate_active", lambda: True)
    assert _block(guard, INCIDENT) is None


def test_non_blocked_create_is_ignored(guard):
    assert guard.pre_tool_call(tool_name="kanban_create", session_id="w1",
                               args={"title": "x", "human_request": INCIDENT}) is None


def test_blocked_create_is_held_per_title(guard):
    def create(title):
        return guard.pre_tool_call(tool_name="kanban_create", session_id="w1",
                                   args={"title": title, "initial_status": "blocked", "human_request": INCIDENT})
    assert "unblock_card_hold" in create("one")["message"]
    assert "unblock_card_hold" in create("one")["message"]
    assert create("one") is None
    assert "unblock_card_hold" in create("two")["message"]


def test_brief_rule_error_fails_open(guard, tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "_brief_rule", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _block(guard, INCIDENT, "t_err") is None
    assert _events(tmp_path, "unblock_card_error")


def test_owner_rule_runs_before_the_brief(guard):
    in_limits = dict(GOOD, scope=GOOD["scope"].replace("lowers the card's acceptance criteria; ", ""))
    r = _block(guard, dict(in_limits, why_human="no tried line"), "t_own")
    assert r and "owner_recommendation_hold" in r["message"]


def test_retitled_creates_are_capped_per_session(guard):
    held = sum(bool(guard.pre_tool_call(tool_name="kanban_create", session_id="w9", args={
        "title": f"retitle {i}", "initial_status": "blocked", "human_request": INCIDENT})) for i in range(8))
    assert held == 4


def test_contract_scope_message_on_workers(guard):
    r = _block(guard, dict(INCIDENT, scope="acme/widgets#12 head abcdef1"), "t_pr")
    assert r and "keep scope exactly as it is" in r["message"]
