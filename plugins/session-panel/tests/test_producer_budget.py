"""A pass spends a bounded time in the source producer, so presentation never waits on a slow writer.

2026-11-24: in a contended serve the canonical writer took 5-50 s per chat; 46 admitted chats made one
producer phase 900 s and one pass 21 min. Every panel showed a bundle up to 1.5 h old (a card archived
an hour earlier still read "waiting") while the row stripe, computed elsewhere, was current.
"""
import types

import pytest
from pathlib import Path

from test_feed import make
from test_selective_producer import WATCHED, setup, write


def budgeted(r, calls, reach):
    """A reader that reaches only `reach` chats before its deadline."""
    r.reconcile_budgeted = True

    def reconcile(sessions, deadline=None):
        calls.append(list(sessions))
        assert deadline is not None
        r.reconcile_unreached = list(sessions)[reach:]
        return set()
    r.reconcile_sources = reconcile


def test_chats_past_the_budget_carry_to_the_next_pass_and_every_chat_is_still_presented():
    f, r, source, calls = setup()
    budgeted(r, calls, reach=10)
    f.watch(WATCHED)
    f.tick()
    assert len(calls[0]) == len(WATCHED)
    assert set(WATCHED) <= set(f.bundles), 'hydration ran in the same pass, producer unfinished or not'
    carried = set(calls[0][10:])
    assert f._producer_carry == carried
    assert not carried & set(f._reconcile_stamps), 'unreached chats are not settled on this stamp'
    assert f._due(f.clock())[0] == 0, 'carried work wakes the next pass at once'
    calls.clear()
    f.tick()  # no source write: carried work runs anyway, and only it
    assert set(calls[0]) == carried
    for _ in range(5):
        if not f._producer_carry:
            break
        f.tick()
    assert not f._producer_carry and set(WATCHED) <= set(f._reconcile_stamps)
    calls.clear()
    f.tick()
    assert calls == [], 'once every chat ran, nothing re-runs without a source write'


def test_visible_and_fresh_chats_run_before_owed_work():
    f, r, source, calls = setup()
    budgeted(r, calls, reach=100)
    f.watch(WATCHED)
    f.tick()
    f._reconcile_deferred = {'C1', 'C2'}
    f._panel_subscribers = {'p': ('C30', f.clock() + 60)}
    calls.clear()
    write(r, source, 't_C20', 'C20')
    write(r, source, 't_C30', 'C30')
    f.tick()
    order = calls[0]
    assert order[0] == 'C30', 'a mounted panel first'
    assert order.index('C20') < order.index('C1') and order.index('C20') < order.index('C2')


def test_forget_drops_carried_work():
    f, r, source, calls = setup()
    budgeted(r, calls, reach=1)
    f.watch(['C1', 'C2'])
    f.tick()
    assert f._producer_carry
    for s in list(f._producer_carry):
        f._forget(s)
    assert not f._producer_carry


def test_core_readers_stop_starting_chats_after_the_deadline(monkeypatch):
    pytest.importorskip('hermes_cli.kanban_sync_client')
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        '_spf_api_budget', Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    assert spec is not None and spec.loader is not None
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    r = api.CoreReaders(state_db=Path('/nonexistent/state.db'))
    ran = []
    clock = {'t': 0.0}

    def panel_reconcile(home, session, lineage):
        ran.append(session)
        clock['t'] += 3.0
        return []
    monkeypatch.setattr(r, '_user_step_api', lambda: types.SimpleNamespace(panel_reconcile=panel_reconcile))
    monkeypatch.setattr(r, 'lineage', lambda s: [s])
    monkeypatch.setattr(api.time, 'monotonic', lambda: clock['t'])
    left = r.reconcile_sources(['A', 'B', 'C', 'D'], deadline=5.0)
    assert ran == ['A', 'B'] and r.reconcile_unreached == ['C', 'D'] and left == set()
    clock['t'] = 100.0
    ran.clear()
    r.reconcile_sources(['A', 'B'], deadline=50.0)
    assert ran == ['A'], 'one chat always runs, even past the deadline'
    assert r.reconcile_unreached == ['B']
    ran.clear()
    r.reconcile_sources(['A', 'B'])
    assert ran == ['A', 'B'] and r.reconcile_unreached == []


@pytest.mark.parametrize('failed', [False, True])
def test_reader_stage_carriage_and_total_residual(monkeypatch, failed):
    pytest.importorskip('hermes_cli.kanban_sync_client')
    import importlib.util
    import time
    spec = importlib.util.spec_from_file_location(
        '_spf_api_timing', Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    assert spec is not None and spec.loader is not None
    api = importlib.util.module_from_spec(spec)
    import sys
    monkeypatch.setitem(sys.modules, spec.name, api)
    spec.loader.exec_module(api)
    r = api.CoreReaders(state_db=Path('/nonexistent/state.db'))
    class Result(list):
        unchanged = True
    def panel_reconcile(home, session, lineage):
        value = RuntimeError('unavailable') if failed else Result()
        value.panel_timing = dict(version=1, call_id='fixture:1', wall_ns=0, cpu_ns=0,
                                  stages={'residual': dict(wall_ns=0, cpu_ns=0, calls=1)})
        if failed:
            raise value
        return value
    monkeypatch.setattr(r, '_user_step_api', lambda: types.SimpleNamespace(panel_reconcile=panel_reconcile))
    def lineage(s):
        time.sleep(.01)
        return [s]
    monkeypatch.setattr(r, 'lineage', lineage)
    assert r.reconcile_sources(['A']) == ({'A'} if failed else set())
    row = r.reconcile_timings[0]
    assert row['unchanged'] is (not failed) and row['deferred'] is failed
    trace = row['stage_timing']
    assert trace['call_id'] == 'fixture:1'
    assert trace['preflight_wall_ns'] >= 5_000_000
    for clock in ('wall', 'cpu'):
        assert trace[f'reader_{clock}_ns'] == (trace[f'preflight_{clock}_ns'] +
                    trace[f'{clock}_ns'] + trace[f'reader_residual_{clock}_ns'])
