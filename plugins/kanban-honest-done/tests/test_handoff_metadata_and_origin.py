"""Regression tests for self-heal defect agent-1fd13f5232 (runners card t_c4cc0cc4, Oct 27 2026).

The card closed as done while its handoff metadata said "Chat panel link rendering (acceptance b) not verified" and
"No message sent to the origin chat", and its body named an origin chat that was never linked to the card, so that
chat never heard the card finished.

Gate B must read the completion's metadata and the plain "not verified / not performed" wording; Gate D must refuse
completion while a card that names an origin chat (``origin ... @session:X``) has no link/report to X.

    uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
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


plugin = _load("kanban_honest_done_plugin_r2", "__init__.py")
core = plugin.core

OWN = "t_c4cc0cc4"
ORIGIN = "20261025_233046_d48ade"
HUB_SUPERVISOR = "20261023_040944_37ae77"

# The exact handoff the card closed with (task_runs id 260 on the runners board).
SUMMARY = ("PR117 merged (head-pinned rebase, merge commit b23a0d382e) after a claude-opus-5-5/anthropic delta review "
           "returned APPROVE. Hub and MacBook are both on the merge commit. On the MacBook, kanban_active for "
           "20261025_233046_d48ade is now 2 (was 31), and `hermes-hub links` returns 6 rows instead of 11.")
METADATA = {
    "pr": "daveey/hermes#117", "head": "8ef587c825", "merge_commit": "b23a0d382ee3a63b5c0bf37a38fb43c35c524ca6",
    "review": "opus-review-r6.txt APPROVE, 350 tests passed on clean worktree",
    "ci": "all required checks green on head",
    "readback": {"macbook_head": "b23a0d382e", "kanban_active": 2, "links_rows": 6,
                 "open": ["t_82f80db4", "t_174a18ae"]},
    "not_done": ("renderer DOM check not performed; used the same active_task_counts() the session API uses. Chat "
                 "panel link rendering (acceptance b from earlier comment) not verified. No message sent to the "
                 "origin chat."),
    "worker_session_id": "20261027_151849_ae4c58",
}
BODY = (f"Origin chat: @session:default@macbook/{ORIGIN} (runners panel). The user still sees busy cards.\n"
        "## Plan\n- Fix the counts.\n- Report the counts on this card and to the origin chat.\n")
TITLE = "Runners panel: chat shows 31 busy cards"


def make_board(tmp_path, body=BODY, links=((HUB_SUPERVISOR, "hub", 0),), subs=(), comments=(), extra=()):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, status TEXT NOT NULL,
                            created_at INTEGER NOT NULL DEFAULT 0, completed_at INTEGER, session_id TEXT, result TEXT);
        CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id));
        CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, kind TEXT,
                                  payload TEXT, created_at INTEGER);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, summary TEXT, metadata TEXT);
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, body TEXT,
                                    created_at INTEGER);
        CREATE TABLE task_chat_links (id INTEGER PRIMARY KEY, uid TEXT, task_id TEXT NOT NULL,
                                      chat_gateway TEXT NOT NULL, chat_profile TEXT, chat_session TEXT NOT NULL,
                                      kind TEXT NOT NULL, released INTEGER NOT NULL DEFAULT 0, created_at INTEGER);
        CREATE TABLE kanban_notify_subs (task_id TEXT NOT NULL, platform TEXT NOT NULL, chat_id TEXT NOT NULL,
                                         thread_id TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL DEFAULT 0);
    """)
    c.execute("INSERT INTO tasks (id, title, body, status) VALUES (?,?,?,?)", (OWN, TITLE, body, "running"))
    for tid, title, status in extra:
        c.execute("INSERT INTO tasks (id, title, status) VALUES (?,?,?)", (tid, title, status))
    for session, gw, released in links:
        c.execute("INSERT INTO task_chat_links (task_id, chat_gateway, chat_session, kind, released, created_at) "
                  "VALUES (?,?,?,?,?,0)", (OWN, gw, session, "created", released))
    for platform, chat in subs:
        c.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id) VALUES (?,?,?)", (OWN, platform, chat))
    for text in comments:
        c.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,0)", (OWN, "w", text))
    c.commit()
    c.close()
    return lambda: [db]


# --- B: metadata + "not verified" ----------------------------------------------------------------------------------

def test_incident_handoff_refused_by_metadata(tmp_path):
    """The exact t_c4cc0cc4 handoff (origin link present, so only Gate B can refuse)."""
    paths = make_board(tmp_path, links=((HUB_SUPERVISOR, "hub", 0), (ORIGIN, "macbook", 0)))
    why = plugin.classify("kanban_complete", {"summary": SUMMARY, "metadata": METADATA}, OWN, paths)
    assert why and "Refused" in why and "not verified" in why


def test_incident_handoff_via_tool_call_and_cli_metadata(tmp_path):
    paths = make_board(tmp_path, links=((ORIGIN, "macbook", 0),))
    wrapped = {"calls": [{"name": "kanban_complete", "arguments": {"summary": SUMMARY, "metadata": METADATA}}]}
    assert plugin.classify("tool_call", wrapped, OWN, paths)
    md = json.dumps({"not_done": "Chat panel link rendering not verified."})
    cmd = f"hermes kanban complete {OWN} --summary 'PR117 merged.' --metadata '{md}'"
    assert plugin.classify("terminal", {"command": cmd}, OWN, paths)


def test_metadata_admission_allowed_with_named_followup(tmp_path):
    paths = make_board(tmp_path, links=((ORIGIN, "macbook", 0),), extra=[("t_aaaaaaaa", "panel check", "ready")])
    c = sqlite3.connect(paths()[0])
    c.execute("INSERT INTO task_links VALUES (?,?)", (OWN, "t_aaaaaaaa"))
    c.commit()
    c.close()
    md = dict(METADATA, not_done=METADATA["not_done"] + " Follow-up card t_aaaaaaaa verifies the panel.")
    assert plugin.classify("kanban_complete", {"summary": SUMMARY, "metadata": md}, OWN, paths) is None


@pytest.mark.parametrize("text", [
    "Chat panel link rendering (acceptance b from earlier comment) not verified.",
    "renderer DOM check not performed; used the same active_task_counts() the session API uses.",
    "The phone path was not checked.",
    "Delivery to the Mac could not be confirmed.",
    "Live rollout not yet tested on the mini.",
])
def test_not_verified_wording_hits(text):
    assert core.dropped_work(text), text


@pytest.mark.parametrize("text", [
    "Nothing left unverified.",
    "Shipped 3 commits; all 14 tests pass; verified live on both hosts.",
    "Build artifacts are not checked into git.",
    "Was the panel not verified?",
])
def test_not_verified_benign(text):
    assert not core.dropped_work(text), text


def test_clean_metadata_allowed(tmp_path):
    paths = make_board(tmp_path, links=((ORIGIN, "macbook", 0),))
    md = {"tests_run": 12, "changed_files": ["a.py"], "decisions": ["kept the old default"], "proof": "verified live"}
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified.", "metadata": md}, OWN, paths) is None


# --- D: origin chat named in prose must be linked / reported -------------------------------------------------------

def test_origin_chat_only_in_prose_refused(tmp_path):
    """The incident's chat links: only the hub supervisor, not the origin chat the body names."""
    paths = make_board(tmp_path)
    why = plugin.classify("kanban_complete", {"summary": "Shipped and verified on both hosts."}, OWN, paths)
    assert why and ORIGIN in why and "cross_session" in why and "follow-through: SENT" in why


def test_origin_chat_linked_allowed(tmp_path):
    paths = make_board(tmp_path, links=((HUB_SUPERVISOR, "hub", 0), (ORIGIN, "macbook", 0)))
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths) is None


def test_origin_chat_is_creating_chat_allowed_before_link_sync(tmp_path):
    """Live test t_01a180920ed19723: the creating chat's 'created' link row is written later by the sync loop, so
    the first completion was refused although the card was the origin chat's own. tasks.session_id counts."""
    paths = make_board(tmp_path, links=())
    c = sqlite3.connect(paths()[0])
    c.execute("UPDATE tasks SET session_id=? WHERE id=?", (ORIGIN, OWN))
    c.commit()
    c.close()
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths) is None


def test_origin_chat_subscribed_allowed(tmp_path):
    paths = make_board(tmp_path, subs=[("tui", ORIGIN)])
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths) is None


def test_origin_chat_cross_gateway_hub_sub_allowed(tmp_path):
    """`notify-subscribe --platform hub --chat-id macbook/default/<session>` is the cross-gateway link."""
    paths = make_board(tmp_path, links=(), subs=[("hub", f"macbook/default/{ORIGIN}")])
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths) is None


def test_refusal_routes_are_worker_runnable(tmp_path):
    """Workers are fenced from `hermes kanban notify-subscribe` on their own board, so the refusal must not send
    them there: it names the cross_session send + follow-through comment, and kanban_block as the fallback."""
    why = plugin.classify("kanban_complete", {"summary": "Shipped."}, OWN, make_board(tmp_path))
    assert "notify-subscribe" not in why and "kanban_block" in why
    assert f"follow-through: SENT via cross_session to @session:{ORIGIN}" in why


# --- E: kanban_create naming another gateway's origin chat must bind it -------------------------------------------

NAMES = lambda: ("hub", {"hub", "macbook", "mac-mini"})  # noqa: E731
CREATE_ARGS = {"title": "Session activity: shepherd links", "body": BODY}


def test_create_naming_remote_origin_unbound_refused():
    """t_c4cc0cc4 was created on the hub with the MacBook origin only in its body."""
    why = plugin.classify_create("kanban_create", CREATE_ARGS, NAMES, env={})
    assert why and f"origin_session='@session:default@macbook/{ORIGIN}'" in why


def test_create_bound_by_arg_or_env_allowed():
    ref = f"@session:default@macbook/{ORIGIN}"
    assert plugin.classify_create("kanban_create", dict(CREATE_ARGS, origin_session=ref), NAMES, env={}) is None
    assert plugin.classify_create("kanban_create", CREATE_ARGS, NAMES, env={"HERMES_ORIGIN_SESSION": ref}) is None


@pytest.mark.parametrize("body", [
    f"Origin chat: @session:default@hub/{ORIGIN}",  # same gateway: origin_session can't bind it, Gate D covers it
    f"Origin chat: @session:default/{ORIGIN}",  # no gateway in the ref
    f"See @session:default@macbook/{ORIGIN} for context.",  # not called the origin
    f'Plan said "report to origin chat @session:default@macbook/{ORIGIN}".',  # quoted
])
def test_create_without_remote_origin_allowed(body):
    assert plugin.classify_create("kanban_create", {"title": "x", "body": body}, NAMES, env={}) is None


def test_origin_inside_multiline_quote_is_not_this_cards_origin():
    """Self-heal card t_01a1815abd46d030 quoted the user's two-line message, which named "Origin @session:...";
    the 400-char single-line quote skip missed it, and Gate D demanded a send into David's chat that the worker
    send guard then refused."""
    body = (f'- Quote: "David requests this work spun out as a NEW SESSION. You are the new chat. Origin '
            f'@session:default@macbook/{ORIGIN}. User approved it.\nFirst verify existing ownership{"x" * 300}"\n'
            "## Do this\n1. Reproduce it.")
    assert core.origin_sessions(body) == []
    assert core.origin_sessions(f"Origin chat: @session:default@macbook/{ORIGIN}\n{body}") == [ORIGIN]


def test_create_fails_open_without_gateway_names():
    assert plugin.classify_create("kanban_create", CREATE_ARGS, lambda: None, env={}) is None
    assert plugin.classify_create("kanban_create", CREATE_ARGS, lambda: ("", set()), env={}) is None


def test_create_gate_via_tool_call_and_hook_outside_worker(monkeypatch):
    wrapped = {"calls": [{"name": "kanban_create", "arguments": CREATE_ARGS}]}
    assert plugin.classify_create("tool_call", wrapped, NAMES, env={})
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_ORIGIN_SESSION", raising=False)
    monkeypatch.setattr(core, "_gateway_names", NAMES)
    out = plugin._pre_tool_call("kanban_create", CREATE_ARGS)
    assert out and out["action"] == "block" and "origin_session" in out["message"]


def test_single_quoted_pattern_talk_is_not_an_admission():
    assert not core.dropped_work("roots: Gate B misses 'not verified'/'not performed' wording.")


def test_origin_chat_released_link_refused(tmp_path):
    paths = make_board(tmp_path, links=((ORIGIN, "macbook", 1),))
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths)


def test_origin_chat_reported_comment_allowed(tmp_path):
    note = f"follow-through: SENT via cross_session to @session:default@macbook/{ORIGIN} at 15:30Z; delivery verified"
    paths = make_board(tmp_path, comments=[note])
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths) is None


def test_origin_chat_sent_without_readback_allowed(tmp_path):
    note = f"follow-through: SENT via cross_session to @session:default@macbook/{ORIGIN} at 15:30Z; delivery not verified"
    paths = make_board(tmp_path, comments=[note])
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths) is None


def test_origin_chat_failed_send_comment_refused(tmp_path):
    """Live test t_01a1808c7af7225a: the send failed, the worker still wrote a SENT note, and the gate passed it."""
    note = (f"follow-through: SENT via cross_session to @session:default@hub/{ORIGIN} at 2026-10-28 03:28 UTC; "
            'delivery not verified. Send result: {"ok": false, "error": "No connected Hermes Desktop that can reach '
            '`hub` picked up the request"}')
    paths = make_board(tmp_path, comments=[note])
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths)


def test_origin_named_in_comment_refused(tmp_path):
    body = "Fix the runner counts."
    note = f"Mac receipt, relayed by the supervisor from origin @session:default@macbook/{ORIGIN}: still 11."
    paths = make_board(tmp_path, body=body, comments=[note])
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths)


@pytest.mark.parametrize("body", [
    "Fix the runner counts. No chat mentioned.",
    f"Asked in the runners chat (@session:default/{ORIGIN}) what this is blocked on.",  # no 'origin'
    'The plan said "report to origin chat @session:…d48ade" but nothing checked it.',  # elided id
])
def test_no_origin_reference_allowed(tmp_path, body):
    paths = make_board(tmp_path, body=body)
    assert plugin.classify("kanban_complete", {"summary": "Shipped and verified."}, OWN, paths) is None


def test_origin_gate_via_cli(tmp_path):
    paths = make_board(tmp_path)
    cmd = f"hermes kanban complete {OWN} --summary 'Shipped and verified.'"
    assert plugin.classify("terminal", {"command": cmd}, OWN, paths)
