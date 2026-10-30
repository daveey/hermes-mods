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
    data = response.json(); assert data['operation']['dispatchState'] == 'delivered'
    assert client.post('/requests/'+r['requestId']+'/decisions', json=body).json() == data
    assert client.get('/operations', params={'session':'session-a','idempotencyKey':'api'}).json() == data
    assert client.get('/operations/'+data['operation']['operationId'], params={'session':'session-a'}).json() == data
    assert client.post('/resolve', json={'id':'legacy','session':'session-a'}).status_code == 410


def test_producer_and_extraction_are_separate(tmp_path, monkeypatch):
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
    plugin.transform_llm_output('Should I change the example?', 'session-a', 'desktop')
    requests = plugin.core.ActionStore().requests(OWNER)
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


def test_identical_extracted_titles_share_one_open_tile_and_model_cannot_decide(tmp_path):
    # Was "do not merge" (2 requests). That default minted a For-you tile per restatement of one open ask
    # (hub chat 37ae77: 3 tiles for t_54aa263b). An exact re-ask of an OPEN, undecided request reuses it;
    # the model's "resolved" still decides nothing, and the request stays incomplete.
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
    assert len(s.requests(OWNER)) == 1
    assert [x['requestId'] for x in second['steps']] == [first['steps'][0]['requestId']]
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
    assert first['operation']['dispatchState'] == 'failed'
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
