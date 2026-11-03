"""Cross-pass remote inventory gate (REMOTE-GATE.md), on fork core's real projection.

Native replay V2: ``remote_cards`` re-projected the origin ledger and the hub replica every 30 s pass because core
keeps that projection 15 s; the inputs rarely change between passes. The gate may skip core's projection only while
every input is unchanged and every clock verdict still holds, and must return exactly what core would project now.
Set HERMES_CORE_SOURCE to the core checkout to run.
"""
import importlib
import json
import os
import sqlite3
import time

import pytest
from test_closeout import core, plugin

T0 = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def gate(tmp_path, monkeypatch):
    source = os.environ.get('HERMES_CORE_SOURCE')
    if not source:
        pytest.skip('set HERMES_CORE_SOURCE for core SQLite integration')
    monkeypatch.syspath_prepend(source)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub'))
    monkeypatch.setenv('HERMES_MAIL_CONFIG', str(tmp_path / 'mail.json'))
    clock = Clock()
    monkeypatch.setattr(time, 'time', clock)
    ledger = importlib.import_module('hermes_cli.kanban_origin_ledger')
    ksa = importlib.import_module('hermes_cli.kanban_session_activity')
    adapter = importlib.import_module('hermes_cli.kanban_origin_adapter')
    profiles = importlib.import_module('hermes_cli.profiles')
    constants = importlib.import_module('hermes_constants')
    monkeypatch.setattr(constants, 'get_hermes_home', lambda: tmp_path)
    monkeypatch.setattr(adapter, '_gateway', lambda _: 'origin')
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    monkeypatch.setattr(ksa, '_LINKS_MAX_AGE_S', 0.0)  # core's own reuse ends before every pass (30 s tick)
    replica = tmp_path / 'hub.db'
    path = tmp_path / 'kanban' / 'origin-ledger.db'
    binding = {'binding_id': 'binding-0001', 'version': 1,
               'owner': {'gateway': 'owner', 'profile': 'work', 'board': 'build', 'task_id': 't_same'},
               'origin': {'gateway': 'origin', 'profile': 'default', 'session': 'chat'},
               'task_record_key': 'work/build/t_same', 'publication_version': 3}
    owner = sqlite3.connect(tmp_path / 'owner.db')
    owner.executescript('''
        CREATE TABLE tasks (id TEXT, status TEXT, completed_at REAL, current_run_id INTEGER,
                            goal_mode INTEGER, completion_contract TEXT);
        INSERT INTO tasks VALUES ('t_same','running',NULL,NULL,0,'local-only');
        CREATE TABLE origin_creation_intents (task_id TEXT, binding_json TEXT);
        CREATE TABLE task_events (id INTEGER, task_id TEXT, kind TEXT, run_id INTEGER,
                                  created_at REAL, payload TEXT);
        CREATE TABLE task_runs (id INTEGER, task_id TEXT, status TEXT, outcome TEXT, ended_at REAL, summary TEXT);
    ''')
    owner.execute('INSERT INTO origin_creation_intents VALUES (?,?)',
                  ('t_same', json.dumps({k: v for k, v in binding.items() if k != 'publication_version'})))
    owner.commit()
    with sqlite3.connect(replica) as db:
        db.executescript('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER,'
                         ' data TEXT, updated_at INTEGER, PRIMARY KEY (gw, coll, key));'
                         'CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);'
                         "INSERT INTO meta VALUES ('gateway', 'origin');")
    seq = [0]

    def publish(status, version, heartbeat=None):
        now = clock.now
        owner.execute('UPDATE tasks SET status=?,completed_at=?', (status, now if status == 'done' else None))
        task = {**binding['owner'], 'id': 't_same', 'status': status, 'origin_bindings': [binding]}
        task = ledger.observe_owner_task(owner, task, now=now)
        owner.commit()
        beat = now if heartbeat is None else heartbeat
        with sqlite3.connect(replica) as db:
            seq[0] += 1
            db.execute('INSERT OR REPLACE INTO replica VALUES (?,?,?,?,?,?,?)',
                       ('owner', 'tasks', binding['task_record_key'], version, seq[0], json.dumps(task), int(now)))
            seq[0] += 1
            db.execute('INSERT OR REPLACE INTO replica VALUES (?,?,?,?,?,?,?)', (
                'owner', 'gateway', 'info', version, seq[0], json.dumps({'gateway': 'owner', 'task_scopes': [
                    {'profile': 'work', 'board': 'build', 'state': 'ok', 'observed_at': beat}]}), int(now)))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_ok',?)", (str(now),))

    def pull(at=None):
        """A hub pull that changes nothing but ``last_ok``."""
        with sqlite3.connect(replica) as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_ok',?)", (str(clock.now if at is None else at),))

    publish('running', 3)
    ledger.accept_binding(path=path, replica=replica, binding=binding, request_id='request-0001',
                          sender='gw:owner', gateway='origin', profile='default', resolve_session=lambda _: 'chat')
    local = tmp_path / 'kanban.db'
    with sqlite3.connect(local) as db:
        db.executescript("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, session_id TEXT);")
    monkeypatch.setattr(ksa, '_board_db_paths', lambda: [local])
    monkeypatch.setattr(ksa, '_hub_db_path', lambda: replica)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [local])
    projections = []
    during = []
    real_project = ledger.project

    def counted(*args, **kwargs):
        projections.append(clock.now)
        while during:
            during.pop(0)()
        return real_project(*args, **kwargs)
    monkeypatch.setattr(ledger, 'project', counted)
    ksa._reset_cache_for_tests()
    plugin._gate_reset()
    core._structural_reads.clear()

    def fresh():
        """What core projects now with no cache anywhere (the reference answer)."""
        saved = list(projections)
        ksa._reset_cache_for_tests(keep_remote=True)
        try:
            with ksa.one_annotation_snapshot():  # pinned: the gate steps aside, core projects itself
                return plugin.remote_cards()
        finally:
            projections[:] = saved

    world = {'clock': clock, 'publish': publish, 'pull': pull, 'projections': projections, 'fresh': fresh,
             'during': during,
             'ksa': ksa, 'ledger_path': path, 'replica': replica, 'owner': owner, 'local': local}
    try:
        yield world
    finally:
        owner.close()
        plugin._gate_reset()
        core._structural_reads.clear()
        ksa._reset_cache_for_tests()


def _norm(cards):
    return sorted(json.dumps(c, sort_keys=True, default=str) for c in cards)


def _passes(world, n=2, step=30.0):
    out = []
    for _ in range(n):
        world['clock'].now += step
        out.append(plugin.remote_cards())
    return out


def test_unchanged_inputs_project_once_and_match_core(gate):
    first, second, third = _passes(gate, 3, step=10.0)
    assert len(gate['projections']) == 1
    assert _norm(second) == _norm(third) == _norm(first) == _norm(gate['fresh']())
    assert [c['status'] for c in first if c.get('via') == 'origin'] == ['running']


def test_hub_pull_restamps_synced_at_without_projecting(gate):
    _passes(gate, 1, step=5.0)
    gate['clock'].now += 5.0
    gate['pull']()
    gate['clock'].now += 5.0
    cards = plugin.remote_cards()
    assert len(gate['projections']) == 1
    assert {c['synced_at'] for c in cards if c.get('via') == 'origin'} == {gate['clock'].now - 5.0}
    assert _norm(cards) == _norm(gate['fresh']())


def test_ledger_commit_reprojects(gate):
    _passes(gate, 1, step=5.0)
    with sqlite3.connect(gate['ledger_path']) as db:
        db.execute("UPDATE origin_obligations SET resolved_session = 'chat2'")
    cards = _passes(gate, 1, step=5.0)[0]
    assert len(gate['projections']) == 2
    assert {c['session'] for c in cards if c.get('via') == 'origin'} == {'chat2'}
    assert _norm(cards) == _norm(gate['fresh']())


def test_replica_task_change_reprojects(gate):
    _passes(gate, 1, step=5.0)
    gate['clock'].now += 5.0
    gate['publish']('done', 4)
    cards = _passes(gate, 1, step=5.0)[0]
    assert len(gate['projections']) == 2
    assert [c['reported_status'] for c in cards if c.get('via') == 'origin'] == ['done']
    assert _norm(cards) == _norm(gate['fresh']())


def test_owner_heartbeat_window_end_reprojects(gate):
    # Fresh replica (every pull lands) but the owner's scope heartbeat ages out: core turns the row unknown.
    _passes(gate, 1, step=5.0)
    projected = len(gate['projections'])
    cards = []
    for _ in range(20):  # 600 s: past the 450 s scope-heartbeat floor window
        gate['clock'].now += 30.0
        gate['pull']()
        cards = plugin.remote_cards()
        assert _norm(cards) == _norm(gate['fresh']()), gate['clock'].now - T0
    statuses = [c['status'] for c in cards if c.get('via') == 'origin']
    assert statuses == ['unknown']
    assert len(gate['projections']) > projected
    assert [t for t in gate['projections'] if t < T0 + 450] == [T0 + 5.0]  # the pulls before reuse it


def test_replica_staleness_verdict_reprojects(gate):
    _passes(gate, 1, step=5.0)
    gate['clock'].now += 200.0  # no pull: last_ok is 205 s old, past 180 s
    cards = plugin.remote_cards()
    assert len(gate['projections']) == 2
    assert [c['stale'] for c in cards if c.get('via') == 'origin'] == [True]
    assert _norm(cards) == _norm(gate['fresh']())


def test_offline_since_reprojects(gate):
    _passes(gate, 1, step=5.0)
    with sqlite3.connect(gate['replica']) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('offline_since', ?)", (str(gate['clock'].now),))
    cards = _passes(gate, 1, step=5.0)[0]
    assert len(gate['projections']) == 2
    assert _norm(cards) == _norm(gate['fresh']())


def test_swallowed_replica_error_in_project_is_not_reused(gate, monkeypatch):
    ledger = importlib.import_module('hermes_cli.kanban_origin_ledger')
    real = ledger.refresh_observations

    def broken(*args, **kwargs):
        raise sqlite3.DatabaseError('database disk image is malformed')
    monkeypatch.setattr(ledger, 'refresh_observations', broken)
    stale = _passes(gate, 1, step=5.0)[0]
    assert [c['stale'] for c in stale if c.get('via') == 'origin'] == [True]  # project() swallowed it
    monkeypatch.setattr(ledger, 'refresh_observations', real)
    cards = _passes(gate, 1, step=5.0)[0]
    assert len(gate['projections']) == 2
    assert [c['stale'] for c in cards if c.get('via') == 'origin'] == [False]


def test_verdict_flip_while_projecting_is_not_reused(gate):
    # The pass starts 170 s after the last pull (fresh); the replica turns stale (> 180 s) while core projects.
    gate['clock'].now += 170.0

    def later():
        gate['clock'].now += 20.0
    gate['during'].append(later)
    plugin.remote_cards()
    gate['clock'].now += 10.0
    gate['pull']()  # fresh again: the same verdict the pass started under
    cards = plugin.remote_cards()
    assert len(gate['projections']) == 2
    assert _norm(cards) == _norm(gate['fresh']())


def test_core_cache_reset_resets_gate(gate):
    _passes(gate, 1, step=5.0)
    gate['ksa']._reset_cache_for_tests()
    _passes(gate, 1, step=5.0)
    assert len(gate['projections']) == 2


def test_alias_tip_change_reprojects(gate, monkeypatch):
    # Unit test of _gate_hit's tip guard: the input-key shortcut (_gate_fast) is pinned off, because it learns of a
    # tip change only through state.db itself (tests/test_idle_tick_reuse.py), which this mock does not touch.
    monkeypatch.setattr(plugin, '_gate_fast', lambda *a: None)
    _passes(gate, 1, step=5.0)
    with plugin._gate_lock:
        memo = plugin._gate['memo']
    tips = iter([memo['tips'] + (('x', 'changed'),)])
    monkeypatch.setattr(plugin, '_alias_tips', lambda sessions: next(tips, memo['tips']))
    _passes(gate, 1, step=5.0)
    assert len(gate['projections']) == 2


def test_outer_annotation_snapshot_bypasses_gate(gate):
    _passes(gate, 1, step=5.0)
    gate['clock'].now += 5.0
    with gate['ksa'].one_annotation_snapshot():
        plugin.remote_cards()
    assert len(gate['projections']) == 2


def test_failed_replica_read_is_not_reused(gate, monkeypatch):
    ksa = gate['ksa']
    _passes(gate, 1, step=5.0)
    plugin._gate_reset()
    real = ksa._read_remote_links

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(ksa, '_read_remote_links', broken)
    _passes(gate, 1, step=5.0)  # stale fallback answer: never stored
    monkeypatch.setattr(ksa, '_read_remote_links', real)
    with plugin._gate_lock:
        assert 'memo' not in plugin._gate
    _passes(gate, 1, step=5.0)
    assert len(gate['projections']) == 3


def test_parked_waits_reuse_gated_projection(gate):
    ksa = gate['ksa']
    _passes(gate, 1, step=5.0)
    gate['clock'].now += 5.0
    gated = plugin._parked_waits(ksa, None)
    assert len(gate['projections']) == 1
    ksa._reset_cache_for_tests(keep_remote=True)
    with ksa.one_annotation_snapshot():
        reference = ksa.parked_waits(now=None)
    assert _norm(gated) == _norm(reference)


def test_alias_sessions_cover_every_tip_core_resolves(gate, monkeypatch):
    ksa = gate['ksa']
    adapter = importlib.import_module('hermes_cli.kanban_origin_adapter')
    ledger = importlib.import_module('hermes_cli.kanban_origin_ledger')
    rows = ledger.project(gate['ledger_path'], replica=gate['replica'], gateway='origin', profile='default')
    row = rows[0]
    legacy = [
        {'session': 'old-chat', 'key': row['key'], 'origin_gateway': 'origin', 'origin_profile': 'default',
         'gateway': 'owner', 'id': 't_same', 'status': 'running', 'stale': False},
        {'session': 'elsewhere', 'key': 'owner/work/build/t_other', 'origin_gateway': 'origin',
         'origin_profile': 'default', 'gateway': 'owner', 'id': 't_other', 'status': 'running', 'stale': False},
        {'session': row['session'], 'key': row['key'], 'origin_gateway': 'origin', 'origin_profile': 'default',
         'gateway': 'owner', 'id': 't_same', 'status': 'running', 'stale': False},
    ]
    monkeypatch.setattr(ksa, '_legacy_remote_links', lambda: [dict(c) for c in legacy])
    state = importlib.import_module('hermes_state').SessionDB(db_path=gate['ledger_path'].parent.parent / 'state.db')
    state.close()
    with sqlite3.connect(gate['ledger_path'].parent.parent / 'state.db') as db:
        cols = [r[1] for r in db.execute('PRAGMA table_info(sessions)')]
        for sid in ('old-chat', row['session'], row['origin_session']):
            db.execute(f"INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, 'cli', 0)", (sid,))
    assert 'parent_session_id' in cols
    resolved = []
    real = adapter._resolve_session

    def spy(state_db, sid, conn):
        resolved.append(sid)
        return real(state_db, sid, conn)
    monkeypatch.setattr(adapter, '_resolve_session', spy)
    ksa._project_remote_links()
    assert resolved, 'fixture must make core resolve a tip'
    assert set(resolved) <= plugin._alias_sessions(legacy, rows)
    assert 'elsewhere' not in plugin._alias_sessions(legacy, rows)


def test_parked_waits_without_reusable_projection_reads_core(gate):
    gate['clock'].now += 5.0
    plugin._parked_waits(gate['ksa'], None)
    assert len(gate['projections']) == 1
