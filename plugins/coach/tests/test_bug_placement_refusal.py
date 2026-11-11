"""An unknown explicit gateway is caller guidance, not broken placement."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('placement_bug_rules', Path(__file__).parents[1] / 'agent_rules.py')
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


def refusal(pin='worker-1'):
    return {'error': f'placement={pin!r} was not honoured and no card was created: '
            f'no gateway is named {pin!r}: hermes-hub knows hub, macbook, mini, worker-2. '
            'Pin one of those, or map this name in kanban.placement.aliases. '
            'A pinned gateway is never silently replaced by a local card. '
            "Use placement='local' (or omit it) to create the card on this gateway, "
            'or create it on the target with `hermes-hub create-task <gateway> ...`.',
            'code': 'placement_unavailable', 'placement': pin, 'created': False}


@pytest.mark.parametrize('pin', ['worker-1', 'retired-node', 'hub-old'])
@pytest.mark.parametrize('shape', ['dict', 'json', 'hinted'])
def test_unknown_gateway_guidance_is_not_a_bug(pin, shape):
    d = refusal(pin)
    result = d if shape == 'dict' else json.dumps(d)
    if shape == 'hinted':
        result = json.dumps(d) + '\n[retry guidance]'
    assert rules.bug_signature('kanban_create', result, 'error', None, d['error']) is None


@pytest.mark.parametrize('change', [
    {'code': None}, {'code': 'internal_error'}, {'created': True}, {'created': None},
    {'placement': 'different'}, {'placement': None},
    {'error': 'placement unavailable: database failed'},
    {'error': "placement='worker-1' was not honoured and no card was created: gateway is offline"},
    {'error': "placement='worker-1' was not honoured and no card was created: no gateway is named 'other': unknown"},
])
def test_other_failures_remain_visible(change):
    d = {**refusal(), **change}
    assert rules.bug_signature('kanban_create', d, 'error', None, d['error'])


def test_wrong_tool_does_not_inherit_exemption():
    d = refusal()
    assert rules.bug_signature('cross_session', d, 'error', None, d['error'])


def test_real_exception_overrides_guidance():
    d = refusal()
    assert rules.bug_signature('kanban_create', d, 'error', 'RuntimeError', 'backend failed')
    d['stderr'] = ('Traceback (most recent call last):\n'
                   '  File "/home/test/.hermes/hermes-agent/tools/kanban_tools_placement.py", line 5, in run\n'
                   'RuntimeError: backend failed\n')
    assert rules.bug_signature('kanban_create', d, 'error', None, d['error'])


def test_observer_does_not_file_repeated_unknown_pins_but_keeps_real_failure():
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
