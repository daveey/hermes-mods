"""Production adapters: temporary databases and fake runtime, never live state."""
import copy
import sys
import types
import sqlite3
import pytest
from test_decisions import core, load_module, ROOT, OWNER, store, request, decision

cards = load_module('production_cards_tests', ROOT / 'cards.py')


def test_runtime_owner_uses_install_and_scoped_profile(monkeypatch):
    monkeypatch.setitem(sys.modules, 'hermes_cli.install_identity', types.SimpleNamespace(get_install_id=lambda: 'a' * 32))
    monkeypatch.setitem(sys.modules, 'hermes_cli.profiles', types.SimpleNamespace(current_profile_name=lambda: 'scoped'))
    monkeypatch.setenv('HERMES_SESSION_PROFILE', 'wrong-launch-profile')
    monkeypatch.setenv('HERMES_GATEWAY_ID', 'unrecognized-env')
    assert core.server_owner('stored') == dict(gatewayId='install:' + 'a' * 32, profile='scoped', storedSessionId='stored')


def test_missing_runtime_identity_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules, 'hermes_cli.install_identity', types.SimpleNamespace(get_install_id=lambda: None))
    monkeypatch.setitem(sys.modules, 'hermes_cli.profiles', types.SimpleNamespace(current_profile_name=lambda: 'default'))
    monkeypatch.setenv('HERMES_GATEWAY_ID', 'not-an-authority')
    with pytest.raises(core.ActionError): core.server_owner('stored')


class Runs:
    def __init__(self): self.rows = {}; self.calls = []
    def deliver(self, op):
        self.calls.append(op)
        self.rows.setdefault(op['operationId'], {'destination': 'test_runtime', 'operationId': op['operationId']})
        return self.rows[op['operationId']]
    def readback(self, op, receipt): return receipt == self.rows.get(op['operationId'])


@pytest.mark.parametrize('effect,wakes', [('approve', 1), ('revise', 1), ('decline', 0)])
def test_production_delivery_wakes_only_authorized_turns(tmp_path, effect, wakes):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r, choice=effect, text='Change the plan'))['operation']
    runs = Runs()
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs)
    result = s.dispatch(OWNER, op['operationId'], receiver)
    assert result['operation']['dispatchState'] == 'delivered'
    assert len(runs.calls) == wakes
    assert s.dispatch(OWNER, op['operationId'], receiver) == result
    assert len(runs.calls) == wakes
    assert result['operation']['intent']['permissionGrants'] == []


def test_remote_alias_fails_before_any_wake(tmp_path):
    s = store(tmp_path)
    ref = dict(gatewayId='remote', profile='default', type='kanban', board='default', id='card')
    r = s.create(OWNER, request(aliases=[ref])); op = s.decide(OWNER, decision(r))['operation']
    runs = Runs(); receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs)
    assert s.dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'failed'
    assert not runs.calls


def test_card_revision_uses_real_core_atomic_comment(tmp_path, monkeypatch):
    kb = pytest.importorskip('hermes_cli.kanban_db')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(str(tmp_path))
    try:
        path = tmp_path / 'kanban.db'
        from agent.delegation_context import kanban_path_is_fenced
        if kanban_path_is_fenced(path):
            pytest.skip('Core fences this delegated process even for scratch DBs; run from a top-level test process')
        ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', board='default', id='card')
        s = store(tmp_path); r = s.create(OWNER, request(aliases=[ref]))
        adapter = cards.QualifiedCardDelivery(home=tmp_path)
        with sqlite3.connect(path) as c:
            c.executescript(kb.SCHEMA_SQL)
            c.execute("INSERT INTO tasks (id,title,status,created_at,session_id,idempotency_key) VALUES ('card','Synthetic decision','blocked',1,?,?)",
                      (OWNER['storedSessionId'], adapter.creation_key(r)))
        adapter.bind(r, ref)
        op = s.decide(OWNER, decision(r, choice='revise', text='Add correctness tests'))['operation']
        first = adapter.deliver(op)
        assert first == cards.QualifiedCardDelivery(home=tmp_path).deliver(op)
        assert adapter.readback(op, first)
        runs = Runs()
        receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs, card_receiver=adapter)
        assert s.dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'delivered'
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM task_events WHERE kind='commented'").fetchone()[0] == 1
            assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
        for effect in ('approve', 'decline'):
            changed = copy.deepcopy(op); changed['intent']['effect'] = effect
            with pytest.raises(RuntimeError, match='payload conflict'): adapter.deliver(changed)
    finally:
        reset_hermes_home_override(token)


def test_default_api_does_not_report_passive_inbox_as_delivered(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load_module('production_route_test', ROOT / 'dashboard' / 'plugin_api.py')
    s = store(tmp_path); r = s.create(OWNER, request())
    monkeypatch.delitem(sys.modules, 'tui_gateway.server', raising=False)
    app = FastAPI(); app.include_router(api.create_router(
        ledger_dir=tmp_path, action_store=s, owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid)))
    client = TestClient(app)
    body = dict(session=OWNER['storedSessionId'], expectedRevision=1, choiceId='approve', idempotencyKey='api')
    result = client.post('/requests/' + r['requestId'] + '/decisions', json=body).json()
    assert result['operation']['dispatchState'] == 'failed'
    assert result['request']['state'] == 'open'
    assert s.operation(OWNER, key='api')['operation']['operationId'] == result['operation']['operationId']


def test_slow_dispatch_does_not_lock_other_requests_or_downgrade_success(tmp_path):
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    s = store(tmp_path); a = s.create(OWNER, request()); b = s.create(OWNER, request())
    op = s.decide(OWNER, decision(a))['operation']
    entered, release = Event(), Event()
    class Slow:
        def deliver(self, op):
            entered.set()
            assert release.wait(5)
            raise RuntimeError('late failure')
        def readback(self, *args): return False
    class Good:
        def deliver(self, op): return {'id': op['operationId']}
        def readback(self, *args): return True
    with ThreadPoolExecutor(max_workers=3) as pool:
        slow = pool.submit(s.dispatch, OWNER, op['operationId'], Slow())
        assert entered.wait(2)
        try:
            other = pool.submit(s.decide, OWNER, decision(b, key='other')).result(timeout=2)
            assert other['operation']['dispatchState'] == 'pending'
            good = pool.submit(s.dispatch, OWNER, op['operationId'], Good()).result(timeout=2)
            assert good['operation']['dispatchState'] == 'delivered'
        finally:
            release.set()
        assert slow.result(timeout=2)['operation']['dispatchState'] == 'delivered'
