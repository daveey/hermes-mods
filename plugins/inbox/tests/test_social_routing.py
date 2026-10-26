"""Social Inbox cards land in the host's own `pr` project; everything else is untouched."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


store = _load("_inbox_store_social_test", HERE / "store.py")
rt = _load("_inbox_routing_test", HERE / "routing.py")


def make_projects(home: Path, pr_path: str, pid="p_x"):
    conn = sqlite3.connect(home / "projects.db")
    conn.executescript(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, slug TEXT UNIQUE, name TEXT, description TEXT, icon TEXT,"
        " color TEXT, board_slug TEXT, primary_path TEXT, created_at INTEGER, archived INTEGER DEFAULT 0);"
        "CREATE TABLE project_folders (project_id TEXT, path TEXT, label TEXT, is_primary INTEGER DEFAULT 0,"
        " added_at INTEGER, PRIMARY KEY (project_id, path));")
    conn.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,0)", (pid, "pr", "pr", None, None, None, None, pr_path, 1))
    conn.execute("INSERT INTO project_folders VALUES (?,?,?,?,?)", (pid, pr_path, None, 1, 1))
    conn.commit()
    conn.close()


@pytest.fixture(params=["/Users/me/code/pr", "/home/ubuntu/code/pr"])  # Mac-like and hub-like hosts
def env(request, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    make_projects(tmp_path, request.param)
    inbox = store.Inbox(home=tmp_path, db_factory=lambda: db, clock=lambda: 1_000_000.0)
    yield inbox, db, request.param
    db.close()


def cwd_of(db, r):
    return db.get_session(r["session_id"])["cwd"]


@pytest.mark.parametrize("kw", [
    dict(key="x-post:Q-7", title="Review caption"),
    dict(key="x-reply-draft:123", title="X reply draft: @a on foo"),
    dict(key="bsky-reply:abc", title="Bluesky reply draft"),
    dict(key="k1", title="X DM from Jane (@jane)"),
    dict(key="k2", title="Approve Bluesky post for Tuesday"),
    dict(key="k3", title="Draft tweet about the Coworld launch"),
    dict(key="k4", title="Bluesky DM monitoring: 2 new messages"),
    dict(key="k5", title="Draft a Bluesky post about PR #123"),
    dict(key="k6", title="Draft an X post about fixing the reply API bug"),
])
def test_social_positive_goes_to_pr(env, kw):
    inbox, db, pr = env
    kw = dict(kw)
    r = inbox.post(kw.pop("title"), "b", "p", **kw)
    assert cwd_of(db, r) == pr


@pytest.mark.parametrize("title", [
    "Merge PR #123 after CI is green",
    "Review pull request: fix inbox store",
    "PR review comments need replies",
    "Deploy the hub",
    "X is the variable name in the parser",
    "Review pull request: fix Bluesky reply parser",
    "Merge PR #123 for Twitter post scheduler",
    "Fix X reply API retry bug",
    "Bluesky reply parser needs a fix",
])
def test_non_social_pr_text_not_routed(env, title):
    inbox, db, pr = env
    assert not rt.is_social(title=title)
    assert not rt.is_social(title=title, poster="Twitter post monitor")
    r = inbox.post(title, "b", "p", cwd="/tmp/work", key="coding:1")
    assert cwd_of(db, r) == "/tmp/work"
    r2 = inbox.post(title + " again", "b", "p", key="coding:2", inherited_cwd="/Users/me/code/hermes")
    assert cwd_of(db, r2) == "/Users/me/code/hermes"


def test_explicit_cwd_wins(env):
    inbox, db, pr = env
    r = inbox.post("X post pending", "b", "p", key="x-post:Z", cwd="/Users/me/code/writing")
    assert cwd_of(db, r) == "/Users/me/code/writing"


def test_inherited_cwd_replaced_unless_already_in_pr(env):
    inbox, db, pr = env
    a = inbox.post("X post pending", "b", "p", key="x-post:A", inherited_cwd="/Users/me")
    assert cwd_of(db, a) == pr
    b = inbox.post("X post pending", "b", "p", key="x-post:B", inherited_cwd=pr + "/twitter")
    assert cwd_of(db, b) == pr + "/twitter"


def test_host_path_resolved_from_local_db_not_copied(tmp_path):
    make_projects(tmp_path, "/home/ubuntu/code/pr", pid="p_85ccfc45")
    assert rt.project_path(tmp_path) == "/home/ubuntu/code/pr"
    assert rt.route_cwd(tmp_path, explicit=None, inherited="/Users/me/code/pr", social=True) == "/home/ubuntu/code/pr"


def test_no_pr_project_keeps_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    inbox = store.Inbox(home=tmp_path, db_factory=lambda: db)
    r = inbox.post("X post pending", "b", "p", key="x-post:A", inherited_cwd="/Users/me")
    assert db.get_session(r["session_id"])["cwd"] == "/Users/me"
    db.close()


def test_tool_passes_inherited_cwd_separately(tmp_path, monkeypatch):
    """The agent tool's own session cwd is inherited (replaceable), a cwd arg is explicit."""
    import json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin = _load("_inbox_plugin_social_test", HERE / "__init__.py")
    seen = {}

    class Fake:
        def post(self, *a, **kw):
            seen.update(kw)
            return {"session_id": "s"}
    monkeypatch.setattr(plugin, "store_module", lambda: type("M", (), {"Inbox": Fake}))
    monkeypatch.setattr(plugin, "_poster_for", lambda sid: ("cron", "/Users/me"))
    out = json.loads(plugin.handle_post({"title": "t", "body": "b", "cwd": "/x"}, session_id="s"))
    assert out["posted"] and seen["cwd"] == "/x" and seen["inherited_cwd"] == "/Users/me"
    plugin.handle_post({"title": "t", "body": "b"}, session_id="s")
    assert seen["cwd"] is None and seen["inherited_cwd"] == "/Users/me"


def test_backfill_moves_only_fallback_cwd_and_is_idempotent(env, monkeypatch):
    inbox, db, pr = env
    home = str(Path.home())
    # A known non-explicit fallback can occur before a pr project is configured.
    with monkeypatch.context() as m:
        m.setattr(store.routing_module(), "project_path", lambda home: None)
        a = inbox.post("X post pending", "b", "p", key="x-post:A")
    b = inbox.post("X post pending", "b", "p", key="x-post:B", cwd="/Users/me/code/writing")  # chosen -> stays
    c = inbox.post("Merge PR #4", "b", "p", key="coding:1", cwd=home)         # not social -> stays
    db.end_session(a["session_id"], "completed")
    dry = {r["key"]: r["action"] for r in inbox.backfill_social()}
    assert dry == {"x-post:A": "would-move", "x-post:B": "skip:explicit-cwd"}
    assert cwd_of(db, a) == home
    done = {r["key"]: r["action"] for r in inbox.backfill_social(apply=True)}
    assert done["x-post:A"] == "moved"
    assert cwd_of(db, a) == pr and cwd_of(db, b) == "/Users/me/code/writing" and cwd_of(db, c) == home
    again = {r["key"]: r["action"] for r in inbox.backfill_social(apply=True)}
    assert again["x-post:A"] == "already"


@pytest.mark.parametrize("explicit", [True, False])
def test_provenance_survives_repost_and_reopen(env, explicit):
    inbox, db, pr = env
    home = str(Path.home())
    r = inbox.post("X post pending", "b", "p", key="x-post:A", cwd=home if explicit else None)
    assert inbox.get(r["session_id"])["cwd_explicit"] == int(explicit)
    inbox.post("X post updated", "b", "p", key="x-post:A", cwd=None if explicit else home)
    fresh = store.Inbox(home=inbox.home, db_factory=lambda: db)
    assert fresh.get(r["session_id"])["cwd_explicit"] == int(explicit)
    db.end_session(r["session_id"], "completed")
    action = fresh.backfill_social(apply=True)[0]["action"]
    assert action == ("skip:explicit-cwd" if explicit else "already")
    assert cwd_of(db, r) == (home if explicit else pr)


def test_legacy_schema_migration_leaves_provenance_unknown(env):
    inbox, db, pr = env
    home = str(Path.home())
    r = inbox.post("X post pending", "b", "p", key="x-post:A", cwd=home)
    db.end_session(r["session_id"], "completed")
    with sqlite3.connect(inbox.path) as side:
        side.execute("ALTER TABLE items DROP COLUMN cwd_explicit")
    assert inbox.get(r["session_id"])["cwd_explicit"] is None
    assert inbox.backfill_social(apply=True)[0]["action"] == "skip:unknown-cwd-provenance"
    assert cwd_of(db, r) == home


@pytest.mark.parametrize("message_count", [1, 8])
@pytest.mark.parametrize("age", [1, 100_000])
def test_backfill_unended_sessions_fail_closed(env, monkeypatch, message_count, age):
    inbox, db, pr = env
    with monkeypatch.context() as m:
        m.setattr(store.routing_module(), "project_path", lambda home: None)
        r = inbox.post("X post pending", "b", "p", key="x-post:A")
    db._execute_write(lambda c: c.execute(
        "UPDATE sessions SET started_at=?, last_activity_at=?, message_count=? WHERE id=?",
        (inbox.clock() - age, inbox.clock() - age, message_count, r["session_id"])))
    assert inbox.backfill_social(apply=True)[0]["action"] == "skip:active"
    assert cwd_of(db, r) == str(Path.home())


@pytest.mark.parametrize("change", ["resume", "cwd", "archive"])
def test_backfill_rechecks_session_at_write(env, monkeypatch, change):
    inbox, db, pr = env
    with monkeypatch.context() as m:
        m.setattr(store.routing_module(), "project_path", lambda home: None)
        r = inbox.post("X post pending", "b", "p", key="x-post:A")
    db.end_session(r["session_id"], "completed")
    write = db._execute_write

    def raced_write(fn):
        sql = {"resume": "ended_at=NULL", "cwd": "cwd='/chosen'", "archive": "archived=1"}[change]
        write(lambda c: c.execute(f"UPDATE sessions SET {sql} WHERE id=?", (r["session_id"],)))
        return write(fn)

    monkeypatch.setattr(db, "_execute_write", raced_write)
    assert inbox.backfill_social(apply=True)[0]["action"] == "skip:changed"
    assert cwd_of(db, r) == ("/chosen" if change == "cwd" else str(Path.home()))


@pytest.mark.parametrize("missing", ["session", "ended_at"])
def test_backfill_missing_lifecycle_evidence_fails_closed(env, monkeypatch, missing):
    inbox, db, pr = env
    with monkeypatch.context() as m:
        m.setattr(store.routing_module(), "project_path", lambda home: None)
        r = inbox.post("X post pending", "b", "p", key="x-post:A")
    get_session = db.get_session
    snapshot = get_session(r["session_id"])
    snapshot.pop("ended_at", None)
    with monkeypatch.context() as m:
        m.setattr(db, "get_session", lambda sid: None if missing == "session" else snapshot)
        assert inbox.backfill_social(apply=True)[0]["action"] == "skip:active"
    assert cwd_of(db, r) == str(Path.home())


def test_backfill_archived_opt_in_still_requires_ended_session(env, monkeypatch):
    inbox, db, pr = env
    with monkeypatch.context() as m:
        m.setattr(store.routing_module(), "project_path", lambda home: None)
        r = inbox.post("X post pending", "b", "p", key="x-post:A")
    db.set_session_archived(r["session_id"], True)
    assert inbox.backfill_social(apply=True)[0]["action"] == "skip:archived"
    assert inbox.backfill_social(apply=True, include_archived=True)[0]["action"] == "skip:active"
    db.end_session(r["session_id"], "completed")
    assert inbox.backfill_social(apply=True, include_archived=True)[0]["action"] == "moved"
    assert cwd_of(db, r) == pr


def test_card_type_alone_classifies():
    assert rt.is_social(type="x-dm", title="anything") and rt.is_social(type="bsky-post")
    assert not rt.is_social(type="default", title="Deploy", key="deploy:1")
