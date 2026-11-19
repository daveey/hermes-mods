"""Gate D must retain the whole identity, including courier-owned origins."""
import importlib.util
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def receipt_fixture(tmp_path, monkeypatch, defect=None):
    p = board(tmp_path, monkeypatch)
    owner = dict(gateway='owner-node', profile='default', board='default', task_id=TASK)
    origin = dict(gateway='origin-node', profile='default', session=SID)
    binding = dict(binding_id='binding-example', version=1, publication_version=2,
                   owner=owner, origin=origin, task_record_key=f'default/default/{TASK}')
    if defect in ('owner_gateway', 'owner_profile', 'owner_board', 'owner_task_id'):
        binding['owner'] = {**owner, defect[6:]: 'wrong-value'}
    if defect in ('origin_gateway', 'origin_profile', 'origin_session'):
        binding['origin'] = {**origin, defect[7:]: 'wrong-value'}
    digest = hashlib.sha256(canonical(binding).encode()).hexdigest()
    key = canonical([binding['owner'], binding['binding_id']])
    ack = dict(status='accepted', request_id='origin-bind-example', binding_hash=digest,
               binding_id=binding['binding_id'], owner=binding['owner'], origin=binding['origin'],
               resolved_session=SID, publication_version=3)
    if defect == 'receipt_status': ack['status'] = 'pending'
    if defect == 'receipt_hash': ack['binding_hash'] = '0' * 64
    if defect == 'receipt_request': ack['request_id'] = 'wrong-request'
    if defect == 'receipt_version': ack['publication_version'] = 1
    if defect == 'receipt_session': ack['resolved_session'] = 'wrong-session'
    marker = {k: v for k, v in binding.items() if k != 'publication_version'}
    payload = dict(id=TASK, gateway=owner['gateway'], profile='default', board='default',
                   status='blocked', origin_bindings=[marker])
    if defect == 'marker': payload['origin_bindings'] = []
    if defect == 'marker_version': payload['origin_bindings'] = [{**marker, 'version': 2}]
    ledger = tmp_path / 'origin-ledger.db'
    with closing(sqlite3.connect(ledger)) as c, c:
        c.execute('CREATE TABLE origin_obligations (binding_id TEXT,binding_hash TEXT,binding_json TEXT,resolved_session TEXT,task_json TEXT,observation_version INTEGER,released INTEGER)')
        c.execute('CREATE TABLE origin_receipts (request_id TEXT,binding_hash TEXT,receipt_json TEXT)')
        c.execute('INSERT INTO origin_obligations VALUES (?,?,?,?,?,?,?)',
                  (key, digest if defect != 'binding_hash' else 'bad', canonical(binding), SID,
                   canonical(payload), 9, int(defect == 'released')))
        if defect != 'no_receipt':
            c.execute('INSERT INTO origin_receipts VALUES (?,?,?)', ('origin-bind-example', digest, canonical(ack)))
    replica = tmp_path / 'hub.db'
    with closing(sqlite3.connect(replica)) as c, c:
        c.execute('CREATE TABLE meta (k TEXT,v TEXT)')
        c.executemany('INSERT INTO meta VALUES (?,?)', [('last_ok', str(time.time() - (1000 if defect == 'stale' else 0))),
                                                       ('offline_since', '1' if defect == 'offline' else '')])
        c.execute('CREATE TABLE replica (gw TEXT,coll TEXT,key TEXT,version INTEGER,data TEXT)')
        if defect != 'no_publication':
            c.execute('INSERT INTO replica VALUES (?,?,?,?,?)', ('owner-node', 'tasks', f'default/default/{TASK}',
                      1 if defect == 'old_publication' else 9, canonical(payload)))
    context = dict(owner=owner, gateway='origin-node', profile='default', ledger=ledger, replica=replica,
                   owner_live=lambda c, task, expected, now: defect != 'owner_stale')
    monkeypatch.setattr(core, '_origin_receipt_context', lambda *args: context, raising=False)
    return p


def test_accepted_courier_receipt_reaches_origin_without_shadow_subscription(tmp_path, monkeypatch):
    p = receipt_fixture(tmp_path, monkeypatch)
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF) is None


@pytest.mark.parametrize('defect', ['owner_gateway', 'owner_profile', 'owner_board', 'owner_task_id',
    'origin_gateway', 'origin_profile', 'origin_session', 'receipt_status', 'receipt_hash',
    'receipt_request', 'receipt_version', 'receipt_session', 'binding_hash', 'released', 'no_receipt',
    'marker', 'marker_version', 'stale', 'offline', 'no_publication', 'old_publication', 'owner_stale'])
def test_invalid_courier_evidence_cannot_authorize_completion(tmp_path, monkeypatch, defect):
    p = receipt_fixture(tmp_path, monkeypatch, defect)
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)
spec = importlib.util.spec_from_file_location('receipt_gate_core', Path(__file__).parents[1] / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
SID = 'local-' + 'a' * 64
TASK = 't_1234567890abcdef'
REF = f'default@origin-node/{SID}'


def board(tmp_path, monkeypatch):
    p = tmp_path / 'kanban.db'
    with closing(sqlite3.connect(p)) as c, c:
        c.execute('CREATE TABLE tasks (id TEXT, session_id TEXT, created_on TEXT)')
        c.execute('INSERT INTO tasks VALUES (?,?,?)', (TASK, 'local-hidden-creator', 'owner-node'))
        c.execute('CREATE TABLE task_comments (task_id TEXT, body TEXT)')
        c.execute('CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT)')
    monkeypatch.setattr(core, 'replica_paths', lambda paths: [])
    monkeypatch.setattr(core, '_gateway_names', lambda: ('origin-node', {'origin-node', 'owner-node'}))
    return p


@pytest.mark.parametrize('token', ['default@origin-node/O', 'default@origin-node/',
                                  'default@origin-node/short', 'default/O', 'default@origin-node'])
def test_short_qualified_token_cannot_backtrack_into_profile(token):
    assert core.origin_sessions('Origin chat @session:' + token) == []


def test_actual_malformed_comment_does_not_block_other_linked_origin(tmp_path, monkeypatch):
    p = board(tmp_path, monkeypatch)
    with closing(sqlite3.connect(p)) as c, c:
        c.execute('INSERT INTO task_comments VALUES (?,?)', (TASK, 'origin session @session:default@origin-node/O'))
        c.execute('INSERT INTO kanban_notify_subs VALUES (?,?,?)', (TASK, 'hub', f'origin-node/default/{SID}'))
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF) is None


@pytest.mark.parametrize('chat', [f'wrong-node/default/{SID}', f'origin-node/other/{SID}',
                                 f'origin-node/default/{SID}extra'])
def test_wrong_qualified_subscription_cannot_satisfy_origin(tmp_path, monkeypatch, chat):
    p = board(tmp_path, monkeypatch)
    with closing(sqlite3.connect(p)) as c, c:
        c.execute('INSERT INTO kanban_notify_subs VALUES (?,?,?)', (TASK, 'hub', chat))
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)


@pytest.mark.parametrize('mutation', ['observation_version', 'task_payload', 'receipt_owner', 'receipt_origin',
                                      'missing_ledger', 'bad_json'])
def test_receipt_integrity_and_freshness_negatives(tmp_path, monkeypatch, mutation):
    p = receipt_fixture(tmp_path, monkeypatch)
    ledger = tmp_path / 'origin-ledger.db'
    if mutation == 'missing_ledger':
        ledger.unlink()
    else:
        with closing(sqlite3.connect(ledger)) as c, c:
            if mutation == 'observation_version':
                c.execute('UPDATE origin_obligations SET observation_version=8')
            elif mutation == 'task_payload':
                c.execute("UPDATE origin_obligations SET task_json='{}'")
            elif mutation == 'bad_json':
                c.execute("UPDATE origin_receipts SET receipt_json='not json'")
            else:
                ack = json.loads(c.execute('SELECT receipt_json FROM origin_receipts').fetchone()[0])
                ack[mutation[8:]]['profile'] = 'foreign'
                c.execute('UPDATE origin_receipts SET receipt_json=?', (canonical(ack),))
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)


def test_gate_reads_never_write_or_initialize_stores(tmp_path, monkeypatch):
    p = receipt_fixture(tmp_path, monkeypatch)
    connections = []
    original = core.ro_connect
    def readonly(path):
        conn = original(path)
        conn.execute('PRAGMA query_only=ON')
        connections.append(conn)
        return conn
    monkeypatch.setattr(core, 'ro_connect', readonly)
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF) is None
    assert len(connections) == 3
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute('SELECT 1')


@pytest.mark.parametrize('kind', ['creator', 'tui', 'link'])
def test_other_gateway_local_evidence_is_not_qualified_membership(tmp_path, monkeypatch, kind):
    p = board(tmp_path, monkeypatch)
    monkeypatch.setattr(core, '_board_profile', lambda p: 'default')
    with closing(sqlite3.connect(p)) as c, c:
        if kind == 'creator':
            c.execute('UPDATE tasks SET session_id=?', (SID,))
        elif kind == 'tui':
            c.execute('INSERT INTO kanban_notify_subs VALUES (?,?,?)', (TASK, 'tui', SID))
        else:
            c.execute('CREATE TABLE task_chat_links (task_id TEXT, chat_gateway TEXT, chat_profile TEXT, chat_session TEXT, released INTEGER)')
            c.execute('INSERT INTO task_chat_links VALUES (?,?,?,?,0)', (TASK, 'other-node', 'default', SID))
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)


def test_shadow_fallback_preserves_board_and_owner(tmp_path, monkeypatch):
    p = board(tmp_path, monkeypatch)
    shadow = tmp_path / 'sync-shadow' / 'other.db'
    shadow.parent.mkdir()
    with closing(sqlite3.connect(shadow)) as c, c:
        c.execute('CREATE TABLE tasks (id TEXT, session_id TEXT, created_on TEXT)')
        c.execute('INSERT INTO tasks VALUES (?,?,?)', (TASK, '', 'owner-node'))
        c.execute('CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT)')
        c.execute('INSERT INTO kanban_notify_subs VALUES (?,?,?)', (TASK, 'hub', f'origin-node/default/{SID}'))
    monkeypatch.setattr(core, 'replica_paths', lambda paths: [shadow])
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)
    correct = shadow.with_name('default.db')
    shadow.rename(correct)
    monkeypatch.setattr(core, 'replica_paths', lambda paths: [correct])
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF) is None
    with closing(sqlite3.connect(correct)) as c, c:
        c.execute("UPDATE tasks SET created_on='foreign-owner'")
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)


@pytest.mark.parametrize('defect', ['', 'gateway', 'profile', 'board', 'duplicate', 'state', 'stale'])
def test_owner_scope_contract_without_new_private_helper(tmp_path, monkeypatch, defect):
    from types import SimpleNamespace
    p = receipt_fixture(tmp_path, monkeypatch)
    context = core._origin_receipt_context(TASK, p, 'owner-node')
    now = time.time()
    scope = dict(profile='default', board='default', state='ok', observed_at=now)
    info = dict(gateway='owner-node', task_scopes=[scope])
    if defect == 'gateway': info['gateway'] = 'foreign'
    if defect in ('profile', 'board', 'state'): scope[defect] = 'foreign'
    if defect == 'duplicate': info['task_scopes'].append(scope.copy())
    if defect == 'stale': scope['observed_at'] = now - 1000
    legacy_api = SimpleNamespace(scope_window=lambda info: 450,
                                 _recent=lambda stamp, now, window=450: stamp is not None and 0 <= now - stamp <= window,
                                 _owner_evidence=lambda task: {})
    with closing(sqlite3.connect(context['replica'])) as c, c:
        c.execute('INSERT INTO replica VALUES (?,?,?,?,?)', ('owner-node', 'gateway', 'info', 1, canonical(info)))
    with closing(core.ro_connect(context['replica'])) as c:
        assert core._sibling('origin_receipts').owner_live(c, {}, context['owner'], now, legacy_api) == (not defect)


def test_unlinked_human_origin_is_still_required(tmp_path, monkeypatch):
    p = board(tmp_path, monkeypatch)
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)


def test_foreign_sent_note_cannot_satisfy_qualified_origin(tmp_path, monkeypatch):
    p = board(tmp_path, monkeypatch)
    with closing(sqlite3.connect(p)) as c, c:
        c.execute('INSERT INTO task_comments VALUES (?,?)',
                  (TASK, f'follow-through: SENT @session:other@origin-node/{SID}; delivery verified'))
    assert core.check_origin_reached(TASK, p, 'Origin chat @session:' + REF)
