"""Exact owner reads, not bounded catalogs or reused gateway names."""
import http.client
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('gone_test_core', Path(__file__).parents[1] / 'core.py')
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
SID = 'local-0123456789abcdef'
REF = f'@session:default@retired-worker/{SID}'
BODY = f'Origin chat: {REF}'
TASK = 't_1234567890abcdef'
KEY = f'remote:reader:default/default/{TASK}:0'


@pytest.fixture
def world(tmp_path, monkeypatch):
    now = int(time.time())
    live = tmp_path / 'kanban.db'
    with sqlite3.connect(live) as c:
        c.executescript('CREATE TABLE tasks(id TEXT, title TEXT, body TEXT, status TEXT, created_at INT); '
                        'CREATE TABLE task_comments(task_id TEXT,body TEXT);')
        c.execute('INSERT INTO tasks VALUES(?,?,?,?,?)', (TASK, 'Repair', BODY, 'running', now - 3600))
    shadow = tmp_path / 'shadow.db'
    with sqlite3.connect(shadow) as c:
        c.executescript('CREATE TABLE kanban_sync_state(mode TEXT,pulled_at REAL); '
                        'CREATE TABLE task_events(uid TEXT,kind TEXT,origin_gateway TEXT,payload TEXT,created_at INT);')
        c.execute('INSERT INTO kanban_sync_state VALUES(?,?)', ('shadow', now))
    hub = tmp_path / 'hub.db'
    with sqlite3.connect(hub) as c:
        c.executescript('CREATE TABLE meta(k TEXT,v TEXT); '
                        'CREATE TABLE replica(gw TEXT,coll TEXT,key TEXT,data TEXT); '
                        'CREATE TABLE sent(id TEXT,body TEXT,state TEXT,result TEXT,created_at REAL,done_at REAL);')
        c.executemany('INSERT INTO meta VALUES(?,?)', [('last_ok', str(now)), ('cursor', '42'), ('head', '42')])
        for gw in ['reader', 'retired-worker']:
            info = {'gateway': gw, 'profiles': ['default', 'archive'],
                    'task_scopes': [{'profile': 'default', 'board': 'default', 'state': 'ok', 'observed_at': now}]}
            c.execute('INSERT INTO replica VALUES(?,?,?,?)', (gw, 'gateway', 'info', json.dumps(info)))
            for profile in info['profiles']:
                rid = f'gone-{gw}-{profile}'
                envelope = {'id': rid, 'to_gw': gw, 'coll': 'sessions', 'key': f'{profile}/{SID}',
                            'action': 'notice-receipts', 'args': {'keys': KEY}}
                result = {'code': 'notice_receipts', 'profile': profile, 'session': SID,
                          'keys': [KEY], 'session_exists': False, 'delivered': []}
                c.execute('INSERT INTO sent VALUES(?,?,?,?,?,?)',
                          (rid, json.dumps(envelope), 'done', json.dumps(result), now - 20, now - 19))
                envelope['id'] = rid + '-round2'
                c.execute('INSERT INTO sent VALUES(?,?,?,?,?,?)',
                          (envelope['id'], json.dumps(envelope), 'done', json.dumps(result), now - 10, now - 9))
    registry = {'me': 'reader', 'machines': [{'name': gw, 'last_seen': now * 1000} for gw in ['reader', 'retired-worker']]}
    gone = core._sibling('origin_gone')
    monkeypatch.setattr(gone, 'runtime', lambda: (hub, registry))
    monkeypatch.setattr(core, 'replica_paths', lambda paths: [shadow])
    monkeypatch.setattr(core, '_origin_receipt_context', lambda *args: None)
    return live, shadow, hub, registry


def check(world, metadata=None):
    return core.check_origin_reached(TASK, world[0], BODY, metadata=metadata)


def note(world):
    evidence = core._sibling('origin_gone').evidence([('retired-worker', 'default', SID)], [world[1]], core.ro_connect,
                                                    task_created_at=int(time.time()) - 3600)
    return evidence.get(('retired-worker', 'default', SID))


@pytest.mark.parametrize('inventory', [True, False])
def test_termination_before_card_or_later_inventory_is_unknown(world, inventory):
    now = int(time.time())
    world[3]['machines'].pop()
    event = {'from': 'retired-worker', 'to': 'reader', 'proof': {
        'instance_id': 'i-0123456789abcdef0', 'ec2_state': 'terminated', 'checked_at': now - 86400}}
    with sqlite3.connect(world[1]) as c:
        c.execute('INSERT INTO task_events VALUES(?,?,?,?,?)',
                  ('old-incarnation', 'rehomed', 'server', json.dumps(event), now - 86400))
    with sqlite3.connect(world[2]) as c:
        if inventory:
            info = json.loads(c.execute("SELECT data FROM replica WHERE gw='retired-worker'").fetchone()[0])
            info['task_scopes'][0]['observed_at'] = now - 3600
            c.execute("UPDATE replica SET data=? WHERE gw='retired-worker'", (json.dumps(info),))
        else:
            c.execute("DELETE FROM replica WHERE gw='retired-worker'")
    assert note(world) is None


def test_production_length_sequential_sweeps(world):
    now = time.time()
    with sqlite3.connect(world[2]) as c:
        c.execute("UPDATE sent SET created_at=?,done_at=? WHERE id NOT LIKE '%round2'", (now-240, now-120))
        c.execute("UPDATE sent SET created_at=?,done_at=? WHERE id LIKE '%round2'", (now-119, now-1))
    assert note(world)


def test_inventory_after_post_card_termination_refuses(world):
    now = int(time.time())
    world[3]['machines'].pop()
    event = {'from': 'retired-worker', 'to': 'reader', 'proof': {
        'instance_id': 'i-0123456789abcdef0', 'ec2_state': 'terminated', 'checked_at': now - 600}}
    with sqlite3.connect(world[1]) as c:
        c.execute('INSERT INTO task_events VALUES(?,?,?,?,?)',
                  ('post-card-old-incarnation', 'rehomed', 'server', json.dumps(event), now - 600))
    with sqlite3.connect(world[2]) as c:
        info = json.loads(c.execute("SELECT data FROM replica WHERE gw='retired-worker'").fetchone()[0])
        info['task_scopes'][0]['observed_at'] = now - 300
        c.execute("UPDATE replica SET data=? WHERE gw='retired-worker'", (json.dumps(info),))
    assert note(world) is None


@pytest.mark.parametrize('first,second', [(601, 10), (400, 181)])
def test_bounded_sweeps_still_expire(world, first, second):
    now = time.time()
    with sqlite3.connect(world[2]) as c:
        c.execute("UPDATE sent SET created_at=?,done_at=? WHERE id NOT LIKE '%round2'", (now-first, now-second-2))
        c.execute("UPDATE sent SET created_at=?,done_at=? WHERE id LIKE '%round2'", (now-second, now-1))
    assert note(world) is None


def test_reused_gateway_exact_absence_passes_only_with_note(world):
    n = note(world)
    assert n and 'follow-through: origin chat gone' in n and 'gone-retired-worker-archive' in n
    assert check(world)
    assert check(world, {'origin_note': n}) is None
    with sqlite3.connect(world[0]) as c:
        c.execute('INSERT INTO task_comments VALUES(?,?)', (TASK, n))
    assert check(world) is None


@pytest.mark.parametrize('case', ['human-desktop', 'human-cli', 'human-tui', 'other-profile-owner',
                                 'pending', 'rejected', 'missing-receipt', 'stale-receipt', 'future-receipt',
                                 'wrong-echo', 'wrong-envelope', 'invalid-bool', 'delivered',
                                 'stale-hub', 'behind-hub', 'offline-hub', 'missing-hub',
                                 'stale-board', 'missing-board', 'missing-info', 'stale-info', 'empty-profiles',
                                 'new-profile', 'new-gateway', 'missing-me', 'unregistered-me',
                                 'empty-membership', 'offline-gateway'])
def test_unknown_or_live_still_refuses(world, case):
    n = note(world)
    live, shadow, hub, registry = world
    if case == 'missing-hub':
        hub.unlink()
    elif case == 'missing-board':
        shadow.unlink()
    elif case == 'stale-board':
        with sqlite3.connect(shadow) as c:
            c.execute('UPDATE kanban_sync_state SET pulled_at=0')
    elif case == 'missing-me':
        registry.pop('me')
    elif case == 'unregistered-me':
        registry['me'] = 'other'
    elif case == 'empty-membership':
        registry['machines'] = []
    elif case == 'new-gateway':
        registry['machines'].append({'name': 'new-worker', 'last_seen': time.time() * 1000})
    elif case == 'offline-gateway':
        registry['machines'][1]['last_seen'] = 0
    else:
        with sqlite3.connect(hub) as c:
            if case == 'missing-receipt':
                c.execute("DELETE FROM sent WHERE id='gone-retired-worker-archive'")
            elif case in ['pending', 'rejected']:
                c.execute('UPDATE sent SET state=?', (case,))
            elif case in ['stale-receipt', 'future-receipt']:
                c.execute('UPDATE sent SET created_at=?,done_at=?', (0, 0) if case == 'stale-receipt' else (time.time()+1000, time.time()+1000))
            elif case in ['stale-hub', 'behind-hub', 'offline-hub']:
                k, v = {'stale-hub': ('last_ok', '0'), 'behind-hub': ('head', '43'), 'offline-hub': ('offline_since', '1')}[case]
                c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, v)) if case == 'offline-hub' else c.execute('UPDATE meta SET v=? WHERE k=?', (v,k))
            elif case == 'missing-info':
                c.execute("DELETE FROM replica WHERE gw='retired-worker'")
            elif case in ['stale-info', 'empty-profiles', 'new-profile']:
                info = json.loads(c.execute("SELECT data FROM replica WHERE gw='retired-worker'").fetchone()[0])
                if case == 'stale-info':
                    info['task_scopes'][0]['observed_at'] = 0
                elif case == 'empty-profiles':
                    info['profiles'] = []
                else:
                    info['profiles'].append('new-profile')
                c.execute("UPDATE replica SET data=? WHERE gw='retired-worker'", (json.dumps(info),))
            else:
                rid = 'gone-retired-worker-archive' if case == 'other-profile-owner' else 'gone-retired-worker-default'
                raw_body, raw_result = c.execute('SELECT body,result FROM sent WHERE id=?', (rid,)).fetchone()
                b, r = json.loads(raw_body), json.loads(raw_result)
                if case.startswith('human-') or case == 'other-profile-owner':
                    r['session_exists'] = True
                elif case == 'wrong-echo':
                    r['session'] = 'different-session'
                elif case == 'wrong-envelope':
                    b['to_gw'] = 'other'
                elif case == 'invalid-bool':
                    r['session_exists'] = 0
                elif case == 'delivered':
                    r['delivered'] = [KEY]
                c.execute('UPDATE sent SET body=?,result=? WHERE id=?', (json.dumps(b), json.dumps(r), rid))
    assert check(world, {'origin_note': n}), case
    assert note(world) is None, case


class Resync(Exception):
    pass


@pytest.mark.parametrize('error', [RuntimeError('registry unreachable'), Resync('registry reset'), http.client.IncompleteRead(b'')])
def test_failed_registry_read_is_unknown(world, monkeypatch, error):
    def fail():
        raise error
    monkeypatch.setattr(core._sibling('origin_gone'), 'runtime', fail)
    assert check(world)


def test_read_only_connections_closed(world, monkeypatch):
    opened = []
    original = core.ro_connect
    def query_only(path):
        c = original(path)
        c.execute('PRAGMA query_only=ON')
        opened.append(c)
        return c
    monkeypatch.setattr(core, 'ro_connect', query_only)
    n = note(world)
    assert n
    assert check(world, {'origin_note': n}) is None
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute('SELECT 1')


def test_copied_note_never_authorizes_changed_proof(world):
    n = note(world)
    assert n
    with sqlite3.connect(world[2]) as c:
        c.execute("UPDATE sent SET id='replacement' WHERE id='gone-reader-default'")
    assert check(world, {'origin_note': n})


def test_worker_refusal_has_actionable_agent_route(world):
    assert 'kanban_supervisor_request' in check(world)


@pytest.mark.parametrize('state', ['terminated', 'running', 'missing'])
def test_terminated_gateway_requires_proof_and_all_other_exact_reads(world, state):
    world[3]['machines'].pop()
    with sqlite3.connect(world[2]) as c:
        c.execute("DELETE FROM replica WHERE gw='retired-worker'")
    if state != 'missing':
        now = int(time.time())
        event = {'from': 'retired-worker', 'to': 'reader', 'proof': {
            'instance_id': 'i-0123456789abcdef0', 'ec2_state': state, 'checked_at': now - 60}}
        with sqlite3.connect(world[1]) as c:
            c.execute('INSERT INTO task_events VALUES(?,?,?,?,?)',
                      ('termination-record', 'rehomed', 'server', json.dumps(event), now - 60))
    n = note(world)
    if state == 'terminated':
        assert n and 'termination-record' in n
        assert check(world, {'origin_note': n}) is None
    else:
        assert n is None
        assert check(world)


def test_publishing_nonmember_must_also_answer(world):
    world[3]['machines'].pop()
    assert note(world)
    with sqlite3.connect(world[2]) as c:
        c.execute("DELETE FROM sent WHERE id LIKE 'gone-retired-worker%'")
    assert note(world) is None


def test_complete_second_round_is_required(world):
    with sqlite3.connect(world[2]) as c:
        c.execute("DELETE FROM sent WHERE id LIKE '%round2'")
    assert note(world) is None
