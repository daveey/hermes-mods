"""Real core snapshots over an isolated board, including a status-change stream."""
import contextlib
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_view import load


QUERY = ('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL '
         'SELECT x+1 FROM n WHERE x<{rows}) SELECT sum(x) FROM n')


def test_board_deadline_identifies_expiring_handler(monkeypatch, caplog):
    import sqlite3
    from types import SimpleNamespace

    api = load()
    merge = api._sibling('board_merge')
    clock, cpu = [100.0], [1.0]
    monkeypatch.setattr(merge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(merge, 'thread_time', lambda: cpu[0])
    query = QUERY.format(rows=100000)
    with contextlib.closing(sqlite3.connect(':memory:')) as db:
        merge.arm_deadline(db)
        clock[0] += 2.5
        cpu[0] += merge.BUDGET_CPU_SECONDS + .1
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            db.execute(query).fetchone()
        assert 'lanes board_merge deadline expired' in caplog.text
        assert f'wall=2.500s thread_cpu={merge.BUDGET_CPU_SECONDS + .1:.3f}s' in caplog.text
        caplog.clear()
        merge.arm_deadline(db)
        assert db.execute(query).fetchone()[0] == 5000050000
        assert not caplog.records


def test_board_deadline_does_not_charge_time_spent_waiting_for_other_threads(monkeypatch, caplog):
    """Live failure: wall=2.0s thread_cpu=0.3s reads were interrupted in a busy backend."""
    import sqlite3
    from types import SimpleNamespace

    api = load()
    merge = api._sibling('board_merge')
    clock, cpu = [100.0], [1.0]
    monkeypatch.setattr(merge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(merge, 'thread_time', lambda: cpu[0])
    with contextlib.closing(sqlite3.connect(':memory:')) as db:
        merge.arm_deadline(db, phase='contexts')
        clock[0] += 10    # five old deadlines: other threads held the interpreter lock
        cpu[0] += 0.3     # this reader did 0.3 s of work
        assert db.execute(QUERY.format(rows=100000)).fetchone()[0] == 5000050000
        assert not caplog.records
        # The snapshot still pins the WAL: starvation has a far backstop, and says so.
        clock[0] += 3600
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            db.execute(QUERY.format(rows=100000)).fetchone()
        assert 'phase=contexts wall=3610.000s thread_cpu=0.300s' in caplog.text


def test_board_budget_leaves_room_for_the_measured_read_in_a_busy_backend(monkeypatch, caplog):
    """Live failure after the CPU budget landed: phase=contexts thread_cpu=2.0-2.7s expired at 2 s.

    The same phase costs 1.1 s in an idle process; a contended interpreter makes
    the identical work cost more CPU, so a budget beside the honest cost fails it.
    """
    import sqlite3
    from types import SimpleNamespace

    api = load()
    merge = api._sibling('board_merge')
    clock, cpu = [100.0], [1.0]
    monkeypatch.setattr(merge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(merge, 'thread_time', lambda: cpu[0])
    with contextlib.closing(sqlite3.connect(':memory:')) as db:
        merge.arm_deadline(db, phase='contexts')
        clock[0] += 19.8
        cpu[0] += 2.7     # the largest reader CPU seen at a live expiry
        assert db.execute(QUERY.format(rows=100000)).fetchone()[0] == 5000050000
        assert not caplog.records
        assert merge.BUDGET_CPU_SECONDS >= 3 * 2.7, 'a budget is headroom over the honest read, not its size'


def test_board_budget_warns_once_while_a_growing_read_still_completes(monkeypatch, caplog):
    import sqlite3
    from types import SimpleNamespace

    api = load()
    merge = api._sibling('board_merge')
    clock, cpu = [100.0], [1.0]
    monkeypatch.setattr(merge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(merge, 'thread_time', lambda: cpu[0])
    with contextlib.closing(sqlite3.connect(':memory:')) as db:
        merge.arm_deadline(db, phase='projection')
        used = merge.BUDGET_CPU_SECONDS * merge.WARN_BUDGET_SHARE + .25
        clock[0] += 7
        cpu[0] += used
        for _ in range(2):
            assert db.execute(QUERY.format(rows=400000)).fetchone()[0] == 80000200000
        lines = [record.getMessage() for record in caplog.records]
        assert lines == [f'lanes board_merge budget half used: phase=projection wall=7.000s '
                         f'thread_cpu={used:.3f}s of {merge.BUDGET_CPU_SECONDS}s'], lines


def test_board_deadline_check_does_not_queue_for_the_interpreter_lock_every_few_steps(monkeypatch):
    """Each check reacquires the GIL; thousands per query starve a contended reader."""
    import sqlite3

    api = load()
    merge = api._sibling('board_merge')
    calls = [0]

    def counted():
        calls[0] += 1
        return 1.0
    monkeypatch.setattr(merge, 'thread_time', counted)
    with contextlib.closing(sqlite3.connect(':memory:')) as db:
        merge.arm_deadline(db)
        armed = calls[0]
        assert db.execute(QUERY.format(rows=200000)).fetchone()[0] == 20000100000
        checks = calls[0] - armed
        assert 1 <= checks <= 100, checks


def test_board_deadline_restarts_for_another_reader_thread(monkeypatch):
    import sqlite3
    import threading

    api = load()
    merge = api._sibling('board_merge')
    cpu = {threading.get_ident(): 50.0}  # the arming thread has a long CPU history
    monkeypatch.setattr(merge, 'thread_time', lambda: cpu.get(threading.get_ident(), 0.0))
    result = []
    with contextlib.closing(sqlite3.connect(':memory:', check_same_thread=False)) as db:
        merge.arm_deadline(db)
        worker = threading.Thread(target=lambda: result.append(
            db.execute(QUERY.format(rows=100000)).fetchone()[0]))
        worker.start()
        worker.join()
    assert result == [5000050000]


def test_governor_receipt_that_did_not_observe_a_governor_is_unknown_not_a_crash():
    """Storage-admission aborts publish cards=None for every governor they did not reach."""
    api = load()
    governors = api._sibling('governors')
    registry = [dict(id='storage_admission', label='Storage admission', kind='hold', threshold='budget'),
                dict(id='llm_pool', label='LLM pool', kind='hold', threshold='pool')]
    snapshot = dict(board='default', available=True, stale=False, idle_total=None, cards={}, governors=[
        dict(governor='llm_pool', kind='hold', active=None, cards=None, card_ids=[], since=None, unknown=True),
        dict(governor='storage_admission', kind='hold', active=True, cards=2, card_ids=['t_a', 't_b'], since=5.0)])
    projected = governors.project([snapshot], [], registry)
    rows = {row['id']: row for row in projected['rows']}
    assert rows['llm_pool']['cards_charged'] == 0
    assert rows['llm_pool']['partial'] is True
    assert rows['llm_pool']['state'] == 'unknown', 'an unobserved governor is never painted off'
    assert rows['storage_admission']['cards_charged'] == 2


@pytest.mark.parametrize('slow_pr', [False, True])
def test_real_board_view_and_status_push(tmp_path, monkeypatch, slow_pr, request):
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
    request.addfinalizer(lambda: api._observations().join(10) if api._observations.cache_info().currsize else None)
    if slow_pr:
        import threading
        from hermes_cli import kanban_lanes_flow_pr as prs
        started = threading.Event()
        release_pr = threading.Event()
        finished_pr = threading.Event()
        # Registered after the observation join, so LIFO teardown unblocks it first.
        request.addfinalizer(release_pr.set)
        class Slow:
            def prefetch(self, urls):
                started.set()
                release_pr.wait(20)  # safety ceiling, not the delivery oracle
                finished_pr.set()
            def get(self, url):
                return {}
        monkeypatch.setattr(prs, 'Reader', Slow)
        with connect() as conn:
            conn.execute("INSERT INTO task_events(task_id,kind,created_at,payload) VALUES('t_fixture0','review_requested',?,?)",
                         (int(time.time()), json.dumps({'metadata': {'pr': 'https://github.com/example/repo/pull/1'}})))
            conn.commit()
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    initial_start = time.monotonic()
    view = api.lane_view(6, '6h', True)
    assert time.monotonic() - initial_start < 3
    if slow_pr:
        assert started.wait(1)
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
            # A status change immediately after the initial frame may wait the
            # configured start-to-start floor, plus a watch tick and scheduling.
            assert time.monotonic() - start < api.MIN_PROJECT_GAP_S + api.WATCH_S + 2
            if slow_pr:
                assert not finished_pr.is_set(), 'status delivery waited for PR I/O'
