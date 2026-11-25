"""Real feed/resume/reloader interleave; bounded teardown, no live services."""
import threading
import pytest
from test_core_readers import api, board
from test_source_coordinator_core import readers_for, compose, strict_empty
from test_routes import _hot, _restore


@pytest.mark.parametrize('remove_snapshot_lock_edge', [False, True])
def test_real_resume_loader_lock_order(api, board, monkeypatch, remove_snapshot_lock_edge):
    path, conn = board
    rd = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(rd, 'external_capture', strict_empty)
    current, core = _hot(monkeypatch, 'review_lock')
    f = core.Feed(rd, emit=lambda _: None)
    owner = compose(f, rd)
    core.PROCESS_FEED = f
    run_feed = current.feed
    monkeypatch.setattr(current, 'feed', lambda: None)  # no second autonomous fixture restart
    if remove_snapshot_lock_edge:
        capture_module = rd.snapshot_modules()
        monkeypatch.setattr(rd, 'snapshot_modules', lambda: capture_module)
    f.stopped.set()
    factory_entered = threading.Event()
    loader_at_process = threading.Event()
    refused = threading.Event()
    errors = []
    real_lock = threading.RLock()

    class BoundedLock:
        def __enter__(self):
            if threading.current_thread().name == 'review-reloader':
                loader_at_process.set()
                if not real_lock.acquire(timeout=1):
                    refused.set()
                    raise TimeoutError('review teardown: loader waits on PROCESS_LOCK')
            else:
                real_lock.acquire()
            return self
        def __exit__(self, *args):
            real_lock.release()

    monkeypatch.setattr(core, 'PROCESS_LOCK', BoundedLock())
    def factory():
        factory_entered.set()
        assert loader_at_process.wait(5)
        return rd  # real snapshot_modules acquires the shared loader lock
    monkeypatch.setattr(core, 'SNAPSHOT_READER_FACTORY', factory)
    monkeypatch.setattr(current, 'CoreReaders', lambda: rd)
    # Keep the actual saved board loop: installation must progress without any
    # current API call, but only after the restart's process lock is released.
    for name in ('_run', '_run_decisions'):
        if hasattr(current, name):
            monkeypatch.setattr(current, name, lambda feed, *args: feed.stopped.wait(5))

    def reload():
        assert factory_entered.wait(5)
        core.SOURCE_STAMP = ('review changed stamp',)
        core.SOURCE_SHA = 'review changed bytes'
        try:
            current._load_feed()
        except Exception as exc:
            errors.append(repr(exc))
    worker = threading.Thread(target=reload, name='review-reloader')
    worker.start()
    successor = None
    try:
        successor = run_feed()
        worker.join(5)
        assert not worker.is_alive()
        print('LOCK_PROBE', {'factory_entered': factory_entered.is_set(),
              'loader_at_process': loader_at_process.is_set(),
              'loader_timed_out': refused.is_set(), 'errors': errors})
        assert not refused.is_set(), 'actual loader and actual resume reverse their lock order'
        assert factory_entered.is_set() and loader_at_process.is_set()
        assert errors == []
    finally:
        factory_entered.set()
        worker.join(5)
        if successor is not None:
            successor.stopped.set()
            successor._source_coordinator.close()
            for name in ('_feed_thread', '_decision_thread', '_board_thread'):
                thread = getattr(successor, name, None)
                if thread:
                    thread.join(6)
        owner.close(); rd.release(); conn.close()
        _restore(current, core)
