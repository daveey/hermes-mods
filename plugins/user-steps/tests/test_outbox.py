"""Owned worker lifetime and coalescing controls; no production state."""
import contextvars
import threading
from test_decisions import ROOT, load_module


def test_mounts_share_worker_and_release_does_not_stop_other_owner():
    runtime = load_module('outbox_lifetime', ROOT / 'outbox.py')
    entered, release, retried = threading.Event(), threading.Event(), threading.Event()
    def recover():
        entered.set()
        assert release.wait(3)
    a, b = runtime.Token(), runtime.Token()
    first = runtime.acquire('profile', a, recover, retried.set, 0.01)
    assert entered.wait(2)
    second = runtime.acquire('profile', b, lambda: None, retried.set, 0.01)
    try:
        assert first is second
        for _ in range(100):
            first.wake.set()
        assert len(runtime._WORKERS) == 1
    finally:
        release.set()
    assert retried.wait(2)
    runtime.release(first, a)
    assert second.thread.is_alive()
    assert runtime.release(second, b)
    assert not runtime._WORKERS
    third = runtime.acquire('profile', a, lambda: None, retried.set, 1)
    assert third is not first
    assert runtime.release(third, a)


def test_workers_retain_distinct_profile_contexts():
    runtime = load_module('outbox_profiles', ROOT / 'outbox.py')
    profile = contextvars.ContextVar('fixture_profile')
    seen, events, workers, tokens = {}, {}, [], []
    try:
        for name in ('a', 'b'):
            event = events[name] = threading.Event()
            token = tokens.append(runtime.Token()) or tokens[-1]
            mark = profile.set(name)
            def recover(name=name, event=event):
                seen[name] = profile.get()
                event.set()
            workers.append(runtime.acquire(name, token, recover, lambda: None, 1))
            profile.reset(mark)
        assert all(event.wait(2) for event in events.values())
        assert seen == {'a': 'a', 'b': 'b'}
    finally:
        for worker, token in zip(workers, tokens):
            assert runtime.release(worker, token)
