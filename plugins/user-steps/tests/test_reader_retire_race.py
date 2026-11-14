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
