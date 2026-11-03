"""Sweep observers vs deleted WAL generations, plus skip/log edge cases of the final-job memo."""
import gc
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing

import pytest

pytestmark = pytest.mark.requires_core  # tests/conftest.py: skipped when no Hermes core is importable

from test_closed_asks import board, ut  # noqa: F401 -- fixture
from test_completed_jobs import bind
from test_idle_sweep import clear_sweep_cache, settled_card  # noqa: F401 -- autouse fixture

linux = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='/proc/self/fd generation probe')


def _deleted_maps(path):
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


def _wal_settled(board):
    path, job = settled_card(board)
    with closing(sqlite3.connect(path)) as con:
        assert con.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL and str(path) in ut._SWEEP_DBS
    return path, job


def _lose_sidecars_and_block(path):
    """An external process checkpointed and unlinked the sidecars (what a sibling's last close does after a
    stray close dropped this process's locks), then another process blocked the card again."""
    with closing(sqlite3.connect(path)) as con:
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    for suffix in ('-wal', '-shm'):
        os.unlink(str(path) + suffix)
    subprocess.run([sys.executable, '-c', "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
                    "c.execute(\"UPDATE tasks SET status='blocked'\"); c.commit(); c.close()", str(path)],
                   check=True, timeout=30)
    assert _deleted_maps(path), 'fixture: the observer should be stranded on the deleted generation'


@linux
def test_sweep_observer_sidecar_loss_then_core_connect_recovers(board):
    lockguard = pytest.importorskip('hermes_cli.kanban_lockguard')
    path, _job = _wal_settled(board)
    _lose_sidecars_and_block(path)
    with closing(lockguard.connect(path)) as con:  # recover() must retire the observer, not refuse
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
    assert not _deleted_maps(path)
    assert str(path) not in ut._SWEEP_DBS


@linux
def test_polled_sweep_reconciles_after_sidecar_loss(board):
    """No core connect in between: the next sweep drops the stranded observer and reconciles."""
    path, job = _wal_settled(board)
    bind(path, job)  # in-process writes only BEFORE the loss: after it they would join the dead generation
    gc.collect()  # bind's `with sqlite3.connect()` handles are closed only by the collector
    _lose_sidecars_and_block(path)
    ut.sweep_jobs()
    assert not _deleted_maps(path)
    with closing(sqlite3.connect(path)) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'


@linux
def test_borrowed_sweep_observer_refuses_recovery_without_waiting(board):
    lockguard = pytest.importorskip('hermes_cli.kanban_lockguard')
    from hermes_cli.kanban_generation import DeletedWalGenerationError
    path, _job = _wal_settled(board)
    _lose_sidecars_and_block(path)
    result = []

    def connect():
        try:
            lockguard.connect(path).close()
            result.append('ok')
        except Exception as exc:  # noqa: BLE001 -- asserted below
            result.append(exc)

    with ut._SWEEP_DB_LOCK:  # a sweep is reading the observer on another thread
        worker = threading.Thread(target=connect)
        worker.start()
        worker.join(10)
        assert not worker.is_alive(), 'recover() must never wait on a borrowed observer'
    assert isinstance(result[0], DeletedWalGenerationError)
    with closing(lockguard.connect(path)) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'


@linux
def test_older_core_without_generation_registry_drops_on_shm_change(board, monkeypatch):
    """Core without the reader registry (no open_ro/register_reader_cache): plain observers, still dropped on a
    -shm generation change BEFORE the reconcile's own core connect (which would otherwise refuse)."""
    generation = pytest.importorskip('hermes_cli.kanban_generation')
    monkeypatch.delattr(generation, 'open_ro')
    monkeypatch.delattr(generation, 'register_reader_cache')
    monkeypatch.setattr(ut, '_SWEEP_GENERATION_HOOK', False)
    path, job = _wal_settled(board)
    bind(path, job)
    gc.collect()
    _lose_sidecars_and_block(path)
    ut.sweep_jobs()
    assert not _deleted_maps(path)
    with closing(sqlite3.connect(path)) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'


def test_relative_skip_matches_the_absolute_job(board, monkeypatch):
    _, job = settled_card(board)
    monkeypatch.chdir(job.parent)
    ut.sweep_jobs(skip=type(job)(job.name))
    assert job not in ut._SWEEP_FINAL
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL


@pytest.mark.parametrize('name', ['final.lock/result.json', 'meta.json', 'owner.json'])
def test_malformed_settled_file_is_rechecked_quietly(board, caplog, name):
    """The legacy readers tolerate a malformed file; the memo must not cache that fallback, and must not
    log an exception for it on every tick either (the sweep was silent about it before the memo)."""
    _, job = settled_card(board)
    (job / name).write_text('{')
    with caplog.at_level(logging.DEBUG, logger='user_terminal'):
        for _ in range(3):
            ut.sweep_jobs()
    assert job not in ut._SWEEP_FINAL
    assert [r for r in caplog.records if r.name == 'user_terminal' and r.levelno >= logging.WARNING] == []
