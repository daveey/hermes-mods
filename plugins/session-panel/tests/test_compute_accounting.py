"""Normal compute attribution includes lazy reads and preserves failure semantics."""
import builtins
from contextlib import contextmanager

import pytest
from test_feed import feedmod, make


@pytest.fixture
def accounting(monkeypatch):
    events, active = [], []

    @contextmanager
    def account(name):
        row = {'name': name}
        events.append(row)
        active.append(name)
        try:
            yield feedmod._NoAccounting()
        except BaseException:
            row['error'] = True
            raise
        finally:
            assert active.pop() == name
            row['closed'] = True

    monkeypatch.setattr(feedmod, 'accounted_pass', account)
    return events, active


def test_compute_stages_cover_lazy_readers_and_reduction(accounting, monkeypatch):
    events, active = accounting
    f, r, _, _ = make()
    for name in ('lineage', 'linked', 'done'):
        original = getattr(r, name)
        def lazy(*args, name=name, original=original):
            assert active[-1] == 'session_panel.compute.' + name
            yield from original(*args)
        monkeypatch.setattr(r, name, lazy)
    original_card = r.card
    def card(*args):
        assert active[-1] == 'session_panel.compute.cards'
        return original_card(*args)
    r.card = card
    def inputs(*args):
        assert active[-1] == 'session_panel.compute.panel_inputs'
        return {}
    r.panel_inputs = inputs
    original_bundle = feedmod.panel_bundle
    def bundle(*args, **kwargs):
        assert active[-1] == 'session_panel.compute.reduction'
        return original_bundle(*args, **kwargs)
    monkeypatch.setattr(feedmod, 'panel_bundle', bundle)
    stages = {}
    result = f.compute('S1', stages=stages)
    assert len(result['cards']) == 3
    assert [e['name'].rsplit('.', 1)[1] for e in events] == [
        'lineage', 'linked', 'done', 'cards', 'cards', 'cards', 'panel_inputs', 'reduction']
    assert all(e['closed'] and not e.get('error') for e in events)
    assert set(stages) == {'lineage_s', 'linked_s', 'done_s', 'cards_s', 'panel_inputs_s', 'reduction_s'}
    assert not active


@pytest.mark.parametrize('stage', ['linked', 'done', 'cards', 'panel_inputs', 'reduction'])
def test_compute_failure_closes_stage_and_preserves_exception(stage, accounting, monkeypatch):
    events, active = accounting
    f, r, _, _ = make()
    error = OSError('fixture read failure')
    def fail(*args, **kwargs):
        raise error
    if stage == 'reduction':
        monkeypatch.setattr(feedmod, 'panel_bundle', fail)
    else:
        monkeypatch.setattr(r, 'card' if stage == 'cards' else stage, fail, raising=False)
    stages = {}
    with pytest.raises(OSError) as caught:
        f.compute('S1', stages=stages)
    assert caught.value is error
    assert events[-1] == {'name': 'session_panel.compute.' + stage, 'error': True, 'closed': True}
    assert stage + '_s' in stages
    assert not active


def test_compute_optional_accounting_is_output_equivalent(monkeypatch):
    f, _, _, _ = make()
    expected = f.compute('S1', seq=10)
    original_import = builtins.__import__
    def without_accounting(name, *args, **kwargs):
        if name == 'hermes_cli.loop_accounting':
            raise ImportError('optional accounting unavailable')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_accounting)
    assert f.compute('S1', seq=10) == expected
