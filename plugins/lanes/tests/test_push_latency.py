"""Change -> frame latency: leading-edge send, coalescing, stage timing, incremental projection."""
import contextlib
import json
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_view import load
import test_pr_projection as fixtures

board = fixtures.board


def stream_app(api, monkeypatch, state, compute_s=0.0, calls=None):
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    monkeypatch.setattr(api, 'change_cursor', lambda: state['cursor'])

    def view(*args):
        seen = state['status']  # the snapshot is taken when the projection starts
        if calls is not None:
            calls.append(seen)
        time.sleep(compute_s)
        return {'status': seen}
    monkeypatch.setattr(api, 'lane_view', view)
    monkeypatch.setattr(api, 'WATCH_S', .02)
    monkeypatch.setattr(api, 'HEARTBEAT_S', 30)
    app = FastAPI()
    app.include_router(api.router)
    return app


def test_first_dirty_tick_after_quiet_projects_without_a_fixed_wait(monkeypatch):
    api = load()
    state = {'status': 'a', 'cursor': 0}
    with TestClient(stream_app(api, monkeypatch, state)) as client:
        with client.websocket_connect('/v1/lanes/stream') as ws:
            assert ws.receive_json()['view']['status'] == 'a'
            time.sleep(.3)  # quiet
            start = time.monotonic()
            state.update(status='b', cursor=1)
            assert ws.receive_json()['view']['status'] == 'b'
            assert time.monotonic() - start < .5, 'the old 1 s trailing debounce is back'


def test_changes_during_a_slow_projection_coalesce_into_one_immediate_follow_up(monkeypatch):
    api = load()
    state = {'status': 'a', 'cursor': 0}
    calls = []
    with TestClient(stream_app(api, monkeypatch, state, compute_s=.4, calls=calls)) as client:
        with client.websocket_connect('/v1/lanes/stream?debug=true') as ws:
            assert ws.receive_json()['view']['status'] == 'a'
            state.update(status='b', cursor=1)
            time.sleep(.15)  # inside the recompute that saw status b
            for step in range(2, 7):
                state.update(status=f's{step}', cursor=step)
                time.sleep(.02)
            first = ws.receive_json()
            second = ws.receive_json()
            assert second['view']['status'] == 's6'
            assert len(calls) == 3, calls  # initial, first change, ONE follow-up for five coalesced changes
            gap = second['timing']['project_start'] - first['timing']['project_end']
            assert 0 <= gap < .2, gap  # the follow-up starts at once, not after a debounce or tick wait


def test_debug_frames_carry_ordered_server_stage_timings_only_on_request(monkeypatch):
    api = load()
    state = {'status': 'a', 'cursor': 0}
    with TestClient(stream_app(api, monkeypatch, state, compute_s=.05)) as client:
        with client.websocket_connect('/v1/lanes/stream') as ws:
            ws.receive_json()
            state.update(status='b', cursor=1)
            assert 'timing' not in ws.receive_json()
        with client.websocket_connect('/v1/lanes/stream?debug=true') as ws:
            ws.receive_json()
            state.update(status='c', cursor=2)
            t = ws.receive_json()['timing']
            order = [t[k] for k in ('cursor_poll', 'detected', 'released', 'project_start', 'project_end', 'sent')]
            assert order == sorted(order) and t['project_end'] - t['project_start'] >= .05, t


def sample_board(connect, count=40):
    now = int(time.time())
    with connect() as conn:
        for i in range(count):
            conn.execute("INSERT INTO tasks(id,title,status,created_at,created_by,tenant) VALUES(?,?,?,?,'user','work')",
                         (f'c{i}', f'card {i}', ('ready', 'running', 'scheduled', 'blocked')[i % 4], now - 60 - i))
            conn.executemany("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES(?,?,?,?)",
                             [(f'c{i}', 'heartbeat', now, '{}')] * 5)
            if i % 5 == 0:
                conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES(?,?,?,?)",
                             (f'c{i}', 'review_requested', now, json.dumps({'metadata': {'pr': f'https://github.com/example/repo/pull/{i + 10}'}})))
            if i % 4 == 2:
                conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES(?,?,?,?)",
                             (f'c{i}', 'scheduled', now, json.dumps({'awaiting': {'kind': 'clock', 'why': 'x'}})))


class Cold:
    """Stand-in memo that never retains anything: a full recompute."""
    def bind(self, evidence):
        return evidence


def views(api, memo, now, params=((6.0, '6h', True), (6.0, '6h', False), (24.0, '24h', True))):
    api._card_memo = lambda: memo
    # Ages and oldest-ready seconds are clock-derived: both recomputes of a step share one frozen clock.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(time, 'time', lambda: now)
        return [api._current_view(api.core(), *p) for p in params]


def mutate(connect, step):
    now = int(time.time())
    with connect() as conn:
        if step == 'create':
            conn.execute("INSERT INTO tasks(id,title,status,created_at,created_by,tenant) VALUES('new','new','triage',?, 'user','work')", (now,))
            conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('new','created',?,'{}')", (now,))
        elif step == 'schedule':
            conn.execute("UPDATE tasks SET status='scheduled' WHERE id='new'")
            conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('new','scheduled',?,?)",
                         (now, json.dumps({'awaiting': {'kind': 'clock', 'why': 'hold'}})))
        elif step == 'publish':
            conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('c1','review_requested',?,?)",
                         (now, json.dumps({'metadata': {'pr': 'https://github.com/example/repo/pull/99'}})))
        elif step == 'archive':
            conn.execute("UPDATE tasks SET status='archived' WHERE id='new'")
            conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('new','archived',?,'{}')", (now,))
        elif step == 'retract':
            conn.execute("DELETE FROM task_events WHERE id=(SELECT max(id) FROM task_events WHERE task_id='c1')")


def test_incremental_views_equal_full_recompute_through_create_status_archive(board, monkeypatch):
    pytest.importorskip('hermes_cli.kanban_lanes_sources')
    api, connect, _calls = board
    sample_board(connect)
    memo = api._card_memo()  # the real memo, persisting across every step below
    for step in (None, 'create', 'schedule', 'publish', 'archive', 'retract', None):
        if step:
            mutate(connect, step)
        before = (memo.hits, memo.misses)
        now = time.time()
        incremental = views(api, memo, now)
        full = views(api, Cold(), now)
        assert json.dumps(incremental, sort_keys=True) == json.dumps(full, sort_keys=True), step
        assert incremental[0]['configured'], incremental[0]
        if step in ('create', 'schedule', 'publish', 'archive', 'retract'):
            assert memo.misses - before[1] <= 2, (step, before, (memo.hits, memo.misses))  # only the touched card
            assert memo.hits - before[0] > 0


@pytest.mark.skipif(not os.environ.get('LANES_BOARD_BACKUP'), reason='set LANES_BOARD_BACKUP to a real kanban.db backup')
def test_incremental_equals_full_on_a_real_board_backup(tmp_path, monkeypatch):
    """Manual proof on a copy of a real board: three expanded/strip views, then card writes."""
    import sqlite3
    kbc = pytest.importorskip('hermes_cli.kanban_db_connect')
    from hermes_cli import kanban_lanes_cli as cli
    copy = tmp_path / 'board.db'
    source = sqlite3.connect(f"file:{os.environ['LANES_BOARD_BACKUP']}?mode=ro", uri=True)
    target = sqlite3.connect(copy)
    source.backup(target)
    source.close(), target.close()

    @contextlib.contextmanager
    def connect():
        with contextlib.closing(kbc.connect(copy)) as conn:
            yield conn
    monkeypatch.setattr(kbc, 'connect_closing', connect)
    monkeypatch.setattr(cli, '_fleet', lambda *a, **k: None)
    api = load()
    memo = api._card_memo()
    with connect() as conn:
        row = conn.execute("SELECT id FROM tasks WHERE status IN ('ready','running','scheduled','blocked') LIMIT 1").fetchone()
    for step in (None, 'create', 'schedule', 'archive'):
        if step:
            mutate(connect, step)
        now = time.time()
        incremental = views(api, memo, now)
        full = views(api, Cold(), now)
        assert json.dumps(incremental, sort_keys=True) == json.dumps(full, sort_keys=True), step
    assert row is not None
    print('memo hits/misses', memo.hits, memo.misses)
