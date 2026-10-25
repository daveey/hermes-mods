"""Exercise freshness at the real gate, including an already-held recheck."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('capability_gate_test', Path(__file__).parents[1] / 'goal_disposition.py')
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)


def rows():
    return [
        {'role': 'user', 'content': 'Repair https://example.com/manage'},
        {'role': 'assistant', 'tool_calls': [{'id': 'c', 'function': {
            'name': 'fetch', 'arguments': json.dumps({'url': 'https://example.com/manage'})}}]},
        {'role': 'tool', 'tool_call_id': 'c', 'content': {'error': 'missing_credentials'}},
    ]


def verdict():
    return dict(goal='Repair example', actionable=True, unfinished=True,
                disposition='capability_boundary', evidence_refs=['user:0', 'tool:c', 'candidate'],
                boundary_kind='credentials', continuation_ref=None, confidence=.99,
                proof=dict(source_ref='tool:c', action='https://example.com/manage'))


@pytest.mark.parametrize('later', ['user', 'result'])
def test_gate_does_not_certify_superseded_boundary(monkeypatch, later):
    events = []
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: verdict())
    messages = rows()
    if later == 'user':
        messages.append({'role': 'user', 'content': 'Credentials repaired; continue'})
    else:
        messages.extend([
            {'role': 'assistant', 'tool_calls': [{'id': 'new', 'function': {
                'name': 'fetch', 'arguments': json.dumps({'url': 'https://example.com/manage'})}}]},
            {'role': 'tool', 'tool_call_id': 'new', 'content': {'status': 200}},
        ])
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(final_response='Missing access', turn_messages=messages) is None
    assert events[-1]['event'] == 'goal_disposition_unknown'
    assert events[-1]['reason'] == 'superseded_capability_evidence'


def test_unproven_boundary_cannot_clear_confirmed_hold(monkeypatch):
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    v = verdict()
    v.pop('proof')
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: v)
    gate = gd.build_gate(SimpleNamespace(log=lambda _: None))
    with pytest.raises(ValueError):
        gate(final_response='Missing access', turn_messages=rows(), attempt=1)


def test_current_boundary_is_logged(monkeypatch):
    events = []
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: verdict())
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(final_response='Missing access', turn_messages=rows()) is None
    assert events[-1]['disposition'] == 'capability_boundary'
