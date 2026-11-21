"""The real readers against a board built with core's own kanban_db: card records, changed cards, stamps, lineage."""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

kanban_db = pytest.importorskip('hermes_cli.kanban_db')


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    name = '_spf_api_test'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def board(tmp_path):
    path = tmp_path / 'kanban.db'
    from hermes_cli.kanban_db_connect import connect
    conn = connect(path)
    return path, conn


def readers_for(api, path, monkeypatch):
    r = api.CoreReaders()
    monkeypatch.setattr(r, 'boards', lambda: [('default', path)])
    return r


def test_card_record_has_what_the_panel_chain_reads(api, board, monkeypatch):
    path, conn = board
    leaf = kanban_db.create_task(conn, title='Leaf work', initial_status='running', session_id='S9')
    waiting = kanban_db.create_task(conn, title='Waits on leaf', parents=[leaf], initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    rec = r.card(waiting, 'default')
    d = rec['detail']
    assert rec['board'] == 'default' and d['slim'] is True and d['source'] == 'live'
    assert d['task']['id'] == waiting and d['task']['title'] == 'Waits on leaf' and d['task']['session_id'] == 'S1'
    assert d['links']['parents'] == [leaf]
    assert d['link_tasks'] == [{'id': leaf, 'title': 'Leaf work', 'status': kanban_db.get_task(conn, leaf).status}]
    assert isinstance(d['events'], list) and len(d['events']) <= api.EVENTS_KEPT
    assert 'wake_check' in d['task']
    assert r.card('t_nope000', 'default') is None
    assert len(api.json.dumps(api.json_safe(rec))) < 20_000, 'slim: a few KB, not the 48 KB full detail'


def test_changed_cards_reports_cards_with_new_events_and_their_chat(api, board, monkeypatch):
    path, conn = board
    a = kanban_db.create_task(conn, title='A', initial_status='running', session_id='S1')
    r = readers_for(api, path, monkeypatch)
    changed, cursor = r.changed_cards(None)
    assert changed == {}, 'the first call only sets the cursor'
    stamp = r.stamps()
    b = kanban_db.create_task(conn, title='B', initial_status='running', session_id='S2')
    kanban_db.block_task(conn, a, reason='waiting on a person')
    changed, cursor2 = r.changed_cards(cursor)
    assert changed.get(a) == 'S1' and changed.get(b) == 'S2'
    assert r.changed_cards(cursor2)[0] == {}, 'nothing new since'
    assert r.stamps() != stamp, 'a board write moves the stamps'


def test_lineage_walks_parent_sessions(api, tmp_path, monkeypatch):
    db = tmp_path / 'state.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT)')
    c.executemany('INSERT INTO sessions VALUES (?, ?)', [('S3', 'S2'), ('S2', 'S1'), ('S1', None), ('L1', 'L2'), ('L2', 'L1')])
    c.commit()
    r = api.CoreReaders()
    assert r.lineage('S3') == ['S3', 'S2', 'S1']
    assert r.lineage('L1') == ['L1', 'L2'], 'a cycle ends the walk'
    assert r.lineage('unknown') == ['unknown']


def test_push_payload_is_json_safe_and_bounded(api):
    body = api.push_payload({'epoch': 'e', 'prev': 1, 'rev': 2, 'bundles': {'S1': {'x': float('nan'), 'title': 'NaN Infinity'}}})
    assert body['bundles']['S1'] == {'x': None, 'title': 'NaN Infinity'}, 'text is untouched'
    big = api.push_payload({'epoch': 'e', 'prev': 1, 'rev': 2, 'bundles': {'S1': {'blob': 'x' * 2000}, 'S2': {}}}, max_bytes=500)
    assert big['bundles'] == {} and big['sessions'] == ['S1', 'S2'], 'too big: names only, clients fetch them'
