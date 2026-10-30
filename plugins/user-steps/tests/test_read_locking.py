"""ActionStore reads must not turn shared readers into pending writers."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('read_lock_core', Path(__file__).resolve().parents[1] / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}


@pytest.mark.parametrize('method,args', [
    ('requests', (OWNER,)), ('views', (OWNER,)), ('pending_owners', ()),
    ('undelivered', ('test-gateway', 'default')),
    ('unnotified_withdrawals', (OWNER,)),
])
def test_pure_reads_coexist_with_shared_reader(tmp_path, monkeypatch, method, args):
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    real_connect = core.sqlite3.connect

    def short_connect(*a, **kw):
        kw['timeout'] = 0.05  # Bound a deterministic lock failure; never wait 30 s in a test.
        return real_connect(*a, **kw)

    monkeypatch.setattr(core.sqlite3, 'connect', short_connect)
    reader = real_connect(store.path)
    reader.execute('BEGIN')
    reader.execute('SELECT count(*) FROM requests').fetchone()
    try:
        assert getattr(store, method)(*args) == []
        # A harmless poll must not take the RESERVED writer slot either.
        writer = real_connect(store.path, timeout=0)
        try:
            writer.execute('BEGIN IMMEDIATE')
            writer.rollback()
        finally:
            writer.close()
    finally:
        reader.rollback()
        reader.close()


@pytest.mark.parametrize('method', [
    'requests', 'views', 'pending_owners', 'undelivered', 'unnotified_withdrawals',
    'get', 'view', 'history', 'operation', 'created_at',
])
def test_populated_reads_and_constructor_under_shared_lock(tmp_path, monkeypatch, method):
    import json
    import uuid
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    rid = str(uuid.uuid4())
    body = dict(schemaVersion=2, owner=OWNER.copy(), requestId=rid, revision=1,
                kind='approval', title='Synthetic change',
                scope=dict(markdown='Change example.py', complete=True, sourceRefs=[]),
                limits=[], exclusions=[], outcomes=dict(approve='Authorize', revise='Revise', decline='Cancel'),
                choices=[dict(id=e, label=e.title(), effect=e) for e in ['approve', 'revise', 'decline']],
                aliases=[], state='open')
    store.create(OWNER, body)
    result = store.decide(OWNER, dict(requestId=rid, expectedRevision=1, choiceId='approve', idempotencyKey='k'))
    oid = result['operation']['operationId']
    expected_args = {
        'requests': (OWNER,), 'views': (OWNER,), 'pending_owners': (),
        'undelivered': ('test-gateway', 'default'), 'unnotified_withdrawals': (OWNER,),
        'get': (OWNER, rid), 'view': (OWNER, rid), 'history': (OWNER, rid),
        'operation': (OWNER, oid), 'created_at': (OWNER, rid),
    }[method]
    expected = getattr(store, method)(*expected_args)
    real_connect = core.sqlite3.connect
    def short_connect(*a, **kw):
        kw['timeout'] = 0.05
        return real_connect(*a, **kw)
    monkeypatch.setattr(core.sqlite3, 'connect', short_connect)
    reader = real_connect(store.path)
    reader.execute('BEGIN')
    reader.execute('SELECT count(*) FROM requests').fetchone()
    try:
        fresh = core.ActionStore(store.path)  # Existing-schema constructor must coexist with a reader.
        assert getattr(fresh, method)(*expected_args) == expected
        json.dumps(expected)  # Fully materialized, no cursor/connection escaping the snapshot.
    finally:
        reader.rollback()
        reader.close()


def test_read_snapshot_is_query_only_and_releases_on_error(tmp_path):
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    with pytest.raises(sqlite3.OperationalError, match='readonly'):
        with store._read() as c:
            c.execute('DELETE FROM requests')
    with store._tx() as c:
        assert c.execute('PRAGMA synchronous').fetchone()[0] == 2  # FULL remains mandatory.
        c.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL)', ('ns', 'id', 's', 1, 'open'))
