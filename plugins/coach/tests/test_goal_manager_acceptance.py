"""Offline complete-turn acceptance: real manager leases, order, and verification."""
import copy
import functools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORDERS = [('coach', 'ask-gate', 'chat-closeout'),
          ('chat-closeout', 'ask-gate', 'coach')]
CANDIDATE = ('The app remains HTTP 503. Run `sudo systemctl restart example` '
             "on the server. I'll report back when it is done.")
REQUEST = 'Repair example app. Why have we not fixed it yet?'


def verdict(disposition='abandoned'):
    return dict(goal='Repair example app', actionable=True, unfinished=True,
                disposition=disposition, evidence_refs=['user:0', 'candidate'],
                boundary_kind=None, continuation_ref=None, confidence=.99)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    import socket
    import hermes_yaml as yaml
    from hermes_cli.plugins import get_plugin_manager, PluginContext
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_VERIFY_ON_STOP', '0')
    monkeypatch.setenv('COACH_LESSONS', '0')
    monkeypatch.setenv('COACH_REPLY_RULES', 'none')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    def no_network(*args, **kwargs):
        raise AssertionError('acceptance tests forbid network')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
    monkeypatch.setattr('agent.auxiliary_client.call_llm', no_network)
    (tmp_path / 'coach').mkdir()
    (tmp_path / 'coach/goal-disposition.json').write_text('{"mode":"enforce"}')
    (tmp_path / 'plugins').mkdir()
    for name in ORDERS[0]:
        (tmp_path / 'plugins' / name).symlink_to(ROOT.parent / name, target_is_directory=True)
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump({
        'plugins': {'enabled': list(ORDERS[0])},
        'agent': {'pre_verify_every_turn': True, 'max_verify_nudges': 1}}))
    events = []
    original = PluginContext.register_hook
    def recording_register(ctx, name, callback):
        if name == 'pre_final_gate':
            @functools.wraps(callback)
            def recorded(**kwargs):
                result = callback(**kwargs)
                events.append((ctx.plugin_id, kwargs['attempt'], copy.deepcopy(result)))
                return result
            recorded.acceptance_plugin = ctx.plugin_id
            return original(ctx, name, recorded)
        return original(ctx, name, callback)
    monkeypatch.setattr(PluginContext, 'register_hook', recording_register)
    manager = get_plugin_manager()
    manager.discover_and_load(force=True)
    def configure():
        ask = manager._plugins['ask-gate'].module
        monkeypatch.setattr(ask, 'LLM', lambda *a, **k: '[]')
        return manager._plugins['coach'].module._load('goal_disposition')
    def order_plugins(order):
        # Re-load individually: enabled-list order is NOT registration order.
        manifests = {n: manager._plugins[n].manifest for n in order}
        for name in order:
            manager.unload(name)
        for name in order:
            manager._load_plugin(manifests[name])
            assert not manager._plugins[name].error
        hooks = manager.iter_hook_callbacks('pre_final_gate')
        assert len(hooks) == 3
        # Wrappers retain callback identity/attributes, including guard priority.
        actual = [h.acceptance_plugin for h in hooks]
        assert actual == list(order)
        print('REGISTERED', actual)
        return configure()
    try:
        yield manager, order_plugins, configure, events
    finally:
        manager.unload()


def run_turn(monkeypatch, *, budget=6, before_call=None, texts=None, request=REQUEST, history=None, db_name="acceptance.db"):
    from run_agent import AIAgent
    with patch('model_tools.get_tool_definitions', return_value=[]), \
         patch('model_tools.check_toolset_requirements', return_value={}), \
         patch('agent.process_bootstrap.OpenAI'):
        agent = AIAgent(session_id='manager-acceptance', api_key='test-key',
                        base_url='https://example.invalid/v1', provider='openai-compat',
                        model='test/model', max_iterations=budget, quiet_mode=True,
                        skip_context_files=True, skip_memory=True, platform='cli')
    from hermes_state import SessionDB
    from hermes_constants import get_hermes_home
    agent._session_db = SessionDB(db_path=get_hermes_home() / db_name)
    agent._session_db_created = False
    agent._cached_system_prompt = 'stable isolated test prompt'
    agent.save_trajectories = False
    agent.compression_enabled = False
    agent._cleanup_task_resources = lambda *a, **k: None
    agent._save_trajectory = lambda *a, **k: None
    screen, interims, calls, persisted = [], [], [], []
    agent.stream_delta_callback = lambda text: screen.append(text) if isinstance(text, str) else None
    agent._emit_interim_assistant_message = lambda row: interims.append(copy.deepcopy(row))
    def api(kwargs):
        calls.append(copy.deepcopy(kwargs))
        if before_call:
            before_call(len(calls), agent)
        text = texts[min(len(calls)-1, len(texts)-1)] if texts else CANDIDATE
        agent._fire_stream_delta(text)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=text, tool_calls=None), finish_reason='stop')], model='test/model', usage=None)
    agent._interruptible_api_call = api
    agent._interruptible_streaming_api_call = lambda kw, on_first_delta=None: api(kw)
    result = agent.run_conversation(request, conversation_history=history)
    assert agent._session_db is not None
    persisted = agent._session_db.get_messages(agent.session_id)
    assert persisted, 'real isolated database must receive the turn'
    return result, calls, screen, interims, persisted, agent


def assert_private(out):
    result, calls, screen, interims, persisted, agent = out
    assert CANDIDATE not in result['final_response']
    assert CANDIDATE not in ''.join(screen)
    for rows in (result['messages'], interims, persisted):
        assert not any(CANDIDATE in str(row.get('content', '')) for row in rows)
        assert not any(row.get('_pre_final_gate_synthetic') for row in rows)


@pytest.mark.parametrize('order', ORDERS)
@pytest.mark.parametrize('operation', ['unload_owner', 'unload_other', 'reload'])
def test_real_manager_identity_across_complete_turn(environment, monkeypatch, order, operation):
    manager, load_order, configure, events = environment
    gd = load_order(order)
    monkeypatch.setattr(gd, 'aux_judge', lambda *a: verdict())
    owner = next(h for h in manager.iter_hook_callbacks('pre_final_gate')
                 if getattr(h, 'assess_before_holds', False))
    def transition(number, agent):
        if number != 2:
            return
        assert events[0][0] == 'coach' and events[0][2]['hold']
        if operation == 'reload':
            manager.discover_and_load(force=True)
            monkeypatch.setattr(configure(), 'aux_judge', lambda *a: verdict())
        else:
            manager.unload('coach' if operation == 'unload_owner' else 'chat-closeout')
        assert (owner in manager.iter_hook_callbacks('pre_final_gate')) == (operation == 'unload_other')
    out = run_turn(monkeypatch, before_call=transition)
    assert_private(out)
    assert 'Unresolved goal' in out[0]['final_response']
    if operation == 'unload_other':
        assert len(out[1]) == 3
        assert 'pre-final hold limit reached' in out[0]['final_response']
    else:
        assert len(out[1]) == 2
        assert 'continuation unavailable' in out[0]['final_response']
    print('CONTROL', operation, 'CALLS', len(out[1]), 'EXECUTED', [(n,a) for n,a,_ in events])


@pytest.mark.parametrize('order', ORDERS)
def test_reversed_registration_executes_interacting_real_holds(environment, monkeypatch, order):
    manager, load_order, configure, events = environment
    gd = load_order(order)
    checks = []
    def judge(*args):
        checks.append(True)
        return verdict('unknown' if len(checks) == 1 else 'abandoned')
    monkeypatch.setattr(gd, 'aux_judge', judge)
    out = run_turn(monkeypatch)
    assert_private(out)
    # Replacement consensus requires every gate to inspect each candidate;
    # priority chooses the winning nudge, not a short-circuit dispatch point.
    dispatch_order = ['coach', *(n for n in order if n != 'coach')]
    assert [(n, a) for n, a, _ in events] == [
        (name, attempt) for attempt in range(3) for name in dispatch_order]
    rounds = [events[offset:offset + 3] for offset in range(0, len(events), 3)]
    assert rounds[0][0][2] is None  # Unknown disposition leaves ordinary holds eligible.
    assert rounds[0][1][2]['hold'] is True
    assert rounds[1][0][2]['hold'] is True
    assert rounds[1][0][2]['unresolved_fallback']['goal'] == 'Repair example app'
    assert len(out[1]) == 3
    # Only the first eligible hold supplies the next model request's nudge.
    for call, winner in zip(out[1][1:], [rounds[0][1], rounds[1][0]]):
        nudges = [row for row in call['messages'] if row.get('role') == 'user']
        assert nudges[-1]['content'] == winner[2]['nudge']
    assert out[5]._pre_final_gate_attempts == 2
    assert 'Unresolved goal' in out[0]['final_response']
    assert 'pre-final hold limit reached' in out[0]['final_response']
    print('EXECUTED', [(n,a) for n,a,_ in events])


@pytest.mark.parametrize('order', ORDERS)
def test_pre_verify_cannot_duplicate_nudge_or_preview_rejected_candidate(environment, monkeypatch, order):
    from hermes_cli.plugins import PluginContext
    manager, load_order, configure, events = environment
    gd = load_order(order)
    monkeypatch.setattr(gd, 'aux_judge', lambda *a: verdict())
    verifications = []
    def verify(**kwargs):
        verifications.append(kwargs)
        return {'action': 'continue', 'message': 'ACCEPTANCE_VERIFY_NUDGE'}
    PluginContext(manager._plugins['coach'].manifest, manager).register_hook('pre_verify', verify)
    out = run_turn(monkeypatch)
    assert_private(out)
    assert len(out[1]) == 3
    assert not verifications, 'pre_verify must not see rejected candidates'
    assert out[5]._pre_final_gate_attempts == 2
    assert getattr(out[5], '_pre_verify_nudges', 0) == 0
    for call in out[1]:
        rows = call['messages']
        assert not any('ACCEPTANCE_VERIFY_NUDGE' in str(row.get('content')) for row in rows)
        for left, right in zip(rows, rows[1:]):
            assert not (left.get('role') == right.get('role') == 'user')
    assert 'Unresolved goal' in out[0]['final_response']


@pytest.mark.parametrize('order', ORDERS)
def test_last_iteration_assesses_coach_before_other_holds(environment, monkeypatch, order):
    manager, load_order, configure, events = environment
    gd = load_order(order)
    monkeypatch.setattr(gd, 'aux_judge', lambda *a: verdict())
    out = run_turn(monkeypatch, budget=1)
    assert_private(out)
    assert len(out[1]) == 1
    # Later validators still run, but cannot erase the coach's unresolved goal
    # when there is no model-call budget left to act on its winning nudge.
    assert [(n, a) for n, a, _ in events] == [
        (name, 0) for name in ['coach', *(n for n in order if n != 'coach')]]
    assert events[0][2]['hold'] is True
    assert events[0][2]['unresolved_fallback']['goal'] == 'Repair example app'
    assert out[5]._pre_final_gate_attempts == 1
    assert 'Unresolved goal: Repair example app' in out[0]['final_response']
    assert 'turn budget exhausted' in out[0]['final_response']


@pytest.mark.parametrize('order', ORDERS)
def test_pre_verify_runs_once_after_owner_accepts(environment, monkeypatch, order):
    from hermes_cli.plugins import PluginContext
    manager, load_order, configure, events = environment
    gd = load_order(order)
    checks = []
    def judge(*args):
        checks.append(True)
        # Controlled semantic response: this test covers dispatch, not judge accuracy.
        return verdict() if len(checks) == 1 else dict(
            verdict('not_action_goal'), actionable=False, unfinished=False)
    monkeypatch.setattr(gd, 'aux_judge', judge)
    seen = []
    def verify(**kwargs):
        seen.append(kwargs)
        return {'action': 'continue', 'message': 'ACCEPTANCE_VERIFY_NUDGE'}
    PluginContext(manager._plugins['coach'].manifest, manager).register_hook('pre_verify', verify)
    accepted = 'The requested analysis is complete.'
    out = run_turn(monkeypatch, texts=[CANDIDATE, accepted, accepted])
    assert_private(out)
    assert len(out[1]) == 3 and len(seen) == 1
    assert seen[0]['final_response'] == accepted and seen[0]['attempt'] == 0
    assert out[5]._pre_final_gate_attempts == 1 and out[5]._pre_verify_nudges == 1
    assert out[0]['final_response'] == accepted
    assert all(not row.get('_pre_verify_synthetic') for row in out[0]['messages'])
    nudges = [row for row in out[1][-1]['messages']
              if row.get('content') == 'ACCEPTANCE_VERIFY_NUDGE']
    assert len(nudges) == 1


def test_complete_turn_captures_before_persist_and_keeps_restriction(environment, monkeypatch):
    import json
    from hermes_state import SessionDB
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    manager, load_order, configure, events = environment
    gd = load_order(ORDERS[0])
    db = SessionDB(get_hermes_home() / 'state.db')
    db.create_session(session_id='manager-acceptance', source='cli', profile_name=get_active_profile_name())
    db.append_message('manager-acceptance', 'user', 'Do not publish')
    db.append_message('manager-acceptance', 'assistant', 'Understood.')
    history = db.get_messages('manager-acceptance')
    db.close()
    seen = []
    def judge(_, prompt):
        seen.append(json.loads(prompt)['evidence'])
        return verdict()
    monkeypatch.setattr(gd, 'aux_judge', judge)
    out = run_turn(monkeypatch, budget=1, history=history, db_name='state.db')
    assert_private(out)
    assert seen
    assert [r['content'] for r in seen[0].values() if r['role']=='user']==['Do not publish', REQUEST]
    assert 'Unresolved goal' in out[0]['final_response']


def test_unsupported_approval_cannot_clear_confirmed_hold(environment, monkeypatch):
    manager, load_order, configure, events = environment
    gd = load_order(ORDERS[0])
    seen = []
    def judge(*args):
        seen.append(True)
        return verdict() if len(seen)==1 else dict(verdict('authorization_boundary'), boundary_kind='approval')
    monkeypatch.setattr(gd, 'aux_judge', judge)
    out = run_turn(monkeypatch, request='Repair example app; you have approval to deploy.')
    assert_private(out)
    assert len(seen)==2
    assert 'continuation unavailable' in out[0]['final_response']
