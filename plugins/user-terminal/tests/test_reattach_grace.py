"""Owned prompts survive a card's unblock / new run for a re-attach grace (user_terminal.reattach_grace_minutes).

Contract: a card-owned job is withdrawn at once when the card is done/archived, on an explicit withdraw, at max
age, or when a run later than the acknowledged one ENDED without re-attaching. An unblock or a new run after the
acknowledged point starts a grace (default 10 min) measured from the earliest such board event; polls never reset
it. The card's own current run re-attaches by calling run_in_user_terminal(job_id=...): that records a per-job
attachment sidecar (run id + board event cursor) before any sweep/check, so the job keeps waiting/running and
returns its result. Other cards, other boards and stale runs cannot attach.

Every board here is a throwaway sqlite file under tmp_path whose task_runs/task_events DDL is copied from core's
kanban_db.py (fallback: a verbatim copy). No live board, card or terminal window is touched; runner.sh runs under
pty.fork().
"""

import importlib.util
import json
import os
import pty
import re
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("user_terminal_reattach",
                                               Path(__file__).resolve().parents[1] / "__init__.py")
ut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ut)

needs_bsd_script = pytest.mark.skipif(sys.platform != "darwin",
                                      reason="job runner uses macOS `script -q FILE cmd` syntax (util-linux differs)")

GRACE_S = 10 * 60  # the default grace
CFG: dict = {}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("HERMES_KANBAN") or k in ("HERMES_USER_TERMINAL_MAX_WAIT_MINUTES",
                                                  "HERMES_USER_TERMINAL_REATTACH_GRACE_MINUTES",
                                                  "HERMES_USER_TERMINAL_CONFIRM", "HERMES_SESSION_ID"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "kanban-home"))  # never the shared board
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HERMES_USER_TERMINAL_RUNNER_WATCHDOG", "0")
    monkeypatch.setattr(ut, "spawn_watchdog", lambda job: None)
    monkeypatch.setattr(ut, "open_window", lambda job: "Terminal")
    # Hermes config: a controlled dict, never the user's real config.yaml.
    CFG.clear()
    cfg = types.ModuleType("hermes_cli.config")
    cfg.load_config = lambda: {"user_terminal": dict(CFG)}
    pkg = types.ModuleType("hermes_cli")
    pkg.config = cfg
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", cfg)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_db", None)


# ---------------------------------------------------------------- board fixture (real core DDL)

_FALLBACK_DDL = {
    "task_events": """CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, run_id INTEGER, kind TEXT NOT NULL,
    payload TEXT, created_at INTEGER NOT NULL);""",
    "task_runs": """CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, profile TEXT, step_key TEXT, status TEXT NOT NULL,
    claim_lock TEXT, claim_expires INTEGER, worker_pid INTEGER, worker_started_at INTEGER,
    max_runtime_seconds INTEGER, last_heartbeat_at INTEGER, started_at INTEGER NOT NULL, ended_at INTEGER,
    outcome TEXT, summary TEXT, metadata TEXT, error TEXT);""",
}


def _ddl(table: str) -> str:
    core = Path(os.environ.get("HERMES_CORE_CHECKOUT") or Path.home() / ".hermes" / "hermes-agent")
    try:
        src = (core / "hermes_cli" / "kanban_db.py").read_text()
        m = re.search(r"CREATE TABLE IF NOT EXISTS %s \(.*?\n\);" % table, src, re.S)
        if m:
            return m.group(0)
    except OSError:
        pass
    return _FALLBACK_DDL[table]


def _board(tmp_path: Path, name: str = "kanban.db") -> Path:
    db = tmp_path / name
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT NOT NULL, current_run_id INTEGER)")
    con.execute(_ddl("task_events"))
    con.execute(_ddl("task_runs"))
    now = int(time.time())
    # run 7 is the worker that asks for the terminal
    con.execute("INSERT INTO task_runs (id, task_id, status, started_at) VALUES (7, 't_card', 'running', ?)",
                (now - 1200,))
    con.execute("INSERT INTO tasks VALUES ('t_card', 'running', 7)")
    con.execute("INSERT INTO tasks VALUES ('t_other', 'running', 30)")
    con.execute("INSERT INTO task_events (task_id, run_id, kind, created_at) VALUES ('t_card', 7, 'claimed', ?)",
                (now - 1200,))
    con.commit()
    con.close()
    return db


def _sql(db: Path, q: str, *a):
    con = sqlite3.connect(db)
    try:
        cur = con.execute(q, a)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _block(db: Path) -> None:
    """Run 7 blocks to wait on the user (core: run ended + current_run_id NULL)."""
    now = int(time.time())
    _sql(db, "UPDATE task_runs SET status='blocked', outcome='blocked', ended_at=? WHERE id=7", now)
    _sql(db, "UPDATE tasks SET status='blocked', current_run_id=NULL WHERE id='t_card'")
    _sql(db, "INSERT INTO task_events (task_id, run_id, kind, created_at) VALUES ('t_card', 7, 'blocked', ?)", now)


def _unblock(db: Path, ago: float = 0) -> int:
    _sql(db, "UPDATE tasks SET status='ready' WHERE id='t_card'")
    return _sql(db, "INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_card', 'unblocked', ?)",
                int(time.time() - ago))


def _claim(db: Path, run: int = 8, ago: float = 0) -> None:
    t = int(time.time() - ago)
    _sql(db, "INSERT INTO task_runs (id, task_id, status, started_at) VALUES (?, 't_card', 'running', ?)", run, t)
    _sql(db, "UPDATE tasks SET status='running', current_run_id=? WHERE id='t_card'", run)
    _sql(db, "INSERT INTO task_events (task_id, run_id, kind, created_at) VALUES ('t_card', ?, 'claimed', ?)", run, t)


def _end_run(db: Path, run: int, outcome: str = "crashed") -> None:
    _sql(db, "UPDATE task_runs SET status=?, outcome=?, ended_at=? WHERE id=?", outcome, outcome, int(time.time()),
         run)
    _sql(db, "UPDATE tasks SET status='ready', current_run_id=NULL WHERE id='t_card'")


def _age_events(db: Path, secs: float) -> None:
    """Move every unblock/claim/run start that happened after the request `secs` into the past."""
    t = int(time.time() - secs)
    _sql(db, "UPDATE task_events SET created_at=? WHERE kind IN ('unblocked', 'claimed') AND (run_id IS NULL OR "
             "run_id <> 7)", t)
    _sql(db, "UPDATE task_runs SET started_at=? WHERE id <> 7", t)


def _as_card(monkeypatch, db: Path, task: str = "t_card", run=8) -> None:
    monkeypatch.setenv("HERMES_KANBAN_TASK", task)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))


def _as_nobody(monkeypatch) -> None:
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_DB", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(k, raising=False)


def _owned_job(tmp_path, monkeypatch, db: Path, command: str = "echo hi", confirm=None, opened=True) -> Path:
    _as_card(monkeypatch, db, run=7)
    assert Path(ut.board_db_path("default")) == db and str(db).startswith(str(tmp_path))  # test-owned board only
    job = ut.create_job(command, "r", str(tmp_path), confirm=confirm)
    if opened:  # a window announced itself (no launch-grace abandonment); pid = this live process
        (job / "opened").write_text(f"{os.getpid()}\n")
    _as_nobody(monkeypatch)
    return job


def _resume(monkeypatch, job: Path, db: Path, task="t_card", run=8, session_id=None) -> dict:
    if task is None:
        _as_nobody(monkeypatch)
    else:
        _as_card(monkeypatch, db, task, run)
    try:
        return json.loads(ut.handle({"job_id": job.name, "wait_s": 1}, session_id=session_id))
    finally:
        _as_nobody(monkeypatch)


def _status(job: Path) -> str:
    return ut.read_status(job).get("status")


# ---------------------------------------------------------------- config

def test_grace_default_env_config_and_persisted_at_creation(tmp_path, monkeypatch):
    assert ut.reattach_grace_minutes() == 10.0
    monkeypatch.setenv("HERMES_USER_TERMINAL_REATTACH_GRACE_MINUTES", "3")
    assert ut.reattach_grace_minutes() == 3.0
    for bad in ("0", "-1", "nan", "inf", "x"):
        monkeypatch.setenv("HERMES_USER_TERMINAL_REATTACH_GRACE_MINUTES", bad)
        assert ut.reattach_grace_minutes() == 10.0
    monkeypatch.setenv("HERMES_USER_TERMINAL_REATTACH_GRACE_MINUTES", "3")
    CFG["reattach_grace_minutes"] = 4
    assert ut.reattach_grace_minutes() == 4.0  # config wins over the env fallback
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    assert json.loads((job / "meta.json").read_text())["reattach_grace_minutes"] == 4


def test_persisted_grace_is_used_by_the_detached_check(tmp_path, monkeypatch):
    CFG["reattach_grace_minutes"] = 1
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    CFG.clear()  # the watchdog may not read config: meta carries the value
    _block(db)
    _unblock(db, ago=90)
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and "1 min" in st["reason"], st


# ---------------------------------------------------------------- grace: unblock and new run

def test_unblock_keeps_job_for_grace_and_polls_never_reset_it(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db, ago=GRACE_S - 120)
    assert ut.watch_once(job, {}) is None
    # Polls by the chat session, by another card, and by the card's stale (ended) run attach nothing.
    for task, run, sid in ((None, None, "chat-1"), ("t_other", 30, None), ("t_card", 7, None)):
        res = _resume(monkeypatch, job, db, task, run, sid)
        assert res["status"] == "pending", res
    assert not (job / "attachment.json").exists()
    assert ut.watch_once(job, {}) is None
    _age_events(db, GRACE_S + 5)  # the same unblock, now older than the grace: polls did not extend it
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and "unblocked" in st["reason"] and "grace" in st["reason"], st


def test_new_run_keeps_job_for_grace_then_withdraws_unattached(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _end_run(db, 7, "continued")
    _claim(db, 8, ago=60)  # a new run with no unblock (e.g. kanban_continue)
    assert ut.watch_once(job, {}) is None
    _age_events(db, GRACE_S + 5)
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and "newer run (8)" in st["reason"], st


def test_later_run_that_ended_unattached_withdraws_at_once(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    assert ut.watch_once(job, {}) is None  # within the grace
    _end_run(db, 8, "crashed")  # run 8 ended without ever attaching
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and "run 8 ended" in st["reason"], st


def test_board_without_task_runs_falls_back_to_current_run_id(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, current_run_id INTEGER)")
    con.execute(_ddl("task_events"))
    con.execute("INSERT INTO tasks VALUES ('t_card', 'running', 9)")
    con.commit()
    con.close()
    created = time.time()
    owner = {"kind": "card", "task_id": "t_card", "run_id": 7, "db": str(db), "event_cursor": 0}
    assert ut._board_check(owner, created) is None  # newer run, grace counted from the request at the latest
    why = ut._board_check(owner, created - GRACE_S - 5)
    assert why and "newer run (9)" in why


def test_old_board_grace_starts_at_new_run_not_job_creation(tmp_path):
    db = _board(tmp_path)
    _sql(db, "DROP TABLE task_runs")
    _sql(db, "UPDATE tasks SET current_run_id=9 WHERE id='t_card'")
    _sql(db, "INSERT INTO task_events (task_id, run_id, kind, created_at) VALUES ('t_card', 9, 'claimed', ?)",
         int(time.time() - 30))
    owner = {"kind": "card", "task_id": "t_card", "run_id": 7, "db": str(db), "event_cursor": 0}
    assert ut._board_check(owner, time.time() - 1200) is None


def test_watchdog_and_attach_share_decision_lock(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    assert ut._attach_lock(job)
    try:
        monkeypatch.setattr(ut, "ATTACH_LOCK_WAIT_S", 0.05)
        # Expiry cannot be committed while an attachment decision owns the lock.
        _age_events(db, GRACE_S + 5)
        assert ut.watch_once(job, {}) is None
        assert not (job / "withdraw").exists()
    finally:
        ut._attach_unlock(job)
    assert ut.watch_once(job, {})["status"] == "withdrawn"


def test_run_ending_before_attachment_lock_is_not_attached(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    real = ut._attach_lock
    def ending_lock(job):
        _end_run(db, 8)
        return real(job)
    monkeypatch.setattr(ut, "_attach_lock", ending_lock)
    res = _resume(monkeypatch, job, db)
    assert res["attachment"]["attached"] is False
    assert not (job / "attachment.json").exists()


# ---------------------------------------------------------------- attaching

def test_resumed_run_attaches_and_keeps_job_past_grace(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    meta_before = (job / "meta.json").read_bytes()
    owner_before = (job / "owner.json").read_bytes()
    _block(db)
    _unblock(db, ago=120)
    _claim(db, 8, ago=100)
    res = _resume(monkeypatch, job, db, "t_card", 8)
    assert res["status"] == "pending" and res["attachment"]["attached"] is True and res["attachment"]["run_id"] == 8
    att = json.loads((job / "attachment.json").read_text())
    cursor = sqlite3.connect(db).execute("SELECT MAX(id) FROM task_events").fetchone()[0]
    assert att["run_id"] == 8 and att["event_cursor"] == cursor and att["task_id"] == "t_card"
    assert (job / "meta.json").read_bytes() == meta_before and (job / "owner.json").read_bytes() == owner_before
    _age_events(db, GRACE_S * 3)
    assert ut.watch_once(job, {}) is None  # acknowledged: no grace runs any more
    (job / "status.json").write_text(json.dumps({"status": "ran", "exit_code": 0}))
    out = _resume(monkeypatch, job, db, "t_card", 8)
    assert out["status"] == "ran" and out["exit_code"] == 0  # the result reaches the resumed run


def test_attachment_is_recorded_before_the_sweep_and_check(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db, ago=60)
    _claim(db, 8, ago=60)
    seen = []
    real = ut.sweep_jobs
    monkeypatch.setattr(ut, "sweep_jobs", lambda skip=None: (seen.append((job / "attachment.json").exists()),
                                                             real(skip=skip))[1])
    _resume(monkeypatch, job, db, "t_card", 8)
    assert seen == [True]


def test_attach_refused_for_other_card_board_or_stale_run(tmp_path, monkeypatch):
    db = _board(tmp_path)
    twin = _board(tmp_path, "twin.db")  # another board that also has a t_card on run 8
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db, ago=60)
    _claim(db, 8, ago=60)
    _sql(twin, "UPDATE tasks SET current_run_id=8 WHERE id='t_card'")
    _sql(twin, "INSERT INTO task_runs (id, task_id, status, started_at) VALUES (8, 't_card', 'running', ?)",
         int(time.time()))
    # run 9: a live t_card run that is not the card's current run (current is 8)
    _sql(db, "INSERT INTO task_runs (id, task_id, status, started_at) VALUES (9, 't_card', 'running', ?)",
         int(time.time()))
    for task, run, board in (("t_other", 30, db), ("t_other", 8, db), ("t_card", 8, twin), ("t_card", 7, db),
                             ("t_card", 9, db)):
        _as_card(monkeypatch, board, task, run)
        try:
            res = json.loads(ut.handle({"job_id": job.name, "wait_s": 1}))
        finally:
            _as_nobody(monkeypatch)
        assert res["attachment"]["attached"] is False and res["attachment"]["why"], res
        assert not (job / "attachment.json").exists()
    _age_events(db, GRACE_S + 5)
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn", st


def test_session_caller_cannot_attach(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    res = _resume(monkeypatch, job, db, None, None, "chat-1")
    assert res["attachment"]["attached"] is False and "not a Kanban worker" in res["attachment"]["why"], res
    assert not (job / "attachment.json").exists()


def test_attached_run_that_ended_cannot_reattach(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    assert _resume(monkeypatch, job, db, "t_card", 8)["attachment"]["attached"] is True
    before = (job / "attachment.json").read_bytes()
    # run 8 has ended (the row is closed) although tasks.current_run_id still names it (mid-transition)
    _sql(db, "UPDATE task_runs SET status='crashed', outcome='crashed', ended_at=? WHERE id=8", int(time.time()))
    res = _resume(monkeypatch, job, db, "t_card", 8)
    assert res["attachment"]["attached"] is False and "not a live run" in res["attachment"]["why"], res
    assert (job / "attachment.json").read_bytes() == before


def test_grace_counts_from_the_earliest_unacknowledged_event(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db, ago=GRACE_S + 30)  # unblocked long ago ...
    _claim(db, 8, ago=60)  # ... a run started only a minute ago and never attached: no fresh grace
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and "unblocked" in st["reason"], st


def test_attach_after_grace_elapsed_is_refused_and_withdrawn(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db, ago=GRACE_S + 30)
    _claim(db, 8, ago=GRACE_S + 30)
    res = _resume(monkeypatch, job, db, "t_card", 8)
    assert res["attachment"]["attached"] is False and res["status"] == "withdrawn", res


def test_attachment_sidecar_is_atomic_locked_and_never_moves_back(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    _as_card(monkeypatch, db, "t_card", 8)
    errs = []

    def go():
        try:
            assert ut.attach_job(job)["attached"] is True
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ths = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ths]
    [t.join(10) for t in ths]
    assert errs == []
    first = json.loads((job / "attachment.json").read_text())
    assert first["run_id"] == 8
    assert not list(job.glob(".attachment*")) and not (job / "attachment.lock").exists()
    # a stale lock (crashed holder) does not block the next attach
    (job / "attachment.lock").mkdir()
    (job / "attachment.lock" / "owner").write_text("999999 Mon Jan  1 00:00:00 2001\n")
    assert ut.attach_job(job)["attached"] is True
    # writes are tmp + os.replace (atomic), never in place
    replaced = []
    real_replace = ut.os.replace
    monkeypatch.setattr(ut.os, "replace", lambda a, b: (replaced.append((Path(a).name, Path(b).name)),
                                                        real_replace(a, b))[1])
    assert ut.attach_job(job)["attached"] is True
    assert [b for _, b in replaced] == ["attachment.json"] and replaced[0][0].startswith(".attachment.")
    monkeypatch.setattr(ut.os, "replace", real_replace)
    # a live holder of attachment.lock (another process) is waited for, never overridden
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (job / "attachment.lock").mkdir()
        (job / "attachment.lock" / "owner").write_text(f"{holder.pid} {ut._proc_start(holder.pid)}\n")
        monkeypatch.setattr(ut, "ATTACH_LOCK_WAIT_S", 0.3)
        before = (job / "attachment.json").read_bytes()
        res = ut.attach_job(job)
        assert res["attached"] is False and "in progress" in res["why"]
        assert (job / "attachment.json").read_bytes() == before
    finally:
        holder.kill()
        holder.wait()
    import shutil
    shutil.rmtree(job / "attachment.lock")
    # a lower run never replaces a higher one
    (job / "attachment.json").write_text(json.dumps({**first, "run_id": 99}))
    assert ut.attach_job(job)["attached"] is False
    assert json.loads((job / "attachment.json").read_text())["run_id"] == 99


def test_forged_attachment_for_another_card_is_ignored(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db, ago=GRACE_S + 5)
    (job / "attachment.json").write_text(json.dumps({"task_id": "t_other", "board_db": str(db), "run_id": 8,
                                                     "event_cursor": 10 ** 6}))
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn", st


# ---------------------------------------------------------------- unchanged immediate withdrawals

@pytest.mark.parametrize("status", ["done", "archived"])
def test_done_or_archived_withdraws_at_once_even_attached_in_grace(tmp_path, monkeypatch, status):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    assert _resume(monkeypatch, job, db, "t_card", 8)["attachment"]["attached"] is True
    _sql(db, "UPDATE tasks SET status=? WHERE id='t_card'", status)
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and f"is {status}" in st["reason"], st


def test_explicit_withdraw_and_max_age_still_apply_when_attached(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    other = _owned_job(tmp_path, monkeypatch, db)
    _block(db)
    _unblock(db)
    _claim(db, 8)
    for j in (job, other):
        assert _resume(monkeypatch, j, db, "t_card", 8)["attachment"]["attached"] is True
    res = json.loads(ut.handle({"action": "withdraw", "job_id": job.name, "reason": "not needed"}))
    assert res["status"] == "withdrawn" and res["reason"] == "not needed"
    meta = json.loads((other / "meta.json").read_text())
    meta["created"] = time.time() - meta["max_wait_minutes"] * 60 - 5
    (other / "meta.json").write_text(json.dumps(meta))
    st = ut.watch_once(other, {})
    assert st and st["status"] == "withdrawn" and st["reason"].startswith("expired after 120 min"), st


# ---------------------------------------------------------------- guidance

def test_guidance_says_withdraw_immediately_and_how_to_reattach(tmp_path, monkeypatch):
    d = ut.SCHEMA["description"]
    assert "withdraw it immediately" in d and "reattach_grace_minutes" in d and "job_id" in d
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db)
    res = _resume(monkeypatch, job, db, "t_card", 7)
    assert "withdraw it immediately" in res["note"]
    assert "re-attach" in res["note"] and "10 min" in res["note"]  # what a card that blocks on it must do
    plain = ut.create_job("true", "", str(tmp_path))
    assert "withdraw it immediately" in ut.wait_for(plain, 0)["note"]


# ---------------------------------------------------------------- real runner under a pty

def _spawn_runner(job: Path):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["SHELL"] = "/bin/bash"
        os.execv("/bin/bash", ["/bin/bash", str(job / "runner.sh"), str(job)])
    return pid, fd


def _pump(fd: int, buf: list, until=lambda: False, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and not until():
        if select.select([fd], [], [], 0.1)[0]:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return
            if not chunk:
                return
            buf.append(chunk)


def _screen(buf: list) -> bytes:
    return b"".join(buf)


def _finish(pid: int, fd: int, buf: list, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    answered = False
    while time.time() < deadline:
        if select.select([fd], [], [], 0.1)[0]:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf.append(chunk)
        if not answered and b"Press Enter" in _screen(buf):
            os.write(fd, b"\n")
            answered = True
    _reap(pid, fd)


def _reap(pid: int, fd: int, timeout: float = 10.0) -> None:
    """SIGKILL the runner and reap it while draining the pty: on macOS a session leader's exit waits for its
    terminal's unread output, so a bare waitpid can hang forever."""
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            break
        if done:
            break
        if select.select([fd], [], [], 0.05)[0]:
            try:
                os.read(fd, 65536)
            except OSError:
                pass
    try:
        os.close(fd)
    except OSError:
        pass


def _child_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("HERMES_KANBAN")}


def _kill_group(job: Path) -> None:
    ident = ut._cmd_ident(job)
    if ident:
        try:
            os.killpg(ident[0], signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


@needs_bsd_script
def test_pty_running_sleep30_blocked_unblocked_attached_then_ran(tmp_path, monkeypatch):
    db = _board(tmp_path)
    job = _owned_job(tmp_path, monkeypatch, db, "sleep 30; echo slept-ok", confirm=False, opened=False)
    buf: list = []
    pid, fd = _spawn_runner(job)
    wd = subprocess.Popen([sys.executable, str(Path(ut.__file__)), "--watchdog", str(job), "60", "0.2"],
                          env=_child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _pump(fd, buf, until=lambda: (job / "cmd.pgid").exists())
        assert _status(job) == "running"
        t0 = time.time()
        _block(db)  # run 7 blocks to wait on the user while its command runs
        _pump(fd, buf, until=lambda: time.time() - t0 >= 5, timeout=6)
        _unblock(db)  # at +5 s
        _claim(db, 8)
        _pump(fd, buf, timeout=1.5)  # the real watchdog polls every 0.2 s meanwhile
        assert _status(job) == "running" and not (job / "withdraw").exists()
        res = _resume(monkeypatch, job, db, "t_card", 8)
        assert res["status"] == "running" and res["attachment"]["attached"] is True, res
        _finish(pid, fd, buf, timeout=45)
        st = ut.wait_for(job, 0)
        assert st["status"] == "ran" and st["exit_code"] == 0 and "slept-ok" in st["output"], st
        assert b"Hermes withdrew" not in _screen(buf)
        assert wd.wait(20) == 0
    finally:
        wd.kill()
        _kill_group(job)
        _reap(pid, fd)


@needs_bsd_script
def test_pty_pending_prompt_unblock_attach_then_authorize_runs(tmp_path, monkeypatch):
    db = _board(tmp_path)
    marker = tmp_path / "RAN"
    job = _owned_job(tmp_path, monkeypatch, db, f"touch {marker}", confirm=True, opened=False)
    buf: list = []
    pid, fd = _spawn_runner(job)
    try:
        _pump(fd, buf, until=lambda: b"Run it?" in _screen(buf))
        assert b"Run it?" in _screen(buf)
        _block(db)
        _unblock(db)
        _claim(db, 8)
        res = _resume(monkeypatch, job, db, "t_card", 8)
        assert res["status"] == "pending" and res["attachment"]["attached"] is True, res
        os.write(fd, b"y\n")
        _finish(pid, fd, buf)
        st = ut.wait_for(job, 0)
        assert st["status"] == "ran" and st["exit_code"] == 0, st
        assert marker.exists() and b"Hermes withdrew" not in _screen(buf)
    finally:
        _kill_group(job)
        _reap(pid, fd)


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@needs_bsd_script
@pytest.mark.parametrize("trigger", ["later_run_ended", "grace_expired"])
def test_pty_unattached_end_or_grace_kills_group_and_window_says_so(tmp_path, monkeypatch, trigger):
    db = _board(tmp_path)
    child_f = tmp_path / "child"
    job = _owned_job(tmp_path, monkeypatch, db, f"sleep 300 & echo $! > {child_f}; sleep 300; touch {tmp_path}/after",
                     confirm=False, opened=False)
    buf: list = []
    pid, fd = _spawn_runner(job)
    child = None
    try:
        _pump(fd, buf, until=lambda: child_f.exists() and child_f.read_text().strip() != "", timeout=15)
        child = int(child_f.read_text())
        assert not _gone(child)
        _block(db)
        _unblock(db)
        _claim(db, 8)
        assert ut.watch_once(job, {}) is None  # within the grace: the command keeps running
        assert not _gone(child)
        if trigger == "later_run_ended":
            _end_run(db, 8, "crashed")
            want = "run 8 ended"
        else:
            _age_events(db, GRACE_S + 5)
            want = "grace"
        st = ut.watch_once(job, {})
        assert st and st["status"] == "withdrawn" and want in st["reason"], st
        deadline = time.time() + 5
        while time.time() < deadline and not _gone(child):
            time.sleep(0.05)
        assert _gone(child), "the command's process group survived"
        _pump(fd, buf, until=lambda: b"Nothing else to do here." in _screen(buf), timeout=10)
        assert f"Hermes withdrew this request: {st['reason']}".encode()[:60] in _screen(buf)
        assert not (tmp_path / "after").exists()
        assert ut.wait_for(job, 0)["status"] == "withdrawn"
    finally:
        if child:
            try:
                os.kill(child, 9)
            except ProcessLookupError:
                pass
        _kill_group(job)
        _reap(pid, fd)
