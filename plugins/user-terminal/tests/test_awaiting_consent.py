"""A pending macOS Automation consent prompt is a wait the job finishes by itself, not a final failure.

Defect agent-2469206a31: osascript timed out (20 s) while macOS asked "Hermes Python wants access to control
Terminal"; the job went straight to ``failed``, so after the user's click nothing re-opened the window and the
agent had to invent a second human step ("unblock the card" / "reply done"). The fix keeps such a job
``awaiting_consent`` and its watchdog retries the open until the window starts, max wait expires (then
``failed`` as before) or the job is withdrawn. osascript errors (non-zero exit) still fail at once.

No live Apple Events: osascript is stubbed through subprocess.run, and the watchdog runs in a thread.
"""
import importlib.util
import json
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ut_consent", Path(__file__).parents[1] / "__init__.py")
ut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ut)


def _simulate_window_ran(job: Path) -> None:
    """What a real runner.sh leaves once the window opened and the command finished (exit 0)."""
    (job / "opened").write_text("1\n")
    (job / "start.lock").mkdir()
    (job / "start.lock" / "owner").write_text("runner 1\n")
    assert ut._take_final(job, {"status": "ran", "exit_code": 0})


class FakeOsa:
    """osascript stub: the first ``timeouts`` calls hang past OSA_TIMEOUT (pending consent), later calls succeed
    (the user clicked OK) and the window 'runs' the job. ``error=True`` makes every call exit non-zero instead."""

    def __init__(self, timeouts: int, error: bool = False):
        self.timeouts, self.error, self.calls = timeouts, error, 0
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0

    def __call__(self, argv, **kw):
        if not argv or argv[0] != "osascript":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")  # ps etc. in osa_diagnosis
        with self.lock:
            self.calls += 1
            n = self.calls
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            time.sleep(0.02)
            if self.error:
                raise subprocess.CalledProcessError(1, argv, output="", stderr="execution error: Terminal got an "
                                                                               "error: boom (-1708)")
            if n <= self.timeouts:
                raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
            script = argv[2]
            job = Path(re.search(r"(/[^'\"\s\\]+)/runner\.sh", script).group(1))
            _simulate_window_ran(job)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        finally:
            with self.lock:
                self.inflight -= 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(jobs))
    for k in ut.worker_identity_keys() + ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(k, raising=False)  # a session-owned job: never the real board of the card running this
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ut, "terminal_order", lambda: ["terminal"])
    monkeypatch.setattr(ut, "_signed_opener_python", lambda: None)
    monkeypatch.setattr(ut, "_responsible_pid", lambda pid: None)
    monkeypatch.setattr(ut, "OPEN_GRACE_S", 0.05)
    monkeypatch.setattr(ut, "CONSENT_RETRY_S", 0.1, raising=False)
    monkeypatch.setattr(ut, "WATCH_INTERVAL_S", 0.05)
    threads = []

    def thread_watchdog(job):  # the real detached watchdog, in-process so the stubs apply
        t = threading.Thread(target=ut.watchdog, args=(job, 0.05, 20), daemon=True)
        t.start()
        threads.append(t)
    monkeypatch.setattr(ut, "spawn_watchdog", thread_watchdog)
    yield jobs
    for t in threads:
        t.join(25)


def _only_job(jobs: Path) -> Path:
    (job,) = [p for p in jobs.iterdir() if p.is_dir()]
    return job


def test_consent_after_first_timeouts_reaches_ran_with_one_tool_call(env, tmp_path, monkeypatch):
    osa = FakeOsa(timeouts=2)
    monkeypatch.setattr(ut.subprocess, "run", osa)
    out = json.loads(ut.handle({"command": "echo ok", "cwd": str(tmp_path), "wait_s": 10}))
    job = _only_job(env)
    assert out["status"] == "ran" and out["exit_code"] == 0 and out["ok"] is True, out
    assert osa.calls == 3 and osa.max_inflight == 1  # retried, one osascript at a time
    assert ut.read_status(job)["status"] == "ran"


def test_awaiting_consent_result_tells_agent_one_human_step(env, tmp_path, monkeypatch):
    osa = FakeOsa(timeouts=10 ** 6)
    monkeypatch.setattr(ut.subprocess, "run", osa)
    out = json.loads(ut.handle({"command": "echo ok", "cwd": str(tmp_path), "wait_s": 1}))
    job = _only_job(env)
    try:
        assert out["ok"] is True and out["status"] == "awaiting_consent", out
        assert out["job_id"] == job.name
        nxt = out["next"]
        assert "click OK" in nxt and "opens by itself" in nxt and "Do not ask them to unblock or reply" in nxt
        assert "osascript timed out" in out["consent_error"]
        assert not (job / "start.lock").exists()  # nothing holds the start gate: the window can still run it
        again = json.loads(ut.handle({"job_id": job.name, "wait_s": 1}))  # resume path reports the same
        assert again["status"] == "awaiting_consent" and "next" in again
    finally:
        ut.withdraw_job(job, "test over")


def test_consent_wait_expiry_fails_as_before(env, tmp_path, monkeypatch):
    osa = FakeOsa(timeouts=10 ** 6)
    monkeypatch.setattr(ut.subprocess, "run", osa)
    monkeypatch.setattr(ut, "max_wait_minutes", lambda: 0.01)  # 0.6 s
    ut.handle({"command": "echo ok", "cwd": str(tmp_path), "wait_s": 1})
    job = _only_job(env)
    deadline = time.time() + 10
    while ut.read_status(job).get("status") not in ut.FINAL and time.time() < deadline:
        time.sleep(0.05)
    st = ut.read_status(job)
    assert st["status"] == "failed" and "osascript timed out" in st["error"], st
    assert "awaiting macOS consent" in st["error"] and "diagnosis" in st
    assert "fail_job" in (job / "start.lock" / "owner").read_text()  # a late window refuses it
    calls = osa.calls
    time.sleep(0.4)
    assert osa.calls == calls  # retries stopped


def test_osascript_error_still_fails_at_once(env, tmp_path, monkeypatch):
    osa = FakeOsa(timeouts=0, error=True)
    monkeypatch.setattr(ut.subprocess, "run", osa)
    out = json.loads(ut.handle({"command": "echo ok", "cwd": str(tmp_path), "wait_s": 1}))
    assert out["status"] == "failed" and "-1708" in out["error"], out
    time.sleep(0.4)
    assert osa.calls == 1  # no retry for an error


def test_withdraw_stops_consent_retries(env, tmp_path, monkeypatch):
    osa = FakeOsa(timeouts=10 ** 6)
    monkeypatch.setattr(ut.subprocess, "run", osa)
    out = json.loads(ut.handle({"command": "echo ok", "cwd": str(tmp_path), "wait_s": 1}))
    assert out["status"] == "awaiting_consent", out
    w = json.loads(ut.handle({"action": "withdraw", "job_id": out["job_id"], "reason": "not needed"}))
    assert w["status"] == "withdrawn"
    time.sleep(0.3)
    calls = osa.calls
    time.sleep(0.5)
    assert osa.calls == calls


def test_relayed_start_reports_awaiting_consent_then_ran(env, tmp_path, monkeypatch):
    osa = FakeOsa(timeouts=1)
    monkeypatch.setattr(ut.subprocess, "run", osa)
    res = ut.start_relayed("echo ok", "why", str(tmp_path), {"gateway": "hub", "session": "s"})
    assert res["ok"] is True and res["status"] == "awaiting_consent" and "next" in res, res
    st = {}
    deadline = time.time() + 10
    while time.time() < deadline and st.get("status") != "ran":
        st = ut.relayed_status(res["job_id"], "hub", 1)
    assert st["status"] == "ran" and st["exit_code"] == 0, st


def test_signed_opener_timeout_is_consent_pending(tmp_path, monkeypatch):
    """The incident's path: the signed 'Hermes Python' opener (launchd) reports its osascript timeout."""
    def run(argv, **kwargs):
        if argv[1] == "bootstrap":
            (tmp_path / "opener-result.json").write_text(json.dumps(
                {"ok": False, "error": "terminal: osascript timed out after 20s. x", "consent_pending": True}))
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(ut.subprocess, "run", run)
    with pytest.raises(ut.OpenTimedOut):
        ut._open_via_launchd(tmp_path, Path("/trusted/python"), {})


def test_opener_main_records_consent_pending(tmp_path, monkeypatch):
    signed = Path("/trusted/python")
    monkeypatch.setattr(ut, "_signed_opener_python", lambda: signed)
    monkeypatch.setattr(ut, "osa_diagnosis", lambda: {"responsible_process": {"path": str(signed)}})
    monkeypatch.setattr(ut.Path, "resolve", lambda self, *a, **k: self)

    def direct(job):
        raise ut.OpenTimedOut("terminal: osascript timed out after 20s.")
    monkeypatch.setattr(ut, "_open_window_direct", direct)
    assert ut.opener_main(tmp_path) == 1
    assert json.loads((tmp_path / "opener-result.json").read_text())["consent_pending"] is True
