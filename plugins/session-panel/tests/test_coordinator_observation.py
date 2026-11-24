"""Composed strict-capture custody over real replica SQL and CoreReaders."""
from collections import Counter
from contextlib import closing
import sqlite3
import threading
import json

import pytest
from test_core_readers import api, board
from test_reader_batch import graph
from test_source_coordinator_core import compose, drain
from test_feed import feedmod


@pytest.fixture
def observed(graph, monkeypatch, tmp_path):
    r, local, remote, ksa = graph
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    now = [1800000000.]
    monkeypatch.setattr(ksa.time, 'time', lambda: now[0])
    path = r.ksc.shadow_path('default')
    c = sqlite3.connect(path)
    columns = {row[1] for row in c.execute('PRAGMA table_info(kanban_sync_state)')}
    if 'pulled_at' not in columns:
        c.execute('ALTER TABLE kanban_sync_state ADD COLUMN pulled_at REAL')
    if not c.execute('SELECT 1 FROM kanban_sync_state').fetchone():
        c.execute("INSERT INTO kanban_sync_state(board_id,gateway_id,hlc_last,mode,pulled_at) VALUES ('default','remote-test','0','replica',?)", (now[0],))
    c.execute('UPDATE kanban_sync_state SET pulled_at=?', (now[0],)); c.commit()
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S0', 'S1'])
    lane = compose(f, r)
    try:
        f.changes(['S0', 'S1']); drain(lane)
        assert all(remote in {row['id'] for row in f.bundles[s]['bundle']['tasks']} for s in ('S0', 'S1'))
        yield r, f, lane, c, remote, now
    finally:
        f.stopped.set(); lane.close(); c.close()


def test_observation_only_real_replica(observed, monkeypatch):
    r, f, lane, c, remote, now = observed
    before = {s: f.bundles[s]['bundle'] for s in ('S0', 'S1')}
    dirty_before = dict(lane.canonical_dirty)
    counts = Counter()
    original = r.external_capture
    def capture(*a, **kw):
        counts['raw'] += 1
        return original(*a, **kw)
    monkeypatch.setattr(r, 'external_capture', capture)
    def forbidden(*a, **kw):
        raise AssertionError('observation-only pull hydrated/reprojected')
    monkeypatch.setattr(r, '_detail', forbidden)
    monkeypatch.setattr(r, 'capture_external', forbidden)
    monkeypatch.setattr(f, 'project_board', forbidden)
    for stamp in (1800000001., 1799999999.):
        c.execute('UPDATE kanban_sync_state SET pulled_at=?', (stamp,)); c.commit()
        lane._capture_one(r, lane.external, (r.board_stamps(), r.external_stamps()))
        for s in before:
            after = f.bundles[s]['bundle']
            assert next(row for row in after['tasks'] if row['id'] == remote)['synced_at'] == stamp
            for key in ('computed_at', 'computed_seq', 'cards'):
                assert after[key] == before[s][key]
            assert f.bundles[s]['board_projection'].external_capture
        assert not lane.pending and lane.canonical_dirty == dirty_before
    assert counts['raw'] == 2, 'one union capture per pull, never per-chat'


def test_composed_projection_retains_exact_single_strict_raw_capture(observed):
    r, f, lane, c, remote, now = observed
    captured = r.external_capture({s: {'lineage': {s}, 'refs': set()} for s in ('S0', 'S1')})
    for s in captured:
        p = f.bundles[s]['board_projection']
        assert r.external_semantic(p.external_capture) == r.external_semantic(captured[s]), 'projection discarded strict capture provenance'
        assert p.external_capture


def test_local_projection_after_observation_does_not_restore_old_pull_clock(observed):
    r, f, lane, c, remote, now = observed
    c.execute('UPDATE kanban_sync_state SET pulled_at=1799999999'); c.commit()
    lane._capture_one(r, lane.external, (r.board_stamps(), r.external_stamps()))
    local_source = lane.sources[0]
    with closing(sqlite3.connect(local_source.canonical_path)) as local, local:
        local.execute("UPDATE tasks SET title='new local source after pull'")
    lane._capture_one(r, local_source, r.board_stamps())
    lane.service()
    for worker in list(lane.active.values()):
        worker.join(3)
    lane.service()
    for s in ('S0', 'S1'):
        assert next(row for row in f.bundles[s]['bundle']['tasks'] if row['id'] == remote)['synced_at'] == 1799999999.


def test_resume_requires_current_strict_provenance_before_clock_carriage(observed, monkeypatch):
    r, f, lane, c, remote, now = observed
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: r)
    successor = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0]).resume(f)
    owner = successor._source_coordinator
    calls = []
    original = r.capture_external
    def materialize(*a, **kw):
        calls.append('current strict materialization')
        return original(*a, **kw)
    monkeypatch.setattr(r, 'capture_external', materialize)
    try:
        c.execute('UPDATE kanban_sync_state SET pulled_at=1799999999'); c.commit()
        owner._capture_one(r, owner.external, (r.board_stamps(), r.external_stamps()))
        assert calls == ['current strict materialization'], 'transferred receipt silently swallowed current-owner acquisition'
        owner.service()
        for worker in list(owner.active.values()):
            worker.join(3)
        owner.service()
        assert next(row for row in successor.bundles['S0']['bundle']['tasks'] if row['id'] == remote)['synced_at'] == 1799999999.
    finally:
        successor.stopped.set(); owner.close()


def test_older_strict_capture_loses_after_newer_observation_without_failure(observed, monkeypatch):
    r, f, lane, c, remote, now = observed
    entered, release = threading.Event(), threading.Event()
    original, failures = r.capture_external, []
    title = c.execute('SELECT title FROM tasks WHERE id=?', (remote,)).fetchone()[0]
    c.execute('UPDATE tasks SET title=? WHERE id=?', ('older in-flight semantic value', remote)); c.commit()
    def held(*a, **kw):
        part = original(*a, **kw)
        if threading.current_thread().name == 'fixture-older-strict':
            entered.set(); assert release.wait(5)
        return part
    monkeypatch.setattr(r, 'capture_external', held)
    fail = lane.store.fail
    def counted_fail(*a, **kw):
        failures.append(a)
        return fail(*a, **kw)
    monkeypatch.setattr(lane.store, 'fail', counted_fail)
    old = threading.Thread(target=lambda: lane._capture_one(r, lane.external, ('older',)), name='fixture-older-strict')
    old.start()
    try:
        assert entered.wait(3)
        c.execute('UPDATE tasks SET title=? WHERE id=?', (title, remote))
        c.execute('UPDATE kanban_sync_state SET pulled_at=1799999999'); c.commit()
        lane._capture_one(r, lane.external, ('newer',))
        assert next(row for row in f.bundles['S0']['bundle']['tasks'] if row['id'] == remote)['synced_at'] == 1799999999.
    finally:
        release.set(); old.join(5)
    assert not old.is_alive() and not failures and not lane.pending
    assert next(row for row in f.bundles['S0']['bundle']['tasks'] if row['id'] == remote)['title'] == title


@pytest.mark.parametrize('field,value', [('title', 'semantic title changed'), ('status', 'done')])
def test_real_semantic_replica_change_materializes_once_not_observation(observed, monkeypatch, field, value):
    r, f, lane, c, remote, now = observed
    counts = Counter()
    original_raw, original_detail = r.external_capture, r._detail
    def raw(*a, **kw):
        counts['raw'] += 1
        return original_raw(*a, **kw)
    def detail(*a, **kw):
        counts['detail'] += 1
        return original_detail(*a, **kw)
    monkeypatch.setattr(r, 'external_capture', raw)
    monkeypatch.setattr(r, '_detail', detail)
    c.execute(f'UPDATE tasks SET {field}=? WHERE id=?', (value, remote)); c.commit()
    lane._capture_one(r, lane.external, ('semantic-change', field))
    assert counts['raw'] == 1 and counts['detail'] > 0
    assert set(lane.pending) == {'S0', 'S1'}
    lane.service()
    for worker in list(lane.active.values()):
        worker.join(3)
    lane.service()
    expected = next(row for row in r.linked(['S0']) + r.done(['S0']) if row['id'] == remote)[field]
    # Canonical core may retain done_pending_release; never normalize that
    # lifecycle evidence to a fabricated done state in the snapshot adapter.
    for s in ('S0', 'S1'):
        rows = f.bundles[s]['bundle']['tasks'] + f.bundles[s]['bundle']['done']
        assert next(row for row in rows if row['id'] == remote)[field] == expected


def test_raw_external_capture_is_frozen_and_materialization_does_not_recapture(observed, monkeypatch):
    from dataclasses import replace
    r, f, lane, c, remote, now = observed
    source = lane.external
    demands = lane.store.demands(source)
    hint = lane.capture.CaptureHints(lane.store.version(source), lane.store.owner_token(source), f.clock())
    captured = lane.capture.capture_external_raw(r, demands, {}, {source: hint})
    with pytest.raises(TypeError):
        captured.captured[next(iter(demands))] = ()
    def forbidden(*a, **kw):
        raise AssertionError('materialization performed a second strict union read')
    monkeypatch.setattr(r, 'external_capture', forbidden)
    snapshot = lane.store.pin(lane.chats['S0'])[0]
    parts = r.capture_external(demands, {}, snapshot.parts, hints={source: hint}, raw=captured)
    assert len(parts) == 1 and parts[0].metrics['union_captures'] == 1
    with pytest.raises(ValueError, match='provenance'):
        r.capture_external(demands, {}, snapshot.parts,
            hints={source: replace(hint, owner_token=object())}, raw=captured)


def test_stale_capture_without_successor_settles_unknown_retry(observed, monkeypatch):
    r, f, lane, c, remote, now = observed
    source = lane.external
    prior = lane.store.version(source)
    demands = lane.store.demands(source)
    assert lane.store.fail(source, prior.source_generation, 'fixture unavailable', demands, prior_version=prior)
    def stale(*a, **kw):
        raise lane.module.StaleCapture('fixture reset while acquisition was in flight')
    monkeypatch.setattr(lane.capture, 'capture_external_raw', stale)
    lane._capture_one(r, source, ('fresh-stamp',))
    assert lane.store.pending(source)
    assert all(a.count == 1 and a.retry_at == f._backoff(1)
               for a in lane.scope_attempts[source].values()), 'lost CAS stranded unknown scopes without retry'


@pytest.mark.parametrize('fresh_projection', [False, True])
def test_projection_started_before_observation_cannot_regress_pull(observed, monkeypatch, fresh_projection):
    r, f, lane, c, remote, now = observed
    entered, release = threading.Event(), threading.Event()
    original = f.project_board if fresh_projection else f.carry_board_observations
    result, errors = [], []
    def held(*a, **kw):
        p = original(*a, **kw)
        if threading.current_thread().name == 'fixture-old-projection':
            entered.set(); assert release.wait(5)
        return p
    # A local change forces the full projection branch, otherwise a canonical
    # retry reuses the old observation parent. Exercise both actual writer fences.
    if fresh_projection:
        with closing(sqlite3.connect(lane.sources[0].canonical_path)) as local, local:
            local.execute("UPDATE tasks SET title='local change before old projection'")
        lane._capture_one(r, lane.sources[0], r.board_stamps())
    monkeypatch.setattr(f, 'project_board' if fresh_projection else 'carry_board_observations', held)
    def project():
        try:
            result.append(lane._project('S0'))
        except BaseException as exc:
            errors.append(exc)
    old = threading.Thread(target=project, name='fixture-old-projection')
    old.start()
    try:
        assert entered.wait(3)
        if fresh_projection:
            # A second current pure worker can win first, then receive the pull.
            # The held full candidate has the SAME admission, not an old version.
            for s in ('S0', 'S1'):
                lane._project(s)
        c.execute('UPDATE kanban_sync_state SET pulled_at=1799999999'); c.commit()
        lane._capture_one(r, lane.external, ('new-observation',))
        assert next(row for row in f.bundles['S0']['bundle']['tasks'] if row['id'] == remote)['synced_at'] == 1799999999.
    finally:
        release.set(); old.join(5)
    assert not old.is_alive() and not errors
    assert next(row for row in f.bundles['S0']['bundle']['tasks'] if row['id'] == remote)['synced_at'] == 1799999999.


@pytest.mark.parametrize('replica_mode', [False, True])
def test_observation_real_origin_ledger_via_session_tasks_without_replica_source(graph, monkeypatch, tmp_path, replica_mode):
    from hermes_cli import kanban_origin_ledger as ledger, kanban_db_sync as sync, kanban_db_notify
    r, local, remote, ksa = graph
    now = [1800000000.]
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'isolated-hub-state'))
    monkeypatch.setattr(ksa.time, 'time', lambda: now[0])
    monkeypatch.setattr(sync, 'sync_config', lambda: {'enabled': True, 'links_reader': 'replica' if replica_mode else 'hub', 'gateway': 'local-test'})
    monkeypatch.setattr(kanban_db_notify, 'is_machine_session', lambda *a, **kw: False)
    # The replica retains real details but lacks this chat's membership. In
    # replica mode only the real origin-obligation selector can retain custody.
    with closing(sqlite3.connect(r.ksc.shadow_path('default'))) as shadow, shadow:
        shadow.execute('DELETE FROM task_chat_links')
    hub, ledger_path = tmp_path / 'fixture-hub.db', tmp_path / 'fixture-origin.db'
    owner = {'gateway': 'remote-test', 'profile': 'default', 'board': 'default', 'task_id': remote}
    bindings = [dict(binding_id=f'binding-000{i}', version=1, owner=owner,
        origin={'gateway': 'local-test', 'profile': 'default', 'session': s},
        task_record_key=f'default/default/{remote}', publication_version=3) for i, s in enumerate(('S0', 'S1'))]
    task = {**owner, 'id': remote, 'title': 'Real ledger work', 'status': 'running', 'origin_bindings': bindings}
    hc = sqlite3.connect(hub)
    hc.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, data TEXT)')
    hc.execute('CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)')
    hc.execute("INSERT INTO meta VALUES ('last_ok', '1800000000')")
    hc.execute("INSERT INTO replica VALUES ('remote-test', 'tasks', ?, 3, ?)", (bindings[0]['task_record_key'], json.dumps(task)))
    hc.commit()
    for i, binding in enumerate(bindings):
        assert ledger.accept_binding(path=ledger_path, replica=hub, binding=binding, request_id=f'request-000{i}',
            sender='gw:remote-test', gateway='local-test', profile='default', resolve_session=lambda sid: sid)['status'] == 'accepted'
    def origin():
        return ledger.project(ledger_path, replica=hub, gateway='local-test', profile='default', now=now[0])
    monkeypatch.setattr(ksa, '_remote_links', lambda **kw: origin())
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', lambda **kw: (origin(), origin()))
    ksa._reset_cache_for_tests()
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S0', 'S1']); lane = compose(f, r)
    try:
        # Exercise actual CoreReaders -> session_tasks selection, not synthetic
        # linked()/done() dictionaries or adding a replica source marker by hand.
        for method in (r.linked, r.done):
            rows = method(['S0'])
            row = next(row for row in rows if row['id'] == remote)
            assert row['via'] == 'origin' and row.get('source') != 'replica'
            if replica_mode:
                assert row['source'] == 'origin-obligation'
        f.changes(['S0', 'S1']); drain(lane)
        before = {s: f.bundles[s]['bundle'] for s in ('S0', 'S1')}
        for s, value in before.items():
            row = next(row for row in value['tasks'] if row['id'] == remote)
            assert row['via'] == 'origin' and row.get('source') != 'replica'
            assert row['binding_id'] == bindings[int(s[-1])]['binding_id']
        def forbidden(*a, **kw):
            raise AssertionError('origin pull rehydrated or reprojected')
        monkeypatch.setattr(r, '_detail', forbidden)
        monkeypatch.setattr(r, 'capture_external', forbidden)
        monkeypatch.setattr(f, 'project_board', forbidden)
        for stamp in (1800000001., 1799999999.):
            hc.execute("UPDATE meta SET v=? WHERE k='last_ok'", (str(stamp),)); hc.commit()
            lane._capture_one(r, lane.external, (r._stat(hub), r._stat(ledger_path)))
            for s in before:
                after = f.bundles[s]['bundle']
                assert next(row for row in after['tasks'] if row['id'] == remote)['synced_at'] == stamp
                for field in ('computed_at', 'computed_seq', 'cards'):
                    assert after[field] == before[s][field]
    finally:
        f.stopped.set(); lane.close(); hc.close()
