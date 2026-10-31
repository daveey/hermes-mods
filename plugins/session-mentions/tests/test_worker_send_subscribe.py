"""A refused worker send into a human chat subscribes that chat to the worker's own card.

Local chat -> platform 'tui' row; chat on another gateway -> the cross-gateway 'hub' row
(`<gw>/<profile>/<sid>`) hub cards already use. Idempotent; the refusal text says so and points
the worker at its completion summary (no relay card). A non-worker send is untouched.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sm_sub_plugin", _ROOT / "__init__.py")
sm = importlib.util.module_from_spec(_spec)
sys.modules["sm_sub_plugin"] = sm
_spec.loader.exec_module(sm)  # type: ignore[union-attr]
rf = sm.rf

REMOTE = "@session:default@macbook/20261023_204125_cb2658"
LOCAL = "@session:default/20261027_133954_1b6eae"


class Subs:
    """Stand-in for kanban_notify_subs keyed like the real table: (task, platform, chat)."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    def __call__(self, **kw):
        self.rows.setdefault((kw["task_id"], kw["platform"], kw["chat_id"]), kw)


@pytest.fixture(autouse=True)
def isolated_board(monkeypatch):
    # TMPDIR may live under the worker's fenced ~/.hermes root. A test board
    # directly under the user home stays outside that root, including in CI.
    # Keep the descendant fence intact; a DB env pin is itself fenced, so use
    # the isolated Kanban home instead. TemporaryDirectory cleans up on failure.
    with TemporaryDirectory(prefix="session-mentions-test-", dir=Path.home()) as directory:
        board_home = Path(directory)
        monkeypatch.setenv("HERMES_HOME", str(board_home))
        monkeypatch.setattr(rf, "_hermes_home", lambda: board_home)
        import sqlite3
        with sqlite3.connect(board_home / "state.db") as conn:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
            conn.executemany("INSERT INTO sessions VALUES (?, 'desktop')", [("20261027_133954_1b6eae",), ("abc123",)])
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(board_home))
        monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        yield board_home


@pytest.fixture
def subs(monkeypatch):
    s = Subs()
    monkeypatch.setattr(rf, "_default_add_sub", s)
    monkeypatch.setattr(rf, "_this_gateway", lambda: "hub")
    monkeypatch.setattr(rf, "session_source", lambda _t: "desktop")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker")
    return s


def test_refused_send_to_remote_chat_creates_one_hub_subscription(subs):
    for _ in range(2):  # the tool and POST /enqueue both refuse: still exactly one row
        err = rf.worker_send_refusal(REMOTE)
        assert err and "subscribed" in err
    assert list(subs.rows) == [("t_worker", "hub", "macbook/default/20261023_204125_cb2658")]


def test_refused_send_to_local_chat_creates_one_tui_subscription(subs):
    for _ in range(2):
        err = rf.worker_send_refusal(LOCAL)
        assert err and "subscribed" in err
    assert list(subs.rows) == [("t_worker", "tui", "20261027_133954_1b6eae")]
    assert subs.rows[("t_worker", "tui", "20261027_133954_1b6eae")]["delivery_metadata"] == {"reason": "send_refused"}


def test_remote_ref_naming_this_gateway_is_local(subs):
    rf.worker_send_refusal("@session:default@hub/abc123")
    assert list(subs.rows) == [("t_worker", "tui", "abc123")]


def test_refusal_text_points_at_completion_summary_not_a_relay_card(subs):
    err = rf.worker_send_refusal(REMOTE)
    assert "kanban_complete summary" in err
    assert f"FINAL SEND for {REMOTE}:" in err
    assert "Do NOT create a relay card" in err
    assert "t_worker" in err


def test_subscription_failure_keeps_the_old_refusal(subs, monkeypatch):
    def boom(**_kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr(rf, "_default_add_sub", boom)
    err = rf.worker_send_refusal(REMOTE)
    assert err and "subscribed" not in err
    assert "hermes-hub create-task" in err and "kanban_block" in err
    assert subs.rows == {}


def test_unconfirmed_source_is_refused_without_subscribing(subs, monkeypatch):
    monkeypatch.setattr(rf, "session_source", lambda _t: None)
    err = rf.worker_send_refusal(REMOTE)
    assert err and "could not be confirmed" in err
    assert subs.rows == {}


def test_worker_to_worker_session_is_not_subscribed(subs, monkeypatch):
    monkeypatch.setattr(rf, "session_source", lambda _t: "kanban")
    assert rf.worker_send_refusal(REMOTE) is None
    assert subs.rows == {}


def test_interactive_sender_is_unchanged(subs, monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert rf.worker_send_refusal(REMOTE) is None
    assert subs.rows == {}
    sent = []
    monkeypatch.setattr(rf, "root", lambda: tmp_path)
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir()
    real = rf.atomic_write

    def write(path, data):
        real(path, data)
        if Path(path).parent.name == "outbox":
            sent.append(data)
            real(tmp_path / "results" / f"{data['id']}.json", {"ok": True})

    monkeypatch.setattr(rf, "atomic_write", write)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})
    out = json.loads(sm.handle({"action": "send", "target": REMOTE, "message": "hi"}))
    assert out["ok"] is True and sent and subs.rows == {}


def test_tool_refusal_end_to_end(subs):
    out = json.loads(sm.handle({"action": "send", "target": REMOTE, "message": "result"}))
    assert out["ok"] is False and "subscribed" in out["error"]
    assert len(subs.rows) == 1


def test_real_kanban_db_gets_exactly_one_row(isolated_board, monkeypatch):
    kbn = pytest.importorskip("hermes_cli.kanban_db_notify")
    kb = pytest.importorskip("hermes_cli.kanban_db")
    from agent.delegation_context import kanban_path_is_fenced

    monkeypatch.setenv("HERMES_HOME", str(isolated_board))
    assert kb.kanban_db_path().resolve() == (isolated_board / "kanban.db").resolve()
    assert not kanban_path_is_fenced(kb.kanban_db_path())
    kb.init_db()
    from hermes_cli import kanban_db_connect as kbc

    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="default")
    monkeypatch.setattr(rf, "_this_gateway", lambda: "hub")
    monkeypatch.setattr(rf, "session_source", lambda _t: "desktop")
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    for target in (REMOTE, REMOTE, LOCAL, LOCAL):
        assert "subscribed" in rf.worker_send_refusal(target)
    with kbc.connect_closing() as conn:
        rows = kbn.list_notify_subs(conn, tid)
    assert sorted((r["platform"], r["chat_id"]) for r in rows) == [
        ("hub", "macbook/default/20261023_204125_cb2658"),
        ("tui", "20261027_133954_1b6eae"),
    ]


def test_default_add_sub_refuses_a_task_this_board_does_not_own(monkeypatch, isolated_board):
    """enqueue() sees foreign worker cards; a refusal must not leave an orphan row in this DB."""
    kb = pytest.importorskip("hermes_cli.kanban_db")  # needs the Hermes runtime, like the test above
    kbc = pytest.importorskip("hermes_cli.kanban_db_connect")

    from agent.delegation_context import kanban_path_is_fenced

    monkeypatch.setenv("HERMES_HOME", str(isolated_board))
    assert kb.kanban_db_path().resolve() == (isolated_board / "kanban.db").resolve()
    assert not kanban_path_is_fenced(kb.kanban_db_path())
    with kbc.connect_closing() as conn:
        mine = kb.create_task(conn, title="mine", assignee="default")
    rf._default_add_sub(task_id=mine, platform="tui", chat_id="s1", notifier_profile="default")
    with pytest.raises(LookupError):
        rf._default_add_sub(task_id="t_foreign", platform="tui", chat_id="s1", notifier_profile="default")
    with kbc.connect_closing() as conn:
        rows = conn.execute("SELECT task_id FROM kanban_notify_subs").fetchall()
    assert [r[0] for r in rows] == [mine]
    monkeypatch.setattr(rf, "_this_gateway", lambda: "hub")
    assert rf.subscribe_refused_chat(LOCAL, "t_foreign") is None


def replica(monkeypatch, tmp_path, owners):
    import sqlite3
    path = tmp_path / "hub.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, data TEXT)")
        for gw, profile, sid in owners:
            conn.execute("INSERT INTO replica VALUES (?, 'sessions', ?, ?)",
                         (gw, f"{profile}/{sid}", json.dumps({"source": "desktop", "profile": profile})))
    monkeypatch.setattr(rf, "replica_db", lambda: path)
    return path


def test_bare_remote_discovery_does_not_create_local_unseen(subs, tmp_path, monkeypatch):
    replica(monkeypatch, tmp_path, [("remote", "default", "s_remote")])
    for _ in range(2):
        assert "subscribed" in rf.worker_send_refusal("@session:default/s_remote")
    assert list(subs.rows) == [("t_worker", "hub", "remote/default/s_remote")]


@pytest.mark.parametrize("owners", [[], [("remote", "other", "s_unknown")],
    [("one", "default", "s_unknown"), ("two", "default", "s_unknown")]])
def test_unknown_or_ambiguous_owner_does_not_subscribe(subs, tmp_path, monkeypatch, owners):
    replica(monkeypatch, tmp_path, owners)
    assert rf.subscribe_refused_chat("@session:default/s_unknown", "t_worker") is None
    assert subs.rows == {}


def test_unreadable_replica_does_not_subscribe(subs, tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    path.write_text("not sqlite")
    monkeypatch.setattr(rf, "replica_db", lambda: path)
    assert rf.subscribe_refused_chat("@session:default/s_unknown", "t_worker") is None
    assert subs.rows == {}


def test_real_discovery_to_completion_leaves_no_undrainable_local_unseen(monkeypatch, isolated_board, tmp_path):
    """Composition the old tests mocked away: REAL session_source discovery of a bare ref in the hub replica,
    the refusal's subscription on a real board, then completion. The remote chat must get the hub route only,
    so this gateway's unseen map (what the chat-closeout watchdog reads) never counts it; a genuinely local
    chat refused the same way still counts as unseen until it is handed the result."""
    kb = pytest.importorskip("hermes_cli.kanban_db")
    kbc = pytest.importorskip("hermes_cli.kanban_db_connect")
    kbn = pytest.importorskip("hermes_cli.kanban_db_notify")
    ksa = pytest.importorskip("hermes_cli.kanban_session_activity")

    path = replica(monkeypatch, tmp_path, [("macbook", "default", "s_remote")])
    import sqlite3
    with sqlite3.connect(path) as conn:  # session_source reads $.source from the replica row
        conn.execute("UPDATE replica SET data=json_set(data, '$.source', 'desktop')")
    monkeypatch.setattr(rf, "_this_gateway", lambda: "hub")
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="default")
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    assert rf.session_source("@session:default/s_remote") == "desktop"  # real discovery, not a mock
    assert "subscribed" in rf.worker_send_refusal("@session:default/s_remote")
    assert "subscribed" in rf.worker_send_refusal(LOCAL)
    with kbc.connect_closing() as conn:
        kb.complete_task(conn, tid, summary="done")
        rows = sorted((r["platform"], r["chat_id"]) for r in kbn.list_notify_subs(conn, tid))
        unseen = ksa._unseen_on(conn)
    assert rows == [("hub", "macbook/default/s_remote"), ("tui", "20261027_133954_1b6eae")]
    assert unseen == {"20261027_133954_1b6eae": 1}
