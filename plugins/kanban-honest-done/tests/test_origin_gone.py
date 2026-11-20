"""Destroyed gateway exception: notes never replace independent evidence."""
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
UID = 'rehome-proof-123'


@pytest.fixture
def world(tmp_path, monkeypatch):
    now = int(time.time())
    live = tmp_path / 'kanban.db'
    with sqlite3.connect(live) as c:
        c.executescript('CREATE TABLE tasks(id TEXT, title TEXT, body TEXT, status TEXT); '
                        'CREATE TABLE task_comments(task_id TEXT,body TEXT);')
        c.execute('INSERT INTO tasks VALUES(?,?,?,?)', (TASK, 'Repair', BODY, 'running'))
    shadow = tmp_path / 'shadow.db'
    payload = {'from': 'retired-worker', 'to': 'hub', 'proof': {
        'instance_id': 'i-0123456789abcdef0', 'ec2_state': 'terminated', 'checked_at': now - 60}}
    with sqlite3.connect(shadow) as c:
        c.executescript('CREATE TABLE kanban_sync_state(mode TEXT,pulled_at REAL); '
                        'CREATE TABLE task_events(uid TEXT,kind TEXT,origin_gateway TEXT,payload TEXT,created_at INT);')
        c.execute('INSERT INTO kanban_sync_state VALUES(?,?)', ('shadow', now))
        c.execute('INSERT INTO task_events VALUES(?,?,?,?,?)',
                  (UID, 'rehomed', 'server', json.dumps(payload), now - 60))
    hub = tmp_path / 'hub.db'
    with sqlite3.connect(hub) as c:
        c.executescript('CREATE TABLE meta(k TEXT,v TEXT); CREATE TABLE replica(gw TEXT,coll TEXT,key TEXT,data TEXT);')
        c.executemany('INSERT INTO meta VALUES(?,?)', [('last_ok', str(now)), ('cursor', '42'), ('head', '42')])
    registry = {'machines': []}
    gone = core._sibling('origin_gone')
    monkeypatch.setattr(gone, 'runtime', lambda: (hub, registry))
    monkeypatch.setattr(core, 'replica_paths', lambda paths: [shadow])
    monkeypatch.setattr(core, '_origin_receipt_context', lambda *args: None)
    note = (f'follow-through: origin chat gone {REF}; server rehomed {UID}; '
            f'instance i-0123456789abcdef0; terminated checked_at={now - 60}; '
            'fresh registry has no active gateway; fresh session replica has no owner')
    # A comment is already durable, so check_origin_reached can remain read-only.
    with sqlite3.connect(live) as c:
        c.execute('INSERT INTO task_comments VALUES(?,?)', (TASK, note))
    return live, shadow, hub, registry, note


def check(world):
    return core.check_origin_reached(TASK, world[0], BODY)


def test_terminated_and_unowned_with_durable_note_passes(world):
    assert check(world) is None


def test_note_required_and_hint_is_exact(world):
    with sqlite3.connect(world[0]) as c:
        c.execute('DELETE FROM task_comments')
    why = check(world)
    assert why and world[4] in why


@pytest.mark.parametrize('case', ['active', 'human-desktop', 'human-cli', 'human-tui', 'other-owner',
                                 'stale-hub', 'behind-hub', 'missing-hub', 'stale-board', 'missing-board',
                                 'not-server', 'offline-only', 'malformed', 'future-proof', 'wrong-gateway'])
def test_unknown_or_live_still_refuses(world, case):
    live, shadow, hub, registry, note = world
    if case == 'active':
        registry['machines'] = [{'name': 'retired-worker', 'state': 'active'}]
    elif case.startswith('human-') or case == 'other-owner':
        with sqlite3.connect(hub) as c:
            gw = 'another-live-gateway' if case == 'other-owner' else 'retired-worker'
            c.execute('INSERT INTO replica VALUES(?,?,?,?)', (gw, 'sessions', f'default/{SID}',
                      json.dumps({'id': SID, 'source': case.removeprefix('human-')})))
    elif case == 'missing-hub':
        hub.unlink()
    elif case == 'missing-board':
        shadow.unlink()
    elif case in ('stale-hub', 'behind-hub'):
        with sqlite3.connect(hub) as c:
            c.execute('UPDATE meta SET v=? WHERE k=?',
                      ('0' if case == 'stale-hub' else '43', 'last_ok' if case == 'stale-hub' else 'head'))
    elif case == 'stale-board':
        with sqlite3.connect(shadow) as c:
            c.execute('UPDATE kanban_sync_state SET pulled_at=0')
    else:
        with sqlite3.connect(shadow) as c:
            if case == 'not-server':
                c.execute("UPDATE task_events SET origin_gateway='worker'")
            else:
                event = json.loads(c.execute('SELECT payload FROM task_events').fetchone()[0])
                if case == 'offline-only':
                    event['proof']['ec2_state'] = 'running'
                elif case == 'malformed':
                    event['proof'] = 'terminated'
                elif case == 'future-proof':
                    event['proof']['checked_at'] = int(time.time()) + 1000
                else:
                    event['from'] = 'different-gateway'
                c.execute('UPDATE task_events SET payload=?', (json.dumps(event),))
    assert check(world), case


def test_failed_registry_read_is_unknown(world, monkeypatch):
    def fail():
        raise RuntimeError('registry unreachable')
    monkeypatch.setattr(core._sibling('origin_gone'), 'runtime', fail)
    assert check(world)


def test_completion_metadata_note_is_accepted(world):
    with sqlite3.connect(world[0]) as c:
        c.execute('DELETE FROM task_comments')
    assert core.check_complete(TASK, 'Shipped.', [world[0]], metadata={'origin_note': world[4]}) is None


def test_worker_refusal_has_actionable_agent_route(world):
    world[3]['machines'] = [{'name': 'retired-worker'}]
    why = check(world)
    assert 'kanban_supervisor_request' in why
    assert 'kanban_block this card with that error' not in why


def test_read_only_and_no_obsolete_adoption_hint(world, monkeypatch):
    opened = []
    original = core.ro_connect
    def query_only(path):
        conn = original(path)
        conn.execute('PRAGMA query_only=ON')
        opened.append(conn)
        return conn
    monkeypatch.setattr(core, 'ro_connect', query_only)
    assert check(world) is None
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute('SELECT 1')
    with sqlite3.connect(world[0]) as c:
        c.execute('DELETE FROM task_comments')
    assert 'adopt-task' not in check(world)


def test_note_for_another_proof_does_not_count(world):
    with sqlite3.connect(world[0]) as c:
        c.execute('UPDATE task_comments SET body=?', (world[4].replace(UID, 'forged-proof'),))
    assert check(world)
