"""Tests for user-terminal: the runner script under a real pty (no window opened).

Run: uv run --with pytest python -m pytest -q -p no:cacheprovider tests_user_terminal.py
"""

import importlib.util
import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("user_terminal", Path(__file__).with_name("__init__.py"))
ut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ut)


def _drive(job: Path, answers: list[str], timeout: float = 20) -> None:
    """Run runner.sh in a pty, typing each answer once its prompt shows up."""
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["SHELL"] = "/bin/bash"
        os.execv("/bin/bash", ["/bin/bash", str(job / "runner.sh"), str(job)])
    buf, deadline, pending = b"", time.time() + timeout, list(answers)
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        if pending and (b"Run it?" in buf if len(pending) == len(answers) else b"Press Enter" in buf):
            os.write(fd, pending.pop(0).encode())
        if not pending:
            pass
    os.waitpid(pid, 0)


def test_confirm_runs_and_captures(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hello-from-job; exit 3", "testing", str(tmp_path))
    _drive(job, ["y\n", "\n"])
    res = ut.wait_for(job, 1)
    assert res["status"] == "ran" and res["exit_code"] == 3
    assert "hello-from-job" in res["output"]


def test_decline(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("touch SHOULD_NOT_EXIST", "", str(tmp_path))
    _drive(job, ["n\n"])
    res = ut.wait_for(job, 1)
    assert res["status"] == "declined"
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


def test_pending_then_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("true", "", str(tmp_path))
    res = ut.wait_for(job, 1)
    assert res["status"] == "pending" and res["job_id"] == job.name
    (job / "status.json").write_text(json.dumps({"status": "ran", "exit_code": 0}))
    out = json.loads(ut.handle({"job_id": job.name, "wait_s": 1}))
    assert out["status"] == "ran"


def test_clean_output_strips_ansi():
    assert ut.clean_output("\x1b[31mred\x1b[0m\r\nprogress 10%\rprogress 100%\n") == "red\nprogress 100%"


def test_terminal_order_prefers_iterm(monkeypatch):
    monkeypatch.delenv("HERMES_USER_TERMINAL", raising=False)
    monkeypatch.setattr(ut, "_has_iterm", lambda: True)
    assert ut.terminal_order()[:2] == ["iterm", "terminal"]
    monkeypatch.setattr(ut, "_has_iterm", lambda: False)
    assert ut.terminal_order()[0] == "terminal"
    monkeypatch.setenv("HERMES_USER_TERMINAL", "cmux")
    if ut.CMUX_BIN.exists():
        assert ut.terminal_order()[0] == "cmux"
