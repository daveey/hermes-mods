"""The replica decode cache re-decodes only rows written since its copy, and stays exact through deletes/resyncs.

hub-sync commits to hub.db several times a minute, so a cache keyed on the file alone re-decoded ~10k session rows
of JSON on nearly every Desktop poll (~15% of a Desktop backend's GIL time, 2026-10-27).
"""
import json
import sqlite3

from conftest import NOW


def _db(hub):
    hv, state = hub
    return hv, state / "hub.db"


def _rep(path, gw, key, data, seq, coll="sessions"):
    with sqlite3.connect(str(path)) as db:
        db.execute("INSERT INTO replica (gw, coll, key, version, seq, data, updated_at) VALUES (?,?,?,?,?,?,?) "
                   "ON CONFLICT(gw, coll, key) DO UPDATE SET version=excluded.version, seq=excluded.seq, "
                   "data=excluded.data, updated_at=excluded.updated_at",
                   (gw, coll, key, 1, seq, json.dumps(data), int((NOW - 50) * 1000)))


def _titles(hv):
    return {(r["gateway"], r["key"]): r.get("title") for r in hv.sessions(include_archived=True)["sessions"]}


def _fresh(hv):
    hv._DECODED.clear()
    return _titles(hv)


def test_new_and_changed_rows_match_a_fresh_full_read_without_redecoding_the_rest(hub, monkeypatch):
    hv, path = _db(hub)
    _titles(hv)  # prime
    decoded = []
    real = hv._loads
    monkeypatch.setattr(hv, "_loads", lambda text: decoded.append(text) or real(text))

    _rep(path, "mini", "default/s_new", {"profile": "default", "id": "s_new", "title": "New", "activity": NOW}, 100)
    _rep(path, "mini", "default/s_mini", {"profile": "default", "id": "s_mini", "title": "Renamed",
                                          "activity": NOW}, 101)
    got = _titles(hv)
    replica_decodes = [t for t in decoded if '"New"' in t or '"Renamed"' in t]
    assert len(replica_decodes) == 2 and len(decoded) <= 3  # the 2 written rows (+ at most the own published row)
    assert got[("mini", "default/s_new")] == "New" and got[("mini", "default/s_mini")] == "Renamed"
    assert got == _fresh(hv)


def test_a_write_to_another_collection_redecodes_nothing(hub, monkeypatch):
    hv, path = _db(hub)
    before = _titles(hv)
    decoded = []
    real = hv._loads
    monkeypatch.setattr(hv, "_loads", lambda text: decoded.append(text) or real(text))
    _rep(path, "mini", "default/t_x", {"id": "t_x", "status": "running"}, 200, coll="tasks")
    assert _titles(hv) == before
    assert not [t for t in decoded if '"title"' in t and '"sessions"' not in t and "Own" not in t and "Local" not in t]


def test_delete_and_resync_reload_in_full(hub):
    hv, path = _db(hub)
    _titles(hv)
    with sqlite3.connect(str(path)) as db:
        db.execute("DELETE FROM replica WHERE gw = 'cloud'")
    after_delete = _titles(hv)
    assert ("cloud", "alpha/s_cloud") not in after_delete
    assert after_delete == _fresh(hv)

    _titles(hv)
    with sqlite3.connect(str(path)) as db:  # resync: same rows re-inserted at lower seqs with different text
        db.execute("UPDATE replica SET seq = 0, data = json_set(data, '$.title', 'Resynced') WHERE gw = 'mini' "
                   "AND coll = 'sessions'")
    got = _titles(hv)
    assert got[("mini", "default/s_mini")] == "Resynced"
    assert got == _fresh(hv)


def test_owner_seen_is_read_once_per_hub_write(hub, monkeypatch):
    hv, path = _db(hub)
    hv._SEEN.clear()
    first = hv.open_view(); a = first.owner_seen(); first.conn.close()
    second = hv.open_view()
    monkeypatch.setattr(second, "conn", _NoGroupBy(second.conn))
    assert second.owner_seen() == a  # unchanged hub.db: served from the memo, no GROUP BY scan
    second.conn.close()
    _rep(path, "cloud", "alpha/s_cloud2", {"id": "s_cloud2"}, 300)
    third = hv.open_view(); c = third.owner_seen(); third.conn.close()
    assert c["cloud"] == (int((NOW - 50) * 1000)) / 1000.0 and c != a


class _NoGroupBy:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        assert "GROUP BY gw" not in sql, "owner_seen re-scanned an unchanged hub.db"
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)
