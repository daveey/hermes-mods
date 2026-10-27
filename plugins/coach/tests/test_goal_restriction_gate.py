"""Exact whole-turn restrictions override a wrong semantic abandonment verdict."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('restrict_gd', Path(__file__).parents[1] / 'goal_disposition.py')
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

RESTRICTIONS = ['Stop.', 'Cancel', 'Do not continue.', 'Analysis only.', 'Status only.',
                'Plan first.', 'Do not change anything.', 'Do not execute anything.']


def wrong_verdict():
    return dict(goal='Repair example app', actionable=True, unfinished=True,
                disposition='abandoned', evidence_refs=['user:0', 'candidate'],
                boundary_kind=None, continuation_ref=None, confidence=.99)


@pytest.mark.parametrize('restriction', RESTRICTIONS)
@pytest.mark.parametrize('attempt', [0, 1])
def test_explicit_restriction_never_nudges_work(monkeypatch, restriction, attempt):
    events = []
    monkeypatch.setattr(gd, 'settings', lambda: {'mode':'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: wrong_verdict())
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    rows = [{'role':'user', 'content':'Repair example app'}, {'role':'user','content':restriction}]
    assert gate(final_response='Acknowledged.', turn_messages=rows, attempt=attempt) is None
    assert events[-1]['disposition'] == 'analysis_only'
    assert events[-1]['reason'] == 'explicit_whole_turn_restriction'


@pytest.mark.parametrize('nonrestriction', [
    'The log says "Stop."', 'Explain the phrase "Analysis only." and repair example app',
    'Do not publish.', 'Stop the broken service and repair it.',
    'Plan first. Then implement it.', 'Status only? No, repair it.',
])
def test_partial_quoted_or_ambiguous_scope_is_not_whole_turn_veto(monkeypatch, nonrestriction):
    monkeypatch.setattr(gd, 'settings', lambda: {'mode':'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: wrong_verdict())
    gate = gd.build_gate(SimpleNamespace(log=lambda _: None))
    assert gate(final_response='Still broken.', turn_messages=[{'role':'user','content':nonrestriction}])['hold']


def test_new_repair_steering_supersedes_old_whole_turn_veto(monkeypatch):
    monkeypatch.setattr(gd, 'settings', lambda: {'mode':'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: wrong_verdict())
    gate = gd.build_gate(SimpleNamespace(log=lambda _:None))
    rows = [{'role':'user','content':'Stop.'}, {'role':'user','content':'Now repair example app'}]
    assert gate(final_response='Still broken.', turn_messages=rows)['hold']
