"""Terminal history must not make every cold queue poll revisit the whole namespace."""
import json
import sqlite3
from contextlib import closing

import pytest

from test_kanban_mirror import fixture, OWNER
from test_kanban_mirror_keyed import keyed, settle, runs  # noqa: F401 (fixture)


TERMINAL = ('resolved', 'superseded', 'cancelled', 'withdrawn')


def seed(tmp_path, state='withdrawn', session=None, count=1):
    mirror, store, path, _ = fixture(tmp_path)
    assert mirror._reconcile(OWNER, True)
    request = store.requests(OWNER)[0]
    with store._read() as c:
        binding = dict(c.execute('SELECT * FROM kanban_projections').fetchone())
    session = session or OWNER['storedSessionId']
    with store._tx() as c:
        c.execute('DELETE FROM kanban_projections')
        c.execute('DELETE FROM requests')
        c.execute('DELETE FROM revisions')
        for i in range(count):
            rid = f'history-{i}'
            body = dict(request, requestId=rid, owner=dict(OWNER, storedSessionId=session))
            c.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL)',
                      (store._ns(OWNER), rid, session, 1, state))
            c.execute('INSERT INTO revisions VALUES (?,?,?,?)',
                      (store._ns(OWNER), rid, 1, json.dumps(body)))
            b = dict(binding, request_id=rid, session=session, request=json.dumps(body))
            c.execute('INSERT INTO kanban_projections VALUES (?,?,?,?,?,?,?)', tuple(b.values()))
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("UPDATE tasks SET status='done',human_request=NULL")
    return mirror, store, path


@pytest.mark.parametrize('session', [OWNER['storedSessionId'], 'session-b'])
@pytest.mark.parametrize('state', [*TERMINAL, 'open', 'needs_context', None, 'future-state'])
def test_candidates_keep_unknown_and_active_but_skip_known_terminal(tmp_path, monkeypatch, session, state):
    mirror, store, _ = seed(tmp_path, state, session)
    seen = []
    monkeypatch.setattr(mirror, '_reconcile_binding', lambda own, b: seen.append((own, b)))
    assert mirror._reconcile(OWNER, True)
    assert len(seen) == (0 if state in TERMINAL else 1)
    if seen:
        assert seen[0][0] == dict(OWNER, storedSessionId=session)
        assert seen[0][1]['session'] == session


@pytest.mark.parametrize('session', [OWNER['storedSessionId'], 'session-b'])
@pytest.mark.parametrize('kind', ['missing', 'wrong-session', 'wrong-namespace'])
def test_unmatched_terminal_row_still_defers(tmp_path, kind, session):
    mirror, store, _ = seed(tmp_path, session=session)
    with store._tx() as c:
        if kind == 'missing':
            c.execute('DELETE FROM requests')
        elif kind == 'wrong-session':
            c.execute("UPDATE requests SET session='another-session'")
        else:
            c.execute("UPDATE requests SET namespace='another-namespace'")
    assert mirror._reconcile(OWNER, True) is False


@pytest.mark.parametrize('session', [OWNER['storedSessionId'], 'session-b'])
def test_terminal_history_needs_no_board_snapshot_store_get_or_tip(tmp_path, monkeypatch, session):
    mirror, _, _ = seed(tmp_path, session=session, count=200)
    def forbidden(*a, **kw):
        pytest.fail('terminal historical binding was revisited')
    monkeypatch.setattr(mirror, '_read_snapshot', forbidden)
    monkeypatch.setattr(mirror.store, 'get', forbidden)
    monkeypatch.setattr(mirror, 'tip', forbidden)
    assert mirror._reconcile(OWNER, True)


@pytest.mark.parametrize('state', ['open', 'needs_context'])
@pytest.mark.parametrize('session', [OWNER['storedSessionId'], 'session-b'])
def test_active_departed_ask_still_withdrawn(tmp_path, state, session):
    mirror, store, _ = seed(tmp_path, state, session)
    assert mirror._reconcile(OWNER, True)
    assert store.get(dict(OWNER, storedSessionId=session), 'history-0')['state'] == 'withdrawn'


def test_revival_and_terminal_race_use_authoritative_reads(tmp_path, monkeypatch):
    mirror, store, _ = seed(tmp_path)
    assert mirror._reconcile(OWNER, True)
    with store._tx() as c:
        c.execute("UPDATE requests SET state='open'")
    original = mirror._reconcile_binding
    seen = []
    def race(own, binding):
        seen.append(binding['request_id'])
        with store._tx() as c:
            c.execute("UPDATE requests SET state='withdrawn'")
        return original(own, binding)
    monkeypatch.setattr(mirror, '_reconcile_binding', race)
    assert mirror._reconcile(OWNER, True)
    assert seen == ['history-0']
    assert store.get(OWNER, 'history-0')['state'] == 'withdrawn'


def test_revival_invalidates_real_prework_memo(keyed, monkeypatch):
    k = keyed
    settle(k)
    request = k.store.requests(OWNER)[0]
    k.store.withdraw(OWNER, request['requestId'], by='user', reason='fixture retirement')
    settle(k)
    assert not runs(k)
    seen = []
    original = k.mirror._reconcile_binding
    def observe(own, binding):
        seen.append(binding['request_id'])
        return original(own, binding)
    monkeypatch.setattr(k.mirror, '_reconcile_binding', observe)
    k.store.revise(OWNER, request['requestId'], request['revision'], request)
    assert runs(k)
    assert request['requestId'] in seen


@pytest.mark.parametrize('session', [OWNER['storedSessionId'], 'session-b'])
def test_open_resolution_remains_candidate(tmp_path, monkeypatch, session):
    mirror, store, _ = seed(tmp_path, 'open', session)
    with store._tx() as c:
        c.execute('UPDATE requests SET resolution=?', (json.dumps({'operationId':'pending-operation'}),))
    seen = []
    monkeypatch.setattr(mirror, '_reconcile_binding', lambda own, b: seen.append(b['request_id']))
    assert mirror._reconcile(OWNER, True)
    assert seen == ['history-0']


def test_terminal_payload_is_not_active_reconciliation_authority(tmp_path):
    mirror, store, _ = seed(tmp_path)
    with store._tx() as c:
        c.execute("UPDATE kanban_projections SET snapshot='corrupt-history'")
        c.execute("UPDATE revisions SET body='corrupt-history'")
    # Terminal history no longer blocks unrelated queue reconciliation due to
    # incidental validation; reviving the row restores the authoritative checks.
    assert mirror._reconcile(OWNER, True)
    with store._tx() as c:
        c.execute("UPDATE requests SET state='open'")
    assert mirror._reconcile(OWNER, True) is False
