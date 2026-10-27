"""Review regressions using isolated stores/boards and explicit runtime doubles."""
import copy
import json
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import core, load_module, ROOT, OWNER, store, request, decision
from test_delivery import cards, Runs
from test_card_delivery import SQLiteCards, setup


def plugin_at(tmp_path, monkeypatch):
    plugin = load_module('review_plugin', ROOT / '__init__.py')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.cards_mod, '_home', lambda: tmp_path)
    return plugin


def test_approval_hook_during_admission_does_not_deny_recorded_scope(tmp_path, monkeypatch):
    plugin = plugin_at(tmp_path, monkeypatch)
    s = plugin.core.ActionStore()
    r = s.create(OWNER, request())
    unrelated = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    class WarmRuntime(Runs):
        def deliver(self, operation):
            assert s.get(OWNER, r['requestId'])['state'] == 'open'
            assert s.operation(OWNER, oid=op['operationId'])['operation']['dispatchStartedAt']
            note = plugin.pre_llm_call(session_id=OWNER['storedSessionId'], platform='desktop')['context']
            assert r['requestId'] not in note
            assert unrelated['requestId'] in note
            assert 'Only confirmed decision receipts authorize' not in note
            assert 'admitted user decision' in note
            return super().deliver(operation)
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=WarmRuntime())
    assert s.dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'delivered'


@pytest.mark.parametrize('text', ['Change 00~plan', 'Change [200~plan', 'Change ^[[201~plan', 'Change 01~ next', '~[[e' * 4])
def test_sanitizer_rejection_leaves_no_frozen_decision(tmp_path, text):
    s = store(tmp_path); r = s.create(OWNER, request())
    with pytest.raises(core.ActionError) as error:
        s.decide(OWNER, decision(r, choice='revise', text=text))
    assert error.value.status == 422
    assert 'resolution' not in s.get(OWNER, r['requestId'])
    assert not s.pending_owners()
    assert s.decide(OWNER, decision(r, choice='revise', text='Clean plan'))['operation']


def test_scope_markers_rejected_before_decision_too(tmp_path):
    s = store(tmp_path); r = request(); r['scope']['markdown'] = 'Implement 00~change'
    r = s.create(OWNER, r)
    with pytest.raises(core.ActionError): s.decide(OWNER, decision(r))
    replacement = copy.deepcopy(r); replacement['scope']['markdown'] = 'Clean scope'
    assert s.revise(OWNER, r['requestId'], 1, replacement)['revision'] == 2


def test_busy_admission_cannot_publish_card_approval(tmp_path):
    adapter, _, path, op = setup(tmp_path, 'approve')
    class Busy(Runs):
        def deliver(self, op): raise RuntimeError('busy owner')
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=Busy(), card_receiver=adapter)
    assert store(tmp_path).dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'failed'
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 0
        assert c.execute('SELECT state FROM user_steps_card_bindings').fetchone()[0] == 'bound'


def test_card_failure_after_admission_reconciles_same_turn(tmp_path):
    adapter, backend, path, op = setup(tmp_path, 'approve')
    prompt = load_module('review_prompt', ROOT / 'prompt_delivery.py')
    accepted = {}; starts = []
    def rpc(method, params):
        oid = params['operation_id']
        if method == 'prompt.admit':
            starts.append(oid)
            accepted[oid] = dict({k: v for k, v in params.items() if k != 'text'},
                                 status='accepted', user_row_id=1, message_uid='uid')
        return accepted.get(oid, {'status': 'absent'})
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=prompt.PromptAdmission(rpc=rpc), card_receiver=adapter)
    backend.fail = True
    assert store(tmp_path).dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'failed'
    assert starts == [op['operationId']]
    backend.fail = False
    results = store(tmp_path).reconcile(OWNER, receiver)
    assert results[0]['operation']['dispatchState'] == 'delivered'
    assert starts == [op['operationId']]
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1


def router_app(tmp_path, s, receiver):
    api = load_module('review_api', ROOT / 'dashboard' / 'plugin_api.py')
    app = FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path, action_store=s, receiver=receiver,
        owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid)))
    return app


def test_startup_recovers_crashed_pending_and_checks_owner(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    foreign = dict(OWNER, gatewayId='foreign')
    other = request(); other['owner'] = foreign
    other = s.create(foreign, other)
    foreign_op = s.decide(foreign, decision(other))['operation']
    class Crash:
        def deliver(self, op): raise KeyboardInterrupt('process died')
    with pytest.raises(KeyboardInterrupt): s.dispatch(OWNER, op['operationId'], Crash())
    assert s.operation(OWNER, oid=op['operationId'])['operation']['dispatchState'] == 'pending'
    runs = Runs()
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs)
    with TestClient(router_app(tmp_path, store(tmp_path), receiver)) as client:
        result = client.get('/operations/' + op['operationId'], params={'session': OWNER['storedSessionId']}).json()
        assert result['operation']['dispatchState'] == 'delivered'
        assert len(runs.rows) == 1
    assert s.operation(foreign, oid=foreign_op['operationId'])['operation']['attempts'] == 0


def test_manual_recovery_after_busy_startup_and_session_isolation(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    class Runtime(Runs):
        busy = True
        def deliver(self, op):
            if self.busy: raise RuntimeError('cold owner')
            return super().deliver(op)
    runs = Runtime(); receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs)
    with TestClient(router_app(tmp_path, s, receiver)) as client:
        assert s.operation(OWNER, oid=op['operationId'])['operation']['dispatchState'] == 'failed'
        url = '/operations/' + op['operationId'] + '/retry'
        assert client.post(url, params={'session': 'sibling'}).status_code == 404
        runs.busy = False
        result = client.post(url, params={'session': OWNER['storedSessionId']}).json()
        assert result['operation']['dispatchState'] == 'delivered'
        assert client.post('/reconcile', params={'session': OWNER['storedSessionId']}).json() == {'results': []}
        assert len(runs.rows) == 1


def test_api_sanitizer_422_without_side_effects(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    runs = Runs(); receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs)
    with TestClient(router_app(tmp_path, s, receiver)) as client:
        body = decision(r, choice='revise', text='Change 00~scope'); body.pop('requestId')
        body['session'] = OWNER['storedSessionId']
        response = client.post('/requests/' + r['requestId'] + '/decisions', json=body)
        assert response.status_code == 422
        assert not runs.calls
        assert not s.pending_owners()


class ProducerCards(SQLiteCards):
    def create_task(self, c, **kwargs):
        assert kwargs['initial_status'] == 'blocked'
        assert kwargs['parents'] == ()
        with self.write_txn(c):
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,NULL,NULL)',
                      ('created', 'blocked', kwargs['body'], kwargs['session_id'], kwargs['idempotency_key']))
        return 'created'


def producer_setup(tmp_path, monkeypatch):
    plugin = plugin_at(tmp_path, monkeypatch)
    path = tmp_path / 'kanban.db'
    with sqlite3.connect(path) as c:
        c.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,body TEXT,session_id TEXT,idempotency_key TEXT,claim_lock TEXT,current_run_id INTEGER); CREATE TABLE task_comments(id INTEGER PRIMARY KEY,task_id TEXT,author TEXT,body TEXT);')
    adapter = cards.QualifiedCardDelivery(home=tmp_path, backend=ProducerCards())
    monkeypatch.setattr(plugin.cards_mod, 'QualifiedCardDelivery', lambda: adapter)
    return plugin, adapter, path


def producer_payload():
    payload = request()
    for key in ('owner', 'requestId', 'revision'): payload.pop(key)
    return payload


def test_producer_creates_binds_before_publish_and_rebinds_revision(tmp_path, monkeypatch):
    plugin, adapter, path = producer_setup(tmp_path, monkeypatch)
    payload = producer_payload()
    out = json.loads(plugin.publish_action_request({'request': payload, 'decisionCard': {'board': 'default'}}, session_id=OWNER['storedSessionId']))
    r = out['request']; ref = r['aliases'][0]
    s = plugin.core.ActionStore()
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT request FROM user_steps_card_bindings').fetchone()[0] == cards.DecisionInbox._encode(r)
        assert c.execute('SELECT idempotency_key FROM tasks').fetchone()[0] == adapter.creation_key(r)
    op = s.decide(OWNER, decision(r, choice='decline'))['operation']
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=Runs(), card_receiver=adapter)
    assert s.dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'delivered'
    payload['aliases'] = [ref]; payload['scope']['markdown'] = 'Replacement plan'
    revised = json.loads(plugin.publish_action_request({'request': payload, 'requestId': r['requestId'], 'expectedRevision': 1}, session_id=OWNER['storedSessionId']))['request']
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT revision,state FROM user_steps_card_bindings').fetchone() == (2, 'bound')
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
    assert revised['revision'] == 2


def test_producer_rejects_arbitrary_alias_without_publishing(tmp_path, monkeypatch):
    plugin, _, path = producer_setup(tmp_path, monkeypatch)
    payload = producer_payload()
    payload['aliases'] = [dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', board='default', id='existing')]
    result = json.loads(plugin.publish_action_request({'request': payload}, session_id=OWNER['storedSessionId']))
    assert 'error' in result
    assert plugin.core.ActionStore().requests(OWNER) == []
    with sqlite3.connect(path) as c: assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0


def test_producer_binding_failure_rolls_back_exposure(tmp_path, monkeypatch):
    plugin, adapter, _ = producer_setup(tmp_path, monkeypatch)
    def fail(*args): raise RuntimeError('fenced binding')
    monkeypatch.setattr(adapter, 'bind_request', fail)
    result = json.loads(plugin.publish_action_request({'request': producer_payload(), 'decisionCard': {'board': 'default'}}, session_id=OWNER['storedSessionId']))
    assert 'error' in result
    assert plugin.core.ActionStore().requests(OWNER) == []
