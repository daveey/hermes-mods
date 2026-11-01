"""Completed prompts resume only their still-blocked owner, through core."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from test_closed_asks import board, ut, snapshot, add_block, at, REQUEST


def bind(path, job, *, via="request"):
    request = {**REQUEST, "action": f"Authenticate for {job.name}. Unblock = resume; archive = abandon."}
    with sqlite3.connect(path) as con:
        con.execute("UPDATE tasks SET human_request=?", (json.dumps(request if via == "request" else REQUEST),))
    add_block(path, at(14, 6), 7, {"reason": f"Waiting on {job.name}" if via == "reason" else "Authenticate"})


def end(job, status="ran"):
    assert ut._take_final(job, {"status": status, "exit_code": 0 if status == "ran" else None})


@pytest.mark.parametrize("entry", ["watch_once", "sweep"])
@pytest.mark.parametrize("via", ["request", "reason"])
def test_worker_ran_unblocks_once_with_comment(board, entry, via):
    path, job, _ = board
    bind(path, job, via=via)
    end(job)
    for _ in range(2):
        ut.watch_once(job, {}) if entry == "watch_once" else ut.sweep_jobs()
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT status FROM tasks").fetchone()[0] == "ready"
        comments = con.execute("SELECT body FROM task_comments").fetchall()
        assert len(comments) == 1
        assert job.name in comments[0][0] and "exit_code=0" in comments[0][0]
        assert "PDT" in comments[0][0] or "PST" in comments[0][0]
        assert con.execute("SELECT count(*) FROM task_events WHERE kind='unblocked'").fetchone()[0] == 1


def test_declined_comments_once_without_unblocking(board):
    path, job, _ = board
    bind(path, job)
    end(job, "declined")
    ut.watch_once(job, {})
    ut.sweep_jobs()
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT status FROM tasks").fetchone()[0] == "blocked"
        comments = con.execute("SELECT body FROM task_comments").fetchall()
        assert len(comments) == 1 and "declined" in comments[0][0]
        assert not con.execute("SELECT 1 FROM task_events WHERE kind='unblocked'").fetchall()


@pytest.mark.parametrize("case", ["unrelated", "done", "archived", "running", "current_run", "later_attachment", "prefix_only"])
def test_completed_job_without_matching_block_has_no_writes(board, case):
    path, job, _ = board
    if case != "unrelated":
        bind(path, job)
    if case in ("done", "archived", "running"):
        with sqlite3.connect(path) as con:
            con.execute("UPDATE tasks SET status=?", (case,))
    if case == "current_run":
        with sqlite3.connect(path) as con:
            con.execute("UPDATE tasks SET current_run_id=8")
    if case == "prefix_only":
        with sqlite3.connect(path) as con:
            con.execute("UPDATE tasks SET human_request=?", (json.dumps({**REQUEST, "action": job.name + "-other"}),))
    if case == "later_attachment":
        (job / "attachment.json").write_text(json.dumps({"task_id": "t_card", "board_db": str(path), "run_id": 8}))
    end(job)
    before = snapshot(path)
    ut.watch_once(job, {})
    ut.sweep_jobs()
    assert snapshot(path) == before


def test_detached_worker_watchdog_unblocks_once(board):
    path, job, db = board
    bind(path, job)
    end(job)
    env = ut.launch_env()
    env["HERMES_USER_TERMINAL_CORE_ROOT"] = str(Path(db.__file__).resolve().parent.parent)
    env.pop("PYTHONPATH", None)
    for _ in range(2):
        result = subprocess.run([sys.executable, str(Path(ut.__file__)), "--watchdog", str(job)],
                                env=env, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT status FROM tasks").fetchone()[0] == "ready"
        assert con.execute("SELECT count(*) FROM task_comments").fetchone()[0] == 1


def test_one_step_card_guidance(board):
    _, job, _ = board
    for text in (ut._card_note(job), ut.SCHEMA["description"]):
        assert "Authenticate in the window; the card resumes by itself when the command finishes." in text
        assert "Unblock = resume without the result; archive = abandon." in text


def test_unblock_comment_failure_is_atomic_and_retried(board, monkeypatch):
    path, job, db = board
    bind(path, job)
    end(job)
    before = snapshot(path)
    original = db.unblock_task
    def fail(con, task, **kwargs):
        def bad(*args, **kw):
            raise RuntimeError("injected commit-path failure")
        with monkeypatch.context() as m:
            m.setattr(db, "add_comment", bad)
            return original(con, task, **kwargs)
    monkeypatch.setattr(db, "unblock_task", fail)
    ut.watch_once(job, {})
    assert snapshot(path) == before
    monkeypatch.setattr(db, "unblock_task", original)
    ut.watch_once(job, {})
    assert len(snapshot(path)["task_comments"]) == 1


def test_final_before_worker_blocks_is_reconciled_later(board):
    path, job, _ = board
    end(job)
    ut.sweep_jobs()
    assert not snapshot(path)['task_comments']
    bind(path, job)
    ut.sweep_jobs()
    assert len(snapshot(path)['task_comments']) == 1


def test_repeated_sweep_never_takes_attachment_lock_after_resume(board, monkeypatch):
    path, job, _ = board
    bind(path, job)
    end(job)
    ut.sweep_jobs()
    def forbidden(*args):
        pytest.fail('completed owner must be filtered before a write lock')
    monkeypatch.setattr(ut, '_attach_lock', forbidden)
    ut.sweep_jobs()
