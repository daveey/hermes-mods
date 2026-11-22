"""Canonical owner projection from captured inputs; no source-capture claim."""
import copy
import json

import pytest
from test_feed import feedmod


@pytest.mark.parametrize('inputs', [None, {}, {
    'outcome_json': {}, 'requests': [], 'subagents': [], 'processes': [],
    'rentals': [], 'input_errors': ['session outcome unavailable'],
    'gateway': 'example', 'profile': 'default', 'gateway_aliases': [],
}])
@pytest.mark.parametrize('base', [None, {'canonical_seq': 3, 'requests': [], 'extra': None}])
def test_captured_projection_matches_legacy_assembly(inputs, base, monkeypatch):
    now = 1700000000.0
    tasks = [{'id': 't_abcdef', 'title': 'Fixture', 'status': 'todo',
              'gateway': 'example', 'profile': 'default', 'board': 'default',
              'wait': {}, 'nullable': None}]
    lineage, done, cards = ['session', 'ancestor'], [], {}
    captured = copy.deepcopy((lineage, tasks, done, cards, inputs, base))
    reducer = feedmod._model().reduce_panel
    # This is the pre-extraction assembly, not a second interpretation of fields.
    expected = {**(base or {}), 'session': 'session', 'lineage': lineage,
                'tasks': tasks, 'done': done, 'cards': cards,
                'computed_at': now, 'computed_seq': 5}
    if inputs is not None:
        expected['canonical_seq'] = 6
        expected.update(inputs)
    expected['model'] = reducer(expected, now=now, wire=True)
    def forbidden():
        raise AssertionError('owner projection must reuse its loaded reducer')
    monkeypatch.setattr(feedmod, '_model', forbidden)
    actual = feedmod.panel_bundle('session', lineage, tasks, done, cards,
        computed_at=now, computed_seq=5, canonical_seq=6,
        base=base, reducer=reducer, **({} if inputs is None else {'inputs': inputs}))
    assert actual == expected
    # Compare exact serialized order/nulls/time, not a volatile-field whitelist.
    assert json.dumps(actual) == json.dumps(expected)
    assert feedmod.wire_bundle(actual) == feedmod.wire_bundle(expected)
    assert feedmod.decision_projection(actual) == feedmod.decision_projection(expected)
    assert (lineage, tasks, done, cards, inputs, base) == captured


def test_compute_rejects_none_from_present_reader(monkeypatch):
    from test_feed import make
    f, readers, _, _ = make()
    monkeypatch.setattr(readers, 'panel_inputs', lambda *args: None, raising=False)
    with pytest.raises(TypeError):
        f.compute('S1')


def test_compute_board_only_does_not_read_extra_inputs(monkeypatch):
    from test_feed import make
    f, readers, _, _ = make()
    base = f.compute('S1')
    base['canonical_seq'] = 42
    def forbidden(*args):
        raise AssertionError('board-only projection must retain captured inputs')
    monkeypatch.setattr(readers, 'panel_inputs', forbidden, raising=False)
    actual = f.compute('S1', base=base)
    assert actual['canonical_seq'] == 42
    assert actual['model'] == base['model']
