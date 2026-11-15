"""Fingerprint age limits reuse, never invalidation of an older answer."""
import threading
from test_waiting_inputs import world, plugin, core, poll, _notice  # noqa: F401


def test_expired_fingerprint_invalidates_answer(world, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(plugin.time, 'monotonic', lambda: now[0])
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    _notice(world['state'], 'c1', 101)
    plugin.waiting_inputs()
    now[0] = 101.5
    assert plugin.waiting_source() == {'c2': ['t_2']}


def test_lock_wait_does_not_expire_invalidation(world, monkeypatch):
    real = core.pending_closeouts
    gate, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        out = real(*args, **kwargs)
        if threading.current_thread().name == 'old-answer':
            gate.set()
            release.wait(5)
        return out
    monkeypatch.setattr(core, 'pending_closeouts', slow)
    plugin._state.pop('waiting_cache', None)
    worker = threading.Thread(target=plugin.waiting_source, name='old-answer')
    worker.start()
    try:
        assert gate.wait(5)
        _notice(world['state'], 'c1', 101)
        plugin.waiting_inputs()
        timer = threading.Timer(plugin.INPUTS_REUSE_S + 0.3, release.set)
        timer.start()
        assert plugin.waiting_source() == {'c2': ['t_2']}
    finally:
        release.set()
        worker.join(5)
