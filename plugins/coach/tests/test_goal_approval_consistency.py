"""Adversarial approval-envelope checks; only existing isolated test helpers."""
import pytest
from test_goal_proofs import evidence, verdict, checked

ACTION = 'systemctl restart example'


def approval(extra=None, request='Repair app'):
    result = dict(status='pending_approval', approval_pending=True, command=ACTION)
    result.update(extra or {})
    rows = evidence(request, args={'command': ACTION}, data=result,
                    candidate=ACTION + ' awaits /approve')
    value = verdict(proof=dict(action=ACTION, source_ref='tool:c', approval_path='/approve'))
    return value, rows


@pytest.mark.parametrize('extra', [dict(success=True), dict(exit_code=0), dict(status_code=200), dict(status_code=204)])
def test_contradictory_approval_cannot_certify(extra):
    value, rows = approval(extra)
    with pytest.raises(ValueError, match='unproven_current_approval_boundary'):
        checked(value, rows)


def test_native_pending_approval_control():
    value, rows = approval()
    assert checked(value, rows)['disposition'] == 'authorization_boundary'


def test_goal_relevance_is_not_deterministically_proven():
    # Documents a remaining semantic gap, not a desired closure guarantee.
    value, rows = approval(request='Summarize the local README; do not operate services')
    value['goal'] = 'Summarize the local README'
    assert checked(value, rows)['disposition'] == 'authorization_boundary'


def test_contradictory_approval_cannot_clear_existing_hold(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from agent.pre_final_gate import evaluate, UnresolvedFallback
    from test_goal_proofs import gd
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    value, _ = approval(dict(exit_code=0))
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: value)
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    monkeypatch.setattr('agent.pre_final_gate.eligible_callbacks', lambda agent: [gate])
    rows = [dict(role='user', content='Repair app'),
            dict(role='assistant', tool_calls=[dict(id='c', function=dict(name='terminal', arguments={'command': ACTION}))]),
            dict(role='tool', tool_call_id='c', content=dict(status='pending_approval', approval_pending=True, command=ACTION, exit_code=0))]
    pending = UnresolvedFallback('Repair app', ('user:0', 'candidate'), recheck_callback=gate)
    decision = evaluate(SimpleNamespace(session_id='', platform='cli', model='test'),
                        ACTION + ' awaits /approve', rows, 1, pending)
    assert not decision.recheck_accepted, events
    assert decision.unresolved_fallback is not None
