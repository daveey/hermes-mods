"""Independent semantic boundary validation through real core hold recheck."""
import json
from types import SimpleNamespace
import pytest
from test_goal_proofs import gd, verdict
ACTION = 'systemctl restart example'
def fixture(kind='approval', scope='Summarize the local README; services are unrelated.'):
    result = ({'status': 'pending_approval', 'approval_pending': True, 'command': ACTION}
              if kind == 'approval' else {'error': 'missing_credentials', 'command': ACTION})
    rows = [{'role': 'user', 'content': scope},
            {'role': 'assistant', 'tool_calls': [{'id': 'c', 'function': {'name': 'terminal', 'arguments': {'command': ACTION}}}]},
            {'role': 'tool', 'tool_call_id': 'c', 'content': result}]
    proof = {'action': ACTION, 'source_ref': 'tool:c'}
    if kind == 'approval': proof['approval_path'] = '/approve'
    v = verdict(disposition='authorization_boundary' if kind == 'approval' else 'capability_boundary',
                boundary_kind=kind if kind == 'approval' else 'credentials', proof=proof)
    return rows, v

def answer(relation='unrelated', **kw):
    return dict(dict(relation=relation, evidence_refs=['user:0', 'tool:c'],
                     rationale='The requested summary does not depend on operating this service.', confidence=.99), **kw)

def run_recheck(monkeypatch, tmp_path, second, kind='approval', required=False):
    from agent.pre_final_gate import evaluate, UnresolvedFallback
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    rows, main = fixture(kind, 'Restore the service using systemctl restart example; this is the only repair path.') if required else fixture(kind)
    calls = []
    def judge(system, prompt):
        calls.append((system, json.loads(prompt)))
        if len(calls) == 1: return main
        if isinstance(second, Exception): raise second
        return second
    monkeypatch.setattr(gd, 'aux_judge', judge)
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    monkeypatch.setattr('agent.pre_final_gate.eligible_callbacks', lambda agent: [gate])
    pending = UnresolvedFallback('Summarize the local README', ('user:0', 'candidate'), recheck_callback=gate)
    decision = evaluate(SimpleNamespace(session_id='', platform='cli', model='test'),
                        ACTION + ' awaits /approve; summary unfinished.', rows, 1, pending)
    return decision, calls, events

@pytest.mark.parametrize('kind', ['approval', 'capability'])
@pytest.mark.parametrize('second', [answer(), answer('unknown'), answer('required', confidence=.2),
                                   {}, TimeoutError('provider timeout'),
                                   answer('required', evidence_refs=['candidate']),
                                   answer('required', confidence=True)])
def test_unproven_relevance_cannot_clear_hold(monkeypatch, tmp_path, kind, second):
    decision, calls, events = run_recheck(monkeypatch, tmp_path, second, kind)
    assert not decision.recheck_accepted, events
    assert decision.unresolved_fallback is not None
    assert len(calls) == 2
    assert calls[0][0] != calls[1][0]
    assert 'disposition' not in calls[1][1]

@pytest.mark.parametrize('kind', ['approval', 'capability'])
def test_independently_required_boundary_preserved(monkeypatch, tmp_path, kind):
    decision, calls, events = run_recheck(monkeypatch, tmp_path, answer('required', rationale='The user requests this exact repair path.'), kind, required=True)
    assert decision.recheck_accepted, events
    assert len(calls) == 2

def test_main_verdict_cannot_stand_in_for_relevance(monkeypatch, tmp_path):
    _, main = fixture()
    decision, calls, events = run_recheck(monkeypatch, tmp_path, main)
    assert not decision.recheck_accepted, events

@pytest.mark.parametrize('second', [answer(), answer('unknown'), TimeoutError('timeout')])
def test_first_assessment_unknown_never_requests_bypass(monkeypatch, second):
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    rows, main = fixture()
    def judge(system, prompt):
        if system == gd.SYSTEM: return main
        if isinstance(second, Exception): raise second
        return second
    monkeypatch.setattr(gd, 'aux_judge', judge)
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(final_response=ACTION + ' awaits /approve', turn_messages=rows) is None
    assert events[-1]['event'] == 'goal_disposition_unknown'


def test_user_denial_preserved_with_separate_necessity(monkeypatch):
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    rows = [{'role': 'user', 'content': 'Do not publish'}]
    main = verdict(boundary_kind='denial', evidence_refs=['user:0', 'candidate'],
                   proof={'action': 'publish', 'source_ref': 'user:0', 'approval_path': 'user_explicit_direction'})
    def judge(system, prompt):
        return main if system == gd.SYSTEM else answer('required', evidence_refs=['user:0'],
                                                     rationale='The latest scope directly prohibits publishing.')
    monkeypatch.setattr(gd, 'aux_judge', judge)
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(final_response='I will not publish; awaiting explicit user direction.', turn_messages=rows, attempt=1) is None
    assert events[-1]['disposition'] == 'authorization_boundary'
