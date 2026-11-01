"""Closed-window ask lifetime: isolated real core board, never live writes."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "__init__.py"
spec = importlib.util.spec_from_file_location("user_terminal_closed", SOURCE)
ut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ut)
REQUEST = {"action": "Type the password in the open window", "why_human": "Only the owner knows it",
           "interaction": "user_terminal", "scope": "One command", "evidence": "A password prompt",
           "choices": [{"label": "Retry", "value": "retry"}, {"label": "Drop", "value": "drop"}]}


@pytest.fixture
def board(monkeypatch):
    # TMPDIR can be under a fenced workspace. Keep inherited descendant markers;
    # only this fixture's board pin/home and worker identity are isolated.
    with tempfile.TemporaryDirectory(prefix="user-terminal-test-", dir=Path.home()) as tmp:
        root = Path(tmp)
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
        monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_WORKER_TASK", raising=False)
        monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(root / "jobs"))
        monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
        monkeypatch.setattr(ut, "confirm_mode", lambda: "off")
        monkeypatch.setattr(ut, "max_wait_minutes", lambda: 120)
        monkeypatch.setattr(ut, "reattach_grace_minutes", lambda: 10)
        monkeypatch.setattr(ut, "_kill_command", lambda *a: None)
        db, classify = ut._kanban_api()
        from agent.delegation_context import kanban_path_is_fenced
        path = root / "kanban.db"
        assert db.kanban_db_path("default").resolve() == path.resolve()
        assert not kanban_path_is_fenced(path)
        con = sqlite3.connect(path)
        con.executescript(db.SCHEMA_SQL)
        con.execute("INSERT INTO tasks (id,title,status,created_at,current_run_id,block_kind,human_request) "
                    "VALUES ('t_card','fixture','blocked',1,NULL,'human',?)", (json.dumps(REQUEST),))
        con.commit()
        con.close()
        owner = {"kind": "card", "board": "default", "task_id": "t_card", "run_id": 7}
        monkeypatch.setattr(ut, "board_db_path", lambda b: str(path))
        job = ut.create_job("true", "fixture", str(root), owner=owner)
        yield path, job, db


def snapshot(path):
    with sqlite3.connect(path) as con:
        return {table: con.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("tasks", "task_comments", "task_events", "task_runs")}


def final(job, status="withdrawn", reason="expired after 120 min; ask again if still needed", winter=False):
    st = {"status": status, "reason": reason, "job_id": job.name}
    assert ut._take_final(job, st)
    stamp = datetime(2026, 1 if winter else 10, 15, 14, 37,
                     tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
    os.utime(job / "final.lock" / "result.json", (stamp, stamp))
    return st


@pytest.mark.parametrize("status,reason", [("withdrawn", "expired after 120 min; ask again if still needed"),
                                           ("abandoned", "the terminal window closed before the command finished"),
                                           ("failed", "could not open a terminal window")])
@pytest.mark.parametrize("entry", ["watch_once", "sweep"])
def test_closed_ask_reconciles_once_preserving_every_other_field(board, status, reason, entry):
    path, job, db = board
    st = final(job, status, reason)
    before = snapshot(path)
    for _ in range(2):
        if entry == "sweep":
            ut.sweep_jobs()
        else:
            assert ut.watch_once(job, {}) == st
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM tasks").fetchone()
        request = json.loads(row["human_request"])
        assert request == {**REQUEST, "action": "window closed 14:37 PDT. Unblock = agent reopens window; archive = drop request."}
        comments = con.execute("SELECT body FROM task_comments").fetchall()
        assert len(comments) == 1
        assert all(x in comments[0][0] for x in (job.name, status, reason, "2026-10-15T14:37:00-07:00"))
    after = snapshot(path)
    # Exact lifecycle preservation, not merely the status column.
    assert before["task_runs"] == after["task_runs"]
    assert len(after["task_events"]) == len(before["task_events"]) + 2
    old = list(before["tasks"][0]); new = list(after["tasks"][0])
    with sqlite3.connect(path) as con:
        columns = [r[1] for r in con.execute("PRAGMA table_info(tasks)")]
    old[columns.index("human_request")] = new[columns.index("human_request")]
    assert old == new
    assert ut.read_status(job) == st


def test_winter_uses_pst(board):
    path, job, _ = board
    final(job, winter=True)
    ut.watch_once(job, {})
    with sqlite3.connect(path) as con:
        request = json.loads(con.execute("SELECT human_request FROM tasks").fetchone()[0])
    assert "14:37 PST" in request["action"]


@pytest.mark.parametrize("state,current", [("ready", None), ("done", None), ("archived", None),
                                           ("triage", None), ("blocked", 8)])
def test_nonparked_owner_has_zero_writes(board, monkeypatch, state, current):
    path, job, db = board
    with sqlite3.connect(path) as con:
        con.execute("UPDATE tasks SET status=?,current_run_id=?", (state, current))
    final(job)
    before = snapshot(path)
    calls = []
    monkeypatch.setattr(db, "add_comment", lambda *a, **k: calls.append(a))
    ut.sweep_jobs()
    assert snapshot(path) == before
    assert not calls


@pytest.mark.parametrize("kind", ["later_attachment", "worker", "fenced", "no_request", "ran", "declined", "manual_withdraw"])
def test_ineligible_jobs_have_zero_writes(board, monkeypatch, kind):
    path, job, db = board
    st = final(job, "ran" if kind == "ran" else "declined" if kind == "declined" else "withdrawn",
               "no longer needed" if kind == "manual_withdraw" else "expired after 120 min; ask again if still needed")
    if kind == "later_attachment":
        (job / "attachment.json").write_text(json.dumps({"task_id": "t_card", "board_db": str(path), "run_id": 8}))
    elif kind == "worker":
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    elif kind == "fenced":
        def refuse(*a):
            raise PermissionError("descendant fence")
        monkeypatch.setattr(db, "_assert_not_delegated_child_mutation", refuse)
    elif kind == "no_request":
        with sqlite3.connect(path) as con:
            con.execute("UPDATE tasks SET human_request=NULL")
    before = snapshot(path)
    ut.watch_once(job, {})
    assert snapshot(path) == before
    assert ut.read_status(job) == st


def test_failed_comment_rolls_back_then_final_path_retries(board, monkeypatch, caplog):
    path, job, db = board
    st = final(job)
    before = snapshot(path)
    original = db.add_comment
    def fail(*a, **k):
        raise RuntimeError("injected comment failure")
    monkeypatch.setattr(db, "add_comment", fail)
    assert ut.watch_once(job, {}) == st
    assert snapshot(path) == before
    assert "injected comment failure" in caplog.text
    monkeypatch.setattr(db, "add_comment", original)
    ut.watch_once(job, {})
    assert len(snapshot(path)["task_comments"]) == 1


@pytest.mark.parametrize("cause", ["expiry", "dead_window", "no_window", "failed_open"])
def test_new_terminal_outcome_propagates(board, monkeypatch, cause):
    path, job, _ = board
    meta = json.loads((job / "meta.json").read_text())
    meta["created"] = 1
    (job / "meta.json").write_text(json.dumps(meta))
    if cause in ("expiry", "dead_window"):
        (job / "opened").write_text("12345\n")
        monkeypatch.setattr(ut, "_runner_gone", lambda *a: cause == "dead_window")
    if cause == "failed_open":
        ut.fail_job(job, "could not open a terminal window")
    result = ut.watch_once(job, {})
    assert result["status"] == {"expiry": "withdrawn", "dead_window": "abandoned", "no_window": "abandoned", "failed_open": "failed"}[cause]
    assert len(snapshot(path)["task_comments"]) == 1


def test_loaded_core_modules_are_never_replaced(board, monkeypatch):
    _, _, db = board
    import hermes_cli
    original = hermes_cli
    monkeypatch.setenv("HERMES_USER_TERMINAL_CORE_ROOT", "/does/not/exist")
    loaded, _ = ut._kanban_api()
    assert loaded is db and sys.modules["hermes_cli"] is original


def test_standalone_watchdog_bootstraps_real_core(board):
    path, job, db = board
    final(job)
    env = ut.launch_env()
    env["HERMES_USER_TERMINAL_CORE_ROOT"] = str(Path(db.__file__).resolve().parent.parent)
    env.pop("PYTHONPATH", None)
    # Run the exact standalone entry point, with no inherited sys.path/modules.
    result = subprocess.run([sys.executable, str(SOURCE), "--watchdog", str(job)], env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert len(snapshot(path)["task_comments"]) == 1, result.stderr
