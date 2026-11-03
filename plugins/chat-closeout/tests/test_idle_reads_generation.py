"""Retained structural-read observers vs deleted WAL generations.

An external process can unlink a database's -wal/-shm while this process still maps them (after a
stray open/close dropped its POSIX locks). A retained observer must then (a) never make core
``kanban_generation.recover()`` refuse every later board connect, and (b) never keep answering from
the dead generation (a frozen ``data_version`` would label old rows current forever).
"""
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing

import pytest
from test_closeout import core
from test_idle_reads import _messages
from test_light_read import _board

linux = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='/proc/self/fd generation probe')


@pytest.fixture(autouse=True)
def close_observers():
    core._structural_reads.clear()
    yield
    core._structural_reads.clear()


def _wal(path):
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA journal_mode=wal').fetchone()[0] == 'wal'


def _deleted_maps(path):
    """This process's descriptors on a deleted -wal/-shm of ``path``."""
    watched = {os.path.realpath(path) + s for s in ('-wal', '-shm')}
    out = []
    for name in os.listdir('/proc/self/fd'):
        try:
            target = os.readlink(f'/proc/self/fd/{name}')
        except OSError:
            continue
        if target.endswith(' (deleted)') and target[:-len(' (deleted)')] in watched:
            out.append(target)
    return out


def _lose_sidecars(path, sql, params=()):
    # What a sibling's last close does once a stray close dropped this process's locks: checkpoint, unlink.
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    for suffix in ('-wal', '-shm'):
        os.unlink(str(path) + suffix)
    subprocess.run(
        [sys.executable, '-c', 'import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); '
         'c.execute(sys.argv[2], json.loads(sys.argv[3])); c.commit(); c.close()',
         str(path), sql, __import__('json').dumps(list(params))],
        check=True, timeout=30,
    )
    assert _deleted_maps(path), 'fixture: an observer should be stranded on the deleted generation'


def _core():
    return pytest.importorskip('hermes_cli.kanban_lockguard')


@linux
@pytest.mark.parametrize('opener', ['same_thread', 'worker_thread'])
def test_board_observer_sidecar_loss_then_core_connect_recovers(tmp_path, opener):
    """worker_thread: the observer was opened on another thread (closeout passes run off the caller's
    thread), so core recover() cannot close it itself; only the registered evict hook can retire it."""
    lockguard = _core()
    path = tmp_path / 'kanban.db'
    _board(path)
    _wal(path)
    if opener == 'same_thread':
        assert core.read_cards([path], ['t_run'])['t_run']['title'] == 'c'
    else:
        seen = []
        worker = threading.Thread(target=lambda: seen.append(core.read_cards([path], ['t_run'])['t_run']['title']))
        worker.start()
        worker.join(30)
        assert seen == ['c']
    _lose_sidecars(path, "UPDATE tasks SET title='renamed' WHERE id='t_run'")
    with closing(lockguard.connect(path)) as conn:  # recover() must retire the observer, not refuse
        assert conn.execute("SELECT title FROM tasks WHERE id='t_run'").fetchone()[0] == 'renamed'
    assert not _deleted_maps(path)
    assert core.read_cards([path], ['t_run'])['t_run']['title'] == 'renamed'


@linux
@pytest.mark.parametrize('kind', ['board'])
def test_polled_observer_reopens_after_sidecar_loss(tmp_path, kind):
    """(state.db is not observed across reads at all: test_idle_hot_paths covers its sidecar loss.)"""
    """No core connect in between: the next read itself notices the generation change."""
    path = tmp_path / ('kanban.db' if kind == 'board' else 'state.db')
    if kind == 'board':
        _board(path)
        _wal(path)
        read = lambda: core.read_cards([path], ['t_run'])['t_run']['title']
        before, sql, after = 'c', "UPDATE tasks SET title='renamed' WHERE id='t_run'", 'renamed'
    else:
        _messages(path)
        _wal(path)
        read = lambda: core.last_user_at([path], ['chat'])
        before, sql, after = 10, "INSERT INTO messages VALUES ('chat', 'user', 'new', 20, NULL)", 20
    assert read() == before
    assert read() == before
    _lose_sidecars(path, sql)
    assert read() == after
    assert not _deleted_maps(path)
    assert read() == after


@linux
def test_borrowed_observer_refuses_recovery_without_waiting_then_retry_succeeds(tmp_path):
    lockguard = _core()
    from hermes_cli.kanban_generation import DeletedWalGenerationError
    path = tmp_path / 'kanban.db'
    _board(path)
    _wal(path)
    core.read_cards([path], ['t_run'])
    entry = core._structural_reads.entries[str(path)]
    _lose_sidecars(path, "UPDATE tasks SET title='renamed' WHERE id='t_run'")
    result = []

    def connect():
        try:
            lockguard.connect(path).close()
            result.append('ok')
        except Exception as exc:  # noqa: BLE001 -- asserted below
            result.append(exc)

    with entry[3]:  # a read is in progress on another thread
        worker = threading.Thread(target=connect)
        worker.start()
        worker.join(10)
        assert not worker.is_alive(), 'recover() must never wait on a borrowed observer'
        entry[1].execute('SELECT 1')  # not closed under its borrower
    assert isinstance(result[0], DeletedWalGenerationError)
    with closing(lockguard.connect(path)) as conn:
        assert conn.execute("SELECT title FROM tasks WHERE id='t_run'").fetchone()[0] == 'renamed'
    with pytest.raises(sqlite3.ProgrammingError):
        entry[1].execute('SELECT 1')


@linux
def test_older_core_without_generation_registry_drops_on_shm_change(tmp_path, monkeypatch):
    """Core without the reader registry (no open_ro/register_reader_cache): plain observers, still dropped on a
    -shm generation change, and before any later core board connect."""
    generation = pytest.importorskip('hermes_cli.kanban_generation')
    lockguard = _core()
    monkeypatch.delattr(generation, 'open_ro')
    monkeypatch.delattr(generation, 'register_reader_cache')
    monkeypatch.setattr(core, '_GENERATION_HOOK', None)
    core._structural_reads.clear()
    path = tmp_path / 'kanban.db'
    _board(path)
    _wal(path)
    assert core.read_cards([path], ['t_run'])['t_run']['title'] == 'c'
    assert not hasattr(core._structural_reads.entries[str(path)][1], '_hermes_generation_shm')
    _lose_sidecars(path, "UPDATE tasks SET title='renamed' WHERE id='t_run'")
    assert core.read_cards([path], ['t_run'])['t_run']['title'] == 'renamed'
    assert not _deleted_maps(path)
    with closing(lockguard.connect(path)) as conn:
        assert conn.execute("SELECT title FROM tasks WHERE id='t_run'").fetchone()[0] == 'renamed'


def test_observers_are_shared_across_threads_and_globally_bounded(tmp_path, monkeypatch):
    """One bounded pool for the process, not MAX_DATABASES per thread (worker threads would
    otherwise retain up to 3 descriptors per database each)."""
    cache = core._structural_reads
    monkeypatch.setattr(cache, 'MAX_DATABASES', 2)
    paths = []
    for i in range(3):
        paths.append(tmp_path / f'state{i}.db')
        _board(paths[-1])
    barrier = threading.Barrier(6)
    errors = []

    def worker():
        try:
            barrier.wait(10)
            for _ in range(5):
                for p in paths:
                    assert core.read_cards([p], ['t_run'])['t_run']['title'] == 'c'
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert len(cache.entries) <= 2
    open_dbs = [n for n in os.listdir('/proc/self/fd') if sys.platform.startswith('linux')
                and os.path.realpath(f'/proc/self/fd/{n}').startswith(str(tmp_path / 'state'))]
    assert len(open_dbs) <= 2 * 3


_LOCK_ORDER_PROBE = r'''
import os, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_closeout import core
from test_light_read import _board
from hermes_cli import kanban_lockguard as lg, kanban_generation as gen
from hermes_cli.kanban_sync_connections import _Pool

path = os.path.join(sys.argv[2], 'kanban.db')
_board(path)
import sqlite3; c = sqlite3.connect(path); c.execute('PRAGMA journal_mode=wal'); c.close()
assert core.read_cards([Path(path)], ['t_run'])['t_run']['title'] == 'c'  # a retained, registered observer
y_in_recover, x_started = threading.Event(), threading.Event()

def fake_stranded(p):
    if threading.current_thread().name == 'Y' and not y_in_recover.is_set():
        y_in_recover.set()        # Y holds the board lifecycle lock inside recover()
        x_started.wait(2)
        time.sleep(0.3)           # let X enter chat-closeout's evict (and its close) first
    return [p + '-shm']           # a deleted WAL generation is stranded
lg.stranded_sidecars = fake_stranded
gen._poisoned.add(os.path.realpath(path))

def X():  # the sync pool's recover() runs without the lifecycle lock
    y_in_recover.wait(2); x_started.set()
    try:
        _Pool(2).take(Path(path), lambda: lg.connect(path))
    except Exception:
        pass

def Y():  # a locked connect(): lifecycle lock held while recover() calls every reader-cache hook
    try:
        lg.connect(path, timeout=2.0).close()
    except Exception:
        pass

ts = [threading.Thread(target=f, name=f.__name__, daemon=True) for f in (Y, X)]
for t in ts: t.start()
for t in ts: t.join(8)
alive = [t.name for t in ts if t.is_alive()]
print('ALIVE', alive, 'LOCKED', core._structural_reads._lock.locked(), flush=True)
os._exit(3 if alive else 0)
'''


def test_evict_never_closes_under_pool_lock_against_lifecycle_lock(tmp_path):
    """Core recover() runs reader-cache hooks both with a board's lifecycle lock held (connect) and without it
    (the sync connection pool). evict must unpublish under the pool lock and close after releasing it:
    closing a board connection takes the lifecycle lock (lock-order inversion otherwise)."""
    _core()
    pytest.importorskip('hermes_cli.kanban_sync_connections')
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.run([sys.executable, '-c', _LOCK_ORDER_PROBE, tests_dir, str(tmp_path)],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert proc.returncode == 0, proc.stdout + proc.stderr
