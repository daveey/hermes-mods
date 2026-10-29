"""Unrelated hub writes must not repeatedly decode the local published JSON."""
import json
import sqlite3


def _published(hv):
    view = hv.open_view()
    try:
        return view._decoded('published', 'tasks')
    finally:
        view.conn.close()


def test_unrelated_commit_reuses_every_published_decode(hub, monkeypatch):
    hv, state = hub
    first = _published(hv)
    calls = []
    original = hv._loads
    monkeypatch.setattr(hv, '_loads', lambda raw: calls.append(raw) or original(raw))
    with sqlite3.connect(state / 'hub.db') as conn:
        conn.execute("UPDATE meta SET v='43' WHERE k='cursor'")
    assert _published(hv) == first
    assert calls == []


def test_same_version_content_update_delete_and_bad_json_are_observed(hub, monkeypatch):
    hv, state = hub
    _published(hv)
    calls = []
    original = hv._loads
    monkeypatch.setattr(hv, '_loads', lambda raw: calls.append(raw) or original(raw))
    # Content comparison must not assume version/seq/sha changes on every local staging write.
    with sqlite3.connect(state / 'hub.db') as conn:
        conn.execute("UPDATE published SET data=? WHERE coll='tasks'", (json.dumps({'id':'t_own','title':'Changed'}),))
    assert _published(hv)[0][1]['title'] == 'Changed'
    assert len(calls) == 1
    with sqlite3.connect(state / 'hub.db') as conn:
        conn.execute("UPDATE published SET data='bad json' WHERE coll='tasks'")
    assert _published(hv) == []
    with sqlite3.connect(state / 'hub.db') as conn:
        conn.execute("DELETE FROM published WHERE coll='tasks'")
    assert _published(hv) == []


def test_published_metadata_and_added_rows_are_fresh(hub):
    hv, state = hub
    _published(hv)
    with sqlite3.connect(state / 'hub.db') as conn:
        conn.execute("UPDATE published SET version=99 WHERE coll='tasks'")
        conn.execute("INSERT INTO published(coll,key,data,version) VALUES('tasks','new','{\"id\":\"new\"}',1)")
    rows = {row['key']: (row, data) for row, data in _published(hv)}
    assert rows['default/t_own'][0]['version'] == 99
    assert rows['new'][1]['id'] == 'new'
