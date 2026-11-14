"""Capacity eviction must drain a borrower that already selected the old entry."""
import threading
from concurrent.futures import ThreadPoolExecutor

from test_reader_lifecycle import ROOT, closed, db, load  # noqa: F401


def test_queued_borrower_cannot_retain_capacity_evicted_entry(db, tmp_path):
    mod = load('capacity_eviction_mirror', ROOT / 'kanban_mirror.py')
    mod._POOL_MAX = 1
    other = tmp_path / 'other.db'
    other.write_bytes(db.read_bytes())
    waiting, resume = threading.Event(), threading.Event()
    opened = []
    original_open = mod._open_ro

    def tracked_open(path):
        c = original_open(path)
        opened.append(c)
        return c

    mod._open_ro = tracked_open

    class DelayedLock:
        """Pause after lookup, before acquisition; eviction can take the idle lock."""
        def __init__(self):
            self.lock = threading.Lock()

        def acquire(self, blocking=True):
            return self.lock.acquire(blocking=blocking)

        def release(self):
            self.lock.release()

        def __enter__(self):
            waiting.set()
            assert resume.wait(5), 'borrower was not released after eviction'
            self.lock.acquire()

        def __exit__(self, *exc):
            self.lock.release()

    try:
        with mod._kept(db) as original:
            assert original.execute('SELECT value FROM sample').fetchone()[0] == 1
        entry = mod._POOL[str(db)]
        entry.lock = DelayedLock()
        borrowed_entries = []

        def read():
            with mod._kept(db, entry_out=borrowed_entries) as c:
                assert c.execute('SELECT value FROM sample').fetchone()[0] == 1
            return c

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read)
            try:
                assert waiting.wait(5), 'borrower did not reach the selected entry'
                # Trigger real capacity eviction, not close_pool or direct _evict.
                with mod._kept(other) as retained:
                    assert retained.execute('SELECT value FROM sample').fetchone()[0] == 1
                assert str(db) not in mod._POOL
                assert set(mod._POOL) == {str(other)}
                closed(original)
            finally:
                resume.set()
            detached = future.result(timeout=5)

        assert borrowed_entries == [entry]
        assert detached is not original
        # Assert observable disposal first: M12 leaks this reopened detached reader.
        closed(detached)
        assert entry.retired
        assert entry.conn is None
        assert not mod._RETIRED
        with mod._kept(other) as reused:
            assert reused is retained
            assert reused.execute('SELECT value FROM sample').fetchone()[0] == 1
        assert len(opened) == 3
    finally:
        resume.set()
        mod.close_pool()
        # Detached mutant connections are outside the pool: clean those up too.
        for c in opened:
            c.close()
