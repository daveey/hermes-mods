"""A presentation revision cannot erase a projected source's withdrawal."""
import json
import sqlite3

import pytest

from test_kanban_mirror import fixture, OWNER
from test_kanban_withdrawn_scope import fresh_producer


@pytest.mark.parametrize('legacy', [False, True])
def test_withdraw_revise_closeout_retry_retains_source_scope(tmp_path, legacy):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.requests(OWNER)[0]
    store.withdraw(OWNER, original['requestId'], by='agent', reason='Keep held; no human work')
    if legacy:
        # Upgrade an old store with only the mutable lifecycle withdrawal.
        with store._tx() as c:
            c.execute('DELETE FROM withdrawal_history')
        mirror, store = fresh_producer(mirror, tmp_path)
    store.revise(OWNER, original['requestId'], 1, dict(original, title='Presentation refresh'))
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO task_comments(task_id,author,body) VALUES ('t_test','owner','Retain incomplete')")
    mirror.reconcile(OWNER)
    mirror, store = fresh_producer(mirror, tmp_path)
    for _ in range(3):
        mirror.reconcile(OWNER)
    assert store.requests(OWNER) == []
    assert len(store.requests(OWNER, include_withdrawn=True)) == 1
    # A real source change, unlike a projection revision, remains publishable.
    hr.update(action='Choose an address for the new delivery', scope='Separate delivery')
    with sqlite3.connect(path) as c:
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
