from pathlib import Path
from test_feed import make, feedmod
from test_routes import load


import pytest

@pytest.mark.parametrize('legacy_event', [False, True])
def test_review_keyed_stream_keeps_heartbeat_and_registration(monkeypatch, legacy_event):
    api = load('_review_keyed_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    f, r, pushes, clock = make()
    f.changes(['S1', 'S2'])
    f.tick()
    lane = feedmod.BoardLane(f, lambda: r)
    monkeypatch.setattr(api, '_event_driven', lambda f: True)
    monkeypatch.setattr(api, '_settle', lambda *args: None)
    monkeypatch.setattr(api, 'write_timing', lambda f: None)
    monkeypatch.setattr(api, 'save_snapshot', lambda *args, **kwargs: True)
    start = f.clock()
    calls = [0]
    def wait(timeout=None):
        calls[0] += 1
        if calls[0] == 1:
            return True
        if calls[0] == 10:
            f.stopped.set()
            return True
        clock['now'] += 5
        f.changes_bytes(['S2'], epoch=f.epoch)
        if legacy_event:
            f._legacy_wake = True
            f._wake.set()
        elif hasattr(f, 'queue_keyed'):
            f.queue_keyed(set(f.interest) if clock['now'] - start == 30 else {'S1'})
        else:
            f._wake.set()  # pre-cutover watcher admits the same file event
        return True
    monkeypatch.setattr(f._wake, 'wait', wait)
    pushes.clear()
    try:
        api._run(f)
        print('MAINTENANCE', {'elapsed': f.clock()-start, 'queued_registrations': f._registrations.qsize(), 'heartbeats': sum(bool(p.get('heartbeat')) for p in pushes), 'pushes':len(pushes)})
        assert f._registrations.qsize() == 0, 'keyed-only passes starve HTTP interest renewals'
        assert sum(bool(p.get('heartbeat')) for p in pushes) == 2, 'heartbeat cadence must survive keyed traffic'
    finally:
        lane.close()


def test_keyed_batches_yield_to_maintenance(monkeypatch):
    api = load('_keyed_batch_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    f, r, pushes, clock = make()
    monkeypatch.setattr(api, '_event_driven', lambda f: True)
    monkeypatch.setattr(api, '_settle', lambda *args: None)
    monkeypatch.setattr(api, 'write_timing', lambda f: None)
    monkeypatch.setattr(api, 'save_snapshot', lambda *args, **kwargs: True)
    keys = {f'S{i}' for i in range(11)}
    seen = []
    beats = []
    monkeypatch.setattr(f, 'beat', lambda: beats.append(len(seen)))
    def tick():
        f._cold.clear()
        f.watch(keys)
        f.queue_keyed(keys)
    monkeypatch.setattr(f, 'tick', tick)
    def refresh(batch):
        assert beats[-1] == len(seen), 'maintenance must precede keyed work'
        assert 0 < len(batch) <= 4
        seen.append(set(batch))
    monkeypatch.setattr(f, 'refresh_keyed', refresh)
    def wait(timeout=None):
        if len(seen) == 3:
            f.stopped.set()
        else:
            assert f._wake.is_set(), 'remaining keys must retain a trailing wake'
        return True
    monkeypatch.setattr(f._wake, 'wait', wait)
    api._run(f)
    assert [len(batch) for batch in seen] == [4, 4, 3]
    assert set.union(*seen) == keys
    assert not f._keyed_pending
