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
    assert client.get('/requests', params={'session':'session-a'}).json() == {'requests':[r]}
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


def test_identical_extracted_titles_do_not_merge_and_model_cannot_decide(tmp_path):
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
