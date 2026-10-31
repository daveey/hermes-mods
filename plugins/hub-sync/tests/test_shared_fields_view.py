"""Phase 6 shared fields through the plugin, against the hermes-hub client checkout (>= 1.5.0).

The merged view shows a set the owner has not applied yet (marked `shared`), drops the marker once the owner's own
value matches, shows an Inbox card's pending action, and the request endpoint turns a shared-field action into a set
in primary mode but a request to the owner when shared fields are off.
"""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from conftest import NOW, PLUGIN, _load, build_db

CANDIDATES = [os.environ.get("HERMES_HUB_CLIENT_REAL"), str(Path.home() / "code" / "hermes-hub" / "client" / "hermes_hub.py")]
CLIENT = next((Path(p) for p in CANDIDATES if p and Path(p).is_file() and 'def push_shared' in Path(p).read_text()), None)
pytestmark = pytest.mark.skipif(CLIENT is None, reason="no hermes-hub client with shared fields (>= 1.5.0)")


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("HERMES_HUB_STATE", str(state))
    monkeypatch.setenv("HERMES_HUB_CLIENT", str(CLIENT))
    monkeypatch.setenv("HERMES_MAIL_CONFIG", str(tmp_path / "cfg" / "config.json"))
    monkeypatch.setenv("HERMES_HUB_URL", "http://127.0.0.1:9")
    client = _load(CLIENT, "shared_hermes_hub")
    build_db(state / "hub.db", client.SCHEMA)
    hv = _load(PLUGIN / "hubview.py", "hv_shared")
    monkeypatch.setattr(hv, "_now", lambda: NOW)
    return hv, client, state / "hub.db"


def _mode(db_path, mode):
    c = sqlite3.connect(str(db_path))
    c.execute("INSERT INTO meta (k, v) VALUES ('shared_fields', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (json.dumps(mode),))
    c.commit()
    c.close()


def _override(db_path, gw, coll, key, field, value, by="cloud", hlc="1700000000000-0000-cloud"):
    c = sqlite3.connect(str(db_path))
    c.execute("INSERT OR REPLACE INTO overrides (gw, coll, key, field, value, hlc, by_gw, seq) VALUES (?,?,?,?,?,?,?,?)",
              (gw, coll, key, field, None if value is None else json.dumps(value), hlc, by, 50))
    c.commit()
    c.close()


def _session(hv, gw, key):
    return next(s for s in hv.sessions(include_archived=True)["sessions"] if s["gateway"] == gw and s["key"] == key)


def test_primary_view_shows_an_unapplied_set_and_marks_it(env):
    hv, client, db = env
    _mode(db, "primary")
    _override(db, "mini", "sessions", "default/s_mini", "title", "Renamed on cloud")
    _override(db, "mini", "sessions", "default/s_mini", "pinned", True)
    s = _session(hv, "mini", "default/s_mini")
    assert s["title"] == "Renamed on cloud" and s["pinned"] is True
    assert s["shared"]["title"] == {"by": "cloud", "owner_value": "Mini chat"}


def test_shadow_and_off_views_are_todays_view(env):
    hv, client, db = env
    _override(db, "mini", "sessions", "default/s_mini", "title", "Renamed on cloud")
    for mode in ("off", "shadow"):
        _mode(db, mode)
        s = _session(hv, "mini", "default/s_mini")
        assert s["title"] == "Mini chat" and "shared" not in s


def test_marker_goes_once_the_owner_publishes_the_same_value(env):
    hv, client, db = env
    _mode(db, "primary")
    _override(db, "mini", "sessions", "default/s_mini", "title", "Mini chat")  # owner already applied it
    s = _session(hv, "mini", "default/s_mini")
    assert s["title"] == "Mini chat" and "shared" not in s


def test_inbox_pending_action_and_archived_override(env):
    hv, client, db = env
    _mode(db, {"inbox": "primary"})
    _override(db, "mini", "inbox", "default/s_mcard", "pending_action",
              {"request_id": "hr-1", "action": "act", "by": "laptop", "created_at": NOW}, by="server",
              hlc="1700000000000-0000-server")
    card = next(i for i in hv.inbox()["items"] if i["key"] == "default/s_mcard")
    assert card["pending_action"]["request_id"] == "hr-1"
    _override(db, "mini", "inbox", "default/s_mcard", "archived", True)
    assert all(i["key"] != "default/s_mcard" for i in hv.inbox()["items"])  # archived cards leave the open list
    closed = next(i for i in hv.inbox(include_closed=True)["items"] if i["key"] == "default/s_mcard")
    assert closed["state"] == "archived"


def test_a_cleared_pending_action_is_not_shown(env):
    hv, client, db = env
    _mode(db, "primary")
    _override(db, "mini", "inbox", "default/s_mcard", "pending_action", None, by="server", hlc="1700000000001-0000-server")
    card = next(i for i in hv.inbox()["items"] if i["key"] == "default/s_mcard")
    assert "pending_action" not in card


def test_request_in_primary_becomes_a_set_not_a_request(env):
    hv, client, db = env
    _mode(db, "primary")
    res = hv.request({"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "rename",
                      "args": {"title": "From the laptop"}}, wait_s=5)
    assert res["state"] == "applied" and res["via"] == "shared_field"
    c = client.open_db(db)
    try:
        assert c.execute("SELECT state FROM sent WHERE id = ?", (res["id"],)).fetchone()["state"] == "done"
        out = c.execute("SELECT gw, coll, key, field, value FROM shared_out").fetchall()
        assert [tuple(r) for r in out] == [("mini", "sessions", "default/s_mini", "title", '"From the laptop"')]
    finally:
        c.close()
    assert _session(hv, "mini", "default/s_mini")["title"] == "From the laptop"  # this gateway sees it at once


def test_request_with_shared_fields_off_is_a_request_to_the_owner(env):
    hv, client, db = env
    _mode(db, "off")
    res = hv.request({"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "pin"}, wait_s=5)
    assert res["state"] == "local" and "via" not in res
    c = client.open_db(db)
    try:
        assert c.execute("SELECT state FROM sent WHERE id = ?", (res["id"],)).fetchone()["state"] == "local"
        assert c.execute("SELECT COUNT(*) FROM shared_out").fetchone()[0] == 0
    finally:
        c.close()


def test_an_inbox_act_stays_a_request_in_primary(env):
    hv, client, db = env
    _mode(db, "primary")
    res = hv.request({"gateway": "mini", "coll": "inbox", "key": "default/s_mcard", "action": "act",
                      "args": {"action": "approve"}, "base_version": 7}, wait_s=5)
    assert res["state"] == "local"
    c = client.open_db(db)
    try:
        assert c.execute("SELECT COUNT(*) FROM shared_out").fetchone()[0] == 0
    finally:
        c.close()


def _full_sessions(hv, gateway=None, exclude=(), include_archived=False, limit=500):
    """The pre-pagination read: decorate every row, then filter, sort and cut."""
    def fn(v):
        rows = [r for r in v.rows("sessions") if (not gateway or r["gateway"] == gateway)
                and r["gateway"] not in set(exclude) and (include_archived or not r.get("archived"))]
        rows.sort(key=lambda r: -(r.get("activity") or r.get("started_at") or 0))
        return {"sessions": rows[:limit]}
    return hv._with_view(fn, sessions=[])["sessions"]


def test_sessions_page_matches_a_full_decorated_read_with_overrides(env):
    hv, client, db = env
    _mode(db, "primary")
    all_rows = hv.sessions(include_archived=True)["sessions"]
    mini = next(s for s in all_rows if s["gateway"] == "mini" and not s.get("archived"))
    old = next(s for s in all_rows if s.get("archived"))
    _override(db, mini["gateway"], "sessions", mini["key"], "archived", True)    # hidden by an override
    _override(db, old["gateway"], "sessions", old["key"], "archived", False)     # un-archived by an override
    _override(db, mini["gateway"], "sessions", mini["key"], "title", "Renamed")
    for kw in ({}, {"include_archived": True}, {"gateway": "mini"}, {"exclude": ["mini"]}, {"limit": 1}, {"limit": 2}):
        got = hv.sessions(**kw)["sessions"]
        assert got == _full_sessions(hv, **kw), kw
    keys = {(s["gateway"], s["key"]) for s in hv.sessions()["sessions"]}
    assert (mini["gateway"], mini["key"]) not in keys and (old["gateway"], old["key"]) in keys
    before = hv.sessions(include_archived=True)["sessions"]
    hv.sessions(limit=1)
    assert hv.sessions(include_archived=True)["sessions"] == before  # cache rows are not mutated


def test_overrides_are_decoded_once_per_hub_db_and_unchanged_values_are_reused(env, monkeypatch):
    """Every poll opens a new View; ~6.6k sessions overrides used to be re-queried and re-decoded each time."""
    hv, client, db = env
    _mode(db, "primary")
    _override(db, "mini", "sessions", "default/s_mini", "title", "Renamed on cloud")
    _override(db, "mini", "sessions", "default/s_mini", "pinned", True)
    calls = []
    real = hv._json
    monkeypatch.setattr(hv, "_json", lambda text: calls.append(text) or real(text))
    first = _session(hv, "mini", "default/s_mini")
    assert first["title"] == "Renamed on cloud" and len(calls) == 2
    assert _session(hv, "mini", "default/s_mini") == first and len(calls) == 2  # same hub.db: no decode
    _override(db, "mini", "sessions", "default/s_mini", "title", "Renamed again")  # a commit moves the signature
    s = _session(hv, "mini", "default/s_mini")
    assert s["title"] == "Renamed again" and s["pinned"] is True
    assert calls[2:] == [json.dumps("Renamed again")]  # only the changed value decoded again
    _override(db, "mini", "sessions", "default/s_mini", "pinned", None)  # cleared: no longer applied
    assert _session(hv, "mini", "default/s_mini").get("pinned") is not True
