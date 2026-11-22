"""Loss of notification coverage is stale data, never permission to poll databases."""
import threading
import time

import pytest

from test_producer import API, Feed, Watched, load, running


@pytest.mark.parametrize('failure', ['unsupported', 'arming', 'waiting'])
def test_watch_failure_never_starts_timed_database_work(tmp_path, monkeypatch, failure):
    api = load('_spf_failure_idle_' + failure, API)
    r = Watched(tmp_path)
    pushed = []
    f = Feed(r, emit=pushed.append, heartbeat_s=0.05)
    f.changes(['S1'])
    calls = {'tick': 0, 'decision': 0, 'beat': 0}
    failed = threading.Event()
    closed = threading.Event()

    class Watch:
        ok = failure != 'unsupported'
        stale = False

        def set_paths(self, paths):
            if failure == 'arming':
                failed.set()
                raise OSError('watch refused')

        def wait(self, timeout):
            failed.set()
            raise OSError('watch lost')

        def close(self):
            closed.set()

    monkeypatch.setattr(api, 'FileWatch', Watch)
    monkeypatch.setattr(api, 'CoreReaders', lambda: r)
    monkeypatch.setattr(api, 'save_snapshot', lambda f: True)
    monkeypatch.setattr(api, 'TICK_S', 0.05)
    monkeypatch.setattr(api, 'MIN_PAUSE_S', 0.01)
    for name, key in [('tick', 'tick'), ('refresh_decisions', 'decision'), ('beat', 'beat')]:
        real = getattr(f, name)
        def wrapped(*args, _real=real, _key=key, **kwargs):
            calls[_key] += 1
            return _real(*args, **kwargs)
        setattr(f, name, wrapped)
    threads = running(api, f)
    try:
        if failure != 'unsupported':
            assert failed.wait(2)
        deadline = time.monotonic() + 2
        while (not calls['tick'] or not calls['decision']) and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)  # allow startup hydration to finish
        before = calls.copy()
        observation = f.bundles['S1']['bundle']['computed_at']
        pushed.clear()
        time.sleep(0.5)
        assert (calls['tick'], calls['decision']) == (before['tick'], before['decision']), calls
        assert calls['beat'] > before['beat'], 'memory ageing/liveness must continue without a watcher'
        assert f.bundles['S1']['bundle']['computed_at'] == observation
        assert pushed and all('S1' not in p.get('fresh', []) for p in pushed), 'no coverage means no fresh vouch'
        assert closed.is_set(), 'even an unavailable watcher is closed'
    finally:
        f.stopped.set()
        for thread in threads:
            thread.join(3)
    assert not any(thread.is_alive() for thread in threads)
