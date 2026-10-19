"""Store tests against a real SessionDB in a temp dir.

Run (Hermes runtime interpreter + pytest venv, see README):
  PYTHONPATH=~/.hermes/hermes-agent:<runtime site-packages> <pytest venv python> -m pytest -q plugins/inbox/tests
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


store = _load("_inbox_store_test", HERE / "store.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    clock = {"t": 1_000_000.0}
    inbox = store.Inbox(home=tmp_path, db_factory=lambda: db, clock=lambda: clock["t"])
    yield inbox, db, clock
    db.close()


def test_post_creates_hidden_unread_session_with_proposal(env):
    inbox, db, _ = env
    r = inbox.post("Destroy idle box", "GPU 0% for 5h", "Destroy vast 123", poster="cron · audit", cwd="/tmp")
    s = db.get_session(r["session_id"])
    assert s["source"] == "desktop" and s["hidden"] and not s["archived"]
    assert s["title"] == "Destroy idle box" and s["cwd"] == "/tmp"
    msgs = db.get_messages(r["session_id"])
    assert [m["role"] for m in msgs] == ["assistant"]
    assert "Destroy vast 123" in msgs[0]["content"] and "cron · audit" in msgs[0]["content"]
    [item] = inbox.list()
    assert item["unread"] and item["poster"] == "cron · audit" and item["accept_prompt"] == "Destroy vast 123"


def test_validation(env):
    inbox, _, _ = env
    with pytest.raises(ValueError):
        inbox.post("", "b", "p")
    with pytest.raises(ValueError):
        inbox.post("t", "b", "  ")


def test_key_dedupes_and_marks_unread_again(env):
    inbox, db, clock = env
    a = inbox.post("Idle box", "5h", "destroy", key="vast:1")
    db.set_session_read(a["session_id"], True)
    assert not inbox.list()[0]["unread"]
    clock["t"] += 10
    b = inbox.post("Idle box (6h)", "6h", "destroy now", key="vast:1")
    assert b == {"session_id": a["session_id"], "updated": True}
    [item] = inbox.list()
    assert item["unread"] and item["title"] == "Idle box (6h)" and item["accept_prompt"] == "destroy now"
    assert len(db.get_messages(a["session_id"])) == 2
    # a different key is a different message
    inbox.post("Other", "x", "y", key="vast:2")
    assert len(inbox.list()) == 2


def test_reading_clears_unread(env):
    inbox, db, _ = env
    sid = inbox.post("t", "b", "p")["session_id"]
    db.set_session_read(sid, True)  # what Desktop does when the chat opens
    assert inbox.list()[0]["unread"] is False


def test_accept_unhides_and_leaves_inbox(env):
    inbox, db, _ = env
    sid = inbox.post("t", "b", "do the thing")["session_id"]
    item = inbox.accept(sid)
    assert item["accept_prompt"] == "do the thing"
    assert not db.get_session(sid)["hidden"]
    assert inbox.list() == []
    assert inbox.accept(sid) is None  # only once


def test_user_turn_accepts_only_open_messages(env):
    inbox, db, _ = env
    sid = inbox.post("t", "b", "p")["session_id"]
    assert inbox.on_user_turn("not-an-inbox-id") is False
    assert inbox.on_user_turn(sid) is True
    assert not db.get_session(sid)["hidden"]
    assert inbox.on_user_turn(sid) is False


def test_archive(env):
    inbox, db, _ = env
    sid = inbox.post("t", "b", "p", key="k")["session_id"]
    assert inbox.archive(sid)
    assert db.get_session(sid)["archived"]
    assert inbox.list() == []
    # an archived message is not reused by its key
    assert inbox.post("t", "b", "p", key="k")["session_id"] != sid
    assert inbox.archive("nope") is False


def test_archived_elsewhere_drops_out(env):
    inbox, db, _ = env
    sid = inbox.post("t", "b", "p")["session_id"]
    db.set_session_archived(sid, True)  # sidebar/session menu archive
    assert inbox.list() == []


def test_duplicate_titles_get_suffix(env):
    inbox, db, _ = env
    a = inbox.post("Same", "b", "p")["session_id"]
    b = inbox.post("Same", "b", "p")["session_id"]
    assert db.get_session(a)["title"] == "Same" and db.get_session(b)["title"] == "Same (2)"


def test_cap_archives_oldest_read(env, monkeypatch):
    inbox, db, clock = env
    monkeypatch.setattr(store, "MAX_OPEN", 3)
    ids = []
    for i in range(3):
        clock["t"] += 1
        ids.append(inbox.post(f"m{i}", "b", "p")["session_id"])
    db.set_session_read(ids[1], True)
    clock["t"] += 1
    inbox.post("m3", "b", "p")
    open_ids = {i["session_id"] for i in inbox.list()}
    assert ids[1] not in open_ids and len(open_ids) == 3
    assert db.get_session(ids[1])["archived"]


def test_tool_handler(env, monkeypatch):
    inbox, db, _ = env
    plugin = _load("_inbox_plugin_test", HERE / "__init__.py")
    monkeypatch.setattr(plugin, "store_module", lambda: type("M", (), {"Inbox": lambda *a, **k: inbox}))
    monkeypatch.setattr(plugin, "_poster_for", lambda sid: ("cron · audit", "/work"))
    out = json.loads(plugin.handle_post({"title": "t", "body": "b", "accept_prompt": "p"}, session_id="x"))
    assert out["posted"] and db.get_session(out["session_id"])["cwd"] == "/work"
    bad = json.loads(plugin.handle_post({"title": "", "body": "b", "accept_prompt": "p"}))
    assert bad["posted"] is False and "title" in bad["error"]
