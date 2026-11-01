"""An ended attempt is not necessarily a decision to abandon its terminal job."""
import os
import time

import pytest

from test_reattach_grace import (
    GRACE_S, _isolated, _board, _owned_job, _block, _unblock, _claim,
    _sql, _resume, _status, _spawn_runner, _pump, _finish, _screen,
    _kill_group, _reap, _gone, ut, needs_bsd_script,
)

GRACE_OUTCOMES = ["crashed", "reclaimed", "released", "rate_limited", "lease_lost",
                  "continued", None, "timed_out", "oom_killed", "signaled",
                  "changes_requested", "future_outcome"]


def end(db, outcome, run=8):
    # Core status is NOT NULL; outcome is nullable independently of status.
    _sql(db, "UPDATE task_runs SET status='ended', outcome=?, ended_at=? WHERE id=?",
         outcome, int(time.time()), run)
    _sql(db, "UPDATE tasks SET status='ready', current_run_id=NULL WHERE id='t_card'")


@pytest.mark.parametrize("outcome", GRACE_OUTCOMES)
@pytest.mark.parametrize("unblock", [True, False])
def test_infra_end_retains_earliest_grace_across_retries(tmp_path, monkeypatch, outcome, unblock):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    if unblock:
        _unblock(db, ago=GRACE_S - 120)
    _claim(db, 8, ago=GRACE_S - 90)
    end(db, outcome)
    assert ut.watch_once(job, {}) is None
    _claim(db, 9)
    end(db, outcome, run=9)
    assert ut.watch_once(job, {}) is None
    # Expire only the EARLIEST event, leaving the later retry fresh.
    if unblock:
        _sql(db, "UPDATE task_events SET created_at=? WHERE kind='unblocked'", int(time.time()-GRACE_S-5))
    else:
        _sql(db, "UPDATE task_runs SET started_at=? WHERE id=8", int(time.time()-GRACE_S-5))
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and "grace" in st["reason"], st


@needs_bsd_script
@pytest.mark.parametrize("outcome", GRACE_OUTCOMES[:7])
def test_pty_infra_end_next_run_attaches_sleep30_result(tmp_path, monkeypatch, outcome):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db, "sleep 30; echo survived", confirm=False, opened=False)
    buf = []
    pid, fd = _spawn_runner(job)
    try:
        _pump(fd, buf, until=lambda: (job / "cmd.pgid").exists())
        assert _status(job) == "running"
        _block(db)
        _unblock(db)
        _claim(db, 8)
        end(db, outcome)
        assert ut.watch_once(job, {}) is None
        assert _status(job) == "running" and not (job / "withdraw").exists()
        _claim(db, 9)
        res = _resume(monkeypatch, job, db, run=9)
        assert res["attachment"]["attached"] is True and res["status"] == "running", res
        _finish(pid, fd, buf, timeout=45)
        result = _resume(monkeypatch, job, db, run=9)
        assert result["status"] == "ran" and result["exit_code"] == 0, result
        assert "survived" in result["output"] and b"Hermes withdrew" not in _screen(buf)
    finally:
        _kill_group(job)
        _reap(pid, fd)


@needs_bsd_script
def test_pty_infra_end_expires_kills_group_and_shows_notice(tmp_path, monkeypatch):
    db = _board(tmp_path)
    child_file = tmp_path / "child"
    job = _owned_job(tmp_path, monkeypatch, db,
                     f"sleep 300 & echo $! > {child_file}; sleep 300", confirm=False, opened=False)
    buf = []
    pid, fd = _spawn_runner(job)
    child = None
    try:
        _pump(fd, buf, until=lambda: child_file.exists() and bool(child_file.read_text().strip()))
        child = int(child_file.read_text())
        _block(db)
        _unblock(db)
        _claim(db, 8)
        end(db, "crashed")
        assert ut.watch_once(job, {}) is None and not _gone(child)
        _sql(db, "UPDATE task_events SET created_at=? WHERE kind='unblocked'", int(time.time()-GRACE_S-5))
        st = ut.watch_once(job, {})
        assert st["status"] == "withdrawn" and "grace" in st["reason"], st
        _pump(fd, buf, until=lambda: _gone(child) and b"Nothing else to do here." in _screen(buf))
        assert _gone(child), "command group child survived"
        assert b"Hermes withdrew this request:" in _screen(buf)
        assert ut.wait_for(job, 0)["status"] == "withdrawn"
    finally:
        if child and not _gone(child):
            os.kill(child, 9)
        _kill_group(job)
        _reap(pid, fd)
