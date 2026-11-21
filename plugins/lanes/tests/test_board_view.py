"""Real core snapshots over an isolated board, including a status-change stream."""
import contextlib
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_view import load


def test_real_board_view_and_status_push(tmp_path, monkeypatch):
    kbc = pytest.importorskip('hermes_cli.kanban_db_connect')
    from hermes_cli import kanban_lanes as lanes, kanban_lanes_cli as cli
    import hermes_constants
    root = tmp_path / 'lanes-home'
    root.mkdir()
    for key in ('HERMES_HOME', 'HERMES_KANBAN_HOME'):
        monkeypatch.setenv(key, str(root))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_BOARD', raising=False)
    monkeypatch.setattr(hermes_constants, 'get_default_hermes_root', lambda: root)
    (root / 'config.yaml').write_text(json.dumps({'kanban': {'admission': {'lanes': {'work': {'share': 1}}, 'lane_default': 'work'}}}))
    monkeypatch.setattr(lanes, '_LAST_GOOD', [])
    monkeypatch.setattr(cli, '_fleet', lambda *args, **kwargs: None)
    path = root / 'fixture.db'
    with contextlib.closing(kbc.connect(path)) as conn:
        for i, status in enumerate(('ready', 'running', 'blocked', 'done')):
            conn.execute('INSERT INTO tasks(id,title,status,created_at,created_by,tenant,completed_at) VALUES(?,?,?,?,?,?,?)',
                         (f't_fixture{i}', 'fixture', status, int(time.time())-60, 'user', 'work', int(time.time()) if status == 'done' else None))
        conn.execute("UPDATE tasks SET human_request=?,block_kind='needs_input' WHERE status='blocked'", (json.dumps({'action': 'fixture ask', 'why_human': 'fixture owner choice', 'interaction': 'reply', 'scope': 'fixture only', 'evidence': 'fixture request'}),))
        conn.commit()
    @contextlib.contextmanager
    def connect():
        with contextlib.closing(kbc.connect(path)) as conn:
            yield conn
    monkeypatch.setattr(kbc, 'connect_closing', connect)
    api = load()
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    view = api.lane_view(6, '6h', True)
    assert view['configured'], view
    assert view['lanes']['work']['not_ready'] == 1
    assert view['totals']['open'] == 3
    assert view['flow']['graphs']['project']['total'] == 3
    assert sum(c['count'] for c in view['floor']['cells'] if c['step'] != 'done') == 3
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        with client.websocket_connect('/v1/lanes/stream?expanded=true') as ws:
            assert ws.receive_json()['view']['totals']['running'] == 1
            start = time.monotonic()
            with connect() as conn:
                conn.execute("UPDATE tasks SET status='running' WHERE id='t_fixture0'")
                # A normal status transition appends its event in the same transaction.
                conn.execute("INSERT INTO task_events(task_id,kind,created_at) VALUES('t_fixture0','fixture_status',?)", (int(time.time()),))
                conn.commit()
            frame = ws.receive_json()
            assert frame['view']['totals']['running'] == 2
            assert time.monotonic() - start < 3
