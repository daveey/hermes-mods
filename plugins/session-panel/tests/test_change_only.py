"""Elapsed time is not permission to reread a database or renew evidence."""
from test_feed import make, feedmod


def test_idle_open_and_failed_work_never_wakes_database_lanes():
    f, r, pushed, t = make()
    f.changes(['S1'])
    f.tick()
    f._retry.add('S1')
    f._decision_failures.add('S1')
    before = f.bundles['S1']['bundle']['computed_at']
    reads = list(r.reads)
    for elapsed in (3, 61, 301, 601):
        t['now'] = before + elapsed
        f.beat()
        assert not f._wake.is_set(), 'time alone must not schedule a database pass'
        assert not f._decide.is_set(), 'failed reads stay stale until an external trigger'
        assert f.bundles['S1']['bundle']['computed_at'] == before
    assert r.reads == reads
    assert 'S1' in f._retry and 'S1' in f._decision_failures


def test_age_only_publish_keeps_evidence_time_and_current_reducer_result():
    f, r, pushed, t = make()
    f.changes(['S1'])
    f.tick()
    before = f.bundles['S1']['bundle']
    t['now'] += 61
    expected = feedmod._model().reduce_panel(before, now=t['now'])
    f.beat()
    after = f.bundles['S1']['bundle']
    assert after['model'] == expected
    assert after['computed_at'] == before['computed_at']
    assert after['computed_seq'] == before['computed_seq']
    assert not f._wake.is_set()


def test_elapsed_full_interval_does_not_read_board_events_or_recompute():
    f, r, pushed, t = make()
    f.changes(['S1'])
    f.tick()
    before = list(r.reads)
    def forbidden(*args, **kwargs):
        raise AssertionError('unchanged source must not be read on a timer')
    r.changed_cards = forbidden
    f.compute = forbidden
    t['now'] += 301
    f.tick()
    assert r.reads == before
