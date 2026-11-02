"""Isolated SQLite contract tests; not evidence of live Kanban mutation."""
from contextlib import contextmanager
import copy
import json
import sqlite3
import pytest
from test_delivery import cards
from test_decisions import OWNER, store, request, decision


class SQLiteCards:
    fail = False

    def connect(self, path):
        return sqlite3.connect(path)

    @contextmanager
    def write_txn(self, c):
        c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise

    def add_comment(self, c, task_id, author, body):
        cid = c.execute('INSERT INTO task_comments(task_id,author,body) VALUES (?,?,?)', (task_id,author,body)).lastrowid
        if self.fail: raise RuntimeError('failure after comment insert')
        return cid

    def archive_task(self, c, task_id):
        with self.write_txn(c):
            return c.execute("UPDATE tasks SET status='archived' WHERE id=? AND status!='archived'",
                             (task_id,)).rowcount == 1


def setup(tmp_path, effect='revise', bind=True):
    path = tmp_path / 'kanban.db'
    ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', board='default', id='card')
    s = store(tmp_path); r = s.create(OWNER, request(aliases=[ref]))
    backend = SQLiteCards()
    adapter = cards.QualifiedCardDelivery(home=tmp_path, backend=backend)
    with sqlite3.connect(path) as c:
        c.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,body TEXT,session_id TEXT,idempotency_key TEXT,claim_lock TEXT,current_run_id INTEGER); CREATE TABLE task_comments(id INTEGER PRIMARY KEY,task_id TEXT,author TEXT,body TEXT); CREATE TABLE task_deps(parent TEXT,child TEXT);')
        c.execute("INSERT INTO tasks VALUES ('card','blocked','scope',?,?,NULL,NULL)", (OWNER['storedSessionId'], adapter.creation_key(r)))
        c.execute("INSERT INTO tasks VALUES ('child','todo','dependent',NULL,NULL,NULL,NULL)")
        c.execute("INSERT INTO task_deps VALUES ('card','child')")
    if bind: adapter.bind(r, ref)
    op = s.decide(OWNER, decision(r, choice=effect, text='Add tests'))['operation']
    return adapter, backend, path, op


@pytest.mark.parametrize('effect', ['approve', 'revise', 'decline'])
def test_atomic_retry_exact_authority_and_only_closes_the_card(tmp_path, effect):
    adapter, backend, path, op = setup(tmp_path, effect)
    with sqlite3.connect(path) as c:
        before = c.execute("SELECT * FROM tasks WHERE id!='card'").fetchall()
    backend.fail = True
    with pytest.raises(RuntimeError): adapter.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 0
        assert c.execute('SELECT state FROM user_steps_card_bindings').fetchone()[0] == 'bound'
    backend.fail = False
    first = adapter.deliver(op)
    assert adapter.deliver(op) == first
    assert adapter.readback(op, first)
    with sqlite3.connect(path) as c:
        # The decided card is archived (it no longer asks David); every other row is untouched.
        assert c.execute("SELECT status FROM tasks WHERE id='card'").fetchone()[0] == 'archived'
        assert c.execute("SELECT * FROM tasks WHERE id!='card'").fetchall() == before
        assert c.execute('SELECT * FROM task_deps').fetchall() == [('card', 'child')]
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1
        state, metadata = c.execute('SELECT state,decision FROM user_steps_card_bindings').fetchone()
        assert state == effect
        assert json.loads(metadata)['intent'] == op['intent']
        c.execute("UPDATE task_comments SET body='tampered'")
    assert not adapter.readback(op, first)


def test_alias_without_producer_binding_is_not_authority(tmp_path):
    adapter, _, path, op = setup(tmp_path, bind=False)
    with pytest.raises(RuntimeError, match='binding'): adapter.deliver(op)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET idempotency_key='unrelated' WHERE id='card'")
    with pytest.raises(RuntimeError, match='producer'): adapter.bind(op['intent']['request'], op['intent']['request']['aliases'][0])


@pytest.mark.parametrize('change', ['scope', 'owner', 'revision', 'card', 'operation'])
def test_cas_rejects_changed_identity_or_scope(tmp_path, change):
    adapter, _, path, op = setup(tmp_path)
    if change == 'scope': op['intent']['request']['scope']['markdown'] = 'Other scope'
    elif change == 'owner': op['owner']['storedSessionId'] = 'other-session'
    elif change == 'revision': op['intent']['request']['revision'] += 1
    elif change == 'card':
        with sqlite3.connect(path) as c: c.execute("UPDATE tasks SET body='edited' WHERE id='card'")
    else:
        adapter.deliver(op)
        op['operationId'] = 'competing-operation'
    with pytest.raises(RuntimeError): adapter.deliver(op)


@pytest.mark.parametrize('field,value', [('gatewayId','other'), ('profile','other'), ('board','../default')])
def test_qualified_alias_cannot_escape_owner_or_board(tmp_path, field, value):
    adapter, _, _, op = setup(tmp_path)
    op['intent']['request']['aliases'][0][field] = value
    with pytest.raises(RuntimeError): adapter.deliver(op)


def test_binding_cas_revision_and_restart(tmp_path):
    adapter, backend, path, op = setup(tmp_path)
    r = op['intent']['request']; ref = r['aliases'][0]
    assert adapter.bind(r, ref) == adapter.bind(r, ref)
    replacement = copy.deepcopy(r); replacement['revision'] = 2
    replacement['scope']['markdown'] = 'Replacement exact scope'
    adapter.bind(replacement, ref, expected_revision=1)
    with pytest.raises(RuntimeError): adapter.deliver(op)
    with pytest.raises(RuntimeError): adapter.bind(r, ref)
    restarted = cards.QualifiedCardDelivery(home=tmp_path, backend=backend)
    assert restarted.bind(replacement, ref, expected_revision=1)


def test_restart_reconcile_lost_card_reply(tmp_path):
    adapter, backend, _, op = setup(tmp_path, 'decline')
    adapter.deliver(op)  # committed; caller lost response before ActionStore dispatch
    restarted = cards.QualifiedCardDelivery(home=tmp_path, backend=backend)
    class NoRuns:
        def deliver(self, op): raise AssertionError('Decline must never wake')
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.sqlite3', runs=NoRuns(), card_receiver=restarted)
    results = store(tmp_path).reconcile(OWNER, receiver)
    assert results[0]['operation']['dispatchState'] == 'delivered'
    assert receiver.readback(op, results[0]['operation']['receipt'])


def test_duplicate_delivery_race_one_comment(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    adapter, _, path, op = setup(tmp_path, 'approve')
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: adapter.deliver(op), range(8)))
    assert all(receipt == receipts[0] for receipt in receipts)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1
        c.execute("UPDATE user_steps_card_bindings SET decision='{}'")
    assert not adapter.readback(op, receipts[0])


@pytest.mark.parametrize('field,value', [('status', 'running'), ('claim_lock', 'claim'), ('current_run_id', 1)])
def test_live_execution_cannot_receive_decision(tmp_path, field, value):
    adapter, _, path, op = setup(tmp_path, 'decline')
    with sqlite3.connect(path) as c:
        c.execute(f'UPDATE tasks SET {field}=? WHERE id=?', (value, 'card'))
    with pytest.raises(RuntimeError): adapter.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 0


REPLICATION = {'sync_version': 2, 'lease_version': 2, 'created_on': 'macbook', 'field_hlc': 'hlc', 'ready_since': 5}


def _add_replication_columns(path):
    with sqlite3.connect(path) as c:
        for col in REPLICATION:
            c.execute(f'ALTER TABLE tasks ADD COLUMN {col}')


def test_replication_bookkeeping_does_not_break_the_binding_cas(tmp_path):
    """Board-service sync stamps sync/lease columns on a bound card with no task event (t_01a199c0a2e3ea8b:
    an approved decision then failed 'Card binding CAS conflict' 298 times). Bookkeeping is not a card edit."""
    adapter, _, path, op = setup(tmp_path, 'approve')
    _add_replication_columns(path)  # a schema migration after binding
    with sqlite3.connect(path) as c:
        for col, value in REPLICATION.items():
            c.execute(f"UPDATE tasks SET {col}=? WHERE id='card'", (value,))
    adapter.validate(op)
    receipt = adapter.deliver(op)
    assert adapter.readback(op, receipt)


def test_legacy_full_row_fingerprint_survives_later_sync_stamps(tmp_path):
    """A binding written by the old full-row fingerprint (sync columns NULL at bind time) still verifies after
    the sync stamps them, so the stuck bindings already in the field deliver too."""
    adapter, _, path, op = setup(tmp_path, 'approve', bind=False)
    _add_replication_columns(path)
    r = op['intent']['request']
    adapter.bind(r, r['aliases'][0])
    with sqlite3.connect(path) as c:
        cur = c.execute("SELECT * FROM tasks WHERE id='card'")
        task = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        import hashlib
        legacy = hashlib.sha256(cards.DecisionInbox._encode(task).encode()).hexdigest()
        c.execute("UPDATE user_steps_card_bindings SET fingerprint=? WHERE task='card'", (legacy,))
        for col, value in REPLICATION.items():
            c.execute(f"UPDATE tasks SET {col}=? WHERE id='card'", (value,))
    adapter.validate(op)
    assert adapter.readback(op, adapter.deliver(op))


def test_legacy_fingerprint_still_rejects_a_real_card_edit(tmp_path):
    adapter, _, path, op = setup(tmp_path, 'approve', bind=False)
    _add_replication_columns(path)
    r = op['intent']['request']
    adapter.bind(r, r['aliases'][0])
    with sqlite3.connect(path) as c:
        cur = c.execute("SELECT * FROM tasks WHERE id='card'")
        task = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        import hashlib
        legacy = hashlib.sha256(cards.DecisionInbox._encode(task).encode()).hexdigest()
        c.execute("UPDATE user_steps_card_bindings SET fingerprint=? WHERE task='card'", (legacy,))
        c.execute("UPDATE tasks SET body='edited', sync_version=3 WHERE id='card'")
    with pytest.raises(RuntimeError, match='CAS'): adapter.validate(op)
    with pytest.raises(RuntimeError, match='CAS'): adapter.deliver(op)
