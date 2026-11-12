"""Gate H (consent stop survives delegation) and Gate I (a delivery claim needs the write's result).

Incident (defect kanban-6cf8e9e3fd, 2026-11-12): a chat's comment on another gateway's card hit an approval prompt that
timed out ("Silence is not consent ... do NOT attempt the same outcome via a different path"). The chat then ordered a
worker card to post the same comment. That worker completed saying the tests "were posted on t_01a1d0d5507f91c2"
13 s BEFORE its operator request was even filed (and refused: the run had ended). Nothing was ever posted.

Run from the plugin dir:  uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("kanban_honest_done_plugin_dc", "__init__.py")
core = plugin.core

OWN = "t_01a1d126a2f05af6"      # the worker that claimed the post
TARGET = "t_01a1d0d5507f91c2"   # N1, the card it claimed to post on
OTHER = "t_0000aaaa1111bbbb"
CHAT = "20261112_055658_bb1bdd"
INCIDENT_SUMMARY = (f"Ran the leftover suites on the mini; 3 tests still fail. The leftover tests were posted on {TARGET} "
                    "for its owner.")


def make_board(tmp_path, name="kanban.db", own_created=1000):
    db = tmp_path / name
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, status TEXT NOT NULL,
                            created_at INTEGER NOT NULL DEFAULT 0, completed_at INTEGER, session_id TEXT, result TEXT);
        CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id));
        CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, kind TEXT,
                                  payload TEXT, created_at INTEGER);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, summary TEXT);
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, body TEXT,
                                    created_at INTEGER);
        CREATE TABLE supervisor_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, requester_task_id TEXT,
                                          requester_run_id INTEGER, ops_json TEXT, reason TEXT, status TEXT,
                                          error TEXT, created_at INTEGER, finished_at INTEGER);
    """)
    c.execute("INSERT INTO tasks (id, title, status, created_at, session_id, body) VALUES (?,?,?,?,?,?)",
              (OWN, "Run leftover tests and report them on N1", "running", own_created, CHAT, "Run them."))
    c.execute("INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)", (TARGET, "N1", "running", 10))
    c.execute("INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)", (OTHER, "other", "running", 10))
    c.commit()
    c.close()
    return db


def add_comment(db, task_id, at, body="leftover tests: a, b, c"):
    c = sqlite3.connect(db)
    c.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
              (task_id, "default", body, at))
    c.commit()
    c.close()


def add_request(db, target, status, error=None):
    c = sqlite3.connect(db)
    ops = [{"op": "operator", "action": "comment", "task_id": target, "text": "leftover tests: a, b, c"}]
    cur = c.execute("INSERT INTO supervisor_requests (requester_task_id, requester_run_id, ops_json, status, error, "
                    "created_at) VALUES (?,?,?,?,?,?)", (OWN, 1, json.dumps(ops), status, error, 1500))
    c.commit()
    c.close()
    return cur.lastrowid


@pytest.fixture
def board(tmp_path):
    db = make_board(tmp_path)
    return db, (lambda: [db])


# --- Gate I: a claimed cross-card delivery needs the write's result ----------------------------------------------

def test_completion_before_submission_is_refused(board, home):
    db, paths = board
    why = core.check_complete(OWN, INCIDENT_SUMMARY, paths())
    assert why and "Refused" in why and TARGET in why
    assert "not delivered" in why or "nothing shows" in why
    line = json.loads((home / "logs" / "kanban-honest-done.jsonl").read_text().splitlines()[-1])
    assert line["event"] == "delivery_claim_refused" and line["task"] == OWN


def test_comment_filed_before_completion_passes(board):
    db, paths = board
    add_comment(db, TARGET, at=1200)
    assert core.check_complete(OWN, INCIDENT_SUMMARY, paths()) is None


def test_done_operator_request_passes_filed_before_completion_compat(board):
    db, paths = board
    add_request(db, TARGET, "done")
    assert core.check_complete(OWN, INCIDENT_SUMMARY, paths()) is None


def test_pending_operator_request_is_queued_not_delivered(board):
    db, paths = board
    rid = add_request(db, TARGET, "pending")
    why = core.check_complete(OWN, INCIDENT_SUMMARY, paths())
    assert why and f"#{rid}" in why and "queued" in why


def test_honest_queued_wording_passes(board):
    db, paths = board
    rid = add_request(db, TARGET, "pending")
    summary = (f"Ran the leftover suites; 3 fail. Requested an operator comment on {TARGET} "
               f"(supervisor request #{rid}, pending): queued, not yet delivered.")
    assert core.check_complete(OWN, summary, paths()) is None


def test_failed_operator_request_is_refused(board):
    db, paths = board
    add_request(db, TARGET, "rejected", error="stale_run: the run is not live")
    why = core.check_complete(OWN, INCIDENT_SUMMARY, paths())
    assert why and "rejected" in why and "stale_run" in why


def test_wrong_target_is_refused(board):
    db, paths = board
    add_comment(db, OTHER, at=1200)
    add_request(db, OTHER, "done")
    assert core.check_complete(OWN, INCIDENT_SUMMARY, paths())


def test_old_comment_before_this_card_does_not_count(board):
    db, paths = board
    add_comment(db, TARGET, at=500)  # before OWN was created (1000)
    assert core.check_complete(OWN, INCIDENT_SUMMARY, paths())


def test_comment_on_replica_board_counts(tmp_path):
    db = make_board(tmp_path)
    shadow = make_board(tmp_path, name="shadow.db")
    add_comment(shadow, TARGET, at=1300, body="leftover tests (relayed by kanban-operator-call)")
    assert core.check_complete(OWN, INCIDENT_SUMMARY, [db, shadow]) is None


def test_claim_in_metadata_is_checked(board):
    db, paths = board
    why = core.check_complete(OWN, "Ran the leftover suites.", paths(),
                              metadata={"handoff": f"Results relayed to {TARGET}."})
    assert why and TARGET in why


@pytest.mark.parametrize("summary", [
    f"I did not post on {TARGET}: the request was refused.",
    f"The comment for {TARGET} was not posted.",
    f"Will be posted on {TARGET} once the owner is back?",
    f'The parent asked: "post the tests on {TARGET}".',
    f"Created {TARGET} for the remaining work.",
])
def test_non_claims_pass(board, summary):
    db, paths = board
    assert core.check_complete(OWN, summary, paths()) is None


def test_worker_tool_path_refuses(board):
    db, paths = board
    why = plugin.classify("kanban_complete", {"summary": INCIDENT_SUMMARY}, OWN, paths_fn=paths)
    assert why and TARGET in why
    why = plugin.classify("terminal", {"command": f"hermes kanban complete {OWN} --summary '{INCIDENT_SUMMARY}'"},
                          OWN, paths_fn=paths)
    assert why and TARGET in why


# --- Gate H: a consent stop carries to delegated writes -------------------------------------------------------------

TIMEOUT_RESULT = json.dumps({
    "output": "", "exit_code": -1,
    "error": ("BLOCKED: Command timed out without user response. The user has NOT consented to this action. Do NOT "
              "retry this command, do NOT rephrase it, and do NOT attempt the same outcome via a different command. "
              "Stop the current workflow and wait for the user to respond before taking any further destructive or "
              "irreversible action. Silence is not consent."),
    "status": "blocked"})
BLOCKED_ARGS = {"command": f"hermes kanban comment {TARGET} 'leftover tests: a, b, c'"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "hermes-home"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    return h


def _record(session=CHAT):
    plugin._post_tool_call(tool_name="terminal", args=BLOCKED_ARGS, result=TIMEOUT_RESULT, session_id=session)


def test_consent_timeout_then_worker_relay_is_refused(home, tmp_path, monkeypatch):
    _record()
    # 1) the chat orders a worker card to do it
    why = plugin._pre_tool_call(tool_name="kanban_create", session_id=CHAT,
                                args={"title": "Post leftover tests", "body": f"Post the leftover tests on {TARGET}."})
    assert why and why["action"] == "block" and TARGET in why["message"]
    why = plugin._pre_tool_call(tool_name="kanban_comment", session_id=CHAT,
                                args={"task_id": OWN, "body": f"Also post the N1 results on {TARGET}."})
    assert why and why["action"] == "block"
    # 2) a worker card created by that chat files the operator request itself
    db = make_board(tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_TASK", OWN)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.setattr(plugin, "_in_worker", lambda env=None: True)
    why = plugin._pre_tool_call(tool_name="kanban_supervisor_request", session_id="worker-session",
                                args={"ops": [{"op": "operator", "action": "comment", "task_id": TARGET,
                                               "text": "leftover tests"}], "reason": "report"})
    assert why and why["action"] == "block" and CHAT in why["message"]


def test_consent_stop_covers_operator_request_and_shell(home):
    _record()
    ops = {"ops": [{"op": "operator", "action": "comment", "task_id": TARGET, "text": "x"}]}
    assert plugin._pre_tool_call(tool_name="kanban_supervisor_request", args=ops, session_id=CHAT)
    assert plugin._pre_tool_call(tool_name="tool_call", session_id=CHAT,
                                 args={"calls": [{"name": "kanban_supervisor_request", "arguments": ops}]})
    assert plugin._pre_tool_call(tool_name="terminal", session_id=CHAT,
                                 args={"command": f"hermes kanban create 'post it' --body 'comment on {TARGET}'"})
    assert plugin._pre_tool_call(tool_name="execute_code", session_id=CHAT,
                                 args={"code": f"terminal('hermes kanban comment {TARGET} hi')"})
    assert plugin._pre_tool_call(tool_name="delegate_task", session_id=CHAT,
                                 args={"tasks": [{"goal": f"post the tests on {TARGET}"}]})


def test_user_reply_clears(home):
    _record()
    args = {"title": "x", "body": f"post on {TARGET}"}
    assert plugin._pre_tool_call(tool_name="kanban_create", args=args, session_id=CHAT)
    # a notice turn (no canonical user content) does not clear it
    plugin._pre_llm_call(session_id=CHAT, user_message="[kanban notice]", canonical_user_content=None)
    assert plugin._pre_tool_call(tool_name="kanban_create", args=args, session_id=CHAT)
    plugin._pre_llm_call(session_id=CHAT, user_message="yes go ahead",
                         canonical_user_content={"message_index": 3, "content": "yes go ahead"})
    assert plugin._pre_tool_call(tool_name="kanban_create", args=args, session_id=CHAT) is None


@pytest.mark.parametrize("result", [
    json.dumps({"error": f"operator_call_refused: stale_run: {OWN} run is not live"}),
    json.dumps({"error": f"foreign_card: {TARGET} belongs to gateway hermes-hub"}),
    "[kanban-honest-done] Refused: this handoff says part of the work was not done",
    json.dumps({"error": "BLOCKED: Failed to send approval request to user. Do NOT retry."}),
])
def test_technical_refusal_is_not_a_veto(home, result):
    plugin._post_tool_call(tool_name="terminal", args=BLOCKED_ARGS, result=result, session_id=CHAT)
    assert plugin._pre_tool_call(tool_name="kanban_create", session_id=CHAT,
                                 args={"title": "x", "body": f"post on {TARGET}"}) is None


def test_other_cards_and_other_chats_unaffected(home):
    _record()
    assert plugin._pre_tool_call(tool_name="kanban_create", session_id=CHAT,
                                 args={"title": "x", "body": f"look at {OTHER}"}) is None
    assert plugin._pre_tool_call(tool_name="kanban_create", session_id="20261112_000000_other",
                                 args={"title": "x", "body": f"post on {TARGET}"}) is None
    # reads are not delegation
    assert plugin._pre_tool_call(tool_name="kanban_show", session_id=CHAT, args={"task_id": TARGET}) is None


def test_quoted_stop_text_is_not_a_stop(home):
    out = json.dumps({"output": "tools/approval.py:842: BLOCKED: Command timed out without user response. The user "
                                "has NOT consented", "exit_code": 0})
    plugin._post_tool_call(tool_name="terminal", args={"command": f"grep -rn BLOCKED tools # {TARGET}"},
                           result=out, session_id=CHAT)
    assert plugin._pre_tool_call(tool_name="kanban_create", session_id=CHAT,
                                 args={"title": "x", "body": f"post on {TARGET}"}) is None


def test_post_hook_records_and_logs_telemetry(home):
    _record()
    stops = plugin.consent_mod.active_stops(CHAT)
    assert [s["targets"] for s in stops] == [[TARGET]]
    plugin._pre_tool_call(tool_name="kanban_create", session_id=CHAT, args={"title": "x", "body": f"on {TARGET}"})
    lines = [json.loads(x) for x in (home / "logs" / "kanban-honest-done.jsonl").read_text().splitlines()]
    kinds = [x["event"] for x in lines]
    assert kinds == ["consent_stop_recorded", "consent_relay_refused"]
    assert lines[1]["targets"] == [TARGET] and lines[1]["session"] == CHAT
