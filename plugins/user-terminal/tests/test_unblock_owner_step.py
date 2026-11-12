"""kanban_unblock of an owner-terminal (user_terminal) ask from the owner chat must say the step still has to run.

Self-heal kanban-e23aa32ffd: a chat read "approved, unblock" as "call kanban_unblock", reported "ready and queued",
and the host_only owner-terminal step never ran (no worker can run it). These tests drive the plugin's
transform_tool_result hook against a minimal board (task_events + task_comments) and a jobs dir; no core needed.
"""
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("user_terminal_owner_step", PLUGIN / "owner_step.py")
os_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(os_mod)

TASK = "t_0123abcd"
OWNER_REQUEST = {
    "interaction": "user_terminal",
    "action": f"From your own Hermes chat open the owner-terminal flow for task {TASK}. "
              "Unblock = resume verification; archive = abandon reconciliation.",
    "why_human": "host_only boundary: only the owner's Desktop chat may open a terminal",
    "scope": "host mbp, cwd ~/code/x, command `./reconcile.sh --apply`",
    "evidence": "run_in_user_terminal refused: Kanban caller, owner chats only.",
}
APPROVAL_REQUEST = {"interaction": "approval", "action": "Approve the merge. Unblock = merge; archive = drop.",
                    "why_human": "Only David approves merges", "scope": "PR 12", "evidence": "CI green"}
OK_RESULT = json.dumps({"ok": True, "task_id": TASK, "status": "ready", "priority_raised": {"from": 0, "to": 210}})
BLOCK_AT = 1_794_000_000


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_MANAGED_WORKER"):
        monkeypatch.delenv(key, raising=False)
    db = tmp_path / "kanban.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, "
                    "kind TEXT, payload TEXT, created_at INTEGER)")
        con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, "
                    "body TEXT, created_at INTEGER)")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    return db, jobs


def block(db, request, when=BLOCK_AT, reason="waiting on the owner"):
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
                    (TASK, 7, "blocked", json.dumps({"reason": reason, "kind": "needs_input",
                                                     **({"human_request": request} if request else {})}), when))
        con.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
                    (TASK, None, "unblocked", "{}", when + 60))


def job(jobs, *, status="ran", ended=BLOCK_AT + 30, owner=None, reason=f"owner-terminal flow for {TASK}",
        name="20261112-140000-abc123"):
    d = jobs / name
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"owner": owner or {"kind": "session", "session_id": "s1"}}))
    (d / "reason.txt").write_text(reason + "\n")
    (d / "cmd.sh").write_text("./reconcile.sh --apply\n")
    (d / "status.json").write_text(json.dumps({"status": status, "exit_code": 0}))
    import os
    os.utime(d / "status.json", (ended, ended))
    return d


def run(db, jobs, result=OK_RESULT, tool="kanban_unblock", args=None):
    return os_mod.transform_unblock_result(tool, args if args is not None else {"task_id": TASK}, result,
                                           db_path=lambda board: str(db), jobs_root=lambda: jobs)


def test_user_terminal_block_without_job_adds_next_step(env):
    db, jobs = env
    block(db, OWNER_REQUEST)
    out = run(db, jobs)
    assert isinstance(out, str)
    res = json.loads(out)
    assert res["status"] == "ready" and res["priority_raised"] == {"from": 0, "to": 210}  # nothing dropped
    step = res["next_step"]
    assert "run_in_user_terminal" in step and "did not run" in step and "queued" in step
    assert res["owner_step"]["interaction"] == "user_terminal"
    assert "./reconcile.sh --apply" in res["owner_step"]["scope"]


def test_host_only_reason_without_interaction_still_counts(env):
    db, jobs = env
    req = {**OWNER_REQUEST, "interaction": "reply"}
    block(db, req)
    assert "next_step" in json.loads(run(db, jobs))


def test_job_ran_after_block_suppresses_next_step(env):
    db, jobs = env
    block(db, OWNER_REQUEST)
    job(jobs)
    assert run(db, jobs) is None


def test_job_owned_by_card_ran_after_block_suppresses(env):
    db, jobs = env
    block(db, OWNER_REQUEST)
    job(jobs, owner={"kind": "card", "task_id": TASK, "board": "default"}, reason="unrelated words")
    assert run(db, jobs) is None


@pytest.mark.parametrize("case", ["before_block", "declined", "other_task", "running"])
def test_job_that_does_not_prove_the_step_keeps_next_step(env, case):
    db, jobs = env
    block(db, OWNER_REQUEST)
    if case == "before_block":
        job(jobs, ended=BLOCK_AT - 30)
    elif case == "declined":
        job(jobs, status="declined")
    elif case == "other_task":
        job(jobs, reason="owner-terminal flow for t_ffffeeee")
    else:
        job(jobs, status="running")
    assert "next_step" in json.loads(run(db, jobs))


def test_comment_with_job_id_after_block_suppresses(env):
    """On a screenless gateway the job lives on the Mac; the chat comments its job id on the card."""
    db, jobs = env
    block(db, OWNER_REQUEST)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                    (TASK, "chat", "Ran it: job 20261112-140501-a1b2c3, exit 0, log ~/x.log", BLOCK_AT + 40))
    assert run(db, jobs) is None


def test_approval_comment_without_job_id_keeps_next_step(env):
    """The incident: the chat commented "David approved" and unblocked; that is not the step running."""
    db, jobs = env
    block(db, OWNER_REQUEST)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                    (TASK, "chat", "David approved. Unblocking.", BLOCK_AT + 40))
    assert "next_step" in json.loads(run(db, jobs))


def test_non_user_terminal_block_is_unchanged(env):
    db, jobs = env
    block(db, APPROVAL_REQUEST)
    assert run(db, jobs) is None


def test_block_without_human_request_is_unchanged(env):
    db, jobs = env
    block(db, None)
    assert run(db, jobs) is None


@pytest.mark.parametrize("key", ["HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_MANAGED_WORKER"])
def test_worker_context_is_unchanged(env, monkeypatch, key):
    db, jobs = env
    block(db, OWNER_REQUEST)
    monkeypatch.setenv(key, "t_other")
    assert run(db, jobs) is None


def test_db_read_error_returns_result_unchanged(env, tmp_path, caplog):
    db, jobs = env
    block(db, OWNER_REQUEST)
    broken = tmp_path / "not-a-db"
    broken.write_text("garbage, not sqlite")
    with caplog.at_level("WARNING"):
        out = os_mod.transform_unblock_result("kanban_unblock", {"task_id": TASK}, OK_RESULT,
                                              db_path=lambda board: str(broken), jobs_root=lambda: jobs)
    assert out is None
    assert any("owner_step" in r.getMessage() or "owner-step" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("result", [json.dumps({"error": "could not unblock"}), "not json",
                                    json.dumps({"ok": True, "task_id": TASK, "status": "blocked"})])
def test_failed_or_odd_unblock_result_is_unchanged(env, result):
    db, jobs = env
    block(db, OWNER_REQUEST)
    assert run(db, jobs, result=result) is None


def test_other_tools_are_unchanged(env):
    db, jobs = env
    block(db, OWNER_REQUEST)
    assert run(db, jobs, tool="kanban_comment") is None


def test_board_argument_is_passed_through(env):
    db, jobs = env
    block(db, OWNER_REQUEST)
    seen = []
    out = os_mod.transform_unblock_result("kanban_unblock", {"task_id": TASK, "board": "ops"}, OK_RESULT,
                                          db_path=lambda board: seen.append(board) or str(db),
                                          jobs_root=lambda: jobs)
    assert seen == ["ops"] and "next_step" in json.loads(out)


def _plugin():
    spec2 = importlib.util.spec_from_file_location("user_terminal_reg", PLUGIN / "__init__.py")
    ut = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(ut)
    return ut


def test_plugin_hook_reads_the_board_and_job_store(env, monkeypatch):
    """The registered hook resolves the board db and the plugin's jobs dir itself."""
    db, jobs = env
    ut = _plugin()
    monkeypatch.setattr(ut, "board_db_path", lambda board: str(db))
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(jobs))
    block(db, OWNER_REQUEST)
    assert "next_step" in json.loads(ut.transform_tool_result(tool_name="kanban_unblock", args={"task_id": TASK},
                                                             result=OK_RESULT, session_id="s1", duration_ms=3))
    job(jobs)
    assert ut.transform_tool_result(tool_name="kanban_unblock", args={"task_id": TASK}, result=OK_RESULT) is None
    assert ut.transform_tool_result(tool_name="terminal", args={}, result="x") is None


def test_plugin_registers_the_hook_on_both_paths(monkeypatch):
    ut = _plugin()
    for screen in (True, False):
        hooks = []

        class Ctx:
            def register_tool(self, **kw):
                pass

            def register_hook(self, name, fn):
                hooks.append(name)

        monkeypatch.setattr(ut, "has_screen", lambda: screen)
        monkeypatch.setattr(ut, "start_reconciler", lambda ctx: None)
        ut.register(Ctx())
        assert "transform_tool_result" in hooks, screen
