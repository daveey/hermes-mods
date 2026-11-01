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
        # The window opened at 14:00 and run 7 blocked on it at 14:05 (both PDT).
        meta = json.loads((job / "meta.json").read_text())
        meta["created"] = at(14, 0)
        (job / "meta.json").write_text(json.dumps(meta))
        add_block(path, at(14, 5), 7, {"human_request": REQUEST, "kind": "human"})
        yield path, job, db


def at(hour, minute, winter=False):
    return datetime(2026, 1 if winter else 10, 15, hour, minute,
                    tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


def add_block(path, when, run_id, payload):
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) "
                    "VALUES ('t_card', ?, 'blocked', ?, ?)", (run_id, json.dumps(payload), int(when)))


def snapshot(path):
    with sqlite3.connect(path) as con:
        return {table: con.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("tasks", "task_comments", "task_events", "task_runs")}


def final(job, status="withdrawn", reason="expired after 120 min; ask again if still needed", winter=False):
    st = {"status": status, "reason": reason, "job_id": job.name}
    assert ut._take_final(job, st)
    stamp = at(14, 37, winter)
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
        assert request == {**REQUEST, "action": "window closed 14:37 PDT (it was for: fixture). Unblock = agent reopens window; archive = drop request."}
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
    meta = json.loads((job / "meta.json").read_text())
    meta["created"] = at(14, 0, winter=True)
    (job / "meta.json").write_text(json.dumps(meta))
    add_block(path, at(14, 5, winter=True), 7, {"human_request": REQUEST})
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


def production_python(core: Path) -> str | None:
    """The interpreter watchdog.sh execs: the plugin writes the gateway's own sys.executable
    (ut.watchdog_python), and the gateway runs the launcher's runtime Python, re-exec'd into its
    signed twin when one matches (hermes_cli._launchers.signed_python_app). Resolve it the same way,
    never as this test runner's interpreter. HERMES_USER_TERMINAL_WATCHDOG_PYTHON overrides."""
    override = os.environ.get("HERMES_USER_TERMINAL_WATCHDOG_PYTHON")
    if override:
        return override if Path(override).is_file() else None
    launcher = core / ".hermes" / "bin" / "hermes"
    if not launcher.is_file():
        return None
    out = subprocess.run([str(launcher), "--print-runtime-command"], capture_output=True, text=True, timeout=120)
    try:
        store = Path(json.loads(out.stdout)[0])
    except (ValueError, IndexError, TypeError):
        return None
    try:
        from hermes_cli._launchers import signed_python_app
        exe = signed_python_app(store) or store
    except ImportError:
        exe = store
    return str(exe) if exe.is_file() else None


def test_standalone_watchdog_bootstraps_real_core(board):
    path, job, db = board
    final(job)
    core = Path(db.__file__).resolve().parent.parent
    python = production_python(core)
    if python is None:
        pytest.skip(f"no production watchdog interpreter: no runtime launcher under {core}/.hermes/bin "
                    "and HERMES_USER_TERMINAL_WATCHDOG_PYTHON unset")
    env = ut.launch_env()
    env["HERMES_USER_TERMINAL_CORE_ROOT"] = str(core)
    env.pop("PYTHONPATH", None)
    # Run the exact standalone entry point on the interpreter watchdog.sh execs, with no inherited
    # sys.path/modules (the runner's own Python differed from production: 3.11 vs 3.14).
    result = subprocess.run([python, str(SOURCE), "--watchdog", str(job)], env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert len(snapshot(path)["task_comments"]) == 1, result.stderr


UNRELATED = {**REQUEST, "action": "Pick A or B for the schema. Unblock = A; archive = B."}


@pytest.mark.parametrize("later_run", [7, 8, None])
def test_old_job_never_rewrites_a_later_unrelated_ask(board, later_run):
    """The job failed while the card ran; the card later blocked on something else (same run, a later run,
    or another actor's metadata-only edit). The old job's window is not what the card waits on."""
    path, job, _ = board
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM task_events")
        con.execute("UPDATE tasks SET status='running', current_run_id=7, human_request=NULL")
    final(job, "failed", "could not open a terminal window")
    ut.sweep_jobs()
    assert not snapshot(path)["task_comments"]
    with sqlite3.connect(path) as con:
        con.execute("UPDATE tasks SET status='blocked', current_run_id=NULL, human_request=?",
                    (json.dumps(UNRELATED),))
    payload = {"human_request": UNRELATED, "kind": "human"}
    if later_run is None:
        payload.update(actor="agent", metadata_only=True)
    add_block(path, at(14, 50), later_run, payload)
    before = snapshot(path)
    ut.sweep_jobs()
    ut.sweep_jobs()
    assert snapshot(path) == before


@pytest.mark.parametrize("case", ["no_block_event", "block_before_window", "other_run_in_window"])
def test_ask_not_set_during_this_window_has_zero_writes(board, case):
    path, job, _ = board
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM task_events")
    if case == "block_before_window":
        add_block(path, at(13, 0), 7, {"human_request": REQUEST})
    elif case == "other_run_in_window":
        add_block(path, at(14, 5), 9, {"human_request": REQUEST})
    final(job)
    before = snapshot(path)
    ut.sweep_jobs()
    assert snapshot(path) == before


def test_own_amendment_does_not_hide_the_binding_block(board):
    """A later sweep reads past this plugin's own metadata-only event and still dedups by marker."""
    path, job, _ = board
    final(job)
    ut.sweep_jobs()
    with sqlite3.connect(path) as con:
        kinds = [json.loads(p).get("actor") for (p,) in con.execute(
            "SELECT payload FROM task_events WHERE kind='blocked' ORDER BY id")]
    assert kinds[-1] == "user-terminal"
    before = snapshot(path)
    ut.sweep_jobs()
    assert snapshot(path) == before


def strict_classifier(db, classify, before=None, failures=0):
    """A core whose classifier opens a plain write_txn (no nested opt-in), as cores before the
    composable classifier did and as a stale editable core snapshot still does."""
    state = {"calls": 0}

    def strict(conn, task, **kw):
        state["calls"] += 1
        if getattr(conn, "in_transaction", False):
            raise RuntimeError("write_txn: already inside a transaction. Nested composition must opt in "
                               "explicitly with write_txn(conn, allow_nested=True)")
        if state["calls"] <= failures:
            raise sqlite3.OperationalError(f"injected classify failure {state['calls']}")
        if before:
            before(conn, task)
        return classify(conn, task, **kw)
    return strict, state


def amendments(path):
    with sqlite3.connect(path) as con:
        return [p for (p,) in con.execute("SELECT payload FROM task_events WHERE kind='blocked'")
                if json.loads(p).get("actor") == "user-terminal"]


def test_classifier_without_nested_opt_in_still_reconciles(board, monkeypatch, caplog):
    """The closed-ask write never calls core's classifier inside a plugin transaction."""
    path, job, db = board
    st = final(job)
    real_db, classify = ut._kanban_api()
    strict, _ = strict_classifier(real_db, classify)
    monkeypatch.setattr(ut, "_kanban_api", lambda: (real_db, strict))
    assert ut.watch_once(job, {}) == st
    assert "reconciliation failed" not in caplog.text, caplog.text
    with sqlite3.connect(path) as con:
        request = json.loads(con.execute("SELECT human_request FROM tasks").fetchone()[0])
    assert request == {**REQUEST, "action": "window closed 14:37 PDT (it was for: fixture). "
                       "Unblock = agent reopens window; archive = drop request."}
    assert len(snapshot(path)["task_comments"]) == 1 and len(amendments(path)) == 1


def test_watchdog_retries_a_failed_reconcile_within_its_window(board, monkeypatch, caplog):
    """classify fails once, then succeeds on the watchdog's own retry: one comment, one amendment."""
    path, job, db = board
    caplog.set_level("INFO", logger="user_terminal")
    final(job)
    real_db, classify = ut._kanban_api()
    strict, state = strict_classifier(real_db, classify, failures=1)
    monkeypatch.setattr(ut, "_kanban_api", lambda: (real_db, strict))
    monkeypatch.setattr(ut, "RECONCILE_RETRY_INTERVAL_S", 0.05)
    monkeypatch.setattr(ut, "RECONCILE_RETRY_WINDOW_S", 5.0)
    ut.watchdog(job, interval=0.05, max_s=2)
    assert state["calls"] == 2
    assert len(snapshot(path)["task_comments"]) == 1
    assert len(amendments(path)) == 1
    assert caplog.text.count("reconciliation failed") == 1
    assert "injected classify failure 1" in caplog.text
    assert "succeeded on retry" in caplog.text


def test_watchdog_retry_gives_up_after_its_window(board, monkeypatch, caplog):
    path, job, db = board
    final(job)
    real_db, classify = ut._kanban_api()
    strict, state = strict_classifier(real_db, classify, failures=10 ** 6)
    monkeypatch.setattr(ut, "_kanban_api", lambda: (real_db, strict))
    monkeypatch.setattr(ut, "RECONCILE_RETRY_INTERVAL_S", 0.05)
    monkeypatch.setattr(ut, "RECONCILE_RETRY_WINDOW_S", 0.3)
    ut.watchdog(job, interval=0.05, max_s=2)
    assert caplog.text.count("reconciliation failed") == state["calls"] >= 3
    assert "leaving it to the next sweep" in caplog.text
    assert not amendments(path)


def test_block_landing_between_check_and_classify_keeps_the_newer_ask(board, monkeypatch):
    """Eligibility is re-checked just before core's own classify transaction; a block that lands in
    between is detected afterwards and its newer ask is put back."""
    path, job, db = board
    final(job)
    real_db, classify = ut._kanban_api()

    def interleave(conn, task):
        assert classify(conn, task, human_request=UNRELATED, actor="agent", reason="a newer ask")
    strict, _ = strict_classifier(real_db, classify, before=interleave)
    monkeypatch.setattr(ut, "_kanban_api", lambda: (real_db, strict))
    ut.watch_once(job, {})
    with sqlite3.connect(path) as con:
        request = json.loads(con.execute("SELECT human_request FROM tasks").fetchone()[0])
    assert request == UNRELATED
    before = snapshot(path)
    ut.sweep_jobs()
    assert snapshot(path) == before


def test_watchdog_waits_out_a_busy_board_the_sweep_does_not(board, monkeypatch):
    """The detached watchdog waits up to WATCHDOG_BUSY_TIMEOUT_S for the board's write lock;
    the tool handler's sweep keeps its 2 s wait."""
    path, job, db = board
    final(job)
    from hermes_cli import kanban_lockguard
    seen = []
    real = kanban_lockguard.connect

    def spy(*a, **k):
        seen.append(k.get("timeout"))
        return real(*a, **k)
    monkeypatch.setattr(kanban_lockguard, "connect", spy)
    ut.sweep_jobs()
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM task_comments")
        con.execute("UPDATE tasks SET human_request=?", (json.dumps(REQUEST),))
    ut.watchdog(job, interval=0.05, max_s=1)
    # (core helpers may open their own short-timeout connections; only the plugin's board opens count)
    assert seen[0] == 2.0 and ut.WATCHDOG_BUSY_TIMEOUT_S == 30.0 and 30.0 in seen
    assert len(snapshot(path)["task_comments"]) == 1
