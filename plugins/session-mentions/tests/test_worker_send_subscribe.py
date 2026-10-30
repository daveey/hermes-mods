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
def isolated_board(monkeypatch, tmp_path):
    # HERMES_HOME does not isolate the shared, cross-profile Kanban board.
    # CI and dispatched workers inherit an explicit live DB pin.
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)


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


def test_real_kanban_db_gets_exactly_one_row(tmp_path, monkeypatch):
    kbn = pytest.importorskip("hermes_cli.kanban_db_notify")
    kb = pytest.importorskip("hermes_cli.kanban_db")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert kb.kanban_db_path().resolve() == (tmp_path / "kanban.db").resolve()
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


def test_default_add_sub_refuses_a_task_this_board_does_not_own(monkeypatch, tmp_path):
    """enqueue() sees foreign worker cards; a refusal must not leave an orphan row in this DB."""
    kb = pytest.importorskip("hermes_cli.kanban_db")  # needs the Hermes runtime, like the test above
    kbc = pytest.importorskip("hermes_cli.kanban_db_connect")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert kb.kanban_db_path().resolve() == (tmp_path / "kanban.db").resolve()
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
