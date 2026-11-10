"""The gate against the REAL hermes_state_outcome (not the fake), on a temp HERMES_HOME state.db.

Core checkout: HERMES_OUTCOME_CORE_DIR, HERMES_CORE_CHECKOUT, or ~/.hermes/hermes-agent.
A named core with missing core/dependencies fails rather than silently removing integration
coverage; with no core at all (public plugin-suites CI) these tests skip.
Run with a dependency-equipped interpreter via scripts/test-all -k session-outcome.
"""
import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("HERMES_OUTCOME_CORE_DIR") or os.environ.get("HERMES_CORE_CHECKOUT")
            or Path.home() / ".hermes" / "hermes-agent").expanduser()

SUMMARY = "Rebased the branch, fixed the two failing tests and pushed; CI is green on the new head."


@pytest.fixture
def real(tmp_path, monkeypatch):
    # No core at all (public plugin-suites CI: core is private) -> skip. A named core
    # (HERMES_OUTCOME_CORE_DIR, or HERMES_CORE_CHECKOUT which scripts/test-all sets whenever it found
    # one) that is missing or broken still fails below, never skips.
    named = os.environ.get("HERMES_OUTCOME_CORE_DIR") or os.environ.get("HERMES_CORE_CHECKOUT")
    if not named and not (CORE / "hermes_state_outcome.py").is_file():
        pytest.skip(f"needs Hermes core (none at {CORE}; HERMES_CORE_CHECKOUT unset)")
    assert (CORE / "hermes_state_outcome.py").is_file(), f"missing real core at {CORE}"
    # This is a library test, not a launch: never provision/re-exec for the disposable home.
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
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
        pytest.fail(f"real-core dependencies missing in {sys.executable}: {exc}")
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


@pytest.mark.parametrize("keep,corrupt", [(False, False), (True, False), (False, True)])
def test_done_cron_lifecycle_against_real_core(real, tmp_path, keep, corrupt):
    import json
    from cron import jobs

    so, plugin = real
    with jobs.use_cron_store(tmp_path):
        job = jobs.create_job(prompt="Check status", schedule="every 1h", owner_session_id="chat")
        if corrupt:
            (tmp_path / "cron" / "jobs.json").write_text("broken", encoding="utf-8")
        result = json.loads(plugin.handle({"kind": "done", "summary": "Installed.", "keep_crons": keep}, session_id="chat"))
        assert result["declared"]
        assert so.get_outcome("chat")["kind"] == "done"
        if corrupt:
            assert result["cron_release_error"]
            assert (tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8") == "broken"
        elif keep:
            assert jobs.get_job(job["id"])["owner_session_id"] == "chat"
        else:
            assert result["released_crons"][0]["id"] == job["id"]
            assert not jobs.get_job(job["id"])["owner_session_id"]


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

    # Core may use its heuristic on judge outage, but the gate requires a declaration.
    def down(s, u):
        raise RuntimeError("pool down")
    plugin._llm = down
    plugin._JUDGED.clear()
    assert so.derive_outcome("chat", ignore_live_turn=True, pending_reply=SUMMARY)["kind"] == "done"
    assert gate(plugin, SUMMARY + " ")["hold"]
    assert gate(plugin, "Hmm?")["hold"]


def test_open_step_makes_real_core_say_decision(real, monkeypatch):
    so, plugin = real
    plugin._llm = lambda s, u: "NO"
    us = plugin._user_steps()
    us.add_step("chat", "Ship it now?", ["Yes", "Later"])
    out = so.derive_outcome("chat", ignore_live_turn=True, pending_reply="Looking at it.")
    assert out["kind"] == "decision" and out["items"][0]["question"] == "Ship it now?"
    # The reply may resolve that step after the gate (user-steps' transform): the gate judges the reply itself.
    held = gate(plugin, "Looking at it.")
    assert held and held["hold"]
    plugin._llm = lambda s, u: "YES"
    plugin._JUDGED.clear()
    assert gate(plugin, SUMMARY) is None


def test_wait_on_a_card_links_it_to_the_waiting_chat(real, monkeypatch):
    """agent-884fdd96f8: a chat that declares it waits on a card must show that card in its task list. A declared
    wait only stored the outcome, so the chat said "the remaining work is ours" with nothing scheduled in view."""
    so, plugin = real
    monkeypatch.setenv("HERMES_KANBAN_HOME", os.environ["HERMES_HOME"])
    for k in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(k, raising=False)
    for name in [m for m in sys.modules if m.startswith("hermes_cli.kanban")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="repair the forwarder", assignee="default")
        assert kbn.list_notify_subs(conn, tid) == []
    out = plugin.handle({"kind": "wait", "on": [{"kind": "card", "id": tid}, {"kind": "card", "id": "t_nowhere"}]},
                        session_id="chat")
    import json as _json
    res = _json.loads(out)
    assert res.get("declared") is True, res
    with kbc.connect_closing() as conn:
        subs = kbn.list_notify_subs(conn, tid)
    assert [(s["platform"], s["chat_id"]) for s in subs] == [("tui", "chat")], subs
    assert subs[0]["delivery_metadata"]["reason"] == "shepherd"
    assert subs[0]["delivery_metadata"]["via"] == "outcome-wait"
    assert res["linked"] == [tid]
    assert "t_nowhere" in " ".join(res.get("not_linked", [])), res


def test_wait_from_a_headless_session_reports_not_linked(real, monkeypatch):
    """Core drops a tui sub for a headless ``local-<sha256>`` id (a one-shot/worker run); the plugin must say so
    instead of reporting the card linked (seen live on the hub: ``linked`` with no row written)."""
    so, plugin = real
    monkeypatch.setenv("HERMES_KANBAN_HOME", os.environ["HERMES_HOME"])
    for k in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(k, raising=False)
    for name in [m for m in sys.modules if m.startswith("hermes_cli.kanban")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="repair", assignee="default")
    headless = "local-" + "a" * 64
    res = plugin._link_wait_cards(headless, [tid])
    with kbc.connect_closing() as conn:
        assert kbn.list_notify_subs(conn, tid) == []
    assert res[0] == [] and "not linked" in res[1][0], res
