"""Explicit withdrawal retires the source ask, not just one projection UUID."""
import json
import sqlite3

import pytest
from test_kanban_mirror import fixture, OWNER


@pytest.mark.parametrize('actor', ['agent', 'user', 'hygiene', 'system'])
def test_withdraw_owner_disposition_closeout_update_retry(tmp_path, actor):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.requests(OWNER)[0]
    store.withdraw(OWNER, original['requestId'], by=actor, reason='Keep held; no human action')
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE task_links(parent TEXT, child TEXT)")
        c.execute("INSERT INTO task_links VALUES ('t_test','dependent')")
        c.execute("INSERT INTO task_comments(task_id,author,body) VALUES ('t_test','owner','Retain incomplete; no repair or archive')")
        # Closeout/update changes presentation and lifecycle, not actionable scope.
        hr['evidence'] = 'Owner disposition: capability_unavailable; retained incomplete'
        hr['why_human'] = 'Historical explanation updated'
        c.execute('UPDATE tasks SET title=?, human_request=?', ('Updated title', json.dumps(hr)))
        c.execute("INSERT INTO task_events(task_id,kind) VALUES ('t_test','blocked')")
    for _ in range(3):
        mirror.reconcile(OWNER)
    assert store.requests(OWNER) == []
    assert len(store.requests(OWNER, include_withdrawn=True)) == 1
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
        assert c.execute('SELECT * FROM task_links').fetchall() == [('t_test', 'dependent')]
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1


def test_unchanged_ask_stays_withdrawn_after_fresh_producer(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    rid = store.requests(OWNER)[0]['requestId']
    store.withdraw(OWNER, rid, by='agent', reason='Retain incomplete')
    mirror, store = fresh_producer(mirror, tmp_path)
    for _ in range(3):
        mirror.reconcile(OWNER)
    assert not store.requests(OWNER)
    assert len(store.requests(OWNER, include_withdrawn=True)) == 1


def fresh_producer(mirror, tmp_path):
    store = mirror.core.ActionStore(tmp_path / 'actions.sqlite3')
    fresh = type(mirror)(mirror.core, store, boards=list(mirror.boards.items()),
                         backend=mirror.backend, session_exists=mirror.session_exists, tip=mirror.tip)
    fresh.remote.gateway = lambda: 'fixture-sync'
    return fresh, store


def test_new_human_only_scope_still_publishable(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    store.withdraw(OWNER, store.requests(OWNER)[0]['requestId'], by='agent', reason='Not needed')
    hr.update(action='Pick a delivery address', scope='A new delivery only', why_human='Only user knows the address')
    with sqlite3.connect(path) as c:
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1


def test_withdrawn_scope_survives_owner_rotation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    store.withdraw(OWNER, store.requests(OWNER)[0]['requestId'], by='agent', reason='Owner retained incomplete')
    mirror.tip = lambda sid: 'session-b'
    mirror.reconcile(dict(OWNER, storedSessionId='session-b'))
    assert not store.requests(dict(OWNER, storedSessionId='session-b'))


def test_existing_duplicate_is_retired_without_card_transition(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.requests(OWNER)[0]
    snap = mirror._read_snapshot('default', 't_test', OWNER)
    duplicate = mirror._request(OWNER, 'default', snap, 1)
    mirror._publish(OWNER, 'default', snap, duplicate)
    store.withdraw(OWNER, original['requestId'], by='agent', reason='Keep held')
    mirror.reconcile(OWNER)
    assert not store.requests(OWNER)
    assert store.get(OWNER, duplicate['requestId'])['state'] == 'withdrawn'
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'


def test_withdrawal_between_selection_and_publish_fails_closed(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.requests(OWNER)[0]
    snap = mirror._read_snapshot('default', 't_test', OWNER)
    candidate = mirror._request(OWNER, 'default', snap, 1)
    store.withdraw(OWNER, original['requestId'], by='agent', reason='No human action')
    with pytest.raises(mirror.core.ActionError, match='withdrawn source ask'):
        mirror._publish(OWNER, 'default', snap, candidate)
    assert not store.requests(OWNER)
