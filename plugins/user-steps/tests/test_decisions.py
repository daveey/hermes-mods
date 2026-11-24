"""Canonical decisions; all persistence is isolated under pytest's temporary directory."""
import copy
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import uuid
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('decisions_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}


def request(complete=True, aliases=None):
    return dict(schemaVersion=2, owner=OWNER.copy(), requestId=str(uuid.uuid4()), revision=1,
                kind='approval', title='Synthetic change',
                scope=dict(markdown='Change example.py; run correctness tests; deliver patch.',
                           complete=complete, sourceRefs=[]),
                limits=[dict(key='spend', value='none')], exclusions=['No training'],
                outcomes=dict(approve='Authorize this scope only', revise='Keep work blocked', decline='Cancel request'),
                choices=[dict(id=e, label=e.title(), effect=e) for e in ['approve', 'revise', 'decline']],
                aliases=aliases or [], state='open' if complete else 'needs_context')


def store(tmp_path):
    return core.ActionStore(tmp_path / 'actions.sqlite3')


def decision(r, key='key-1', choice='approve', text=None):
    return dict(requestId=r['requestId'], expectedRevision=r['revision'], choiceId=choice,
                revisionText=text, idempotencyKey=key)


def test_schema_and_incomplete_rejected(tmp_path):
    s = store(tmp_path)
    r = s.create(OWNER, request(False))
    with pytest.raises(core.ActionError) as err:
        s.decide(OWNER, decision(r))
    assert err.value.status == 422
    for mutate in [lambda r: r.update(revision=True), lambda r: r.update(requestId='bad'),
                   lambda r: r['scope'].update(complete='true'),
                   lambda r: r['choices'].append(r['choices'][0]),
                   lambda r: r.update(unexpected='value')]:
        r = request(); mutate(r)
        with pytest.raises(core.ActionError): s.create(OWNER, r)


def test_alias_identity_not_title(tmp_path):
    s = store(tmp_path)
    ref = dict(gatewayId='test-gateway', profile='default', type='kanban', board='one', id='card')
    a = s.create(OWNER, request(aliases=[ref]))
    b = s.create(OWNER, request(aliases=[dict(ref, board='two')]))
    assert a['requestId'] != b['requestId']
    with pytest.raises(core.ActionError): s.create(OWNER, request(aliases=[ref]))
    assert len(s.requests(OWNER)) == 2
    assert s.requests(dict(OWNER, storedSessionId='branch')) == []
    with pytest.raises(core.ActionError): s.get(dict(OWNER, profile='other'), a['requestId'])


def test_cas_immutable_revision_and_exact_replay(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    replacement = copy.deepcopy(r); replacement['scope']['markdown'] = 'Different plan'
    r2 = s.revise(OWNER, r['requestId'], 1, replacement)
    assert r2['revision'] == 2
    assert s.history(OWNER, r['requestId'])[0]['scope'] == r['scope']
    with pytest.raises(core.ActionError) as err: s.decide(OWNER, decision(r))
    assert err.value.status == 409
    first = s.decide(OWNER, decision(r2))
    assert first == s.decide(OWNER, decision(r2))
    with pytest.raises(core.ActionError): s.decide(OWNER, decision(r2, choice='decline'))
    with pytest.raises(core.ActionError): s.decide(OWNER, decision(r2, key='other'))
    assert store(tmp_path).operation(OWNER, first['operation']['operationId']) == first


def test_race_one_decision(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    def submit(i):
        try: return s.decide(OWNER, decision(r, key=str(i)))
        except core.ActionError: return None
    with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(submit, range(8)))
    assert sum(x is not None for x in results) == 1


class DurableReceiver:
    """Test receiver models a durable idempotency contract, including lost replies."""
    def __init__(self, path): self.path = path; self.fail = True
    def deliver(self, operation):
        rows = json.loads(self.path.read_text()) if self.path.exists() else {}
        key = operation['operationId']
        rows.setdefault(key, operation['intent'])
        self.path.write_text(json.dumps(rows))
        if self.fail: raise OSError('lost response after acceptance')
        return {'deliveryId': key}
    def readback(self, operation, receipt):
        return json.loads(self.path.read_text())[operation['operationId']] == operation['intent']


def test_outbox_lost_response_retry_and_readback(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    receiver = DurableReceiver(tmp_path / 'receiver.json')
    failed = s.dispatch(OWNER, op['operationId'], receiver)
    assert failed['operation']['dispatchState'] == 'failed'
    assert failed['request']['state'] == 'open'
    receiver.fail = False
    done = store(tmp_path).dispatch(OWNER, op['operationId'], receiver)
    assert done['operation']['dispatchState'] == 'delivered'
    assert done['request']['state'] == 'resolved'
    assert len(json.loads(receiver.path.read_text())) == 1
    assert s.dispatch(OWNER, op['operationId'], receiver) == done


@pytest.mark.parametrize('choice,state', [('revise','superseded'), ('decline','cancelled')])
def test_non_authorizing_decisions(tmp_path, choice, state):
    s = store(tmp_path); r = s.create(OWNER, request(False))
    if choice == 'revise':
        with pytest.raises(core.ActionError): s.decide(OWNER, decision(r, choice=choice, text=' '))
    op = s.decide(OWNER, decision(r, choice=choice, text='Need full details'))['operation']
    assert op['intent']['executionAuthorized'] is False
    receiver = DurableReceiver(tmp_path / 'receiver.json'); receiver.fail = False
    assert s.dispatch(OWNER, op['operationId'], receiver)['request']['state'] == state


def test_unknown_legacy_is_incomplete_and_idempotent(tmp_path):
    s = store(tmp_path)
    legacy = {'id':'us_legacy', 'text':'Should I do it?', 'kind':'approve', 'status':'open', 'session':'session-a'}
    a = s.migrate_step(OWNER, legacy); b = s.migrate_step(OWNER, legacy)
    assert a == b
    assert a['scope']['complete'] is False
    assert a['state'] == 'needs_context'
    assert [c['effect'] for c in a['choices']] == ['revise']
    with pytest.raises(core.ActionError): s.decide(OWNER, decision(a))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_real_receiver_readback_no_duplicate_and_no_card_unblock(tmp_path):
    cards = load_module('decision_cards', ROOT / 'cards.py')
    receiver = cards.DecisionInbox(tmp_path / 'inbox.db')
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    one = receiver.deliver(op); two = cards.DecisionInbox(receiver.path).deliver(op)
    assert one == two
    assert receiver.readback(op, one)
    assert one['executionStarted'] is False
    assert receiver.context(OWNER).count(op['operationId']) == 1
    ref = dict(gatewayId='test-gateway', profile='default', type='kanban', board='one', id='card')
    r = s.create(OWNER, request(aliases=[ref]))
    op = s.decide(OWNER, decision(r, key='card', choice='decline'))['operation']
    assert s.dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'failed'


def test_api_owner_isolation_and_retry(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load_module('decisions_api', ROOT / 'dashboard' / 'plugin_api.py')
    cards = load_module('decisions_cards_api', ROOT / 'cards.py')
    s = store(tmp_path); r = s.create(OWNER, request())
    app = FastAPI(); app.include_router(api.create_router(
        ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'state.db', action_store=s,
        owner_resolver=lambda session: dict(OWNER, storedSessionId=session),
        receiver=cards.DecisionInbox(tmp_path / 'inbox.db')))
    client = TestClient(app)
    # GET /requests is the presentation view: the exact frozen body plus when it was asked (additive).
    assert client.get('/requests', params={'session':'session-a'}).json() == {
        'requests':[dict(r, createdAt=s.view(OWNER, r['requestId'])['createdAt'])]}
    assert client.get('/requests/'+r['requestId'], params={'session':'branch'}).status_code == 404
    body = dict(session='session-a', expectedRevision=1, choiceId='approve', idempotencyKey='api')
    assert client.post('/requests/'+r['requestId']+'/decisions', json=dict(body, owner=OWNER)).status_code == 422
    response = client.post('/requests/'+r['requestId']+'/decisions', json=body)
    assert response.status_code == 200
    from async_receipt_support import await_operation
    receipt = response.json()
    assert receipt['operation']['dispatchState'] == 'pending'
    data = await_operation(s, OWNER, key='api')
    assert data['operation']['operationId'] == receipt['operation']['operationId']
    assert client.post('/requests/'+r['requestId']+'/decisions', json=body).json() == data
    assert client.get('/operations', params={'session':'session-a','idempotencyKey':'api'}).json() == data
    assert client.get('/operations/'+data['operation']['operationId'], params={'session':'session-a'}).json() == data
    assert client.post('/resolve', json={'id':'legacy','session':'session-a'}).status_code == 410


@pytest.mark.parametrize('same_turn', [True, False])
def test_producer_and_extraction_are_separate_across_turns(tmp_path, monkeypatch, same_turn):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path)); monkeypatch.setenv('HERMES_GATEWAY_ID', 'test-gateway')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('decision_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    payload = request(); payload.pop('owner'); payload.pop('requestId'); payload.pop('revision')
    out = json.loads(plugin.publish_action_request({'request':payload}, session_id='session-a'))
    assert out['request']['scope']['complete'] is True
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps':[{
        'text':'Approve example', 'kind':'approve', 'verbatim_span':'Should I change the example?', 'options':[]}], 'resolved':[]}))
    if not same_turn:
        plugin._TURN_PUBLISHED.pop('session-a')  # runtime resets producer provenance on the next turn
    # At the exhausted publication budget, the transform must still not mint next to
    # the same-turn publication. Earlier-turn identity is deliberately unchanged.
    plugin.publication_buffer('session-a', 'desktop', 'Should I change the example?', attempt=2, hold_budget=2)
    plugin.transform_llm_output('Should I change the example?', 'session-a', 'desktop')
    requests = plugin.core.ActionStore().requests(OWNER)
    if same_turn:
        assert requests == [out['request']]
    else:
        assert len(requests) == 2
        assert requests[1]['scope']['complete'] is False
        assert requests[1]['choices'][0]['effect'] == 'revise'


def test_real_receiver_crash_after_commit_and_changed_scope(tmp_path):
    cards = load_module('crash_cards', ROOT / 'cards.py')
    inbox_path = tmp_path / 'receiver.sqlite3'
    class CrashAfterCommit(cards.DecisionInbox):
        def deliver(self, operation):
            super().deliver(operation)
            raise SystemExit('simulated process death after receiver commit')
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    with pytest.raises(SystemExit): s.dispatch(OWNER, op['operationId'], CrashAfterCommit(inbox_path))
    assert store(tmp_path).operation(OWNER, op['operationId'])['operation']['dispatchState'] == 'pending'
    receiver = cards.DecisionInbox(inbox_path)
    delivered = store(tmp_path).dispatch(OWNER, op['operationId'], receiver)
    assert receiver.context(OWNER, s.requests(OWNER)).count(op['operationId']) == 1
    replacement = copy.deepcopy(r); replacement['scope']['markdown'] = 'A changed scope'
    s.revise(OWNER, r['requestId'], 1, replacement)
    assert receiver.context(OWNER, s.requests(OWNER)) == ''
    assert delivered['operation']['intent']['permissionGrants'] == []


def test_readback_failure_never_success_and_other_request_not_locked(tmp_path):
    s = store(tmp_path); a = s.create(OWNER, request()); b = s.create(OWNER, request())
    op = s.decide(OWNER, decision(a))['operation']
    receiver = DurableReceiver(tmp_path / 'receiver.json'); receiver.fail = False
    receiver.readback = lambda *args: False
    result = s.dispatch(OWNER, op['operationId'], receiver)
    assert result['operation']['dispatchState'] == 'failed'
    assert result['request']['state'] == 'open'
    assert result['request']['resolution']['operationId'] == op['operationId']
    assert s.decide(OWNER, decision(b, key='unrelated'))['operation']['dispatchState'] == 'pending'
    with pytest.raises(core.ActionError): s.revise(OWNER, a['requestId'], 1, a)


def test_identical_extracted_titles_do_not_merge_and_model_cannot_decide(tmp_path):
    # Original Direction-B contract (d4339ac8), restored: an equal title is not identity. The later "share one
    # open tile" rewrite (d1074801) folded distinct asks by normalized title; only exact provenance may fold.
    s = store(tmp_path); lg = core.Ledger(tmp_path / 'ledger', tmp_path / 'missing-state.db')
    reply = 'Should I change the example?'
    payload = {'steps':[{'text':'Same title', 'kind':'approve', 'verbatim_span':reply}], 'resolved':[]}
    first = core.process_reply(reply, 'session-a', ledger=lg, action_store=s, action_owner=OWNER,
                               llm=lambda *a: json.dumps(payload))
    payload['resolved'] = [first['steps'][0]['id']]
    reply2 = 'Should I change the example now?'
    payload['steps'][0]['verbatim_span'] = reply2
    second = core.process_reply(reply2, 'session-a', ledger=lg, action_store=s, action_owner=OWNER,
                                llm=lambda *a: json.dumps(payload))
    assert second['resolved'] == []
    assert len(s.requests(OWNER)) == 2
    assert all(not r['scope']['complete'] for r in s.requests(OWNER))


def test_registered_producer_revise_and_consumer_hook(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path)); monkeypatch.setenv('HERMES_GATEWAY_ID', 'test-gateway')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('registered_producer', ROOT / '__init__.py')
    # This test exercises registration on a supported core. Keep the real
    # capability check; only supply the external core module in standalone runs.
    import sys
    import types
    gate = types.ModuleType('agent.pre_final_gate')
    gate.__dict__['PRE_FINAL_GATE_CAPABILITIES'] = {'needs_buffer': True, 'post_transform_release': True}
    monkeypatch.setitem(sys.modules, 'agent.pre_final_gate', gate)
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    class Registry:
        def register_hook(self, *a): pass
        def register_tool(self, **kw): self.tool = kw
    registry = Registry(); plugin.register(registry)
    assert registry.tool['name'] == 'action_request'
    payload = request(True)
    for key in ('owner','requestId','revision'): payload.pop(key)
    r = json.loads(registry.tool['handler']({'request':payload}, session_id='session-a'))['request']
    s = plugin.core.ActionStore()
    op = s.decide(OWNER, decision(r, choice='revise', text='Provide the exact full plan'))['operation']
    assert s.operation(OWNER, op['operationId'])['operation']['dispatchState'] == 'pending'
    # A normal LLM hook cannot falsely acknowledge an unperformed wake/card transition.
    assert plugin.pre_llm_call(session_id='session-a', platform='desktop') is None
    assert s.operation(OWNER, op['operationId'])['operation']['dispatchState'] == 'pending'
    s.dispatch(OWNER, op['operationId'], plugin.cards_mod.DecisionInbox(tmp_path / 'user-steps' / 'decision-inbox.sqlite3'))
    note = plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    assert op['operationId'] in note
    assert s.operation(OWNER, op['operationId'])['request']['state'] == 'superseded'
    payload['scope']['complete'] = True; payload['state'] = 'open'
    revised = json.loads(registry.tool['handler']({'request':payload, 'requestId':r['requestId'],
                                                 'expectedRevision':1}, session_id='session-a'))['request']
    assert revised['revision'] == 2
    assert op['operationId'] not in plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    assert 'error' in json.loads(plugin.publish_action_request({'request':dict(payload, owner=OWNER)}, session_id='session-a'))


@pytest.fixture
def turn_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    plugin = load_module('turn_delivery_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.chat, 'reconcile', lambda *a: None)
    plugin._BACKFILLED = True
    return plugin, plugin.core.ActionStore()


@pytest.mark.parametrize('refusal', ['exact session is busy or has pending input',
                                   'exact session has no unique live owner'])
def test_next_owner_turn_delivers_refused_decision(turn_delivery, refusal):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    class Refusing:
        def deliver(self, operation): raise RuntimeError(refusal)
    s.dispatch(OWNER, op['operationId'], Refusing())
    note = (plugin.pre_llm_call(session_id='session-a', platform='desktop') or {}).get('context', '')
    assert note.count(op['operationId']) == 1
    done = s.operation(OWNER, op['operationId'])
    assert done['operation']['dispatchState'] == 'delivered'
    assert done['operation']['receipt']['destination'] == 'turn_context'
    assert done['request']['state'] == 'resolved'
    assert not [r for r in s.requests(OWNER) if r['state'] in ('open', 'needs_context')]


@pytest.mark.parametrize('field', ['storedSessionId', 'profile', 'gatewayId'])
def test_other_owner_turn_cannot_take_refused_decision(turn_delivery, monkeypatch, field):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    s.dispatch(OWNER, op['operationId'], BusyReceiver())
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, **{field: 'other'}))
    note = (plugin.pre_llm_call(session_id='other', platform='desktop') or {}).get('context', '')
    assert op['operationId'] not in note
    assert s.operation(OWNER, op['operationId'])['operation']['dispatchState'] == 'failed'


@pytest.mark.parametrize('kind', ['kanban', 'fresh', 'transport'])
def test_turn_does_not_acknowledge_other_pending_work(turn_delivery, kind):
    plugin, s = turn_delivery
    aliases = [dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban',
                    board='example', id='card')] if kind == 'kanban' else []
    r = s.create(OWNER, request(aliases=aliases))
    op = s.decide(OWNER, decision(r))['operation']
    if kind == 'kanban':
        s.dispatch(OWNER, op['operationId'], BusyReceiver())
    elif kind == 'transport':
        s.dispatch(OWNER, op['operationId'], None)
    before = s.operation(OWNER, op['operationId'])
    note = (plugin.pre_llm_call(session_id='session-a', platform='desktop') or {}).get('context', '')
    assert op['operationId'] not in note
    assert s.operation(OWNER, op['operationId']) == before


def test_turn_and_dispatch_share_delivery_lease_and_replay_once(turn_delivery, monkeypatch):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r))['operation']['operationId']
    s.dispatch(OWNER, oid, BusyReceiver())
    # An admission already in flight excludes context attachment, even when
    # persisted state still carries the previous refusal.
    with s.delivery_lease(OWNER, oid, dispatch=True) as held:
        assert held
        assert plugin.pre_llm_call(session_id='session-a', platform='desktop') is None
        assert s.operation(OWNER, oid)['operation']['dispatchState'] == 'failed'
    original = plugin.cards_mod.TurnContextInbox.deliver
    calls = []
    def record_and_race(inbox, operation):
        calls.append(operation['operationId'])
        receiver = BusyReceiver(); receiver.busy = False
        # Separate ActionStore/descriptor while the turn owns the exclusive lease.
        other = plugin.core.ActionStore(s.path)
        before_race = other.operation(OWNER, oid)
        assert other.dispatch(OWNER, oid, receiver) == before_race
        assert receiver.delivered == []
        return original(inbox, operation)
    monkeypatch.setattr(plugin.cards_mod.TurnContextInbox, 'deliver', record_and_race)
    assert plugin.pre_llm_call(session_id='session-a', platform='desktop')['context'].count(oid) == 1
    before = s.operation(OWNER, oid)
    # Existing canonical receipt remains visible, but is neither re-delivered
    # nor duplicated. Ordinary dispatch also becomes a no-op on this identity.
    assert plugin.pre_llm_call(session_id='session-a', platform='desktop')['context'].count(oid) == 1
    assert s.dispatch(OWNER, oid, BusyReceiver()) == before
    assert calls == [oid]
    with plugin.cards_mod.DecisionInbox()._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM deliveries WHERE id=?', (oid,)).fetchone()[0] == 1


def test_interrupted_retry_is_uncertain_not_a_fresh_refusal(turn_delivery):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r))['operation']['operationId']
    s.dispatch(OWNER, oid, BusyReceiver())
    class Interrupted:
        def deliver(self, op): raise SystemExit('process exited during admission')
    with pytest.raises(SystemExit): s.dispatch(OWNER, oid, Interrupted())
    before = s.operation(OWNER, oid)
    assert before['operation']['dispatchState'] == 'pending'
    assert plugin.pre_llm_call(session_id='session-a', platform='desktop') is None
    assert s.operation(OWNER, oid) == before


@pytest.mark.parametrize('later_attempt', [False, True])
def test_legacy_pending_refusal_must_follow_latest_attempt(turn_delivery, later_attempt):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r))['operation']['operationId']
    op = s.dispatch(OWNER, oid, BusyReceiver())['operation']
    # Historical writer: queueing a refused operation vs crashing after starting
    # another attempt with the old error still persisted.
    op['dispatchState'] = 'pending'
    if later_attempt: op['dispatchStartedAt'] = '9999-01-01T00:00:00+00:00'
    with s._tx() as c:
        c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), oid))
    note = (plugin.pre_llm_call(session_id='session-a', platform='desktop') or {}).get('context', '')
    assert (oid in note) is (not later_attempt)
    assert s.operation(OWNER, oid)['operation']['dispatchState'] == ('pending' if later_attempt else 'delivered')


@pytest.mark.parametrize('cancelled', [True, False])
def test_crash_after_admission_never_reuses_old_busy_refusal(turn_delivery, cancelled):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r))['operation']['operationId']
    s.dispatch(OWNER, oid, BusyReceiver())
    class Runs:
        def deliver(self, op): return dict(deliveryId=op['operationId'], terminalCancelled=cancelled)
        def readback(self, op, receipt): return receipt['deliveryId'] == op['operationId']
    delivery = plugin.cards_mod.DecisionDelivery(runs=Runs())
    class Interrupted:
        def deliver(self, op):
            receipt = delivery.deliver(op)
            assert delivery.readback(op, receipt)
            raise SystemExit('after canonical admission, before local settlement')
    with pytest.raises(SystemExit): s.dispatch(OWNER, oid, Interrupted())
    before = s.operation(OWNER, oid)
    note = (plugin.pre_llm_call(session_id='session-a', platform='desktop') or {}).get('context', '')
    assert oid not in note
    assert s.operation(OWNER, oid) == before
    # Only the canonical composite may recover its accepted/cancelled receipt.
    done = s.dispatch(OWNER, oid, delivery)
    assert done['operation']['dispatchState'] == ('cancelled' if cancelled else 'delivered')
    assert done['operation']['receipt']['destination'] == 'canonical_decision_delivery'


@pytest.mark.parametrize('cancelled', [True, False])
@pytest.mark.parametrize('crash_first', [True, False])
def test_overlapping_refusal_cannot_hide_uncertain_admission(turn_delivery, cancelled, crash_first):
    import threading
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r))['operation']['operationId']
    s.dispatch(OWNER, oid, BusyReceiver())
    entered, release = threading.Event(), threading.Event()
    errors = []
    prompt = load_module('overlap_prompt', ROOT / 'prompt_delivery.py')
    def rpc(method, params):
        return dict(params, status='accepted', dispatch='cancelled' if cancelled else 'queued',
                    user_row_id=1, message_uid='fixture-message')
    delivery = plugin.cards_mod.DecisionDelivery(runs=prompt.PromptAdmission(rpc=rpc))
    class Interrupted:
        def deliver(self, op):
            receipt = delivery.deliver(op)
            assert receipt.get('terminalCancelled', False) is cancelled
            raise SystemExit('after canonical admission before settlement')
    class Slow:
        def deliver(self, op):
            entered.set()
            assert release.wait(10)
            return (Interrupted() if crash_first else BusyReceiver()).deliver(op)
    def background():
        try: s.dispatch(OWNER, oid, Slow())
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=background)
    thread.start()
    try:
        assert entered.wait(10)
        if crash_first:
            s.dispatch(OWNER, oid, BusyReceiver())
        else:
            with pytest.raises(SystemExit): s.dispatch(OWNER, oid, Interrupted())
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert len(errors) == (1 if crash_first else 0)
    assert all(isinstance(exc, SystemExit) for exc in errors)
    before = s.operation(OWNER, oid)
    note = (plugin.pre_llm_call(session_id='session-a', platform='desktop') or {}).get('context', '')
    assert oid not in note
    assert s.operation(OWNER, oid) == before
    done = s.dispatch(OWNER, oid, delivery)
    assert done['operation']['dispatchState'] == ('cancelled' if cancelled else 'delivered')
    assert done['operation']['receipt']['destination'] == 'canonical_decision_delivery'


def test_overlapping_refusals_still_attach_when_both_settle(turn_delivery):
    import threading
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r))['operation']['operationId']
    entered, release = threading.Event(), threading.Event()
    class SlowBusy:
        def deliver(self, op):
            entered.set()
            assert release.wait(10)
            return BusyReceiver().deliver(op)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s.dispatch, OWNER, oid, SlowBusy())
        try:
            assert entered.wait(10)
            s.dispatch(OWNER, oid, BusyReceiver())
        finally: release.set()
        future.result(timeout=10)
    assert not s.operation(OWNER, oid)['operation']['uncertainAttempts']
    assert oid in plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']


@pytest.mark.parametrize('legacy', [False, True])
def test_uncertainty_survives_subsequent_busy_retry(turn_delivery, legacy):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r))['operation']['operationId']
    class Interrupted:
        def deliver(self, op): raise SystemExit('unknown admission outcome')
    with pytest.raises(SystemExit): s.dispatch(OWNER, oid, Interrupted())
    if legacy:
        op = s.operation(OWNER, oid)['operation']
        op.pop('uncertainAttempts', None)
        with s._tx() as c:
            c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), oid))
    s.dispatch(OWNER, oid, BusyReceiver())
    before = s.operation(OWNER, oid)
    assert plugin.pre_llm_call(session_id='session-a', platform='desktop') is None
    assert s.operation(OWNER, oid) == before


@pytest.mark.parametrize('choice,state,text', [('revise', 'superseded', 'Change the plan'),
                                             ('decline', 'cancelled', None)])
def test_turn_attachment_preserves_non_authorizing_state(turn_delivery, choice, state, text):
    plugin, s = turn_delivery
    r = s.create(OWNER, request())
    oid = s.decide(OWNER, decision(r, choice=choice, text=text))['operation']['operationId']
    s.dispatch(OWNER, oid, BusyReceiver())
    assert oid in plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    done = s.operation(OWNER, oid)
    assert done['request']['state'] == state
    assert done['operation']['intent']['executionAuthorized'] is False


def test_alternating_busy_closed_chat_delivers_all_on_next_turn(turn_delivery):
    plugin, s = turn_delivery
    panel = load_module('turn_panel_model', ROOT.parent / 'session-panel' / 'model.py')
    ids = []
    class Closed:
        def deliver(self, operation): raise RuntimeError('exact session has no unique live owner')
    for i in range(3):
        r = s.create(OWNER, request())
        oid = s.decide(OWNER, decision(r, key=str(i)))['operation']['operationId']
        ids.append(oid)
        for receiver in [BusyReceiver(), Closed()] * 3:
            s.dispatch(OWNER, oid, receiver)
        assert panel.recorded(s.get(OWNER, r['requestId']))
    note = plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    assert all(note.count(oid) == 1 for oid in ids)
    assert all(s.operation(OWNER, oid)['operation']['dispatchState'] == 'delivered' for oid in ids)
    assert not any(panel.recorded(r) for r in s.requests(OWNER))


@pytest.mark.parametrize('choice,state,text', [('approve', 'resolved', None),
                                             ('revise', 'superseded', 'Need another plan'),
                                             ('decline', 'cancelled', None)])
def test_aliased_step_without_request_id_follows_terminal_request(turn_delivery, choice, state, text):
    plugin, s = turn_delivery
    lg = plugin.core.Ledger()
    step = plugin.core.add_step('session-a', 'Choose the example configuration', ['One', 'Two'], ledger=lg)
    assert not step.get('requestId')
    ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='user_step', id=step['id'])
    r = s.create(OWNER, request(aliases=[ref]))
    assert plugin.core.open_steps('session-a', lg)
    oid = s.decide(OWNER, decision(r, choice=choice, text=text))['operation']['operationId']
    s.dispatch(OWNER, oid, plugin.cards_mod.DecisionInbox())
    assert s.get(OWNER, r['requestId'])['state'] == state
    assert plugin.core.open_steps('session-a', lg) == []
    # Projection only: no synthetic resolution is written to the raw ledger.
    assert lg.read('session-a')['steps'][0]['status'] == 'open'


@pytest.mark.parametrize('mismatch', ['session', 'profile', 'gateway', 'alias-profile', 'alias-gateway', 'source-only'])
def test_alias_projection_never_closes_unrelated_step(turn_delivery, mismatch):
    plugin, s = turn_delivery
    lg = plugin.core.Ledger()
    step = plugin.core.add_step('session-a', 'Choose the example configuration', ['One', 'Two'], ledger=lg)
    own = OWNER.copy()
    if mismatch in ('session', 'profile', 'gateway'):
        own[{'session': 'storedSessionId', 'profile': 'profile', 'gateway': 'gatewayId'}[mismatch]] = 'other'
    ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='user_step', id=step['id'])
    if mismatch == 'alias-profile': ref['profile'] = 'other'
    if mismatch == 'alias-gateway': ref['gatewayId'] = 'other'
    body = request(aliases=[] if mismatch == 'source-only' else [ref]); body['owner'] = own
    if mismatch == 'source-only': body['scope']['sourceRefs'] = [ref]
    r = s.create(own, body)
    oid = s.decide(own, decision(r))['operation']['operationId']
    s.dispatch(own, oid, plugin.cards_mod.DecisionInbox())
    assert step['id'] in [v['id'] for v in plugin.core.open_steps('session-a', lg)]


def test_session_outcome_provider_drops_migrated_alias_only_step(turn_delivery, monkeypatch):
    plugin, s = turn_delivery
    outcome = load_module('turn_outcome_provider', ROOT.parent / 'session-outcome' / '__init__.py')
    monkeypatch.setattr(outcome, '_user_steps', lambda: plugin.core)
    lg = plugin.core.Ledger()
    step = plugin.core.add_step('session-a', 'Choose a configuration', ['One', 'Two'], ledger=lg)
    r = s.migrate_step(OWNER, step)
    assert not lg.read('session-a')['steps'][0].get('requestId')
    assert outcome.provider('session-a')['decision_items'][0]['id'] == step['id']
    label = r['choices'][0]['label']
    oid = s.decide(OWNER, decision(r, choice=r['choices'][0]['id'], text=label))['operation']['operationId']
    s.dispatch(OWNER, oid, BusyReceiver())
    assert oid in plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    assert s.get(OWNER, r['requestId'])['state'] == 'resolved'
    assert outcome.provider('session-a') is None


class BusyReceiver:
    """Models the owning runtime refusing admission while the session is mid-turn."""
    def __init__(self): self.busy = True; self.delivered = []
    def deliver(self, operation):
        if self.busy:
            raise RuntimeError('exact session is busy or has pending input; retry later with the same operation')
        self.delivered.append(operation['operationId'])
        return {'deliveryId': operation['operationId']}
    def readback(self, operation, receipt):
        return receipt['deliveryId'] in self.delivered


def test_busy_session_is_classified_and_background_retry_delivers_same_operation(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load_module('decisions_api_busy', ROOT / 'dashboard' / 'plugin_api.py')
    s = store(tmp_path); r = s.create(OWNER, request())
    receiver = BusyReceiver()
    router = api.create_router(ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'state.db', action_store=s,
                               owner_resolver=lambda session: dict(OWNER, storedSessionId=session), receiver=receiver)
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    body = dict(session='session-a', expectedRevision=1, choiceId='approve', idempotencyKey='busy')
    first = client.post('/requests/' + r['requestId'] + '/decisions', json=body).json()
    from async_receipt_support import await_operation
    assert first['operation']['dispatchState'] == 'pending'
    recorded_id = first['operation']['operationId']
    first = await_operation(s, OWNER, key='busy', state='failed')
    assert first['operation']['operationId'] == recorded_id
    assert first['operation']['error'] == core.BUSY_ERROR
    assert first['request']['state'] == 'open'
    assert router.retry_pending() == 1  # still busy: attempted, stays failed
    assert s.operation(OWNER, key='busy')['operation']['dispatchState'] == 'failed'
    receiver.busy = False  # the session's turn ended
    assert router.retry_pending() == 1
    done = s.operation(OWNER, key='busy')
    assert done['operation']['dispatchState'] == 'delivered'
    assert done['operation']['operationId'] == first['operation']['operationId']
    assert done['request']['state'] == 'resolved'
    assert receiver.delivered == [first['operation']['operationId']]
    assert router.retry_pending() == 0  # nothing left to deliver


def test_reconcile_stops_after_max_attempts(tmp_path):
    # Busy refusals no longer count (see test_clarify_early); the cap applies to real delivery failures.
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    class Broken:
        def deliver(self, o): raise RuntimeError('transport down')
        def readback(self, o, receipt): return False
    receiver = Broken()
    for _ in range(3): s.dispatch(OWNER, op['operationId'], receiver)
    assert s.reconcile(OWNER, receiver, max_attempts=3) == []
    assert len(s.reconcile(OWNER, receiver, max_attempts=4)) == 1


# ---- go options on a run-timing question authorize exactly the tile's scope (self-heal agent-aa8d66631c) ----
TIMING = {'id': 'step-timing', 'session': 'session-a', 'kind': 'choose',
          'text': 'When should I run the Mac mini launcher migration?',
          'paragraph': 'When should I run the Mac mini launcher migration: now, when idle, or later?',
          'verbatim_span': 'now, when idle, or later?', 'options': ['now', 'when idle', 'later']}


def _decide_label(tmp_path, step, label):
    s = store(tmp_path)
    r = s.migrate_step(OWNER, dict(step))
    choice = next(c for c in r['choices'] if c['label'] == label)
    out = s.decide(OWNER, decision(r, key=f'k-{label}', choice=choice['id'], text=label))
    return r, choice, out['operation']['intent']


@pytest.mark.parametrize('label', ['now', 'when idle'])
def test_go_answer_to_run_timing_question_authorizes_the_tile_scope(tmp_path, label):
    r, choice, intent = _decide_label(tmp_path, TIMING, label)
    assert choice.get('authorizes') is True
    assert intent['executionAuthorized'] is True
    assert intent['request']['scope']['markdown'] == TIMING['paragraph']  # frozen scope only
    assert 'approve' in r['outcomes']['answer'].lower()


def test_wait_answer_and_free_text_grant_nothing(tmp_path):
    r, choice, intent = _decide_label(tmp_path, TIMING, 'later')
    assert not choice.get('authorizes') and intent['executionAuthorized'] is False
    (tmp_path / 'b').mkdir(exist_ok=True); s = store(tmp_path / 'b')
    r = s.migrate_step(OWNER, dict(TIMING, id='step-2'))
    out = s.decide(OWNER, decision(r, key='free', choice='answer-text', text='now please'))
    assert out['operation']['intent']['executionAuthorized'] is False


def test_go_word_on_a_non_run_question_grants_nothing(tmp_path):
    step = {'id': 'step-color', 'session': 'session-a', 'kind': 'choose', 'text': 'Which colour do you want?',
            'paragraph': 'Which colour do you want: yes-blue or no-red?', 'options': []}
    s = store(tmp_path)
    r = s.migrate_step(OWNER, step)
    assert not any(c.get('authorizes') for c in r['choices'])


def test_authorizes_flag_is_only_valid_on_answer_choices():
    r = request(); r['choices'][0]['authorizes'] = True  # on an approve choice: rejected
    with pytest.raises(core.ActionError): core.validate_action(dict(r))


@pytest.mark.parametrize('question,label,authorized', [
    ('Run the hermes-only migration now?', 'Approve: run it now', True),
    ('Run the migrations now?', 'Go: hermes + babybear', True),
    ('Run the migration now?', 'Go now: example-org/example-hermes', True),
    ('File the issue now?', 'Yes, file it', True),
    ('Run the migration now?', 'Run it now', True),
    ('Run the migration now?', 'Hold', False),
    ('Run the migration now?', 'Later', False),
    ('File the issue now?', "Don't file", False),
    ('Run the migration now?', 'Not now', False),
    ('Run the migration now?', 'Approve if tests pass', False),
    ('Run the migration now?', 'Go maybe', False),
    ('Run both migrations now?', 'Only hermes', False),
    ('Which colour?', 'Go: blue', False),
    ('Which colour?', 'Only hermes', False),
])
def test_decide_tile_go_labels(tmp_path, question, label, authorized):
    step = core.add_step('session-a', question, [label, 'Other answer'],
                         ledger=core.Ledger(tmp_path / 'ledger', tmp_path / 'missing.db'))
    r, choice, intent = _decide_label(tmp_path, step, label)
    assert bool(choice.get('authorizes')) is authorized
    assert intent['executionAuthorized'] is authorized
    assert intent['request']['scope'] == r['scope']
    assert intent['revisionText'] == label


@pytest.mark.parametrize('label,flag,authorized', [
    ('Execute the selected plan', True, True),
    ('Only hermes', True, True),
    ('Approve: run it now', False, False),
    ('Hold', True, False),
    ('Later', True, False),
    ("Don't file", True, False),
    ('Approve if tests pass', True, False),
])
def test_explicit_producer_authorization(tmp_path, label, flag, authorized):
    step = core.add_step('session-a', 'Run both migrations now?',
                         [{'label': label, 'value': 'pick', 'authorizes': flag}, 'Other answer'],
                         ledger=core.Ledger(tmp_path / 'ledger', tmp_path / 'missing.db'))
    assert step['choices'][0]['authorizes'] is flag
    _, _, intent = _decide_label(tmp_path, step, label)
    assert intent['executionAuthorized'] is authorized
    assert intent['revisionText'] == label  # Only hermes restricts the frozen question; never run both.
    delivery = load_module('scope_prompt_delivery', ROOT / 'prompt_delivery.py')
    params = delivery.PromptAdmission._params({'operationId': 'scope-probe', 'owner': OWNER, 'intent': intent})
    assert 'The selected answer restricts the frozen scope' in params['text']
    assert json.loads(params['text'].split('\n', 1)[1])['revisionText'] == label


@pytest.mark.parametrize('question,label', [
    ('Which colour?', 'Go: run the migration'),
    ('Which colour?', 'Only hermes'),
])
def test_explicit_flag_needs_action_in_question(tmp_path, question, label):
    step = core.add_step('session-a', question,
                         [{'label': label, 'value': 'go', 'authorizes': True}, 'Hold'],
                         ledger=core.Ledger(tmp_path / 'ledger', tmp_path / 'missing.db'))
    assert _decide_label(tmp_path, step, label)[2]['executionAuthorized'] is False


def test_limit_permission_required_is_a_boolean_flag():
    r = request()
    r['limits'] = [dict(key='iam', value='one secret ARN', permissionRequired=True),
                   dict(key='spend', value='none', permissionRequired=False)]
    assert core.validate_action(r)['limits'][0]['permissionRequired'] is True
    r['limits'] = [dict(key='iam', value='x', permissionRequired='yes')]
    with pytest.raises(core.ActionError, match='permissionRequired must be a boolean'):
        core.validate_action(r)
    r['limits'] = [dict(key='iam', value='')]
    with pytest.raises(core.ActionError, match='nonempty string'):
        core.validate_action(r)


class RaisingReceiver:
    def __init__(self, message): self.message = message
    def deliver(self, operation): raise RuntimeError(self.message)
    def readback(self, operation, receipt): return False


def test_failed_delivery_records_the_sanitized_error_text(tmp_path, caplog):
    """The log said only classification=RuntimeError for 380 failures (t_01a199c0a2e3ea8b); nobody could tell a
    permanent CAS conflict from a closed chat. The op and the log now carry the message, with secrets cut."""
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    secret = 'sk-' + 'a' * 40
    msg = f'Card binding CAS conflict at https://example.com/x?token={secret} key {secret}'
    with caplog.at_level('WARNING', logger='user-steps'):
        got = s.dispatch(OWNER, op['operationId'], RaisingReceiver(msg))['operation']
    assert got['dispatchState'] == 'failed' and got['attempts'] == 1
    assert got['lastError']['message'].startswith('Card binding CAS conflict')
    assert got['lastError']['class'] == 'RuntimeError'
    assert secret not in json.dumps(got) and 'example.com' not in json.dumps(got)
    assert 'Card binding CAS conflict' in caplog.text and secret not in caplog.text


@pytest.mark.parametrize('message', [
    'exact session has no unique live owner on this runtime',
    'exact session is lazy or rotated',
    'exact session is detached; reattach its owner first',
])
def test_closed_owner_chat_is_a_wait_not_a_counted_failure(tmp_path, message):
    """Startup reconciliation runs before Desktop loads any chat, so prompt.admit can only answer 'no unique live
    owner'. Counting that burned the 240-attempt budget in an hour; after that, nothing retried the decision even
    while its chat was open (t_01a199c0a2e3ea8b)."""
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    for _ in range(3):
        got = s.dispatch(OWNER, op['operationId'], RaisingReceiver(message))['operation']
    assert got['dispatchState'] == 'failed' and got['attempts'] == 0
    assert got['ownerWaits'] == 3 and got['error'] == core.OWNER_WAIT_ERROR
    ok = DurableReceiver(tmp_path / 'receiver.json'); ok.fail = False
    # attempts stayed 0, so the capped background retrier still picks it up once the chat is open
    assert s.reconcile(OWNER, ok, max_attempts=1)[0]['operation']['dispatchState'] == 'delivered'


def test_client_timing_endpoint_logs_one_joinable_line(tmp_path, caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load_module('timing_api', ROOT / 'dashboard' / 'plugin_api.py')
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']['operationId']
    receiver = DurableReceiver(tmp_path / 'receiver.json'); receiver.fail = False
    before = s.dispatch(OWNER, op, receiver)
    app = FastAPI(); app.include_router(api.create_router(
        ledger_dir=tmp_path / 'ledger', action_store=s,
        owner_resolver=lambda session: dict(OWNER, storedSessionId=session)))
    client = TestClient(app)
    body = dict(session='session-a', operation=op, posted_ms=13.0, responded_ms=53.5,
                refreshed_ms=173.5, painted_ms=190.2)
    with caplog.at_level('INFO', logger='user-steps'):
        assert client.post('/client-timing', json=body).json() == {'ok': True}
        for change in [dict(painted_ms=-1), dict(x=1), dict(posted_ms=54),
                       dict(responded_ms=True), dict(refreshed_ms='174'),
                       dict(painted_ms=600001), dict(operation='line\nbreak')]:
            assert client.post('/client-timing', json={**body, **change}).status_code == 422
        assert client.post('/client-timing', json={**body, 'operation': 'unknown'}).status_code == 404
        assert client.post('/client-timing', json={**body, 'session': 'other'}).status_code == 404
    lines = [r.getMessage() for r in caplog.records if 'Decision client timing' in r.getMessage()]
    assert lines == [f'Decision client timing operation={op} owner_ms=13.0 post_ms=40.5 refresh_ms=120.0 paint_ms=16.7 total_ms=190.2']
    assert s.operation(OWNER, op) == before
    with caplog.at_level('INFO', logger='user-steps'):
        early = {**body, 'visible_ms': 70.0, 'painted_ms': 70.0, 'refreshed_ms': 2053.5}
        assert client.post('/client-timing', json=early).status_code == 200
        for change in [dict(visible_ms=True), dict(visible_ms='70'), dict(visible_ms=-1),
                       dict(visible_ms=600001), dict(visible_ms=71.0), dict(painted_ms=50.0),
                       dict(refreshed_ms=50.0)]:
            assert client.post('/client-timing', json={**early, **change}).status_code == 422
    lines = [r.getMessage() for r in caplog.records if r.name == 'user-steps' and r.getMessage().startswith('Decision client timing ')]
    assert lines[-1].endswith('refresh_ms=2000.0 paint_ms=0.0 total_ms=70.0 visible_ms=70.0')
    assert s.operation(OWNER, op) == before


def test_approve_grants_the_permission_required_limits_it_showed(tmp_path):
    """Nothing ever filled permissionGrants, so an approved request with a permissionRequired limit could never
    be acted on: the owner chat kept asking for a "separate owner permission" no surface could give, and the
    Cogamer Tools cards looped for a day (2026-11-24). The person approving IS the owner: Approve grants the
    flagged limits of the exact revision it approved, nothing else; revise/decline grant nothing."""
    s = store(tmp_path); req = request()
    req['limits'] = [dict(key='iam', value='one secret ARN', permissionRequired=True),
                     dict(key='spend', value='none', permissionRequired=False)]
    r = s.create(OWNER, req)
    op = s.decide(OWNER, decision(r))['operation']
    assert op['intent']['permissionGrants'] == [dict(key='iam', value='one secret ARN')]
    prompt_delivery = load_module('grant_prompt_delivery', ROOT / 'prompt_delivery.py')
    text = prompt_delivery.PromptAdmission._params(op)['text']
    assert 'need independent owner permission' not in text
    assert 'grants the permissionRequired limits in permissionGrants' in text
    other = request(); other['limits'] = req['limits']
    declined = s.create(OWNER, other)
    op = s.decide(OWNER, decision(declined, key='key-2', choice='decline'))['operation']
    assert op['intent']['permissionGrants'] == []
    assert 'need independent owner permission' in prompt_delivery.PromptAdmission._params(op)['text']


def test_approve_without_flagged_limits_keeps_the_admitted_text(tmp_path):
    """An operation without grants keeps its exact admission text: a restarted readback recomputes its digest."""
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    assert op['intent']['permissionGrants'] == []
    prompt_delivery = load_module('nogrant_prompt_delivery', ROOT / 'prompt_delivery.py')
    assert 'permissionRequired items need independent owner permission; no GPU permission is granted here.' in \
        prompt_delivery.PromptAdmission._params(op)['text']
