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
    """The plugin provider, called the way a v1 core calls it: with the exact displayed question bound to the
    decision's request (title + ordered choice labels). Unbound calls are covered in test_bound_decisions.py."""
    early = load_module('clarify_early_provider', ROOT / 'clarify_early.py')
    delivery = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs or Runs())
    raw = early.make_provider(store=lambda: s, delivery=lambda: delivery, db=db,
                              owner_for=lambda sid: dict(OWNER, storedSessionId=sid))

    def call(session_id, tool_call_id):
        qs = [b['questions'][0] for b in _bindings(s)]
        return raw(session_id, tool_call_id, question=descriptor(
            [(q['question'], [c['label'] for c in q['choices']]) for q in qs], tool_call_id))
    return call


def _bindings(s):
    with s._read() as c:
        return [json.loads(x[0]) for x in c.execute('SELECT body FROM question_bindings WHERE revision>0 ORDER BY rowid')]


def descriptor(questions, tool_call_id):
    import hashlib
    qs = [{'question': q, 'choices': list(c)} for q, c in questions]
    return {'schemaVersion': 1, 'toolCallId': tool_call_id, 'questions': qs,
            'digest': hashlib.sha256(json.dumps(qs, sort_keys=True, separators=(',', ':'),
                                                ensure_ascii=False).encode()).hexdigest()}


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

@pytest.fixture
def no_host_plugins(tmp_path, monkeypatch):
    """Keep the host's ~/.hermes plugins out of the core clarify path.

    clarify_tool fires on_human_input_request, which discovers and loads every plugin under HERMES_HOME. On a
    host whose plugins do slow work at register time (ask-gate warms a Kanban replica projection on the hub) the
    clarify outlives t.join(5), and its leaked poller then answers the next test's decision."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes-home'))
    try:
        import tools.human_input_hooks as hooks
    except ImportError:
        return
    monkeypatch.setattr(hooks, '_fire', lambda hook_name, payload: None)


def test_blocked_clarify_returns_answered_elsewhere_and_marks_delivered(tmp_path, no_host_plugins):
    ce = pytest.importorskip('tools.clarify_early')
    from tools.clarify_tool import clarify_tool
    ce.reset_for_tests()
    ce.POLL_INTERVAL_S = 0.05
    try:
        s = store(tmp_path); owner = dict(OWNER); r = s.create(owner, request())
        early = load_module('clarify_early_provider', ROOT / 'clarify_early.py')
        delivery = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=Runs())
        ce.register_provider(early.make_provider(store=lambda: s, delivery=lambda: delivery, db=None,
                                                 owner_for=lambda sid: dict(OWNER, storedSessionId=sid)))
        if not hasattr(ce, 'question_descriptor'):
            pytest.skip('core predates bound clarify questions: the provider fails closed (test_bound_decisions)')

        class Blocking:
            def __init__(self): self.event = threading.Event()
            def __call__(self, qs): self.event.wait(5); return {'answers': {}, 'outcome': 'cancelled'}
            def cancel_early(self, reason): self.event.set(); return True

        box = {}
        t = threading.Thread(target=lambda: box.update(out=json.loads(clarify_tool(
            [{'question': r['title'], 'choices': [c['label'] for c in r['choices']]}], callback=Blocking(),
            session_id='session-a', tool_call_id='call_z'))))
        t.start(); time.sleep(0.3)
        assert t.is_alive()
        op = s.decide(owner, decision(r))['operation']  # David decides in the For-you queue
        s.dispatch(owner, op['operationId'], BusyReceiver())  # the runtime refuses: the turn is active
        t.join(5)
        assert not t.is_alive(), 'clarify did not return within 5s'
        assert box['out']['status'] == 'answered_elsewhere'
        done = s.operation(owner, oid=op['operationId'])['operation']
        assert done['dispatchState'] == 'delivered' and done['receipt']['run']['toolCallId'] == 'call_z'
        assert done['clarifyBinding']['toolCallId'] == 'call_z'
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


def test_blocked_clarify_ignores_an_unrelated_same_session_decision_end_to_end(tmp_path, no_host_plugins):
    """Negative control through the real core clarify tool: same session, different question."""
    ce = pytest.importorskip('tools.clarify_early')
    if not hasattr(ce, 'question_descriptor'):
        pytest.skip('core predates bound clarify questions')
    from tools.clarify_tool import clarify_tool
    ce.reset_for_tests(); ce.POLL_INTERVAL_S = 0.05
    try:
        s = store(tmp_path); owner = dict(OWNER); r = s.create(owner, request())
        early = load_module('clarify_early_provider', ROOT / 'clarify_early.py')
        delivery = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=Runs())
        ce.register_provider(early.make_provider(store=lambda: s, delivery=lambda: delivery, db=None,
                                                 owner_for=lambda sid: dict(OWNER, storedSessionId=sid)))

        class Blocking:
            def __init__(self): self.event = threading.Event()
            def __call__(self, qs): self.event.wait(5); return {'answers': {'0': 'b'}, 'outcome': 'answered'}
            def cancel_early(self, reason): self.event.set(); return True
        cb = Blocking(); box = {}
        t = threading.Thread(target=lambda: box.update(out=json.loads(clarify_tool(
            [{'question': 'Which colour?', 'choices': ['a', 'b']}], callback=cb, session_id='session-a',
            tool_call_id='call_u'))))
        t.start(); time.sleep(0.2)
        op = s.decide(owner, decision(r))['operation']
        s.dispatch(owner, op['operationId'], BusyReceiver())
        time.sleep(0.5)
        assert t.is_alive(), 'an unrelated decision of the same session must not end the clarify'
        cb.event.set(); t.join(5)
        assert not t.is_alive(), 'clarify did not return within 5s'
        assert box['out'].get('status') != 'answered_elsewhere'
        assert s.operation(owner, oid=op['operationId'])['operation']['dispatchState'] == 'failed'
    finally:
        ce.reset_for_tests()
