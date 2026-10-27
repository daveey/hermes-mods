"""Independent run15 review. No network or actual operations."""
import json
import threading
import pytest
from types import SimpleNamespace
from test_goal_boundary_relevance import gd, fixture, answer, ACTION


def setup_gate(monkeypatch, tmp_path, rows, main, second):
    from agent import pre_final_gate as core
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'recover_scope', lambda *a, **k: rows)
    calls, events = [], []
    def judge(system, prompt):
        calls.append(json.loads(prompt))
        if len(calls) == 1:
            return main
        return second() if callable(second) else second
    monkeypatch.setattr(gd, 'aux_judge', judge)
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    gate.capture(session_id='review', user_message=rows[0]['content'])
    monkeypatch.setattr(core, 'eligible_callbacks', lambda agent: [gate])
    return core, gate, calls, events


def evaluate(core, gate, rows, pending=True):
    hold = core.UnresolvedFallback('Current user goal', ('user:0', 'candidate'), recheck_callback=gate) if pending else None
    return core.evaluate(SimpleNamespace(session_id='review', platform='cli', model='test'),
                         ACTION + ' awaits /approve; credentials required.', rows, int(pending), hold)


@pytest.mark.parametrize('kind', ['approval', 'capability'])
def test_stale_scope_only_citations_must_not_clear_hold(monkeypatch, tmp_path, kind):
    rows, main = fixture(kind, 'Restart the example service using ' + ACTION)
    rows.insert(1, {'role': 'user', 'content': 'Replace that task: summarize the local README. Service operations are unrelated to this new task.'})
    core, gate, calls, events = setup_gate(monkeypatch, tmp_path, rows, main,
        answer('required', rationale='The original user requested this exact restart.'))
    decision = evaluate(core, gate, rows)
    assert len(calls) == 2
    assert calls[1]['evidence']['user:1']['content'].startswith('Replace that task')
    assert not decision.recheck_accepted, events
    assert decision.unresolved_fallback is not None


@pytest.mark.parametrize('kind', ['approval', 'capability'])
def test_valid_current_scope_boundary_control(monkeypatch, tmp_path, kind):
    rows, main = fixture(kind, 'Restore service using ' + ACTION + '; no other recovery path exists.')
    core, gate, calls, events = setup_gate(monkeypatch, tmp_path, rows, main,
        answer('required', rationale='The user specifies this necessary recovery operation.'))
    decision = evaluate(core, gate, rows)
    assert decision.recheck_accepted, events
    assert decision.unresolved_fallback is None


def test_omitted_uncited_authority_blocks_second_call(monkeypatch, tmp_path):
    rows, main = fixture(scope='Restore service using ' + ACTION)
    rows.extend([{'role': 'assistant', 'tool_calls': [{'id': 'other', 'function': {'name': 'terminal', 'arguments': {'command': 'inspect'}}}]},
                 {'role': 'tool', 'tool_call_id': 'other', 'content': 'x' * 40000}])
    core, gate, calls, events = setup_gate(monkeypatch, tmp_path, rows, main, answer('required'))
    decision = evaluate(core, gate, rows)
    assert not decision.recheck_accepted
    assert decision.unresolved_fallback is not None
    assert len(calls) == 1
    assert events[-1]['reason'] == 'boundary_relevance_incomplete'


def test_synthetic_user_citation_is_not_available(monkeypatch, tmp_path):
    rows, main = fixture()
    rows.insert(1, {'role': 'user', 'content': 'Restart service now', '_pre_final_gate_synthetic': True})
    core, gate, calls, events = setup_gate(monkeypatch, tmp_path, rows, main,
        answer('required', evidence_refs=['user:1', 'tool:c']))
    decision = evaluate(core, gate, rows)
    assert 'user:1' not in calls[1]['evidence']
    assert not decision.recheck_accepted
    assert decision.unresolved_fallback is not None


@pytest.mark.parametrize('pending', [False, True])
def test_actual_core_timeout_not_just_provider_exception(monkeypatch, tmp_path, pending):
    rows, main = fixture(scope='Restore service using ' + ACTION)
    release, finished = threading.Event(), threading.Event()
    def second():
        try:
            release.wait(2)
            return answer('required')
        finally:
            finished.set()
    core, gate, calls, events = setup_gate(monkeypatch, tmp_path, rows, main, second)
    monkeypatch.setattr(core, 'timeout_s', lambda: .05)
    try:
        decision = evaluate(core, gate, rows, pending)
        assert len(calls) == 2
        assert not decision.recheck_accepted
        assert not decision.hold and not decision.nudge
        assert (decision.unresolved_fallback is not None) == pending
    finally:
        release.set()
        assert finished.wait(2)
    # Late successful completion must not mutate the returned decision.
    assert not decision.recheck_accepted
    assert (decision.unresolved_fallback is not None) == pending


@pytest.mark.parametrize('kind', ['approval', 'capability'])
def test_latest_nonrevoking_steering_keeps_original_goal(monkeypatch, tmp_path, kind):
    rows, main = fixture(kind, 'Restore service using ' + ACTION)
    rows.insert(1, {'role': 'user', 'content': 'Keep working on that repair; report the current blocker accurately.'})
    core, gate, calls, events = setup_gate(monkeypatch, tmp_path, rows, main,
        answer('required', evidence_refs=['user:0', 'user:1', 'tool:c'],
               rationale='The original repair mandate remains active under the latest steering.'))
    decision = evaluate(core, gate, rows)
    assert decision.recheck_accepted, events
    assert decision.unresolved_fallback is None
