"""close_pool() racing a borrower's exit must never leave a retired reader open.

The borrower checks ``entry.retired`` and then releases ``entry.lock``. If close_pool runs in
that gap, its non-blocking acquire fails and it skips the close, so the borrower itself must
close the retired entry after release; otherwise the connection lives until cyclic GC.
"""
import importlib.util
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'kanban_mirror.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'race.db'
    with closing(sqlite3.connect(path)) as c, c:
        c.execute('CREATE TABLE sample(value)')
    return path


class RetireBeforeRelease:
    """entry.lock stand-in: the borrower's final release first runs close_pool on another thread,
    i.e. after the borrower's retired check and before the lock is free."""

    def __init__(self, mod, when):
        self.lock, self.mod, self.when, self.fired = threading.Lock(), mod, when, False
        self.borrower = threading.get_ident()

    def acquire(self, blocking=True):
        return self.lock.acquire(blocking=blocking)

    def _retire(self):
        t = threading.Thread(target=self.mod.close_pool)
        t.start()
        t.join(5)
        assert not t.is_alive()
        self.fired = True

    def release(self):
        mine = threading.get_ident() == self.borrower and not self.fired
        if mine and self.when == 'before':
            self._retire()
        self.lock.release()
        if mine and self.when == 'after':
            self._retire()

    def __enter__(self):
        self.acquire()

    def __exit__(self, *exc):
        self.release()


@pytest.mark.parametrize('when', ['before', 'after'])
def test_close_pool_in_borrower_release_gap_closes_reader(db, when):
    mod = load(f'race_mirror_{when}')
    with mod._kept(db):
        pass  # pool the entry
    entry = mod._POOL[str(db)]
    entry.lock = RetireBeforeRelease(mod, when)
    with mod._kept(db) as c:
        assert c.execute('SELECT count(*) FROM sample').fetchone()[0] == 0
    assert entry.lock.fired
    assert entry.retired and not mod._POOL
    assert entry.conn is None, 'retired reader left open until GC'
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        c.execute('SELECT 1')
    assert entry.lock.acquire(blocking=False)  # lock left free
    entry.lock.lock.release()


def test_close_pool_in_gap_after_failed_read_closes_reader(db):
    mod = load('race_mirror_error')
    with mod._kept(db):
        pass
    entry = mod._POOL[str(db)]
    entry.lock = RetireBeforeRelease(mod, 'before')
    with pytest.raises(sqlite3.OperationalError):
        with mod._kept(db) as c:
            c.execute('SELECT * FROM missing_table')
    assert entry.lock.fired and entry.retired and entry.conn is None
    with mod._kept(db) as later:  # module retired: one-shot readers only
        assert later.execute('SELECT 1').fetchone()[0] == 1
    assert not mod._POOL


def test_retirement_recheck_returns_while_queued_borrower_owns_lock(db):
    mod = load('race_mirror_queued')
    with mod._kept(db):
        pass
    entry = mod._POOL[str(db)]
    first = threading.get_ident()
    queued, holding, finish = (threading.Event() for _ in range(3))
    errors, connections, entries, rechecks = [], [], [], []

    class HandoffLock:
        """Force the release gap, retaining a real lock and real second borrow.

        Reject a contended blocking recheck synchronously instead of letting a
        mutant deadlock the test. Event timeouts are watchdogs, not race timing.
        """
        def __init__(self):
            self.lock = threading.Lock()
            self.handed_off = False

        def acquire(self, blocking=True):
            if threading.get_ident() == first and self.handed_off:
                assert holding.is_set() and not finish.is_set()
                assert self.lock.locked()
                rechecks.append(blocking)
                assert not blocking, 'retirement recheck would wait for queued borrower'
            elif threading.get_ident() != first:
                queued.set()  # _kept already selected this entry, before retirement
            return self.lock.acquire(blocking=blocking)

        def release(self):
            if threading.get_ident() == first and not self.handed_off:
                assert queued.wait(5), 'second borrower did not queue'
                # First borrower has passed its in-lock retired check.
                mod.close_pool()
                assert entry.retired and not mod._POOL
                self.handed_off = True
                self.lock.release()
                assert holding.wait(5), 'second borrower did not take ownership'
            else:
                self.lock.release()

        def __enter__(self):
            self.acquire()

        def __exit__(self, *exc):
            self.release()

    entry.lock = lock = HandoffLock()

    def borrow_second():
        try:
            with mod._kept(db, entry_out=entries) as conn:
                connections.append(conn)
                assert conn.execute('SELECT count(*) FROM sample').fetchone()[0] == 0
                holding.set()
                assert finish.wait(10), 'first borrower did not release second'
                assert conn.execute('SELECT 1').fetchone()[0] == 1
        except BaseException as exc:
            errors.append(exc)

    second = threading.Thread(target=borrow_second, daemon=True)
    conn = None
    try:
        with mod._kept(db) as conn:
            second.start()
            assert queued.wait(5), 'second borrower did not select entry'
        # This point must be reached while the second borrower still holds the
        # same connection; no sleep or elapsed-time threshold proves ordering.
        assert holding.is_set() and not finish.is_set() and lock.lock.locked()
        assert entries == [entry] and connections == [conn]
        assert entry.conn is conn and rechecks == [False]
        assert not errors
    finally:
        finish.set()
        if second.ident is not None:
            second.join(5)
        assert not second.is_alive(), 'second borrower leaked'
        try:
            assert entry.conn is None, 'last queued borrower left retired reader open'
            if conn is not None:
                with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                    conn.execute('SELECT 1')
        finally:
            # Retired entries are detached: clean up even on a failed assertion.
            with lock.lock:
                mod._close_entry(entry)
    assert not errors
    assert entry.conn is None and not mod._POOL
    assert conn is not None
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        conn.execute('SELECT 1')
