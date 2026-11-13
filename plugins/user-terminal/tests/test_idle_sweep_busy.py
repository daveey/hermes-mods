"""A busy board (core ``BoardLifecycleBusy`` / SQLITE_BUSY) is a normal "retry next pass", never an ERROR.

The self-heal error detector files every ERROR record as a defect: a sweep that logged a busy board
via ``log.exception`` raised false alarms (defect plugin-efe5866d0e).
"""
import logging
import shutil
import sqlite3
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.requires_core  # tests/conftest.py: skipped when no Hermes core is importable

from test_closed_asks import board, ut  # noqa: F401 -- fixture
from test_idle_sweep import clear_sweep_cache, settled_card  # noqa: F401 -- autouse fixture


def _errors(caplog):
    return [r for r in caplog.records if r.name == 'user_terminal' and r.levelno >= logging.ERROR]


def _two_memoized_jobs(board):
    _, job = settled_card(board)
    second = job.with_name(job.name + '-second')
    shutil.copytree(job, second)
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL and second in ut._SWEEP_FINAL
    return job, second


def test_busy_board_skips_quietly_once_per_pass_and_retries(board, caplog):
    from hermes_cli.kanban_generation import BoardLifecycleBusy
    job, second = _two_memoized_jobs(board)
    with caplog.at_level(logging.DEBUG, logger='user_terminal'), \
            patch.object(ut, '_sweep_db_stamp', side_effect=BoardLifecycleBusy()) as observe:
        ut.sweep_jobs()
    assert _errors(caplog) == []
    assert observe.call_count == 1, 'a busy board is observed once per pass, not once per job'
    assert job not in ut._SWEEP_FINAL and second not in ut._SWEEP_FINAL
    busy = [r for r in caplog.records if r.name == 'user_terminal' and 'busy' in r.getMessage()]
    assert len(busy) == 1 and busy[0].levelno == logging.DEBUG
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger='user_terminal'):
        ut.sweep_jobs()  # the board is free again: both jobs reconcile and memoize
    assert _errors(caplog) == []
    assert job in ut._SWEEP_FINAL and second in ut._SWEEP_FINAL


def test_native_sqlite_busy_is_quiet_too(board, caplog):
    job, _second = _two_memoized_jobs(board)
    exc = sqlite3.OperationalError('database is locked')
    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
    with caplog.at_level(logging.DEBUG, logger='user_terminal'), \
            patch.object(ut, '_sweep_db_stamp', side_effect=exc):
        ut.sweep_jobs()
    assert _errors(caplog) == []
    assert job not in ut._SWEEP_FINAL


def test_other_board_errors_still_log_error(board, caplog):
    job, second = _two_memoized_jobs(board)
    with caplog.at_level(logging.DEBUG, logger='user_terminal'), \
            patch.object(ut, '_sweep_db_stamp', side_effect=sqlite3.OperationalError('disk I/O error')):
        ut.sweep_jobs()
    assert len(_errors(caplog)) == 2  # one per job, unchanged behaviour for real failures
    assert job not in ut._SWEEP_FINAL and second not in ut._SWEEP_FINAL


def test_reconcile_busy_is_quiet(board, caplog):
    from hermes_cli.kanban_generation import BoardLifecycleBusy
    _, job, _ = board
    with caplog.at_level(logging.DEBUG, logger='user_terminal'), \
            patch.object(ut, 'watch_once', side_effect=BoardLifecycleBusy()):
        ut._reconcile(job)
    assert _errors(caplog) == []
    with caplog.at_level(logging.DEBUG, logger='user_terminal'), \
            patch.object(ut, 'watch_once', side_effect=sqlite3.OperationalError('disk I/O error')):
        ut._reconcile(job)
    assert len(_errors(caplog)) == 1


def test_is_board_busy_without_core_helper(monkeypatch):
    import hermes_cli.kanban_generation as generation
    monkeypatch.delattr(generation, 'is_busy')
    locked = sqlite3.OperationalError('database table is locked')
    locked.sqlite_errorcode = sqlite3.SQLITE_LOCKED
    assert ut._is_board_busy(locked)
    assert ut._is_board_busy(generation.DeletedWalGenerationError('retry'))
    assert not ut._is_board_busy(sqlite3.OperationalError('disk I/O error'))
    assert not ut._is_board_busy(ValueError('x'))
