"""Strip, Floor and Flow read the same fresh local/fleet source set."""
import contextlib
import json
import time
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_pr_projection as fixtures

board = fixtures.board


from test_projector import actual_board


@pytest.mark.parametrize('variants', [(True, True, True), (False, True, False)])
def test_owner_status_push_does_not_wait_for_replica(actual_board, tmp_path, monkeypatch, variants):
    sources = pytest.importorskip('hermes_cli.kanban_lanes_sources')
    from hermes_cli import kanban_db_connect as kbc
    api, config = actual_board
    from pathlib import Path
    fleet_path = Path(config['fleet'])
    @contextlib.contextmanager
    def connect():
        with contextlib.closing(kbc.connect(Path(config['local']))) as conn:
            yield conn
    # Genuine configured primary mode: the production child cannot inherit a
    # monkeypatched board_mode or replica selector from this test process.
    with connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id='t_fixture'")
        conn.execute("INSERT INTO tasks(id,title,status,created_at,tenant,sync_version) VALUES('t_pr','fixture','running',100,'work',1)")
        conn.execute("INSERT INTO tasks(id,title,status,created_at,tenant) VALUES('unrelated','fixture','archived',100,'work')")
        conn.execute('BEGIN')
        conn.executemany("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_pr','heartbeat','{}',100)",
                         [()] * 40000)
        conn.execute('DELETE FROM kanban_sync_outbox')  # historical events were already acknowledged
        conn.commit()
    with contextlib.closing(kbc.connect(fleet_path)) as fleet:
        fleet.execute("DELETE FROM tasks WHERE id='t_fixture'")
        fleet.execute("INSERT INTO tasks(id,title,status,created_at,tenant,sync_version) VALUES('t_pr','fixture','ready',100,'work',1)")
        fleet.commit()
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    # Deliberately equal revision with a lagging status: local owns equal rows.
    view = api.lane_view(6, '6h', True)
    assert view['totals']['running'] == 1, view
    assert view['totals']['open'] == view['flow']['graphs']['project']['total'] == 1
    assert [c['count'] for c in view['floor']['cells'] if c['step'] == 'running'] == [1]
    assert sources is not None
    app = FastAPI()
    app.include_router(api.router)
    stop = threading.Event()
    def churn():
        with connect() as conn:
            while not stop.is_set():
                conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('unrelated','heartbeat','{}',100)")
                stop.wait(.05)
    writer = threading.Thread(target=churn)
    with TestClient(app) as client, contextlib.ExitStack() as sockets:
        viewers = [sockets.enter_context(client.websocket_connect(f'/v1/lanes/stream?expanded={str(flag).lower()}')) for flag in variants]
        for ws, expanded in zip(viewers, variants):
            assert ws.receive_json()['view']['totals']['running'] == 1
        start = time.monotonic()
        writer.start()
        try:
            with connect() as conn:
                conn.execute("UPDATE tasks SET status='scheduled' WHERE id='t_pr'")
                conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_pr','scheduled','{}',100)")
            for ws, expanded in zip(viewers, variants):
                frame = ws.receive_json()
                assert time.monotonic() - start < 3
                assert frame['view']['totals']['running'] == 0
                assert frame['view']['totals']['open'] == 1
                if expanded:
                    assert frame['view']['flow']['graphs']['project']['total'] == 1
            start = time.monotonic()
            with connect() as conn:
                conn.execute("UPDATE tasks SET status='archived' WHERE id='t_pr'")
                conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_pr','archived','{}',100)")
            for ws, expanded in zip(viewers, variants):
                frame = ws.receive_json()
                assert time.monotonic() - start < 3
                assert frame['view']['totals']['open'] == 0
                if expanded:
                    assert frame['view']['flow']['graphs']['project']['total'] == 0
        finally:
            stop.set()
            writer.join(3)
            assert not writer.is_alive()
    # We never changed the replica, even to simulate convergence.
    with contextlib.closing(kbc.connect(fleet_path)) as fleet:
        assert fleet.execute("SELECT status FROM tasks WHERE id='t_pr'").fetchone()[0] == 'ready'


def test_context_identity_changes_with_selected_database(board, tmp_path):
    sources = pytest.importorskip('hermes_cli.kanban_lanes_sources')
    from hermes_cli import kanban_db_connect as kbc, kanban_db_sync as sync, kanban_lanes_flow as flow
    api, connect, _calls = board
    with connect() as local, contextlib.closing(kbc.connect(tmp_path / 'fleet.db')) as fleet:
        for db in (local, fleet):
            sync.apply_sync_schema_steps(db)
        fleet.execute("INSERT INTO tasks(id,title,status,created_at,tenant,completion_contract,sync_version) VALUES('t_pr','old','ready',100,'work',?,2)", (fixtures.URL,))
        fleet.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_pr','created','{}',100)")
        local.execute("UPDATE tasks SET status='ready',completion_contract=?,sync_version=1 WHERE id='t_pr'", (fixtures.URL,))
        first = api._pr_contexts(fleet, flow, evidence=sources.Sources(local, fleet))
        local.execute("UPDATE tasks SET sync_version=3 WHERE id='t_pr'")
        second = api._pr_contexts(fleet, flow, evidence=sources.Sources(local, fleet))
        assert first[fixtures.URL] != second[fixtures.URL]


def test_fleet_reader_gets_each_phase_budget_not_its_aged_open_deadline(board, tmp_path):
    """Live failure (hub :9149, 708e95bb): 'default: lanes flow is unavailable: interrupted'.

    The projector child opens local and fleet under one 2 s open deadline. Each
    phase re-armed only the local handle, so flow.snapshot, which reads the
    fleet handle (Sources.conn), was interrupted once the whole job passed 2 s.
    """
    sources = pytest.importorskip('hermes_cli.kanban_lanes_sources')
    from hermes_cli import kanban_db_connect as kbc, kanban_db_sync as sync
    api, connect, _calls = board
    with connect() as local, contextlib.closing(kbc.connect(tmp_path / 'fleet.db')) as fleet:
        for db in (local, fleet):
            sync.apply_sync_schema_steps(db)
        fleet.execute("INSERT INTO tasks(id,title,status,created_at,tenant,sync_version) VALUES('t_fleet','fixture','running',?,'work',1)",
                      (int(time.time()) - 60,))
        fleet.commit()
        evidence = sources.presentation(local, fleet)  # opened inside the open deadline
        assert evidence.conn is fleet
        # Then the job-wide open deadline passes (job time > 2 s) while the
        # phases run. The fleet handle is the one nobody re-armed.
        fleet.set_progress_handler(lambda: 1, 1)
        view = api._project_evidence(api.core(), local, evidence, 6.0, '6h', False)
    assert 'interrupted' not in (view.get('flow_error') or ''), view.get('flow_error')
    assert view['flow_complete'], view.get('flow_error')


def test_failed_flow_keeps_fresh_strip(board, monkeypatch):
    pytest.importorskip('hermes_cli.kanban_lanes_sources')
    from hermes_cli import kanban_lanes_flow as flow
    api, _connect, _calls = board
    def broken(*a, **kw):
        raise RuntimeError('fixture flow failure')
    monkeypatch.setattr(flow, 'snapshot', broken)
    view = api.lane_view(6, '6h', True)
    assert view['configured'] and view['totals']['open'] == 1
    assert 'fixture flow failure' in view['flow_error']
