"""Private-pipe projection RPC boundaries, real readers and process lifecycle."""
import concurrent.futures
import contextlib
import importlib.util
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

DASH = Path(__file__).parents[1] / 'dashboard'


def module(name):
    spec = importlib.util.spec_from_file_location('probe_' + name, DASH / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_cursor_500_urls_is_fixed_size_command():
    rpc = module('projector_protocol')
    cursor = [1, tuple('https://github.com/example/repo/pull/' + str(i) for i in range(500))]
    cmd = rpc.project_command('session', 1, (6., '6h', True), 2, cursor, 'primary')
    assert len(rpc.encode(cmd, rpc.COMMAND_CAP)) < 1024
    assert 'pull/' not in json.dumps(cmd)
    assert len(cmd['revision'][1]) == 64
    assert cmd == json.loads(rpc.encode(cmd, rpc.COMMAND_CAP))


def test_protocol_rejects_nonfinite_oversize_and_bad_params():
    rpc = module('projector_protocol')
    with pytest.raises(ValueError):
        rpc.encode({'v': float('nan')}, rpc.COMMAND_CAP)
    with pytest.raises(ValueError):
        rpc.encode({'v': 'x' * rpc.COMMAND_CAP}, rpc.COMMAND_CAP)
    for params in ([6, '6h', 1], [float('inf'), '6h', True], [2, '6h', True]):
        with pytest.raises(ValueError):
            rpc.project_command('s', 1, params, 0, [], 'off')


def test_borrowed_reader_reopens_rebuilt_file_and_never_writes(tmp_path):
    pytest.importorskip('hermes_cli.kanban_lockguard')
    worker = module('projector')
    path = tmp_path / 'source.db'
    def seed(value):
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE value(v)')
            db.execute('INSERT INTO value VALUES(?)', (value,))
            db.commit()
    seed(3)
    with worker.readers(str(path), None, 'off') as (db, fleet):
        assert fleet is None
        assert db.execute('SELECT v FROM value').fetchone()[0] == 3
        with pytest.raises(sqlite3.OperationalError):
            db.execute('INSERT INTO value VALUES(4)')
    path.unlink()
    seed(5)
    with worker.readers(str(path), None, 'off') as (db, _):
        assert db.execute('SELECT v FROM value').fetchone()[0] == 5
    with pytest.raises(Exception):
        with worker.readers(str(path), str(tmp_path / 'missing.db'), 'off'):
            pass


def test_borrowed_fleet_reader_uses_board_budget_not_two_second_job_clock(actual_board, monkeypatch):
    _, config = actual_board
    worker = module('projector')
    clock = [100.]
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])
    query = ('WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL '
             'SELECT x+1 FROM n WHERE x<20000) SELECT max(x) FROM n')
    with worker.readers(config['local'], config['fleet'], 'primary') as (local, fleet):
        # Waiting for the control thread must not expire an otherwise healthy
        # source, including the replica used by presentation(...).conn.
        clock[0] = 103.
        for db in (local, fleet):
            assert db.execute(query).fetchone()[0] == 20000
        # Keep the bounded snapshot lifetime; this is not an unlimited reader.
        clock[0] = 131.
        for db in (local, fleet):
            with pytest.raises(sqlite3.OperationalError, match='interrupted'):
                db.execute(query).fetchone()


def test_fallback_holds_single_slot_until_actual_completion():
    import threading
    client = module('projector_client')
    slot = client.Fallback()
    gate = threading.Event()
    one = slot.submit(('key', 1), lambda: gate.wait(5))
    assert slot.submit(('key', 1), lambda: None) is one
    with pytest.raises(client.Unavailable):
        slot.submit(('other', 1), lambda: None)
    # A cancelled waiter cannot release capacity held by synchronous code.
    assert not one.cancel()
    with pytest.raises(client.Unavailable):
        slot.submit(('other', 2), lambda: None)
    gate.set()
    assert one.result(2)
    assert slot.submit(('next', 2), lambda: 'fresh').result(2) == 'fresh'


@pytest.fixture
def actual_board(tmp_path, monkeypatch):
    pytest.importorskip('hermes_cli.kanban_lanes_sources')
    # The test interpreter already has the runtime prepared. Keep its exact
    # import paths while isolating all data under the fixture home; bootstrapping
    # a fresh PM dependency generation here tests installation, not projection.
    import sys
    from hermes_cli import _launchers
    paths = list(sys.path)
    def prepared_runtime(root, args=(), *, code=None, **kwargs):
        return [sys.executable, '-I', '-c',
                f'import sys, runpy; sys.path[:] = {paths!r}; ' + code, *args]
    monkeypatch.setattr(_launchers, 'runtime_command', prepared_runtime)
    from hermes_cli import kanban_db_connect as kbc, kanban_db_sync as sync
    import hermes_constants
    home = tmp_path / 'home'
    home.mkdir()
    for key in ('HERMES_HOME', 'HERMES_KANBAN_HOME'):
        monkeypatch.setenv(key, str(home))
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(hermes_constants, 'get_default_hermes_root', lambda: home)
    (home / 'config.yaml').write_text(json.dumps({'kanban': {
        'sync': {'enabled': True},
        'admission': {'lanes': {'work': {'share': 1}}, 'lane_default': 'work'}}}))
    local, fleet = home / 'kanban.db', home / 'kanban' / 'sync-shadow' / 'default.db'
    fleet.parent.mkdir(parents=True)
    for path, status in ((local, 'running'), (fleet, 'ready')):
        with contextlib.closing(kbc.connect(path)) as db:
            db.execute("UPDATE kanban_sync_state SET mode='primary'")
            db.execute("INSERT INTO tasks(id,title,status,created_at,tenant,sync_version) VALUES('t_fixture','fixture',?,100,'work',1)", (status,))
            db.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_fixture','created','{}',100)")
            db.commit()
    api = module('plugin_api')
    config = {'local': str(local), 'fleet': str(fleet), 'mode': 'primary',
              'home': str(home), 'identity': api._loaded_identity}
    yield api, config
    if api._projector_instance:
        api._projector_instance.close()


def start_process(api, config, clock=None, prefix='', directory=DASH):
    import hermes_cli
    from hermes_cli._launchers import runtime_command
    root = Path(hermes_cli.__file__).resolve().parent.parent
    code = (f'import time; time.time = lambda: {clock!r}; ' if clock else '')
    code += prefix
    code += f"runpy.run_path({str(directory / 'projector.py')!r}, run_name='__main__')"
    command = runtime_command(root, code=code, home=config['home'])
    return api._rpc_client.Process(config, command)


def project(api, process, config, generation=1):
    command = api._rpc_client.rpc.project_command(process.session, 0, (6., '6h', True), generation, [generation], config['mode'])
    return process.request(command, 10)


def test_managed_subprocess_primary_overlay_parity_and_source_rebuild(actual_board, monkeypatch):
    from hermes_cli import kanban_lanes_sources as sources, kanban_db_connect as kbc
    api, config = actual_board
    fixed = 1800000000.
    child = start_process(api, config, fixed)
    try:
        monkeypatch.setattr(time, 'time', lambda: fixed)
        worker = module('projector')
        for index, status in enumerate(('running', 'scheduled', 'archived', 'ready')):
            with contextlib.closing(kbc.connect(Path(config['local']))) as db:
                db.execute('UPDATE tasks SET status=?', (status,))
                db.commit()
            with worker.readers(config['local'], config['fleet'], 'primary') as (local, fleet):
                expected = api._project_evidence(api.core(), local, sources.presentation(local, fleet), 6., '6h', True)
                collapsed = api._project_evidence(api.core(), local, sources.presentation(local, fleet), 6., '6h', False)
                before = local.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            result = project(api, child, config, index)
            assert result['projector_pid'] != os.getpid()
            assert result['views'] == {'collapsed': collapsed, 'expanded': expected}
            assert json.dumps(result['view'], sort_keys=True) == json.dumps(expected, sort_keys=True)
            assert result['view']['totals']['open'] == (0 if status == 'archived' else 1)
            assert result['view']['totals']['running'] == (1 if status == 'running' else 0)
            with worker.readers(config['local'], config['fleet'], 'primary') as (local, _):
                assert local.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before
        # Replace the fleet at the same pathname BETWEEN JOBS; no retained handle.
        replacement = Path(config['fleet']).with_name('replacement.db')
        with contextlib.closing(kbc.connect(replacement)) as db:
            db.execute("INSERT INTO tasks(id,title,status,created_at,tenant,sync_version) VALUES('new','new','ready',100,'work',1)")
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        replacement.replace(config['fleet'])
        result = project(api, child, config, 10)
        assert result['view']['totals']['open'] == 2
        assert child.request({'op': 'observation_cursor'}, 1)
    finally:
        child.close()
        assert child.proc.poll() is not None


def test_child_mode_mismatch_and_identity_mismatch_fail_closed(actual_board):
    api, config = actual_board
    child = start_process(api, config)
    try:
        with pytest.raises(api._rpc_client.Unavailable):
            project(api, child, config | {'mode': 'shadow'})
    finally:
        child.close()
    bad = dict(config['identity'])
    bad[str(DASH / 'view.py')] = '0' * 64
    with pytest.raises(api._rpc_client.Unavailable):
        start_process(api, config | {'identity': bad})


def test_result_fences_session_revision_params_and_nonfinite():
    rpc = module('projector_protocol')
    cmd = rpc.project_command('s', 1, (6., '6h', True), 1, [], 'off')
    reply = cmd | {'op': 'result', 'view': {'configured': False}, 'projector_pid': 2,
                   'views': {'collapsed': {'configured': False}, 'expanded': {'configured': False}},
                   'timing': {'project_start': 1., 'project_end': 2.}}
    assert rpc.validate_result(reply, cmd) == reply
    for delta in ({'session': 'old'}, {'revision': [0, '0' * 64]}, {'params': [24., '24h', True]},
                  {'views': None}, {'views': {'expanded': {'configured': False}}},
                  {'views': {'expanded': {'configured': False}, 'collapsed': {'configured': 1}}},
                  {'view': []}, {'projector_pid': None}, {'timing': {'project_start': 1., 'project_end': float('nan')}}):
        with pytest.raises(ValueError):
            rpc.validate_result(reply | delta, cmd)


def test_real_stream_auth_shared_child_and_archive_push(actual_board, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hermes_cli import kanban_db_connect as kbc
    from starlette.websockets import WebSocketDisconnect
    api, config = actual_board
    app = FastAPI()
    app.include_router(api.router)
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: False)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/v1/lanes/stream'):
                pass
        assert api._projector_instance is None
        monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/v1/lanes/stream?hours=2'):
                pass
        assert api._projector_instance is None
        with client.websocket_connect('/v1/lanes/stream?expanded=true&debug=true') as one:
            initial = one.receive_json()
            assert initial['view']['totals']['running'] == 1
            assert initial['projector']['mode'] == 'projector', initial
            pid = initial['projector']['projector_pid']
            with client.websocket_connect('/v1/lanes/stream?expanded=true&debug=true') as two:
                assert two.receive_json()['projector']['projector_pid'] == pid
                changed = time.monotonic()
                with contextlib.closing(kbc.connect(Path(config['local']))) as db:
                    db.execute("UPDATE tasks SET status='archived'")
                    db.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_fixture','archived','{}',100)")
                    db.commit()
                for socket in (one, two):
                    frame = socket.receive_json()
                    while frame.get('type') == 'heartbeat' or frame.get('view', {}).get('totals', {}).get('open') != 0:
                        assert time.monotonic() - changed < api.MIN_PROJECT_GAP_S + 2  # coalesced trailing run plus transport
                        frame = socket.receive_json()
                    assert time.monotonic() - changed < api.MIN_PROJECT_GAP_S + 2  # coalesced trailing run plus transport
                    assert frame['projector']['mode'] == 'projector'
                    assert frame['projector']['projector_pid'] == pid
    assert api._active_streams == 0


def test_slow_compat_projection_does_not_block_heartbeat_or_disconnect(monkeypatch):
    import asyncio
    import threading
    from test_view import load
    api = load()
    # Exercise the early-heartbeat schedule even on an otherwise idle host,
    # without bypassing the real identity fence.
    matches = api._identity.matches
    def slow_identity(*args):
        time.sleep(.08)
        return matches(*args)
    monkeypatch.setattr(api._identity, 'matches', slow_identity)
    gate = threading.Event()
    began = threading.Event()
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    monkeypatch.setattr(api, 'change_cursor', lambda: 0)
    monkeypatch.setattr(api, 'WATCH_S', .01)
    monkeypatch.setattr(api, 'HEARTBEAT_S', .03)
    def hung(*args):
        began.set()
        assert gate.wait(30), 'test did not release the projection'
        return {'configured': False}
    monkeypatch.setattr(api, 'lane_view', hung)
    class Socket:
        query_params = {}
        heartbeat = asyncio.Event()
        async def accept(self):
            pass
        async def send_json(self, value):
            # An early heartbeat can precede worker startup under load. Only
            # a heartbeat DURING the blocked projection proves independence.
            if value.get('type') == 'heartbeat' and began.is_set():
                assert not gate.is_set()
                self.heartbeat.set()
        async def receive(self):
            await self.heartbeat.wait()
            return {'type': 'websocket.disconnect'}
    async def probe():
        try:
            # Real watchdog, shorter than the held worker's safety timeout.
            await asyncio.wait_for(api.stream_lanes(Socket()), 5)
            assert began.is_set()
            assert not gate.is_set()
            assert api._compat_fallback.active is not None
            assert not api._compat_fallback.active[1].done()
            assert api._active_streams == 0
        finally:
            active = api._compat_fallback.active
            gate.set()
            if active is not None:
                await asyncio.wait_for(asyncio.wrap_future(active[1]), 5)
    asyncio.run(probe())


def test_child_control_and_eof_exit_while_projection_hangs(actual_board):
    import threading
    api, config = actual_board
    child = start_process(api, config, prefix="from hermes_cli import kanban_lanes_cli as cli; import time; cli.snapshot = lambda *a, **k: time.sleep(20); ")
    errors = []
    def request():
        try:
            project(api, child, config)
        except api._rpc_client.Unavailable:
            errors.append('closed')
    thread = threading.Thread(target=request)
    thread.start()
    try:
        time.sleep(.1)
        assert child.request({'op': 'observation_cursor'}, 1)
        # Parent death closes stdin. Child must exit despite the hung worker.
        child.proc.stdin.close()
        child.proc.wait(2)
        thread.join(2)
        assert errors == ['closed']
        assert not thread.is_alive()
    finally:
        child.close()


def test_loaded_parent_rejects_changed_plugin_and_core_identity(actual_board, tmp_path):
    import shutil
    api, config = actual_board
    directory = tmp_path / 'dashboard'
    shutil.copytree(DASH, directory)
    manifest = api._identity.capture(directory)
    child = start_process(api, config | {'identity': manifest}, directory=directory)
    try:
        (directory / 'view.py').write_text((directory / 'view.py').read_text() + '\n# deployment moved\n')
        with pytest.raises(api._rpc_client.Unavailable):
            project(api, child, config)
        assert not api._identity.matches(manifest)
    finally:
        child.close()
    bad_core = dict(config['identity'])
    key = next(p for p in bad_core if p.endswith('/kanban_lanes_cli.py'))
    bad_core[key] = '0' * 64
    with pytest.raises(api._rpc_client.Unavailable):
        start_process(api, config | {'identity': bad_core})


def test_supervisor_backoff_never_computes_in_parent_and_coalesces(monkeypatch):
    from types import SimpleNamespace
    client = module('projector_client')
    state = {'value': 1}
    api = SimpleNamespace(MIN_PROJECT_GAP_S=.01,
                          _lane_views=lambda *a: {name: {'configured': False, 'value': state['value']}
                                                 for name in ('collapsed', 'expanded')})
    supervisor = client.Supervisor(api, {})
    supervisor.retry_at = time.monotonic() + 30
    config = {'local': 'fixture', 'fleet': None}
    monkeypatch.setattr(supervisor, '_configuration', lambda: config)
    try:
        deadline = supervisor.retry_at
        with pytest.raises(client.Unavailable):
            supervisor.submit((6., '6h', False), 1, [1]).result(2)
        state['value'] = 2
        with pytest.raises(client.Unavailable):
            supervisor.submit((6., '6h', False), 2, [2]).result(2)
        assert supervisor.retry_at == deadline  # callers do not extend backoff forever
        with supervisor.cv:
            one = supervisor.submit((6., '6h', False), 3, [3])
            two = supervisor.submit((6., '6h', False), 4, [4])
            assert one is two and not one.done()
            assert len(supervisor.queue) == 1
            assert next(iter(supervisor.queue.values()))[0][0] == 4
        with pytest.raises(client.Unavailable):
            one.result(2)
        for _ in range(128):
            supervisor.acquire()
        with pytest.raises(client.Unavailable):
            supervisor.acquire()
        for _ in range(128):
            supervisor.release()
    finally:
        supervisor.close()


@pytest.mark.parametrize('moves', [False, True])
def test_active_generation_retry_is_bounded_and_retains_backoff(monkeypatch, moves):
    from types import SimpleNamespace
    client = module('projector_client')
    supervisor = client.Supervisor(SimpleNamespace(MIN_PROJECT_GAP_S=0), {})
    state = [0]
    spawned = []
    monkeypatch.setattr(client.identity, 'matches', lambda manifest: manifest == state[0])
    monkeypatch.setattr(supervisor, '_configuration', lambda: {
        'identity': state[0], 'local': 'fixture', 'fleet': None, 'mode': 'off'})
    class Broken:
        session = 'fixture'
        closed = False
        def request(self, command, timeout):
            if moves:
                state[0] += 1
            raise client.Unavailable('injected request failure')
        def close(self):
            self.closed = True
    def spawn(config):
        child = Broken()
        spawned.append(child)
        return child
    monkeypatch.setattr(supervisor, '_spawn', spawn)
    try:
        with pytest.raises(client.Unavailable):
            supervisor.submit((6., '6h', True), 1, [1]).result(2)
        assert len(spawned) == (2 if moves else 1)
        assert all(child.closed for child in spawned)
        assert supervisor.failures == 1
        assert supervisor.retry_at > time.monotonic()
    finally:
        supervisor.close()


def test_cursor_reads_coalesce_for_32_viewers(actual_board, monkeypatch):
    import threading
    api, config = actual_board
    child = start_process(api, config)
    supervisor = api._rpc_client.Supervisor(api, api._loaded_identity)
    supervisor.process = child
    barrier = threading.Barrier(32)
    calls = []
    request = child.request
    def slow_request(*args):
        calls.append(1)
        time.sleep(.1)  # Make the overlapping poll window deterministic.
        return request(*args)
    monkeypatch.setattr(child, 'request', slow_request)
    def poll(_):
        barrier.wait(3)
        return supervisor.cursor()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(poll, range(32)))
        assert all(isinstance(value, str) and len(value) == 64 for value in results)
        assert len(set(results)) == 1
        assert len(calls) == 1
        assert not child.closed
    finally:
        supervisor.close()


def test_stream_replacement_revision_not_labelled_current(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_view import load
    api = load()
    class Replacement:
        def acquire(self):
            pass
        def release(self):
            pass
        def cursor(self):
            return 'a' * 64
        def submit(self, params, generation, cursor):
            future = concurrent.futures.Future()
            now = time.time()
            future.set_result({'view': {'configured': False}, 'mode': 'projector',
                               'projector_pid': 123, 'revision': [generation + 1, 'b' * 64],
                               'timing': {'project_start': now, 'project_end': now}})
            return future
    monkeypatch.setattr(api, '_projector_enabled', True)
    monkeypatch.setattr(api, '_projector', Replacement)
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    monkeypatch.setattr(api, 'change_cursor', lambda: ['unchanged'])
    monkeypatch.setattr(api, 'WATCH_S', .01)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client, client.websocket_connect('/v1/lanes/stream?debug=true') as ws:
        frame = ws.receive_json()
        assert frame['projector']['source_current'] is False
        assert frame['projector']['revision'][1] == 'b' * 64
        assert 'detected' not in frame['timing']


def test_parent_cursor_keeps_planner_config_and_observation_triggers(actual_board):
    api, config = actual_board
    base = api.change_cursor()
    home = Path(config['home'])
    planner = home / 'kanban' / 'lanes-planner.json'
    planner.write_text('{}')
    planned = api.change_cursor()
    assert planned != base
    cfg = home / 'config.yaml'
    cfg.write_text(cfg.read_text() + '\n# policy changed\n')
    assert api.change_cursor() != planned
    before = api.change_cursor()
    store = api._observations()
    store.reader({'https://github.com/example/repo/pull/1': ('new-evidence',)})
    assert api.change_cursor() != before


@pytest.mark.parametrize('protocol_change', [False, True])
def test_stream_respawns_current_code_but_refuses_new_protocol(actual_board, tmp_path, monkeypatch, protocol_change):
    import shutil
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    _, config = actual_board
    directory = tmp_path / 'plugins' / 'lanes' / 'dashboard'
    shutil.copytree(DASH, directory)
    shutil.copytree(DASH.parents[1] / 'session-panel', directory.parents[1] / 'session-panel')
    api = module('projector_identity').load(directory / 'plugin_api.py', 'updated_api')
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    app = FastAPI()
    app.include_router(api.router)
    try:
        with TestClient(app) as client:
            with client.websocket_connect('/v1/lanes/stream?debug=true&expanded=true') as ws:
                initial = ws.receive_json()
                assert initial['view']['flow_complete'] is True, json.dumps(initial['view'])
                assert initial['view']['status']
                old = api._projector().process
                target = directory / ('projector_protocol.py' if protocol_change else 'view.py')
                text = target.read_text()
                if protocol_change:
                    text = text.replace('VERSION = 1', 'VERSION = 999')
                    assert text != target.read_text()
                else:
                    text += '\n# deployed new dashboard bytes\n'
                target.write_text(text)
                # Parameter refresh forces demand even on an otherwise idle board.
                ws.send_json({'expanded': False})
                if protocol_change:
                    with pytest.raises(WebSocketDisconnect):
                        while True:
                            ws.receive_json()
                else:
                    frame = ws.receive_json()
                    while frame.get('type') == 'heartbeat':
                        frame = ws.receive_json()
                    assert frame['view']['flow_complete'] is True
                    assert frame['view']['status']
                    assert frame['projector']['mode'] == 'projector'
                    assert frame['projector']['projector_pid'] != initial['projector']['projector_pid']
                assert old.proc.poll() is not None
    finally:
        if api._projector_instance:
            api._projector_instance.close()


@pytest.mark.parametrize('boundary', ['before_request', 'after_result'])
@pytest.mark.parametrize('protocol_change', [False, True])
def test_stream_keeps_active_job_across_code_update(actual_board, tmp_path, monkeypatch, boundary, protocol_change):
    import shutil
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    _, _config = actual_board
    directory = tmp_path / 'plugins' / 'lanes' / 'dashboard'
    shutil.copytree(DASH, directory)
    shutil.copytree(DASH.parents[1] / 'session-panel', directory.parents[1] / 'session-panel')
    api = module('projector_identity').load(directory / 'plugin_api.py', 'active_update_api')
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    app = FastAPI()
    app.include_router(api.router)
    try:
        with TestClient(app) as client, client.websocket_connect('/v1/lanes/stream?debug=true&expanded=true') as ws:
            initial = ws.receive_json()
            assert initial['view']['flow_complete'] is True
            old = api._projector().process
            request = old.request
            moved = []
            def move():
                if moved:
                    return
                moved.append(True)
                target = directory / ('projector_protocol.py' if protocol_change else 'view.py')
                text = target.read_text()
                text = text.replace('VERSION = 1', 'VERSION = 999') if protocol_change else text + '\n# active deployment\n'
                target.write_text(text)
            def updating_request(command, timeout):
                if command['op'] != 'project':
                    return request(command, timeout)
                if boundary == 'before_request':
                    move()  # real child per-job identity refusal
                result = request(command, timeout)
                if boundary == 'after_result':
                    move()  # parent post-result identity fence
                return result
            monkeypatch.setattr(old, 'request', updating_request)
            ws.send_json({'expanded': False})
            if protocol_change:
                with pytest.raises(WebSocketDisconnect):
                    while True:
                        ws.receive_json()
            else:
                frame = ws.receive_json()
                while frame.get('type') == 'heartbeat':
                    frame = ws.receive_json()
                assert frame['view']['flow_complete'] is True
                assert frame['view']['status']
                assert frame['projector']['projector_pid'] != initial['projector']['projector_pid']
            assert moved == [True]
            assert old.proc.poll() is not None
    finally:
        if api._projector_instance:
            api._projector_instance.close()


@pytest.mark.parametrize('warm', [False, True])
def test_rest_and_default_warm_never_project_in_enabled_parent(actual_board, monkeypatch, warm):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api, _ = actual_board
    called = []
    def forbidden(*args):
        called.append(args)
        raise AssertionError('parent projection is forbidden')
    monkeypatch.setattr(api, '_lane_view', forbidden)
    monkeypatch.setattr(api, '_lane_views', forbidden)
    warmer = api.start_legacy() if warm else None
    app = FastAPI()
    app.include_router(api.router)
    try:
        with TestClient(app) as client:
            deadline = time.monotonic() + 10
            while True:
                view = client.get('/v1/lanes').json()
                if not view.get('warming') or called or time.monotonic() >= deadline:
                    break
            assert not called
            assert view['flow_complete'] is True
            assert view['status']
            assert api._projector().process.proc.pid != os.getpid()
    finally:
        if warmer:
            warmer.close()


def test_child_observation_cursor_marks_parent_dirty_without_board_change(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_view import load
    api = load()
    state = {'observation': 0, 'unavailable': False, 'submitted': 0}
    class FakeSupervisor:
        def acquire(self):
            pass
        def release(self):
            pass
        def cursor(self):
            return None if state['unavailable'] else state['observation']
        def submit(self, params, generation, cursor):
            state['submitted'] += 1
            result = concurrent.futures.Future()
            now = time.time()
            result.set_result({'view': {'configured': False, 'observation': state['observation']},
                               'mode': 'projector', 'projector_pid': 123,
                               'revision': [generation, api._rpc_client.rpc.digest(cursor)],
                               'timing': {'project_start': now, 'project_end': now}})
            return result
    monkeypatch.setattr(api, '_projector_enabled', True)
    monkeypatch.setattr(api, '_projector', FakeSupervisor)
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    monkeypatch.setattr(api, 'change_cursor', lambda: ['unchanged-board'])
    monkeypatch.setattr(api, 'WATCH_S', .01)
    monkeypatch.setattr(api, 'MIN_PROJECT_GAP_S', .01)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client, client.websocket_connect('/v1/lanes/stream') as ws:
        assert ws.receive_json()['view']['observation'] == 0
        state['unavailable'] = True
        time.sleep(.1)
        assert state['submitted'] == 1  # unavailable is not a new revision
        state['unavailable'] = False
        time.sleep(.1)
        assert state['submitted'] == 1  # recovery to the same cursor is not dirty
        state['observation'] = 1
        assert ws.receive_json()['view']['observation'] == 1
