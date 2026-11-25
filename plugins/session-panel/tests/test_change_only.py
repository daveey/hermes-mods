"""A source event, never elapsed wall time, admits database work."""
from test_feed import make
import pytest


def test_failed_admission_preserves_cursors_and_retries_without_a_write():
    f, r, pushed, t = make()
    r.panel_files_stamp = lambda: r.stamp
    r.panel_stamps = lambda ids: {s: r.stamp for s in ids}
    f.watch(['S1'])
    f.tick()
    old = (dict(f._panel_stamps), f._panel_files, f._stamps, f._cursor)
    read = r.changed_cards
    r.stamp += 1
    r.links['S1'][0]['title'] = 'New panel value'
    r.changed_cards = lambda cursor: (_ for _ in ()).throw(OSError('busy'))
    with pytest.raises(OSError):
        f.tick()
    assert (f._panel_stamps, f._panel_files, f._stamps, f._cursor) == old
    t['now'] += f.heartbeat_s
    f.beat()
    assert 'S1' not in pushed[-1]['fresh']
    assert f._wake.is_set()
    r.changed_cards = read
    f.tick()
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'New panel value'
    assert 'S1' not in f._retry


@pytest.mark.parametrize('warm', [False, True])
def test_admission_retry_is_bounded_even_when_source_stays_unreadable(warm):
    f, r, pushed, t = make()
    f.watch(['S1'])
    if warm:
        f.tick()
    r.stamp += 1
    r.changed_cards = lambda cursor: (_ for _ in ()).throw(OSError('busy'))
    with pytest.raises(OSError):
        f.tick()
    attempts = 0
    for _ in range(30):
        t['now'] += 1
        f.beat()
        if f._wake.is_set():
            attempts += 1
            with pytest.raises(OSError):
                f.tick()
    assert 1 <= attempts <= 3
    assert 'S1' in f._retry
    assert not f._wake.is_set()


def test_external_commits_compare_captured_inputs_before_compute():
    f, r, pushed, t = make()
    values = {'S1': ('receipt-a',), 'S2': ('receipt-b',)}
    r.external_stamps = lambda: r.stamp
    r.external_capture = lambda held: {s: values[s] for s in held}
    f.watch(['S1', 'S2'])
    f.tick()
    before = {s: b['bundle'] for s, b in f.bundles.items()}
    for _ in range(10):
        r.stamp += 1
        r.events = []
        f.tick()
    assert all(f.bundles[s]['bundle'] is b for s, b in before.items())
    values['S1'] = ('receipt-a-released',)
    r.links['S1'][0]['title'] = 'Receipt changed'
    r.stamp += 1
    f.tick()
    assert f.bundles['S2']['bundle'] is before['S2']
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'Receipt changed'


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
    parked = after['model']['sections']['waiting'][0]
    assert 'overdue' in parked['detail'] and 'panel data' in parked['detail']
    assert 'status unknown' not in parked['detail']
    # Within the quiet refresh cadence the parked clock wait keeps its delivery colour (HISTORICAL_DELIVERY_S).
    assert parked['evidence_state'] == 'unknown' and parked.get('wait_kind') == 'clock'
    assert {k: v for k, v in after.items() if k != 'model'} == {k: v for k, v in before.items() if k != 'model'}
    assert f._carried == {'S1'} and f._reissue == {'S1'}
    assert 'S1' not in pushed[-1]['fresh']


def test_failed_source_stays_unvouched_after_its_bounded_retries_and_recovers_on_event(monkeypatch):
    f, r, pushed, t = make()
    r.links['S1'][0]['_execution_error'] = True
    f.watch(['S1'])
    f.tick()
    assert 'S1' in f._retry
    retries = 0
    for _ in range(60):  # three short retries of the stale read, then nothing without a source event
        t['now'] += 1
        f.beat()
        if f._wake.is_set():
            retries += 1
            f.tick()
    assert retries == 3 and 'S1' in f._retry
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


def test_feed_uses_semantic_membership_changes_instead_of_event_ids():
    from types import SimpleNamespace
    f, r, pushed, t = make()
    f.watch(['S1', 'S2'])
    f.tick()
    other = f.bundles['S1']['bundle']
    # Only the newly subscribed session is dirty. There is no task event.
    r.links['S2'] = [{'id': 't_sub0001', 'title': 'New subscription', 'status': 'running', 'board': 'default'}]
    changes = SimpleNamespace(affects=lambda held: 'S2' in held['lineage'])
    calls = []
    def semantic(cursor):
        calls.append(cursor)
        return changes, {'source': 'new revision'}
    r.board_changes = semantic
    r.events = []
    r.stamp += 1
    f.tick()
    assert calls, 'the canonical semantic cursor, not max(event.id), must be consulted'
    assert f.bundles['S1']['bundle'] is other
    assert f.bundles['S2']['bundle']['tasks'][0]['id'] == 't_sub0001'


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
