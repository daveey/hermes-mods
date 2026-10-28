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


def test_profile_homes_lists_root_then_profiles_with_an_inbox(tmp_path):
    (tmp_path / "profiles" / "alpha-follow" / "inbox").mkdir(parents=True)
    (tmp_path / "profiles" / "alpha-follow" / "inbox" / "inbox.db").write_text("")
    (tmp_path / "profiles" / "no-inbox").mkdir(parents=True)
    homes = store.profile_homes(tmp_path)
    assert homes == [("default", tmp_path), ("alpha-follow", tmp_path / "profiles" / "alpha-follow")]
    assert store.profile_home(tmp_path, "alpha-follow") == tmp_path / "profiles" / "alpha-follow"
    assert store.profile_home(tmp_path, "default") == tmp_path
    assert store.profile_home(tmp_path, "") == tmp_path
    with pytest.raises(ValueError):
        store.profile_home(tmp_path, "../etc")


def test_for_home_binds_a_profiles_own_state_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    prof = tmp_path / "profiles" / "p1"
    prof.mkdir(parents=True)
    r = store.Inbox.for_home(prof).post("t", "b", "do", key="k")
    from hermes_state import SessionDB
    db = SessionDB(db_path=prof / "state.db")
    try:
        assert db.get_session(r["session_id"])["hidden"]
    finally:
        db.close()
    assert [i["session_id"] for i in store.Inbox.for_home(prof).list()] == [r["session_id"]]
    assert store.Inbox.for_home(tmp_path).list() == []


def test_same_key_after_a_reply_lands_in_that_conversation_not_a_new_card(env):
    """The user replied to a card (accepted -> a normal chat); a follow-up on the same key goes into
    that chat, unread, instead of opening a duplicate card. Archived = dismissed: a new card."""
    inbox, db, clock = env
    a = inbox.post("Board seat?", "asked in #x", "post reply", key="slack:C1:1.0:board")
    assert inbox.on_user_turn(a["session_id"])  # the user typed a reply
    db.set_session_read(a["session_id"], True)
    clock["t"] += 10
    b = inbox.post("Board seat? (follow-up)", "asked again", "post reply 2", key="slack:C1:1.0:board")
    assert b == {"session_id": a["session_id"], "updated": True, "conversation": True}
    s = db.get_session(a["session_id"])
    assert not s["hidden"] and float(s.get("last_read_at") or 0) < clock["t"]
    assert "asked again" in db.get_messages(a["session_id"])[-1]["content"]
    assert inbox.list() == []  # still a chat in Recents, not back in the Inbox
    inbox.archive(a["session_id"])
    c = inbox.post("Board seat? (3rd)", "again", "p", key="slack:C1:1.0:board")
    assert c["session_id"] != a["session_id"] and not c["updated"]


def test_prompt_label_replaces_the_prompt_dump_on_the_card(env):
    inbox, db, clock = env
    long_prompt = "Review improvement 1 (coach-apply show 1). " * 10
    r = inbox.post("Tip: Park cards", "**Now:** x", long_prompt, key="k:1", poster="coach · improvements",
                   prompt_label="check whether this is helping, then keep or undo it")
    [m] = db.get_messages(r["session_id"])
    assert "**Do it:** check whether this is helping, then keep or undo it" in m["content"]
    assert "Review improvement 1" not in m["content"] and "**Proposed:**" not in m["content"]
    assert "_From coach · improvements. Reply to adjust it, or archive to dismiss._" in m["content"]
    assert inbox.list()[0]["accept_prompt"] == long_prompt.strip()  # Do it still sends the whole prompt
    clock["t"] += 10
    inbox.post("Tip: Park cards", "update", long_prompt, key="k:1", prompt_label="keep or undo")
    assert "**Do it:** keep or undo" in db.get_messages(r["session_id"])[-1]["content"]


def test_without_prompt_label_the_card_still_shows_the_prompt(env):
    inbox, db, _ = env
    r = inbox.post("t", "b", "Destroy vast 123")
    assert "**Proposed:** Destroy vast 123" in db.get_messages(r["session_id"])[0]["content"]


def test_tool_and_cli_pass_prompt_label(env, monkeypatch):
    inbox, db, _ = env
    plugin = _load("_inbox_plugin_test2", HERE / "__init__.py")
    monkeypatch.setattr(plugin, "store_module", lambda: type("M", (), {"Inbox": lambda *a, **k: inbox}))
    monkeypatch.setattr(plugin, "_poster_for", lambda sid: ("cron · audit", "/work"))
    out = json.loads(plugin.handle_post({"title": "t", "body": "b", "accept_prompt": "long p",
                                         "prompt_label": "stop the box"}, session_id="x"))
    assert "**Do it:** stop the box" in db.get_messages(out["session_id"])[0]["content"]
    import argparse
    ap = argparse.ArgumentParser()
    plugin._setup_cli(ap)
    args = ap.parse_args(["post", "t2", "--body", "b", "--prompt", "p", "--prompt-label", "do the thing"])
    assert plugin._run_cli(args) == 0
    sid = [i for i in inbox.list() if i["title"] == "t2"][0]["session_id"]
    assert "**Do it:** do the thing" in db.get_messages(sid)[0]["content"]


# ── noise breaker: a poster David keeps archiving is refused ───────────────────
def _env_with_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    clock = {"t": 1_000_000.0}
    tasks = []
    inbox = store.Inbox(home=tmp_path, db_factory=lambda: db, clock=lambda: clock["t"],
                        task_creator=lambda **kw: tasks.append(kw) or "t_mute1")
    return inbox, db, clock, tasks


def _post_and_archive(inbox, clock, poster, n):
    for i in range(n):
        clock["t"] += 60
        r = inbox.post(f"Main CI red @ {i}", "red", "fix it", key=f"ci:{i}", poster=poster)
        inbox.archive(r["session_id"])


def test_poster_muted_after_three_ignored_cards(tmp_path, monkeypatch):
    inbox, db, clock, tasks = _env_with_tasks(tmp_path, monkeypatch)
    _post_and_archive(inbox, clock, "cron · ci-main-watch", 3)
    with pytest.raises(store.PosterMuted):
        inbox.post("Main CI red @ 4", "red", "fix it", key="ci:4", poster="cron · ci-main-watch")
    assert not [i for i in inbox.list() if i["key"] == "ci:4"]
    # one fix card for the poster, idempotent across repeated refusals
    with pytest.raises(store.PosterMuted):
        inbox.post("Main CI red @ 5", "red", "fix it", key="ci:5", poster="cron · ci-main-watch")
    assert {t["idempotency_key"] for t in tasks} == {"inbox-muted:cron · ci-main-watch"}
    assert tasks[0]["assignee"] == "default"
    # other posters are unaffected
    assert inbox.post("Approve spend", "b", "go", poster="cron · audit")["updated"] is False
    db.close()


def test_accepted_card_breaks_the_streak(tmp_path, monkeypatch):
    inbox, db, clock, tasks = _env_with_tasks(tmp_path, monkeypatch)
    _post_and_archive(inbox, clock, "coach", 2)
    clock["t"] += 60
    r = inbox.post("useful", "b", "go", key="u", poster="coach")
    inbox.accept(r["session_id"])
    _post_and_archive(inbox, clock, "coach", 2)
    assert inbox.post("still allowed", "b", "go", poster="coach")["updated"] is False
    assert tasks == []
    db.close()


def test_sidebar_archive_counts_and_unmute_and_window(tmp_path, monkeypatch):
    inbox, db, clock, tasks = _env_with_tasks(tmp_path, monkeypatch)
    for i in range(3):  # archived through the session door, not inbox.archive
        clock["t"] += 60
        r = inbox.post(f"x{i}", "b", "go", key=f"x{i}", poster="p")
        db.set_session_archived(r["session_id"], True)
    with pytest.raises(store.PosterMuted):
        inbox.post("x3", "b", "go", poster="p")
    clock["t"] += 60
    inbox.unmute("p")
    assert inbox.post("after unmute", "b", "go", poster="p")["updated"] is False
    db.close()


def test_mute_expires_after_window(tmp_path, monkeypatch):
    inbox, db, clock, tasks = _env_with_tasks(tmp_path, monkeypatch)
    _post_and_archive(inbox, clock, "p", 3)
    clock["t"] += store.MUTE_WINDOW_S + 1
    assert inbox.post("a week later", "b", "go", poster="p")["updated"] is False
    db.close()


def test_rekey_update_of_open_card_is_not_refused(tmp_path, monkeypatch):
    inbox, db, clock, tasks = _env_with_tasks(tmp_path, monkeypatch)
    clock["t"] += 60
    inbox.post("open one", "b", "go", key="keep", poster="p")
    _post_and_archive(inbox, clock, "p", 3)
    assert inbox.post("open one v2", "b2", "go", key="keep", poster="p")["updated"] is True
    db.close()


def test_mute_card_failure_is_visible_not_silent(tmp_path, monkeypatch, capsys, caplog):
    """A fenced worker terminal cannot create the Kanban card: the refusal still fires and says so."""
    inbox, db, clock, tasks = _env_with_tasks(tmp_path, monkeypatch)

    def fenced(**kw):
        raise RuntimeError("board writes are fenced (HERMES_DELEGATED_CHILD_CONTEXT)")
    inbox._task_creator = fenced
    _post_and_archive(inbox, clock, "p-fenced", 3)
    with caplog.at_level("WARNING"):
        with pytest.raises(store.PosterMuted):
            inbox.post("x", "b", "go", key="k:x", poster="p-fenced")
    err = capsys.readouterr().err
    assert "inbox-muted:p-fenced" in err and "NOT be filed" in err and "fenced" in err
    assert "NOT be filed" in caplog.text
    db.close()
