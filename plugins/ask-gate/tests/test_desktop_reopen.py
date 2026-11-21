"""Desktop restart asks require a recent, non-superseded ETA result."""
import time
import pytest
from conftest import llm_returning

ASK = 'Please quit and reopen Desktop.'
READY = 'example-mac: has abc123; a Desktop quit + reopen runs it'
NOT_READY = 'example-mac: rebuilt Desktop app is NOT ready yet'


def reply(gate, text=ASK, **kw):
    gate.LLM = llm_returning([('ok', 'physical act')])
    return gate.pre_final_gate(session_id='s1', platform='desktop', final_response=text, **kw)


def eta(gate, result):
    gate.post_tool_call(session_id='s1', tool_name='terminal',
                        args={'command': 'hermes-core-wt eta abc123'}, result=result)


def test_reply_without_eta_is_held(gate):
    result = reply(gate, '::user-step{action="quit and reopen Desktop"}')
    assert result and result['hold']
    assert 'Desktop reopen ask without readiness proof' in result['nudge']


def test_recent_ready_allows(gate):
    eta(gate, READY)
    assert reply(gate) is None


@pytest.mark.parametrize('results', [[NOT_READY], [READY, NOT_READY]])
def test_not_ready_holds(gate, results):
    for result in results:
        eta(gate, result)
    assert reply(gate)['hold']


def test_other_host_not_ready_does_not_invalidate(gate):
    eta(gate, READY + '\nother-mac: NOT ready')
    assert reply(gate) is None


def test_expired_proof_holds(gate, monkeypatch):
    eta(gate, READY)
    now = time.time()
    monkeypatch.setattr(gate.desktop_reopen.time, 'time', lambda: now + 901)
    assert reply(gate)['hold']


@pytest.mark.parametrize('text', [ASK, 'reopen Desktop', 'restart Desktop', 'relaunch Hermes',
    'restart the app to apply', '⌘Q and reopen', 'Update ready — restart to apply'])
def test_phrasings(gate, text):
    assert reply(gate, text)['hold']


@pytest.mark.parametrize('text', ["don't reopen yet", "Don't reopen Desktop yet.",
    'gateway restarted', 'Restart the gateway.', 'Restart Hermes gateway.', 'Relaunch Hermes serve.',
    '⌘R reload', 'you restarted at 3pm',
    'You restarted Desktop at 3pm.', 'We will not ask you to reopen Desktop yet.'])
def test_controls(gate, text):
    if text in ('Restart Hermes gateway.', 'Relaunch Hermes serve.'):
        # Other ask-gate policies may hold agent-owned backend handoffs; this lint must not.
        assert gate._desktop_reopen_gate('s1', 'pre_final_gate', text) is None
    else:
        assert reply(gate, text) is None


@pytest.mark.parametrize('tool,args', [
    ('action_request', {'request': {'kind': 'approval', 'title': 'Apply update',
                                   'scope': {'markdown': 'Please restart Desktop.'}}}),
    ('action_request', {'request': {'title': 'Apply update', 'scope': {'markdown': '⌘Q and reopen'}}}),
    ('action_request', {'request': {'title': 'Reopen Desktop'}}),
    ('kanban_create', {'human_request': {'action': 'Reopen Desktop'}}),
    ('kanban_block', {'human_request': {'action': 'Reopen Desktop'}}),
])
def test_structured_surfaces(gate, tool, args):
    result = gate.pre_tool_call(session_id='s1', tool_name=tool, args=args)
    assert result['action'] == 'block'
    assert 'Desktop reopen ask without readiness proof' in result['message']


def test_errors_fail_open(gate, monkeypatch):
    monkeypatch.setattr(gate.desktop_reopen, 'ready', lambda *a, **kw: 1 / 0)
    assert reply(gate) is None


def test_hold_budget_shared_per_session(gate):
    for _ in range(gate.desktop_reopen.MAX_HOLDS):
        assert reply(gate)['hold']
    assert reply(gate) is None


def test_arguments_and_unrelated_outputs_are_not_proof(gate):
    gate.LOG.add('s1', 'terminal', 'echo "' + READY + '"', READY)
    assert reply(gate)['hold']


def test_previous_turn_proof_survives_log_reset(gate):
    eta(gate, READY)
    gate.LOG.reset('s1')
    assert reply(gate) is None


def test_ready_then_not_ready_in_same_result(gate):
    eta(gate, READY + '\n' + NOT_READY)
    assert reply(gate)['hold']


@pytest.mark.parametrize('age,allowed', [(0, True), (900, True), (901, False), (-1, False)])
def test_timestamped_turn_messages(gate, age, allowed):
    now = time.time()
    messages = [
        {'role': 'assistant', 'tool_calls': [{'id': 'eta1', 'function': {
            'name': 'terminal', 'arguments': '{"command":"hermes-core-wt eta abc123"}'}}]},
        {'role': 'tool', 'tool_call_id': 'eta1', 'content': READY, 't': now - age},
    ]
    # Freeze the boundary so exactly 900 seconds is unambiguous.
    from unittest.mock import patch
    with patch.object(gate.desktop_reopen.time, 'time', return_value=now):
        result = reply(gate, turn_messages=messages)
    assert (result is None) == allowed


def test_untimed_history_is_not_fresh_proof(gate):
    messages = [
        {'role': 'assistant', 'tool_calls': [{'id': 'eta1', 'function': {
            'name': 'terminal', 'arguments': 'hermes-core-wt eta abc123'}}]},
        {'role': 'tool', 'tool_call_id': 'eta1', 'content': READY},
    ]
    assert reply(gate, turn_messages=messages)['hold']


def test_same_user_step_draft_with_and_without_proof(gate, monkeypatch):
    # Isolate unrelated lifecycle reconciliation, not detection or proof reading.
    monkeypatch.setattr(gate.user_step_lifecycle, 'preflight', lambda *a: None)
    draft = '::user-step{id="us_1234567890" s="s1" t="After that, quit and reopen Desktop to see it."}'
    assert reply(gate, draft)['hold']
    eta(gate, {'output': READY})
    assert reply(gate, draft) is None


def test_session_isolation_and_telemetry(gate, monkeypatch):
    events = []
    monkeypatch.setattr(gate.core, 'log_event', lambda event, **kw: events.append((event, kw)))
    gate.post_tool_call(session_id='other', tool_name='terminal',
                       args='hermes-core-wt eta abc123', result=READY)
    assert reply(gate)['hold']
    assert any(event == 'desktop_reopen_hold' and kw['session'] == 's1' for event, kw in events)


def test_later_ready_restores_proof(gate):
    eta(gate, NOT_READY)
    eta(gate, READY)
    assert reply(gate) is None
