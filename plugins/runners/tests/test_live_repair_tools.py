"""Registered tool entry paths use live repair state and retain authority fences."""
import json
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from plugins.runners import register
from plugins.runners.tests.test_repairs import env, core_types, PRINCIPAL, reserve
from plugins.runners.tests.test_receipts import make_store, OP


@pytest.fixture
def live_tools(env, monkeypatch, tmp_path):
    from plugins.runners.dashboard import plugin_api as api
    from plugins.runners import operations, receipts
    e = env
    row = reserve(e)
    e.domain.start(row['id'], principal=PRINCIPAL)
    profile = e.service.config['owner_profile']
    server = SimpleNamespace(_sessions_lock=threading.RLock(), _profile_home=lambda _: None,
        _session_auth_user_id=lambda r: r['auth_user_id'],
        _sessions={'runtime': dict(session_key='chat_first', profile_home=None,
            auth_user_id=PRINCIPAL, agent=SimpleNamespace(session_id='chat_compressed'))})
    module = ModuleType('tui_gateway')
    module.server = server
    monkeypatch.setitem(sys.modules, 'tui_gateway', module)
    service_id = 'service_' + 'a' * 32
    e.service.config['bindings'] = [dict(runner_id=e.runner['id'], service_ref=service_id, host_id=e.host['id'])]
    e.runner['display_name'] = 'example-canary-1'
    path = make_store(tmp_path, ns=json.dumps(['install:test', profile]))
    monkeypatch.setattr(receipts, 'store_path', lambda: path)
    calls = []
    monkeypatch.setattr(operations, 'BrokerClient', lambda: SimpleNamespace(
        request=lambda body: calls.append(body) or dict(ok=True)))
    monkeypatch.setattr(api, 'service', lambda: e.service)
    monkeypatch.setattr(api, 'active_profile', lambda: profile)
    monkeypatch.setattr(api, 'repair_domain', lambda _: e.domain)
    tools = {}
    register(SimpleNamespace(register_tool=lambda **kw: tools.update({kw['name']: kw['handler']})))
    status = e.core.rows[row['session']['start_request_id']]
    status.update(active_turn=True, first_turn_state='running', reconciliation='running')
    args = dict(repair_id=row['id'], operation_id=OP)
    operate_args = dict(repair_id=row['id'], operation='diagnose', target_id=service_id)
    def invoke(tool, sid='runtime'):
        return json.loads(tools[tool](args if tool == 'runners_receipt' else operate_args, session_id=sid))
    return SimpleNamespace(e=e, row=row, status=status, server=server, calls=calls, invoke=invoke)


def persist(e, row, state, active=1):
    with e.domain._db(transaction=True) as db:
        stored = json.loads(db.execute('SELECT payload FROM repair_domain_records WHERE id=?', (row['id'],)).fetchone()[0])
        stored['state'] = state
        db.execute('UPDATE repair_domain_records SET payload=?,active=? WHERE id=?',
                   (json.dumps(stored), active, row['id']))


@pytest.mark.parametrize('state', ['unknown_outcome', 'paused'])
@pytest.mark.parametrize('sid', ['runtime', 'chat_first', 'chat_compressed'])
def test_receipt_reconciles_resumed_chat(live_tools, state, sid):
    t = live_tools
    persist(t.e, t.row, state)
    assert t.invoke('runners_receipt', sid)['verified'] is True


@pytest.mark.parametrize('state', ['unknown_outcome', 'paused'])
def test_operate_reconciles_resumed_chat(live_tools, state):
    t = live_tools
    persist(t.e, t.row, state)
    assert t.invoke('runners_operate')['ok'] is True
    assert len(t.calls) == 1


@pytest.mark.parametrize('tool', ['runners_receipt', 'runners_operate'])
@pytest.mark.parametrize('terminal', ['closed', 'failed', 'archived'])
def test_terminal_repair_stays_refused(live_tools, tool, terminal):
    t = live_tools
    if terminal == 'archived':
        t.status.update(archived=True, active_turn=False)
    else:
        persist(t.e, t.row, terminal, active=0)
    out = t.invoke(tool)
    assert out.get('reason', out.get('refusal')) == 'repair_not_active'
    assert not t.calls


@pytest.mark.parametrize('tool', ['runners_receipt', 'runners_operate'])
@pytest.mark.parametrize('other', ['principal', 'session', 'owner', 'revision', 'entity'])
def test_other_authority_stays_refused(live_tools, tool, other):
    t = live_tools
    persist(t.e, t.row, 'active')
    if other == 'principal':
        t.server._sessions['runtime']['auth_user_id'] = 'other-principal'
    elif other == 'session':
        t.server._sessions['runtime'].update(session_key='other-chat', agent=SimpleNamespace(session_id='other-tip'))
    elif other == 'owner':
        t.e.service.config['owner_epoch'] += 1
    elif other == 'revision':
        t.e.service.config['config_revision'] += 1
        t.e.core.store = SimpleNamespace(status=lambda *a: t.status)
    else:
        # The requested target/receipt must not authorize an unrelated repair subject.
        with t.e.domain._db(transaction=True) as db:
            stored = json.loads(db.execute('SELECT payload FROM repair_domain_records WHERE id=?', (t.row['id'],)).fetchone()[0])
            stored['entity_id'] = 'runner_' + '9' * 32
            db.execute('UPDATE repair_domain_records SET payload=? WHERE id=?', (json.dumps(stored), t.row['id']))
    out = t.invoke(tool)
    assert not out.get('verified', out.get('ok', False))
    assert not t.calls


def test_idle_running_first_turn_remains_unknown(live_tools):
    t = live_tools
    t.status.update(active_turn=False, reconciliation='unknown_outcome', first_turn_state='running')
    result = t.e.domain.reconcile(t.row['id'], principal=PRINCIPAL)
    assert result['state'] == 'unknown_outcome'
    assert t.e.domain.get(t.row['id'], principal=PRINCIPAL)['state'] == 'unknown_outcome'


@pytest.mark.parametrize('tool', ['runners_receipt', 'runners_operate'])
def test_lineage_tip_only_binding(live_tools, tool):
    t = live_tools
    t.status['resolved_session_id'] = 'chat_compressed'
    t.server._sessions['runtime']['session_key'] = 'unmatched-old-key'
    out = t.invoke(tool, 'chat_compressed')
    assert out.get('verified', out.get('ok')) is True


@pytest.mark.parametrize('tool', ['runners_receipt', 'runners_operate'])
def test_principal_change_during_reconciliation_refused(live_tools, tool, monkeypatch):
    t = live_tools
    reconcile = t.e.domain.reconcile
    def changed(*a, **kw):
        result = reconcile(*a, **kw)
        t.server._sessions['runtime']['auth_user_id'] = 'different-principal'
        return result
    monkeypatch.setattr(t.e.domain, 'reconcile', changed)
    out = t.invoke(tool)
    assert out.get('reason', out.get('refusal')) == 'repair_access_denied'
    assert not t.calls
