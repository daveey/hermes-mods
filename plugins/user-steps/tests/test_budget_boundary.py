"""publication_buffer at the hold-budget boundary: replace only after the agent ignored this hook's own nudges.

A one-shot that hits its iteration cap gets one tool-less summary call; core then runs the gate at its
exhaustion boundary (attempt == hold_budget) without ever having held. The agent never saw a nudge and
has no tools to publish action_request, so replacing its summary with "The approval request is not
ready" destroyed the deliverable (hub session local-18520ccf..., 2026-11-13).
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = {'gatewayId': 'test', 'profile': 'default', 'storedSessionId': 'session'}
SUMMARY = ('I ran `true` 5 of the 10 requested times before the iteration cap stopped me. '
           'Want me to continue with the remaining 5 calls?')
CAP_CONTEXT = [
    {'role': 'user', 'content': '[selfheal-probe] run "true" with terminal 10 times, one call each'},
    {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1'}]},
    {'role': 'tool', 'content': '{"exit_code": 0}'},
    {'role': 'assistant', 'content': SUMMARY},
]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    mod = load('budget_boundary_plugin', ROOT / '__init__.py')
    st = mod.core.ActionStore(tmp_path / 'actions.sqlite3')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    monkeypatch.setattr(mod.core, 'server_owner', lambda _: OWNER)
    monkeypatch.setattr(mod.core, 'ActionStore', lambda **kw: st)
    monkeypatch.setattr(mod.core, '_worth_asking', lambda _: True)
    monkeypatch.setattr(mod.core, 'extract', lambda *_, **__: {
        'steps': [{'kind': 'approve', 'text': 'Want me to continue with the remaining 5 calls?'}]})
    return mod


def held(nudge):
    """The two rows core keeps in the turn for one hold (agent/turn_stop_gates.py)."""
    return [{'role': 'assistant', 'content': 'draft', '_pre_final_gate_synthetic': True},
            {'role': 'user', 'content': f'[runtime] Your draft above was NOT shown to the user.\n\n{nudge}',
             '_pre_final_gate_synthetic': True}]


def test_cap_summary_never_nudged_passes_through_with_note(plugin):
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=SUMMARY,
                                    turn_messages=CAP_CONTEXT, attempt=2, hold_budget=2)
    assert out is not None and 'replace' not in out and not out.get('hold'), out
    assert 'no permission has been granted' in out['append']


def test_cap_summary_after_one_nudge_still_passes_through(plugin):
    ctx = CAP_CONTEXT[:3] + held(plugin.PUBLISH_NUDGE) + CAP_CONTEXT[3:]
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=SUMMARY,
                                    turn_messages=ctx, attempt=2, hold_budget=2)
    assert 'replace' not in out and out.get('append'), out


def test_other_gates_holds_do_not_count_as_our_nudge(plugin):
    ctx = CAP_CONTEXT[:3] + held('Verify your claim first.') * 2 + CAP_CONTEXT[3:]
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=SUMMARY,
                                    turn_messages=ctx, attempt=2, hold_budget=2)
    assert 'replace' not in out and out.get('append'), out


def test_ignored_every_nudge_is_still_replaced(plugin):
    ctx = CAP_CONTEXT[:3] + held(plugin.PUBLISH_NUDGE) * 2 + CAP_CONTEXT[3:]
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=SUMMARY,
                                    turn_messages=ctx, attempt=2, hold_budget=2)
    assert out == {'replace': plugin.NOT_READY}


def test_zero_hold_budget_keeps_replacing(plugin):
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=SUMMARY,
                                    turn_messages=CAP_CONTEXT, attempt=0, hold_budget=0)
    assert out == {'replace': plugin.NOT_READY}


def test_under_budget_still_holds(plugin):
    out = plugin.publication_buffer(session_id='session', platform='cli', final_response=SUMMARY,
                                    turn_messages=CAP_CONTEXT, attempt=0, hold_budget=2)
    assert out['hold'] and out['nudge'] == plugin.PUBLISH_NUDGE
