"""Complete-membership semantic capture against disposable committed SQLite stores."""
from contextlib import closing
import json
from types import SimpleNamespace

import pytest

from test_withdrawal import ROOT, OWNER, core, load_module

membership = load_module('panel_membership_test', ROOT / 'panel_membership.py')
NOW = 1000.5
GW = 'source'
STEP = "SELECT child.id FROM sessions parent JOIN sessions child ON child.parent_session_id=parent.id WHERE parent.id=? AND parent.end_reason='compression' ORDER BY child.id LIMIT 1"


def linked(task, gateway, profile):
    return [v['session'] for v in task.get('sessions', [])
            if v.get('gw') == gateway and v.get('profile') == profile
            and v.get('rel') in ('create-task', 'adopt-task', 'cross_task', 'shepherd')]


def validate(binding):
    assert set(binding) == {'binding_id', 'version', 'owner', 'origin', 'task_record_key', 'publication_version'}


def heartbeats(infos):
    return {(g, s['profile'], s['board']): (s['observed_at'], 450)
            for g, raw in infos for s in json.loads(raw)['task_scopes'] if s['state'] == 'ok'}


@pytest.fixture
def sources(tmp_path):
    paths = {name: tmp_path / (name + '.db') for name in ('state', 'replica', 'ledger')}
    def sql(name, query, args=()):
        # Fixture writes only; production uses core's guarded readonly adapter.
        import sqlite3
        with closing(sqlite3.connect(paths[name])) as conn, conn:
            return conn.execute(query, args).fetchall()
    sql('state', 'CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT)')
    sql('state', 'INSERT INTO sessions VALUES (?,NULL,NULL)', (OWNER['storedSessionId'],))
    sql('replica', 'CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)')
    for k, v in [('gateway', GW), ('last_ok', str(NOW)), ('offline_since', '')]:
        sql('replica', 'INSERT INTO meta VALUES (?,?)', (k, v))
    sql('replica', 'CREATE TABLE replica(gw TEXT,coll TEXT,key TEXT,data TEXT,version INTEGER,PRIMARY KEY(gw,coll,key))')
    sql('ledger', 'CREATE TABLE origin_obligations(binding_id TEXT,binding_hash TEXT,binding_json TEXT,resolved_session TEXT,task_json TEXT,observation_version INTEGER,released INTEGER)')
    sql('ledger', 'CREATE TABLE origin_receipts(binding_hash TEXT,receipt_json TEXT)')
    info = dict(gateway='remote', task_scopes=[dict(profile='default', board='default', state='ok', observed_at=NOW)])
    sql('replica', 'INSERT INTO replica VALUES (?,?,?,?,1)', ('remote', 'gateway', 'info', json.dumps(info)))
    def task(tid='t_one', session=None, **extra):
        return dict(id=tid, status='blocked', sessions=[dict(gw=GW, profile=OWNER['profile'],
                    session=session or OWNER['storedSessionId'], rel='create-task')], **extra)
    def put(record, key=None, gw='remote'):
        sql('replica', 'INSERT OR REPLACE INTO replica VALUES (?,?,?,?,1)',
            (gw, 'tasks', key or 'default/default/' + record['id'], json.dumps(record)))
    def obligation(tid='t_ledger', session=None):
        binding = dict(binding_id=tid, version=1, publication_version=1,
                       origin=dict(gateway=GW, profile=OWNER['profile'], session=session or OWNER['storedSessionId']),
                       owner=dict(gateway='remote', profile='default', board='default', task_id=tid),
                       task_record_key='default/default/' + tid)
        record = dict(id=tid, status='blocked')
        # Ledger-only membership has no chat link in its current owner record.
        # A retained observation without that record is unknown, not healthy.
        put(record)
        sql('ledger', 'INSERT INTO origin_obligations VALUES (?,?,?,?,?,1,0)',
            (tid, tid, json.dumps(binding), session or OWNER['storedSessionId'], json.dumps(record)))
    def capture(**kw):
        args = dict(gateway=GW, state_path=paths['state'], replica_path=paths['replica'], ledger_path=paths['ledger'],
                    connect=core._ro_connect, step_sql=STEP, chain_cap=1000, linked_sessions=linked,
                    validate_binding=validate, scope_heartbeats=heartbeats, now=NOW)
        args.update(kw)
        return membership.capture(OWNER, **args)
    return SimpleNamespace(paths=paths, sql=sql, task=task, put=put, obligation=obligation, capture=capture)


def test_unrelated_committed_writes_do_not_change_capture(sources):
    s = sources
    s.put(s.task())
    before = s.capture()
    assert before is not None
    s.put(s.task('t_other', session='another-chat', title='unrelated'))
    s.obligation('t_elsewhere', session='another-chat')
    s.sql('state', "INSERT INTO sessions VALUES ('another-chat',NULL,NULL)")
    s.sql('replica', "INSERT INTO meta VALUES ('cursor','999')")
    assert membership.unchanged(before, s.capture())


def test_arrival_removal_and_content_are_semantic(sources):
    s = sources
    empty = s.capture()
    assert empty
    s.put(s.task())
    arrived = s.capture()
    assert arrived and arrived != empty
    s.put(s.task(title='new title'))
    assert s.capture() not in (empty, arrived, None)
    s.sql('replica', "DELETE FROM replica WHERE coll='tasks'")
    assert s.capture() == empty
    assert not membership.unchanged(arrived, s.capture())


def test_ledger_only_arrival_change_receipt_and_removal(sources):
    s = sources
    empty = s.capture()
    s.obligation()
    added = s.capture()
    assert added and added != empty
    s.sql('ledger', "UPDATE origin_obligations SET released=1")
    released = s.capture()
    assert released and released != added
    s.sql('ledger', 'INSERT INTO origin_receipts VALUES (?,?)', ('t_ledger', '{"legacy_alias":"alias"}'))
    receipt = s.capture()
    assert receipt and receipt != released
    s.sql('ledger', 'DELETE FROM origin_receipts')
    s.sql('ledger', 'DELETE FROM origin_obligations')
    assert s.capture() == empty


def test_compression_alias_and_alias_membership(sources):
    s = sources
    s.put(s.task('t_tip', session='tip'))
    before = s.capture()
    s.sql('state', "UPDATE sessions SET end_reason='compression'")
    s.sql('state', 'INSERT INTO sessions VALUES (?,?,NULL)', ('tip', OWNER['storedSessionId']))
    aliased = s.capture()
    assert aliased and aliased != before
    s.put(s.task('t_tip', session='tip', title='tip task changed'))
    assert s.capture() not in (aliased, before, None)
    s.sql('state', "DELETE FROM sessions WHERE id='tip'")
    assert s.capture() == before


@pytest.mark.parametrize('source', ['state', 'replica', 'ledger'])
def test_missing_required_store_is_changed_even_when_repeated(sources, source):
    s = sources
    previous = s.capture()
    s.paths[source].unlink()
    assert s.capture() is None
    assert not membership.unchanged(previous, s.capture())
    assert not membership.unchanged(s.capture(), s.capture())
    assert not s.paths[source].exists()  # read never initializes missing inputs


@pytest.mark.parametrize('mutation', [
    "UPDATE meta SET v='nan' WHERE k='last_ok'",
    "UPDATE meta SET v='100000' WHERE k='last_ok'",
    "DELETE FROM meta WHERE k='last_ok'",
    "UPDATE meta SET v='1' WHERE k='offline_since'",
    "UPDATE meta SET v='other' WHERE k='gateway'",
    "DELETE FROM replica WHERE coll='gateway'",
    "UPDATE replica SET data='not json' WHERE coll='tasks'",
    "UPDATE replica SET version=NULL WHERE coll='tasks'",
])
def test_unknown_sources_never_compare_unchanged(sources, mutation):
    s = sources
    s.put(s.task())
    before = s.capture()
    assert before
    s.sql('replica', mutation)
    assert s.capture() is None
    assert not membership.unchanged(before, s.capture())
    assert not membership.unchanged(None, s.capture())


@pytest.mark.parametrize('extra', [dict(status='unknown'), dict(stale=True), dict(partial_source_unknown='failed')])
def test_unknown_member_is_changed(sources, extra):
    s = sources
    record = s.task()
    record.update(extra)
    s.put(record)
    assert s.capture() is None


def test_freshness_expiry_and_observation_only_refresh(sources):
    s = sources
    s.put(s.task())
    before = s.capture()
    assert before
    assert s.capture(now=NOW + 181) is None
    s.sql('replica', "UPDATE meta SET v=? WHERE k='last_ok'", (str(NOW + 181),))
    assert s.capture(now=NOW + 181) == before
    assert s.capture(now=NOW + 451) is None


def test_alias_cycle_and_cap_are_unknown(sources):
    s = sources
    s.sql('state', 'UPDATE sessions SET parent_session_id=?,end_reason=?', ('tip', 'compression'))
    s.sql('state', 'INSERT INTO sessions VALUES (?,?,?)', ('tip', OWNER['storedSessionId'], 'compression'))
    assert s.capture() is None
    assert s.capture(chain_cap=1) is None


def test_legacy_twin_and_rehomed_publication_are_not_ignored(sources):
    s = sources
    s.put(s.task(created_at=123))
    before = s.capture()
    s.put(dict(id='t_one', status='blocked', sessions=[], created_at=123), key='default/default/t_one', gw='elsewhere')
    # Missing owner scope is unknown, not unchanged absence.
    assert not membership.unchanged(before, s.capture())
    s.sql('replica', "DELETE FROM replica WHERE gw='elsewhere'")
    assert s.capture() == before
    s.put(dict(id='t_one', status='blocked', sessions=[]), key='default/t_one')
    assert s.capture() is None


def test_core_production_adapter_and_canonical_compression(sources, monkeypatch):
    pytest.importorskip('hermes_state_compression')
    from hermes_state import SessionDB
    from test_kanban_mirror import fixture
    from hermes_constants import get_hermes_home
    s = sources
    state = SessionDB(s.paths['state'].with_name('real-state.db'))
    try:
        # Real SessionDB schema and canonical continuation selection; unrelated
        # model/session writes must not leak into the capture.
        state.create_session(session_id=OWNER['storedSessionId'], source='cli', model='test')
        state.create_session(session_id='tip', source='cli', model='test', parent_session_id=OWNER['storedSessionId'])
        state._execute_write(lambda c: c.execute("UPDATE sessions SET end_reason='compression' WHERE id=?", (OWNER['storedSessionId'],)))
        (s.paths['state'].parent / 'mirror').mkdir()
        mirror, _, _, _ = fixture(s.paths['state'].parent / 'mirror')
        monkeypatch.setattr(mirror.core, '_state_db', lambda: s.paths['state'].with_name('real-state.db'))
        mirror.remote.gateway = lambda: GW
        glob = mirror.remote.membership_capture.__func__.__globals__
        view = glob['load_file'](ROOT.parent / 'hub-sync' / 'hubview.py')
        monkeypatch.setattr(view, 'db_path', lambda: s.paths['replica'])
        import hermes_constants
        monkeypatch.setattr(hermes_constants, 'get_hermes_home', lambda: s.paths['state'].parent)
        from hermes_cli import kanban_db_sync
        monkeypatch.setattr(kanban_db_sync, 'sync_config', lambda: {'links_reader': 'legacy'})
        ledger = s.paths['state'].parent / 'kanban' / 'origin-ledger.db'
        ledger.parent.mkdir()
        s.paths['ledger'].rename(ledger)
        import time
        monkeypatch.setattr(time, 'time', lambda: NOW)
        s.put(s.task('t_tip', session='tip'))
        captured = mirror.remote.membership_capture(OWNER)
        assert captured is not None
        s.put(s.task('t_tip', session='tip', title='changed'))
        assert mirror.remote.membership_capture(OWNER) != captured
    # Alias/membership input errors are exercised separately in replica mode.
    finally:
        state.close()


def test_reused_id_in_other_chat_does_not_invalidate(sources):
    s = sources
    s.put(s.task(created_at=123))
    before = s.capture()
    s.put(s.task(session='other-chat', created_at=456), gw='elsewhere')
    assert s.capture() == before
    s.put(s.task(session='other-chat', created_at=456, title='other title'), gw='elsewhere')
    assert s.capture() == before


@pytest.mark.parametrize('column,value', [
    ('task_json', '{"status":"unknown"}'),
    ('task_json', '{"status":"blocked","stale":true}'),
    ('task_json', '{"status":"blocked","partial_source_unknown":"failed"}'),
    ('observation_version', None),
    ('observation_version', 0),
])
def test_unknown_ledger_observation_is_changed(sources, column, value):
    s = sources
    s.obligation()
    assert s.capture()
    s.sql('ledger', 'UPDATE origin_obligations SET ' + column + '=?', (value,))
    assert s.capture() is None
    assert not membership.unchanged(s.capture(), s.capture())


@pytest.mark.parametrize('mutation', [
    "DELETE FROM replica WHERE coll='tasks'",
    "UPDATE replica SET version=2 WHERE coll='tasks'",
    "UPDATE replica SET data='{\"id\":\"t_ledger\",\"status\":\"running\"}' WHERE coll='tasks'",
])
def test_retained_ledger_requires_exact_current_owner_record(sources, mutation):
    s = sources
    s.obligation()
    before = s.capture()
    assert before
    s.sql('replica', mutation)
    assert s.capture() is None
    assert not membership.unchanged(before, s.capture())
    assert not membership.unchanged(s.capture(), s.capture())


def test_missing_ledger_owner_record_matches_real_canonical_unknown(sources):
    ledger = pytest.importorskip('hermes_cli.kanban_origin_ledger')
    from hermes_cli.kanban_scope_heartbeat import scope_heartbeats
    s = sources
    s.obligation()
    args = dict(validate_binding=ledger.validate_binding, scope_heartbeats=scope_heartbeats)
    assert s.capture(**args)
    s.sql('replica', "DELETE FROM replica WHERE coll='tasks'")
    # Canonical projection is writeful; call it only as an independent fixture oracle.
    projected = ledger.project(s.paths['ledger'], replica=s.paths['replica'],
                               gateway=GW, profile=OWNER['profile'], now=NOW)
    assert [(r['status'], r['stale']) for r in projected] == [('unknown', True)]
    assert s.capture(**args) is None
    s.put(dict(id='t_ledger', status='blocked'))
    projected = ledger.project(s.paths['ledger'], replica=s.paths['replica'],
                               gateway=GW, profile=OWNER['profile'], now=NOW)
    assert [(r['status'], r['stale']) for r in projected] == [('blocked', False)]
    assert s.capture(**args)


@pytest.mark.parametrize('status,released,verified,known', [
    ('done', 0, False, False), ('done', 1, False, False),
    ('done', 1, True, True), ('archived', 0, False, True),
])
def test_terminal_ledger_eligibility_uses_canonical_evidence(sources, status, released, verified, known):
    ledger = pytest.importorskip('hermes_cli.kanban_origin_ledger')
    from hermes_cli.kanban_scope_heartbeat import scope_heartbeats
    import hashlib
    s = sources
    s.obligation()
    task = dict(id='t_ledger', status=status)
    if status == 'archived' or verified:
        task['origin_observation'] = dict(schema=1, verified=verified, observed_at=NOW,
            task_hash=hashlib.sha256(ledger._json(task).encode()).hexdigest())
    s.put(task)
    s.sql('ledger', 'UPDATE origin_obligations SET task_json=?,released=?', (json.dumps(task), released))
    projected = ledger.project(s.paths['ledger'], replica=s.paths['replica'],
                               gateway=GW, profile=OWNER['profile'], now=NOW)
    assert (projected[0]['status'] != 'unknown') is known
    assert bool(s.capture(validate_binding=ledger.validate_binding, scope_heartbeats=scope_heartbeats)) is known


@pytest.mark.parametrize('mutation', [
    "UPDATE replica SET version=2 WHERE coll='tasks'",
    "UPDATE replica SET data='{\"id\":\"t_ledger\",\"status\":\"running\"}' WHERE coll='tasks'",
])
def test_busy_ledger_retained_mismatch_matches_canonical_unknown(sources, mutation):
    ledger = pytest.importorskip('hermes_cli.kanban_origin_ledger')
    from hermes_cli.kanban_scope_heartbeat import scope_heartbeats
    import sqlite3
    s = sources
    s.obligation()
    s.sql('replica', mutation)
    # Real writer contention prevents canonical refresh; retained observations
    # must fail closed without capture trying a write of its own.
    with closing(sqlite3.connect(s.paths['ledger'])) as writer:
        writer.execute('BEGIN IMMEDIATE')
        projected = ledger.project(s.paths['ledger'], replica=s.paths['replica'],
                                   gateway=GW, profile=OWNER['profile'], now=NOW)
        assert [(r['status'], r['stale']) for r in projected] == [('unknown', True)]
        assert s.capture(validate_binding=ledger.validate_binding, scope_heartbeats=scope_heartbeats) is None
