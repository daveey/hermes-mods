"""Stage attribution preserves feed behavior, including a missed wake."""
from contextlib import contextmanager

import pytest
from test_feed import feedmod, make


@pytest.fixture
def passes(monkeypatch):
    records = []
    @contextmanager
    def account(name):
        class Pass:
            def skip(self): row['skipped'] = True
            def change(self, value=True): row['changed'] = value
            def fallback(self, value=True): row['fallback'] = value
            def rows(self, n): row['rows'] = n
        row = {'name': name}
        records.append(row)
        try:
            yield Pass()
        except BaseException:
            row['error'] = True
            raise
    monkeypatch.setattr(feedmod, 'accounted_pass', account)
    return records


def test_tick_hydration_and_unchanged_storm_accounted(passes):
    f, r, pushed, clock = make()
    f.watch(['S1'])
    f.tick()
    assert any(p['name'] == 'session_panel.compute_all' and p['rows'] == 1 for p in passes)
    assert passes[0]['name'] == 'session_panel.feed_tick'
    reads = len(r.reads)
    for _ in range(50):
        f._wake.set()
        f.tick()
    assert len(r.reads) == reads
    assert sum(p.get('skipped', False) for p in passes if p['name'] == 'session_panel.feed_tick') == 50
    # A committed event is still found even if its wake notification is lost.
    r.stamp += 1
    r.events.append(('t_aaaaaa1', 'S1'))
    f._wake.clear()
    f.tick()
    assert len(r.reads) > reads


def test_producer_counts_unchanged_and_deferred_without_changing_result(passes):
    f, r, _, _ = make()
    f.watch(['S1'])
    r.reconcile_timings = [{'unchanged': True}]
    r.reconcile_sources = lambda sessions, **kwargs: set()
    assert f._run_producer(['S1']) == set()
    assert passes[-1] == {'name':'session_panel.reconcile_sources', 'rows':1,
                           'changed':False, 'fallback':False, 'skipped':True}
    r.reconcile_timings = [{'unchanged': False, 'deferred': True}]
    r.reconcile_sources = lambda sessions, **kwargs: {'S1'}
    assert f._run_producer(['S1']) == {'S1'}
    assert passes[-1]['fallback'] is True


def test_tick_exception_is_accounted_and_keeps_retry(passes, monkeypatch):
    f, r, _, _ = make()
    f.watch(['S1'])
    def fail(): raise OSError('unavailable fixture source')
    monkeypatch.setattr(r, 'stamps', fail)
    with pytest.raises(OSError):
        f.tick()
    assert passes[0]['error'] is True
    assert 'S1' in f._retry
