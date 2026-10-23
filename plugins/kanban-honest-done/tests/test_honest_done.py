"""Tests for kanban-honest-done. Run from the plugin dir:

    uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
"""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("kanban_honest_done_plugin", "__init__.py")
core = plugin.core

OWN = "t_33259f8e"
INCIDENT_TITLE = ("Mac pod: client.restart / client.update + hub upkeep client-update "
                  "(shipped; Rose's iMac run split to follow-up)")
INCIDENT_SUMMARY = (
    "Shipped: client.restart and client.update (commit 74586d98e) are in desktop-pod-1636, and the hub deployed at "
    "196c4b4a9 with the client-update upkeep task. Live on the MBP pod, client.update quit and relaunched the client at "
    "build 1636, but reported updated:false because no downloaded update was ready. I could not run it on the iMac: "
    "her pod ef82ee5a has been offline about 9.7 h, so that run was dropped from this card's scope. Her client is "
    "still on 1609. The upkeep task will update it automatically once her pod is back.")


def make_board(tmp_path, tasks=(), links=(), created_by=()):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, status TEXT NOT NULL,
                            created_at INTEGER NOT NULL DEFAULT 0, completed_at INTEGER, session_id TEXT, result TEXT);
        CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id));
        CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, kind TEXT,
                                  payload TEXT, created_at INTEGER);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, summary TEXT);
    """)
    for tid, title, status in tasks:
        c.execute("INSERT INTO tasks (id, title, status) VALUES (?,?,?)", (tid, title, status))
    for p, ch in links:
        c.execute("INSERT INTO task_links VALUES (?,?)", (p, ch))
    for child, creator in created_by:
        c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,0)",
                  (child, "created", json.dumps({"creator_task_id": creator})))
    c.commit()
    c.close()
    return db


@pytest.fixture
def board(tmp_path):
    def _mk(**kw):
        db = make_board(tmp_path, **kw)
        return lambda: [db]
    return _mk


# --- classifier ---------------------------------------------------------------------------------------------------

def test_incident_summary_and_title_are_dropped_work():
    kinds = {k for k, _ in core.dropped_work_in_handoff(INCIDENT_SUMMARY, INCIDENT_TITLE)}
    assert {"split to follow-up", "dropped from scope"} <= kinds


@pytest.mark.parametrize("text", [
    "I did not run the live check on the phone.",
    "The 10-deploy http_soak was NOT run, so that acceptance item is open.",
    "The speech-end goal remains deferred.",
    "Whether the camera gives a real frame during a ring is unverified.",
    "Non-blocking follow-ups: grant Accessibility per room.",
    "Left the iOS half for a follow-up.",
    "The card was split into a follow-up.",
])
def test_admissions_hit(text):
    assert core.dropped_work(text), text


@pytest.mark.parametrize("text", [
    "Shipped 3 commits; all 14 tests pass; verified live on both hosts.",
    "No follow-ups needed.",
    "Nothing was deferred.",
    "Signal registration deferred at David's request.",
    "Deployment is deferred by active work; hermes-sync prebuilds it.",
    "A follow-up commit 1a2b3c4d fixes the lint.",
    'BB answered "Could not find Sonos playlist" before the fix.',
    "Why did it not run?",
    "Zoom rooms 6: follow-ups",  # title-style mention outside the title gate is not an admission
    "The worker deferred tool loading until first use.",
])
def test_benign_text_does_not_hit(text):
    assert not core.dropped_work(text), text


def test_title_only_counts_strong_kinds():
    assert not core.dropped_work_in_handoff("All done and verified.", "Zoom rooms 6: follow-ups")
    assert core.dropped_work_in_handoff("All done and verified.", INCIDENT_TITLE)


# --- B: kanban_complete -------------------------------------------------------------------------------------------

def test_complete_refused_without_followup_card(board):
    paths = board(tasks=[(OWN, INCIDENT_TITLE, "running")])
    why = plugin.classify("kanban_complete", {"summary": INCIDENT_SUMMARY}, OWN, paths)
    assert why and "Refused" in why and "kanban_create" in why and "kanban_block" in why


def test_complete_refused_when_title_alone_admits_split(board):
    paths = board(tasks=[(OWN, INCIDENT_TITLE, "running")])
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths)


def test_complete_allowed_with_open_followup_created_by_this_task(board):
    paths = board(tasks=[(OWN, "x", "running"), ("t_aaaaaaaa", "imac run", "ready")],
                  created_by=[("t_aaaaaaaa", OWN)])
    summary = INCIDENT_SUMMARY + " Follow-up card t_aaaaaaaa runs it when her pod is back."
    assert plugin.classify("kanban_complete", {"summary": summary}, OWN, paths) is None


def test_complete_allowed_with_linked_followup(board):
    paths = board(tasks=[(OWN, "x", "running"), ("t_bbbbbbbb", "imac run", "blocked")], links=[(OWN, "t_bbbbbbbb")])
    summary = "I could not run it on the iMac; t_bbbbbbbb waits on her pod."
    assert plugin.classify("kanban_complete", {"summary": summary}, OWN, paths) is None


def test_complete_refused_when_named_card_is_unrelated(board):
    paths = board(tasks=[(OWN, "x", "running"), ("t_cccccccc", "other work", "ready")])
    why = plugin.classify("kanban_complete", {"summary": "I could not run it; see t_cccccccc."}, OWN, paths)
    assert why and "t_cccccccc" in why


def test_complete_refused_when_named_followup_was_archived(board):
    paths = board(tasks=[(OWN, "x", "running"), ("t_dddddddd", "imac", "archived")], links=[(OWN, "t_dddddddd")])
    assert plugin.classify("kanban_complete", {"summary": "I could not run it; t_dddddddd."}, OWN, paths)


def test_complete_refused_when_named_card_does_not_exist(board):
    paths = board(tasks=[(OWN, "x", "running")])
    assert plugin.classify("kanban_complete", {"summary": "Split to follow-up t_eeeeeeee."}, OWN, paths)


def test_clean_complete_allowed(board):
    paths = board(tasks=[(OWN, "Mac pod restart", "running")])
    assert plugin.classify("kanban_complete", {"summary": "Shipped; verified live on both Macs."}, OWN, paths) is None


def test_completing_another_card_is_not_gated(board):
    paths = board(tasks=[(OWN, "x", "running"), ("t_ffffffff", "y", "review")])
    args = {"task_id": "t_ffffffff", "summary": "I could not run it."}
    assert plugin.classify("kanban_complete", args, OWN, paths) is None


def test_complete_via_tool_call_wrapper(board):
    paths = board(tasks=[(OWN, "x", "running")])
    args = {"calls": [{"name": "kanban_complete", "arguments": {"summary": "Split to follow-up."}}]}
    assert plugin.classify("tool_call", args, OWN, paths)


def test_complete_via_terminal_cli(board):
    paths = board(tasks=[(OWN, "x", "running")])
    cmd = f'hermes kanban complete {OWN} --summary "Shipped. The iMac run was not run."'
    assert plugin.classify("terminal", {"command": cmd}, OWN, paths)
    ok = f'hermes kanban complete {OWN} --summary "Shipped and verified."'
    assert plugin.classify("terminal", {"command": ok}, OWN, paths) is None


# --- A: editing your own card -------------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    f'hermes kanban edit {OWN} --title "X (shipped; iMac run split to follow-up)" --body "narrowed"',
    f"hermes kanban edit {OWN} --body 'no iMac run'",
    'hermes kanban edit "$HERMES_KANBAN_TASK" --body narrowed',
    f"env -u HERMES_DELEGATED_CHILD_CONTEXT hermes kanban --board hermes edit {OWN} --title=new",
    f"cd /tmp && hermes kanban edit {OWN} --body x && hermes kanban complete {OWN} --summary ok",
])
def test_edit_own_title_body_refused(board, cmd):
    paths = board(tasks=[(OWN, "x", "running")])
    why = plugin.classify("terminal", {"command": cmd}, OWN, paths)
    assert why and "Don't narrow your own card" in why


@pytest.mark.parametrize("cmd", [
    "hermes kanban edit t_12345678 --body 'parent-owned card'",
    f"hermes kanban edit {OWN} --priority 3",
    f"hermes kanban comment {OWN} 'iMac pod offline, waiting'",
    f"hermes kanban show {OWN}",
    "echo kanban edit",
])
def test_other_kanban_commands_allowed(board, cmd):
    paths = board(tasks=[(OWN, "x", "running")])
    assert plugin.classify("terminal", {"command": cmd}, OWN, paths) is None


def test_edit_own_via_execute_code_refused(board):
    paths = board(tasks=[(OWN, "x", "running")])
    code = ("from hermes_tools import terminal\n"
            f"terminal(\"hermes kanban edit {OWN} --body 'dropped the iMac run'\")\n")
    assert "Don't narrow" in plugin.classify("execute_code", {"code": code}, OWN, paths)


def test_complete_via_execute_code_refused(board):
    paths = board(tasks=[(OWN, "x", "running")])
    code = f"terminal('hermes kanban complete {OWN} --summary \"split to follow-up\"')\n"
    assert plugin.classify("execute_code", {"code": code}, OWN, paths)


# --- hook wiring --------------------------------------------------------------------------------------------------

def test_hook_inert_outside_worker(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert plugin._pre_tool_call("kanban_complete", {"summary": "split to follow-up"}) is None


def test_hook_blocks_in_worker(monkeypatch, tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, INCIDENT_TITLE, "running")])
    monkeypatch.setenv("HERMES_KANBAN_TASK", OWN)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.setattr(plugin, "_in_worker", lambda env=None: True)
    monkeypatch.setattr(core, "board_paths", lambda env=None: [db])
    out = plugin._pre_tool_call("kanban_complete", {"summary": INCIDENT_SUMMARY})
    assert out and out["action"] == "block"
    out = plugin._pre_tool_call("terminal", {"command": f"hermes kanban edit {OWN} --body x"})
    assert out and "Don't narrow" in out["message"]


def test_register_wires_pre_tool_call():
    hooks = {}

    class Ctx:
        def register_hook(self, name, fn):
            hooks[name] = fn

    plugin.register(Ctx())
    assert set(hooks) == {"pre_tool_call"}


# --- C: needs_attention (sidebar amber) ---------------------------------------------------------------------------

def _card(summary=INCIDENT_SUMMARY, title=INCIDENT_TITLE):
    return {"id": OWN, "title": title, "summary": summary}


def test_incident_card_needs_attention(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, INCIDENT_TITLE, "done")])
    assert core.needs_attention(_card(), [db]) is True


def test_clean_done_card_does_not_need_attention(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "Mac pod restart", "done")])
    assert core.needs_attention(_card("Shipped; verified live on both Macs.", "Mac pod restart"), [db]) is False


def test_followup_card_clears_attention_even_unnamed(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, INCIDENT_TITLE, "done"), ("t_aaaaaaaa", "imac run", "done")],
                    created_by=[("t_aaaaaaaa", OWN)])
    assert core.needs_attention(_card(), [db]) is False


def test_archived_followup_does_not_clear_attention(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, INCIDENT_TITLE, "done"), ("t_aaaaaaaa", "imac run", "archived")],
                    links=[(OWN, "t_aaaaaaaa")])
    assert core.needs_attention(_card(), [db]) is True


def test_ack_comment_clears_attention(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, INCIDENT_TITLE, "done")])
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT)")
    c.execute("INSERT INTO task_comments (task_id, author, body) VALUES (?, 'me', 'honest-done: ack — fine as is')",
              (OWN,))
    c.commit()
    c.close()
    assert core.needs_attention(_card(), [db]) is False


def test_register_done_check_with_core(monkeypatch):
    import types
    got = {}
    fake = types.ModuleType("hermes_cli.kanban_session_activity")
    fake.register_done_check = lambda name, fn: got.setdefault(name, fn)
    pkg = types.ModuleType("hermes_cli")
    pkg.kanban_session_activity = fake
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_session_activity", fake)
    assert plugin._register_done_check() is True
    assert got["kanban-honest-done"] is core.needs_attention
