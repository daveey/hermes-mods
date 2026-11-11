"""Designed wake-probe refusals are guidance, not broken infrastructure."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('wake_bug_rules', Path(__file__).parents[1] / 'agent_rules.py')
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


def refusal(tool='kanban_block', hours='24.0'):
    return {'error': f'{tool} refused: this park wakes in {hours}h and has no wake_check, '
            'so a condition that comes true early would sit unnoticed until then. '
            'A wait needs a producer (awaiting={kind: card|ci|deploy|external|clock, ref, why}); '
            'a probe only says when to look.', 'rejection': 'invalid_args'}


@pytest.mark.parametrize('tool', ['kanban_block'])
@pytest.mark.parametrize('hours', ['3.0', '24.0'])
@pytest.mark.parametrize('shape', ['dict', 'json', 'hinted'])
def test_missing_probe_guidance_is_not_a_bug(tool, hours, shape):
    d = refusal(tool, hours)
    result = d if shape == 'dict' else json.dumps(d)
    if shape == 'hinted':
        result = json.dumps(d) + '\n[retry guidance]'
    assert rules._guided_refusal(tool, d)
    assert rules.bug_signature(tool, result, 'error', None, d['error']) is None


@pytest.mark.parametrize('change', [
    {'rejection': None}, {'rejection': 'internal_error'},
    {'error': 'kanban_block refused: database unavailable'},
    {'error': 'kanban_block refused: this park wakes in nonsense'},
])
def test_other_failures_remain_visible(change):
    d = {**refusal(), **change}
    assert rules.bug_signature('kanban_block', d, 'error', None, d['error'])


def test_wrong_tool_does_not_inherit_exemption():
    d = refusal()
    assert rules.bug_signature('cross_session', d, 'error', None, d['error'])


def test_real_exception_overrides_guidance():
    d = refusal()
    assert rules.bug_signature('kanban_block', d, 'error', 'RuntimeError', 'backend failed')
    d['stderr'] = ('Traceback (most recent call last):\n'
                   '  File "/home/test/.hermes/hermes-agent/tools/kanban_tools.py", line 5, in run\n'
                   'RuntimeError: backend failed\n')
    assert rules.bug_signature('kanban_block', d, 'error', None, d['error'])


def test_observer_does_not_file_repeated_wake_refusals_but_keeps_real_failure():
    class Sink:
        def __init__(self): self.bugs = []
        def bug(self, *args): self.bugs.append(args)
        def log(self, *args): pass
    sink = Sink()
    observer = rules.AgentCoach(signals=sink)
    for i, hours in enumerate(['24.0', '24.0', '3.0']):
        d = refusal(hours=hours)
        observer.observe(str(i), 'kanban_block', {'kind': 'dependency'}, json.dumps(d),
                         status='error', error_message=d['error'])
    assert sink.bugs == []
    observer.observe('broken', 'kanban_block', {}, {'error': 'database unavailable'},
                     status='error', error_message='database unavailable')
    assert len(sink.bugs) == 1
