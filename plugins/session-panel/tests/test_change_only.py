"""A source event, never elapsed wall time, admits database work."""
from test_feed import make


def test_open_work_is_aged_without_reading_unchanged_sources(monkeypatch):
    f, r, pushed, t = make()
    f.watch(['S1', 'S2'])
    f.tick()
    before = {s: b['bundle']['computed_at'] for s, b in f.bundles.items()}

    def forbidden(*args, **kwargs):
        raise AssertionError('idle clock admitted a source read')

    for method in ('stamps', 'changed_cards', 'lineage', 'linked', 'done', 'card'):
        monkeypatch.setattr(r, method, forbidden)
    # More than the old full AND quiet intervals, with both open and done rows.
    # Drive the real heartbeat admission path, not a mocked scheduler.
    for elapsed in range(1, 361):
        t['now'] = 1000.0 + elapsed
        f.beat()
        if f._wake.is_set():
            f.tick()
    assert {s: b['bundle']['computed_at'] for s, b in f.bundles.items()} == before
    assert pushed[-1].get('heartbeat')
    assert not pushed[-1]['fresh'], 'memory ageing cannot renew source trust'


def test_idle_heartbeat_reduces_a_clock_edge_without_renewing_inputs(monkeypatch):
    from datetime import datetime, timezone
    import copy
    f, r, pushed, t = make()
    t['now'] = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc).timestamp()
    wait = {'wait_state': 'waiting', 'waiting_on': 'the clock until 2026-11-21 00:00 UTC',
            'waiting_since': t['now'] - 30}
    r.links['S1'] = [{'id': 't_aaaaaa1', 'status': 'scheduled', 'board': 'default', 'wait': wait}]
    r.cards = {}
    r.panel_inputs = lambda *args: {'requests': [], 'outcome_json': {}}
    f.changes(['S1'])
    before = copy.deepcopy(f.bundles['S1']['bundle'])
    assert 'overdue' not in before['model']['sections']['waiting'][0]['detail']
    f._carried.add('S1')
    f._reissue.add('S1')
    t['now'] += 120
    f.beat()
    after = f.bundles['S1']['bundle']
    assert 'status unknown' in after['model']['sections']['waiting'][0]['detail']
    assert {k: v for k, v in after.items() if k != 'model'} == {k: v for k, v in before.items() if k != 'model'}
    assert f._carried == {'S1'} and f._reissue == {'S1'}
    assert 'S1' not in pushed[-1]['fresh']


def test_failed_source_stays_unvouched_without_idle_retry_and_recovers_on_event(monkeypatch):
    f, r, pushed, t = make()
    r.links['S1'][0]['_execution_error'] = True
    f.watch(['S1'])
    f.tick()
    assert 'S1' in f._retry
    observed = f.bundles['S1']['bundle']['computed_at']
    t['now'] += 120
    f.beat()
    assert not f._wake.is_set()
    assert f.bundles['S1']['bundle']['computed_at'] == observed
    assert 'S1' not in pushed[-1]['fresh']
    del r.links['S1'][0]['_execution_error']
    r.stamp += 1
    r.events = [('t_aaaaaa1', 'S1')]
    f.tick()
    assert 'S1' not in f._retry
    assert f.bundles['S1']['bundle']['computed_at'] == t['now']
    assert 'S1' in pushed[-1]['fresh']


def test_source_event_after_long_idle_recomputes_only_affected_chat():
    import json
    f, r, pushed, t = make()
    f.watch(['S1', 'S2'])
    f.tick()
    other = f.bundles['S2']['bundle']
    t['now'] += 360
    r.links['S1'][0]['title'] = 'Changed on the source'
    r.stamp += 1
    r.events = [('t_aaaaaa1', 'S1')]
    f.tick()
    assert f.bundles['S2']['bundle'] is other, 'wall time does not expand the invalidation set'
    wire = json.loads(f.changes_bytes(['S1'], epoch=None))
    assert wire['bundles']['S1']['tasks'][0]['title'] == 'Changed on the source'
