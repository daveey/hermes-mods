"""A recorded decision reaches a session blocked in clarify; busy refusals are not delivery attempts."""
import json
import sqlite3
import threading
import time

import pytest
from test_decisions import core, load_module, ROOT, OWNER, store, request, decision, BusyReceiver

cards = load_module('clarify_early_cards', ROOT / 'cards.py')


class Runs:
    def __init__(self): self.calls = []
    def deliver(self, op): self.calls.append(op); return {'destination': 'test_runtime', 'operationId': op['operationId']}
    def readback(self, op, receipt): return True


def recorded(tmp_path, *, session='session-a', key='k1', choice='approve', text=None, title=None):
    """A decision the owning session could not take because its turn is active (dispatchState=failed)."""
    s = store(tmp_path)
    owner = dict(OWNER, storedSessionId=session)
    body = request(); body['owner'] = owner
    if title: body['title'] = title
    r = s.create(owner, body)
    op = s.decide(owner, decision(r, key=key, choice=choice, text=text))['operation']
    s.dispatch(owner, op['operationId'], BusyReceiver())
    return s, owner, r, op


def provider(s, tmp_path, runs=None, db=None):
    early = load_module('clarify_early_provider', ROOT / 'clarify_early.py')
    delivery = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs or Runs())
    return early.make_provider(store=lambda: s, delivery=lambda: delivery, db=db,
                               owner_for=lambda sid: dict(OWNER, storedSessionId=sid))


# ---- (b) busy is not an attempt ----------------------------------------------------------------------------

def test_busy_refusal_does_not_count_toward_the_retry_cap(tmp_path):
    s, owner, r, op = recorded(tmp_path)
    busy = BusyReceiver()
    for _ in range(5): s.dispatch(owner, op['operationId'], busy)
    got = s.operation(owner, oid=op['operationId'])['operation']
    assert got['dispatchState'] == 'failed' and got['error'] == core.BUSY_ERROR
    assert got['attempts'] == 0 and got['busyRefusals'] == 6  # 1 from recorded() + 5
    assert len(s.reconcile(owner, busy, max_attempts=1)) == 1  # still inside any retry budget


def test_other_delivery_failures_still_count(tmp_path):
    s, owner, r, op = recorded(tmp_path)
    class Broken:
        def deliver(self, o): raise RuntimeError('transport down')
        def readback(self, o, receipt): return False
    for _ in range(3): s.dispatch(owner, op['operationId'], Broken())
    assert s.operation(owner, oid=op['operationId'])['operation']['attempts'] == 3
    assert s.reconcile(owner, Broken(), max_attempts=3) == []


# ---- (a)/(c) early answer ----------------------------------------------------------------------------------

def test_pending_decision_becomes_an_early_answer_and_commit_delivers_it_once(tmp_path):
    s, owner, r, op = recorded(tmp_path, title='Canary runner')
    runs = Runs()
    found = provider(s, tmp_path, runs)('session-a', 'call_7')
    d = found.result['decisions']
    assert found.result['status'] == 'answered_elsewhere' and len(d) == 1
    assert d[0]['requestId'] == r['requestId'] and d[0]['title'] == 'Canary runner'
    assert d[0]['choice'] == 'Approve' and d[0]['decidedAt'] == op['recordedAt'] and d[0]['via']
    # nothing is delivered until the clarify actually returns it
    assert s.operation(owner, oid=op['operationId'])['operation']['dispatchState'] == 'failed'
    found.commit()
    done = s.operation(owner, oid=op['operationId'])
    assert done['operation']['dispatchState'] == 'delivered' and done['request']['state'] == 'resolved'
    assert done['operation']['receipt']['run']['toolCallId'] == 'call_7'
    assert done['operation']['receipt']['run']['executionStarted'] is False
    assert runs.calls == []  # no turn is admitted
    assert provider(s, tmp_path, runs)('session-a', 'call_8') is None  # nothing left
    assert s.reconcile(owner, cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs)) == []
    assert runs.calls == []


def test_revise_text_is_the_choice(tmp_path):
    s, owner, r, op = recorded(tmp_path, choice='revise', text='Only the canary')
    assert provider(s, tmp_path)('session-a', 'c').result['decisions'][0]['choice'] == 'Only the canary'


def test_decision_for_a_different_session_is_not_returned(tmp_path):
    s, owner, r, op = recorded(tmp_path, session='session-other')
    assert provider(s, tmp_path)('session-a', 'call_1') is None
    assert s.operation(owner, oid=op['operationId'])['operation']['dispatchState'] == 'failed'


def test_lineage_alias_matches_in_both_directions(tmp_path):
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('create table sessions (id text primary key, parent_session_id text, end_reason text, source text)')
        c.executemany('insert into sessions values (?,?,?,?)',
                      [('session-a', None, 'compression', 'tui'), ('session-a2', 'session-a', None, 'tui')])
    s, owner, r, op = recorded(tmp_path, session='session-a')
    assert provider(s, tmp_path, db=db)('session-a2', 'c') is not None  # agent rotated to the compressed child
    (tmp_path / 'child').mkdir()
    s2, owner2, r2, op2 = recorded(tmp_path / 'child', session='session-a2')
    assert provider(s2, tmp_path, db=db)('session-a', 'c') is not None  # owner is the child, agent on the parent


def test_decline_and_delivered_decisions_are_handled(tmp_path):
    s, owner, r, op = recorded(tmp_path, choice='decline')
    got = provider(s, tmp_path)('session-a', 'c')
    assert got.result['decisions'][0]['choice'] == 'Decline'
    got.commit()
    assert s.operation(owner, oid=op['operationId'])['operation']['dispatchState'] == 'delivered'


# ---- end to end with the core clarify tool -----------------------------------------------------------------

def test_blocked_clarify_returns_answered_elsewhere_and_marks_delivered(tmp_path):
    ce = pytest.importorskip('tools.clarify_early')
    from tools.clarify_tool import clarify_tool
    ce.reset_for_tests()
    ce.POLL_INTERVAL_S = 0.05
    try:
        s = store(tmp_path); owner = dict(OWNER); r = s.create(owner, request())
        ce.register_provider(provider(s, tmp_path))

        class Blocking:
            def __init__(self): self.event = threading.Event()
            def __call__(self, qs): self.event.wait(5); return {'answers': {}, 'outcome': 'cancelled'}
            def cancel_early(self, reason): self.event.set(); return True

        box = {}
        t = threading.Thread(target=lambda: box.update(out=json.loads(clarify_tool(
            [{'question': 'Which?', 'choices': ['a', 'b']}], callback=Blocking(), session_id='session-a', tool_call_id='call_z'))))
        t.start(); time.sleep(0.3)
        assert t.is_alive()
        op = s.decide(owner, decision(r))['operation']  # David decides in the For-you queue
        s.dispatch(owner, op['operationId'], BusyReceiver())  # the runtime refuses: the turn is active
        t.join(5)
        assert box['out']['status'] == 'answered_elsewhere'
        done = s.operation(owner, oid=op['operationId'])['operation']
        assert done['dispatchState'] == 'delivered' and done['receipt']['run']['toolCallId'] == 'call_z'
    finally:
        ce.reset_for_tests()


def test_register_installs_the_provider_with_core():
    ce = pytest.importorskip('tools.clarify_early')
    ce.reset_for_tests()
    try:
        plugin = load_module('user_steps_init_for_early', ROOT / '__init__.py')
        plugin._register_clarify_early(); plugin._register_clarify_early()
        assert len(ce._providers) == 1  # re-registering replaces, never stacks
    finally:
        ce.reset_for_tests()
