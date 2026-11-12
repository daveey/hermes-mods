"""Assignment policy refusals are not infrastructure defects."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('capability_bug_rules', Path(__file__).parents[1] / 'agent_rules.py')
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


def refusal(assignee='remote-ops', missing=None):
    missing = ['terminal', 'file'] if missing is None else missing
    return {'error': f"Assignee {assignee!r} lacks required capabilities: {', '.join(missing)}",
            'code': 'assignee_capability_mismatch', 'assignee': assignee, 'missing': missing}


@pytest.mark.parametrize('shape', ['dict', 'json', 'hinted'])
@pytest.mark.parametrize('missing', [['terminal', 'file'], ['git', 'host_repo'], ['kanban']])
def test_expected_policy_refusal_not_a_bug(shape, missing):
    d = refusal(missing=missing)
    result = d if shape == 'dict' else json.dumps(d)
    if shape == 'hinted':
        result = json.dumps(d) + '\n[retry guidance]'
    assert rules.bug_signature('kanban_create', result, 'error', None, d['error']) is None


@pytest.mark.parametrize('change', [
    {'code': 'internal_error'}, {'assignee': 'different'}, {'assignee': None},
    {'missing': []}, {'missing': 'terminal, file'}, {'missing': [None]},
    {'missing': ['future_capability']}, {'error': 'database unavailable'},
    {'error': refusal()['error'] + ' (profile policy unavailable: config read failed)'},
    {'error': refusal()['error'] + ' (transport failed)'},
])
def test_unproven_or_infrastructure_failures_still_count(change):
    d = {**refusal(), **change}
    assert rules.bug_signature('kanban_create', d, 'error', None, d['error'])


def test_unknown_capability_exact_message_still_counts():
    d = refusal(missing=['future_capability'])
    assert rules.bug_signature('kanban_create', d, 'error', None, d['error'])


def test_exception_and_wrong_tool_still_count():
    d = refusal()
    assert rules.bug_signature('cross_session', d, 'error', None, d['error'])
    assert rules.bug_signature('kanban_create', d, 'error', 'RuntimeError', 'backend failed')
    d['stderr'] = ('Traceback (most recent call last):\n'
                   '  File "/home/test/.hermes/hermes-agent/tools/kanban_tools.py", line 5, in run\n'
                   'RuntimeError: backend failed\n')
    assert rules.bug_signature('kanban_create', d, 'error', None, d['error'])


def test_observer_retains_failure_coaching_without_filing_false_bugs():
    class Sink:
        def __init__(self): self.bugs = []
        def bug(self, *args): self.bugs.append(args)
        def log(self, *args): pass
    sink = Sink()
    observer = rules.AgentCoach(signals=sink)
    for i in range(3):
        d = refusal()
        observer.observe(str(i), 'kanban_create', {'assignee': 'remote-ops'}, json.dumps(d),
                         status='error', error_message=d['error'])
        assert sum(observer._s(str(i))['fails'].values()) == 1
        assert any('<- ERROR' in line for line in observer._s(str(i))['turn_log'])
    assert sink.bugs == []
    observer.observe('broken', 'kanban_create', {}, {'error': 'database unavailable'},
                     status='error', error_message='database unavailable')
    assert len(sink.bugs) == 1
