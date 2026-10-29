"""The gate against the REAL hermes_state_outcome (not the fake), on a temp HERMES_HOME state.db.

Core checkout: HERMES_OUTCOME_CORE_DIR, else ~/code/_wt/session-outcome (the session-outcome core worktree).
Skips when that checkout has no hermes_state_outcome.py. Run it with the Hermes venv so core's deps import:
  ~/.hermes/hermes-agent/venv/bin/python -m pytest -q plugins/session-outcome/tests/test_real_core.py
"""
import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("HERMES_OUTCOME_CORE_DIR") or Path.home() / "code" / "_wt" / "session-outcome")

pytestmark = pytest.mark.skipif(not (CORE / "hermes_state_outcome.py").is_file(),
                                reason=f"no session-outcome core checkout at {CORE}")

SUMMARY = "Rebased the branch, fixed the two failing tests and pushed; CI is green on the new head."


@pytest.fixture
def real(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    for k in ("HERMES_KANBAN_TASK", "HERMES_SESSION_OUTCOME_OFF", "HERMES_PLATFORM", "HERMES_SESSION_PLATFORM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.syspath_prepend(str(CORE))
    # import core from the worktree, not whatever hermes_state an earlier import resolved
    for name in [m for m in sys.modules if m == "hermes_state_outcome" or m.startswith("hermes_state")
                 or m == "hermes_constants"]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        so = importlib.import_module("hermes_state_outcome")
        import hermes_state  # noqa: F401
    except ImportError as exc:
        pytest.skip(f"core deps missing in this interpreter (use the Hermes venv): {exc}")
    assert Path(so.__file__).resolve().parent == CORE.resolve()
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db", raising=False)
    so._reset_hooks_for_tests()
    db = hermes_state.SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("chat", source="desktop")
        db.append_message("chat", role="user", content="fix the build")
    finally:
        db.close()
    spec = importlib.util.spec_from_file_location("session_outcome_plugin_real_core", ROOT / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin.register(type("Ctx", (), {"register_tool": lambda *a, **k: None,
                                     "register_hook": lambda *a, **k: None})())
    yield so, plugin
    so._reset_hooks_for_tests()


def gate(plugin, reply, msgs=None, attempt=0):
    return plugin.pre_final_gate(session_id="chat", platform="desktop", final_response=reply,
                                 turn_messages=msgs or [], attempt=attempt, hold_budget=2)


def test_gate_against_real_core(real, monkeypatch):
    so, plugin = real
    # A live turn marker would make plain derive say "running"; the gate must look past it.
    monkeypatch.setattr(so, "_live_turn", lambda db, sid: True)
    assert so.derive_outcome("chat")["kind"] == "running"
    # it reads the temp state.db: the seeded user row is the tail when no reply is pending
    assert so.derive_outcome("chat", ignore_live_turn=True)["reason"] == "interrupted"

    plugin._llm = lambda s, u: "NO"
    held = gate(plugin, "Looking at it.")
    assert held and held["hold"] and plugin.TAG in held["nudge"]
    # second miss in the same turn passes
    assert gate(plugin, "Looking at it.", msgs=[{"role": "user", "content": held["nudge"]}], attempt=1) is None

    plugin._llm = lambda s, u: "YES"
    assert gate(plugin, SUMMARY) is None

    # judge can't tell -> core's heuristic decides: a long closing report passes, a short line is held
    def down(s, u):
        raise RuntimeError("pool down")
    plugin._llm = down
    plugin._JUDGED.clear()
    assert gate(plugin, SUMMARY + " ") is None
    assert gate(plugin, "Hmm?")["hold"]


def test_open_step_makes_real_core_say_decision(real, monkeypatch):
    so, plugin = real
    plugin._llm = lambda s, u: "NO"
    us = plugin._user_steps()
    us.add_step("chat", "Ship it now?", ["Yes", "Later"])
    out = so.derive_outcome("chat", ignore_live_turn=True, pending_reply="Looking at it.")
    assert out["kind"] == "decision" and out["items"][0]["question"] == "Ship it now?"
    assert gate(plugin, "Looking at it.") is None
