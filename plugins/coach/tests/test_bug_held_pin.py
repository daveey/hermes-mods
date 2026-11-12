"""Deliberate machine holds are placement guidance, not broken infrastructure."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('held_pin_rules', Path(__file__).parents[1] / 'agent_rules.py')
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


def refusal(machine='worker-1'):
    return {'error': f'The pin names machine:{machine}, but that machine is on hold in the Board registry '
            '(hold=true: it copies and leases no new cards), so no worker would claim the card until an operator '
            'releases the hold. Nothing was written. Pin a machine that is not held instead (worker-2), '
            'or leave placement and runs_on out so the card takes the default route. '
            f'`hermes-mail admin machine-audit {machine}` shows who set the hold and '
            'when; a hold is deliberate (a drain or a migration), so do not clear it to place a card.',
            'code': 'placement_unavailable', 'machine': machine, 'created': False,
            'rejection': 'machine_held', 'unheld': ['worker-2']}


@pytest.mark.parametrize('machine', ['worker-1', 'worker-3', 'other-node'])
@pytest.mark.parametrize('shape', ['dict', 'json', 'hinted'])
def test_held_pin_is_guidance(machine, shape):
    d = refusal(machine)
    result = d if shape == 'dict' else json.dumps(d)
    if shape == 'hinted':
        result = json.dumps(d) + '\n[retry guidance]'
    assert rules.bug_signature('kanban_create', result, 'error', None, d['error']) is None


@pytest.mark.parametrize('change', [
    {'code': None}, {'code': 'internal_error'}, {'created': True}, {'created': None},
    {'rejection': None}, {'machine': ''}, {'machine': None}, {'machine': 'different'},
    {'error': 'registry unavailable'}, {'error': 'The pin names machine:worker-1, but registry failed'},
])
def test_malformed_and_infrastructure_failures_stay_visible(change):
    d = {**refusal(), **change}
    assert rules.bug_signature('kanban_create', d, 'error', None, d['error'])


def test_wrong_tool_and_exceptions_stay_visible():
    d = refusal()
    assert rules.bug_signature('cross_session', d, 'error', None, d['error'])
    assert rules.bug_signature('kanban_create', d, 'error', 'RuntimeError', 'backend failed')
    d['stderr'] = ('Traceback (most recent call last):\n'
                   '  File "/home/test/.hermes/hermes-agent/tools/kanban_tools.py", line 5, in run\n'
                   'RuntimeError: backend failed\n')
    assert rules.bug_signature('kanban_create', d, 'error', None, d['error'])


def test_observer_ignores_repeated_holds_but_keeps_real_failure():
    class Sink:
        def __init__(self): self.bugs = []
        def bug(self, *args): self.bugs.append(args)
        def log(self, *args): pass
    sink = Sink()
    observer = rules.AgentCoach(signals=sink)
    for i in range(3):
        d = refusal()
        observer.observe(str(i), 'kanban_create', {'placement': 'worker-1'}, json.dumps(d),
                         status='error', error_message=d['error'])
    assert sink.bugs == []
    observer.observe('broken', 'kanban_create', {}, {'error': 'database unavailable'},
                     status='error', error_message='database unavailable')
    assert len(sink.bugs) == 1
