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

import pytest

_spec = importlib.util.spec_from_file_location("user_terminal", Path(__file__).with_name("__init__.py"))
ut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ut)
_REAL_SPAWN = ut.spawn_watchdog


def _drive(job: Path, answers: list[str], timeout: float = 20) -> bytes:
    """Run runner.sh in a pty, typing each answer once its prompt shows up. With a confirm file the first
    answer goes to ``Run it?``; without one every answer goes to ``Press Enter``."""
    asks = (job / "confirm").exists()
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
        if pending and (b"Run it?" in buf if asks and len(pending) == len(answers) else b"Press Enter" in buf):
            os.write(fd, pending.pop(0).encode())
    os.waitpid(pid, 0)
    return buf


@pytest.fixture(autouse=True)
def _confirm_env(monkeypatch):
    monkeypatch.delenv("HERMES_USER_TERMINAL_CONFIRM", raising=False)
    # No detached watchdogs from unit tests (they would judge fake runner pids); _watch() runs the real one.
    monkeypatch.setattr(ut, "spawn_watchdog", lambda job: None, raising=False)
    # Runner-started watchdogs are covered by test_runner_starts_one_watchdog_before_the_command.
    monkeypatch.setenv("HERMES_USER_TERMINAL_RUNNER_WATCHDOG", "0")
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_BOARD",
              "HERMES_KANBAN_DB", "HERMES_KANBAN_HOME", "HERMES_USER_TERMINAL_MAX_WAIT_MINUTES"):
        monkeypatch.delenv(k, raising=False)  # this suite may itself run inside a Kanban worker


# The job runner wraps the command in BSD/macOS `script -q FILE cmd...`; util-linux `script` takes
# `-c cmd FILE`, so tests that execute the runner script itself can only pass on macOS.
needs_bsd_script = pytest.mark.skipif(sys.platform != "darwin",
                                      reason="job runner uses macOS `script -q FILE cmd` syntax (util-linux differs)")


@needs_bsd_script
def test_default_runs_without_asking(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    monkeypatch.setattr(ut, "confirm_mode", lambda: "off")
    job = ut.create_job("echo hello-from-job; exit 3", "testing", str(tmp_path))
    assert not (job / "confirm").exists()
    screen = _drive(job, ["\n"])
    assert b"Run it?" not in screen and b"Ctrl-C cancels" in screen
    res = ut.wait_for(job, 1)
    assert res["status"] == "ran" and res["exit_code"] == 3
    assert "hello-from-job" in res["output"]


@pytest.mark.parametrize("env,mode", [("", "off"), ("off", "off"), ("remote", "remote"), ("all", "all"),
                                      ("1", "all"), ("bogus", "off")])
def test_confirm_mode_env(monkeypatch, env, mode):
    monkeypatch.setenv("HERMES_USER_TERMINAL_CONFIRM", env)
    monkeypatch.setitem(sys.modules, "hermes_cli", None)  # no config -> env only
    assert ut.confirm_mode() == mode


@pytest.mark.parametrize("mode,origin,asks", [("off", None, False), ("off", {"gateway": "g"}, False),
                                              ("remote", None, False), ("remote", {"gateway": "g"}, True),
                                              ("all", None, True)])
def test_confirm_file_follows_mode(tmp_path, monkeypatch, mode, origin, asks):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    monkeypatch.setattr(ut, "confirm_mode", lambda: mode)
    assert (ut.create_job("true", "", str(tmp_path), origin) / "confirm").exists() is asks


@needs_bsd_script
def test_confirm_runs_and_captures(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hello-from-job; exit 3", "testing", str(tmp_path), confirm=True)
    _drive(job, ["y\n", "\n"])
    res = ut.wait_for(job, 1)
    assert res["status"] == "ran" and res["exit_code"] == 3
    assert "hello-from-job" in res["output"]


def test_decline(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("touch SHOULD_NOT_EXIST", "", str(tmp_path), confirm=True)
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


def test_sudo_failure_hint_replaces_core_tip():
    out = "x\nsudo: a password is required\n\n\n💡 Tip: To enable sudo over messaging, add SUDO_PASSWORD to ~/.hermes/.env on the agent machine."
    new = ut.transform_terminal_output(command="sudo ls", output=out, returncode=1)
    assert "SUDO_PASSWORD to" not in new and "run_in_user_terminal" in new
    assert new.startswith("x\nsudo: a password is required")
    assert ut.transform_terminal_output(command="sudo ls", output=new) is None  # idempotent
    assert ut.transform_terminal_output(command="ls", output="ok") is None


# ---------------------------------------------------------------- remote mode (relay to the owner's Mac)

import threading  # noqa: E402

_rspec = importlib.util.spec_from_file_location(
    "sm_relay_files", Path(__file__).resolve().parent.parent / "session-mentions" / "relay_files.py")
rf = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(rf)

ORIGIN = {"gateway": "nous-1", "profile": "alpha", "session_id": "20261021_000505_e1e78133", "title": "Token revoke"}


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A screenless gateway home (tmp/gw) and the Mac's job dir (tmp/mac); session identity from env only."""
    gw, mac = tmp_path / "gw", tmp_path / "mac"
    gw.mkdir()
    mac.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(gw))
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(mac))
    for k in ("HERMES_PLATFORM", "HERMES_SESSION_PLATFORM", "HERMES_SESSION_SOURCE", "HERMES_USER_TERMINAL_DEVICE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ut, "_home", lambda: gw)
    monkeypatch.setattr(ut, "configured_device", lambda: ut._slug(os.environ.get("HERMES_USER_TERMINAL_DEVICE", "")))
    return ut._root(), mac


def _beat(base: Path, device: str, aliases=()) -> None:
    rf.register_courier(base, device, list(aliases) or [device], ["nous-1"], home=False)


def _outbox(base: Path) -> list:
    return list((base / "outbox").glob("*.json")) if (base / "outbox").is_dir() else []


# Invariant 1 (confirm=remote|all): nothing relayed runs without the owner typing y in the Mac window.
@pytest.mark.parametrize("answer", ["\n", "n\n", "no\n"])
def test_relayed_job_runs_only_after_y(remote, monkeypatch, tmp_path, answer):
    monkeypatch.setattr(ut, "open_window", lambda job: "fake")
    monkeypatch.setattr(ut, "confirm_mode", lambda: "remote")
    marker = tmp_path / "RAN"
    res = ut.start_relayed(f"touch {marker}", "why", str(tmp_path), ORIGIN)
    assert res["ok"] and res["status"] == "waiting_for_user"
    job = ut.jobs_dir() / res["job_id"]
    time.sleep(0.3)
    assert not marker.exists() and ut.read_status(job)["status"] == "waiting_for_user"
    _drive(job, [answer])
    assert ut.relayed_status(res["job_id"], "nous-1", 1)["status"] == "declined"
    assert not marker.exists()


def test_relayed_start_requires_origin_and_never_runs_on_open(remote, monkeypatch, tmp_path):
    monkeypatch.setattr(ut, "open_window", lambda job: "fake")
    assert not ut.start_relayed("true", "", "", None)["ok"]
    assert not ut.start_relayed("true", "", "", {"profile": "x"})["ok"]
    assert "cwd does not exist" in ut.start_relayed("true", "", "/nope/nope", ORIGIN)["error"]


# Invariant 2: only the owner's own sessions may call it; messaging/unknown surfaces are refused.
@pytest.mark.parametrize("env,ok", [
    ({"HERMES_SESSION_PLATFORM": "slack"}, False),
    ({"HERMES_SESSION_SOURCE": "desktop", "HERMES_SESSION_PLATFORM": "slack"}, False),
    ({"HERMES_SESSION_SOURCE": "cron"}, False),
    ({}, False),  # nothing known about the caller: default-deny
    ({"HERMES_SESSION_SOURCE": "desktop"}, True),
    ({"HERMES_SESSION_SOURCE": "tui"}, True),
    ({"HERMES_SESSION_PLATFORM": "api_server"}, True),
    ({"HERMES_SESSION_SOURCE": "cli"}, True),
])
def test_owner_gate(remote, monkeypatch, env, ok):
    base, _ = remote
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out = json.loads(ut.handle_remote({"command": "echo ok", "wait_s": 1}))
    assert out["ok"] is False
    if ok:
        assert "refused" not in out["error"]  # passed the gate; failed only on "no Desktop connected"
    else:
        assert "refused" in out["error"] and "owner" in out["error"] and "paste" in out["error"]
        assert not _outbox(base)  # nothing was sent toward the Mac


def test_owner_gate_reads_session_row_source(remote, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    monkeypatch.setattr(ut, "caller_surfaces", lambda sid: ["desktop", "slack"] if sid == "s-slack" else ["desktop"])
    assert ut.owner_refusal("s-slack") and ut.owner_refusal("s-desk") is None


def test_owner_dm_refusal_routes_to_ops_not_paste_it(remote, monkeypatch):
    import types
    monkeypatch.setitem(sys.modules, "hermes_cli.config", types.SimpleNamespace(
        load_config=lambda: {"owner": {"slack_user_ids": ["U0OWNER"]}}))
    base = {"HERMES_SESSION_PLATFORM": "slack", "HERMES_SESSION_CHAT_TYPE": "dm", "HERMES_SESSION_CHAT_ID": "D1",
            "HERMES_SESSION_USER_ID": "U0OWNER"}
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    out = json.loads(ut.handle_remote({"command": "echo ok", "wait_s": 1}))
    assert out["ok"] is False and "mac_ops_handoff" in out["error"] and "Never hand over commands" in out["error"]
    assert not _outbox(remote[0])  # still refused: nothing sent toward the Mac
    # a channel, or someone else's DM, keeps the old text (no handoff offer)
    for over in ({"HERMES_SESSION_CHAT_TYPE": "channel", "HERMES_SESSION_CHAT_ID": "C1"},
                 {"HERMES_SESSION_USER_ID": "U0STRANGER"}):
        for k, v in {**base, **over}.items():
            monkeypatch.setenv(k, v)
        out = json.loads(ut.handle_remote({"command": "echo ok", "wait_s": 1}))
        assert "mac_ops_handoff" not in out["error"] and "refused" in out["error"]


# Invariant 3: the window names the originating gateway, profile and session; control chars can't hide text.
def test_window_shows_origin_and_neutralises_escapes(remote, monkeypatch, tmp_path):
    monkeypatch.setattr(ut, "open_window", lambda job: "fake")
    monkeypatch.setattr(ut, "confirm_mode", lambda: "all")  # decline so the escape-laden command never runs
    res = ut.start_relayed("echo visible\x1b[2K\rhidden", "reason\x1b]0;x\x07", str(tmp_path),
                           {**ORIGIN, "title": "evil\x1b[31m"})
    job = ut.jobs_dir() / res["job_id"]
    screen = _drive(job, ["n\n"]).decode(errors="replace")
    assert "From another machine" in screen
    assert "Gateway nous-1 · profile alpha · session 20261021_000505_e1e78133" in screen
    assert "^[[2K^Mhidden" in screen and "^[]0;x^G" in screen and "evil^[[31m" in screen
    assert "Hermes (nous-1/alpha) wants to run a command" in screen  # window title
    assert (job / "cmd.sh").read_bytes() == b"echo visible\x1b[2K\rhidden\n"  # runs verbatim, shown visibly


# Invariant 4: no Desktop courier online -> an immediate error, never a silent hang.
def test_no_courier_errors_immediately(remote, monkeypatch):
    base, _ = remote
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    t = time.time()
    out = json.loads(ut.handle_remote({"command": "echo ok", "wait_s": 600}))
    assert time.time() - t < 2 and out["ok"] is False and "No Hermes Desktop" in out["error"]
    assert "do not hand them the command" in out["error"] and not _outbox(base)


def test_configured_mac_offline_or_stale_errors_immediately(remote, monkeypatch):
    base, _ = remote
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    monkeypatch.setenv("HERMES_USER_TERMINAL_DEVICE", "macbook")
    _beat(base, "mac-mini")
    _beat(base, "macbook", ["macbook", "david-mbp"])
    old = time.time() - 120
    os.utime(base / "couriers" / "macbook.json", (old, old))
    t = time.time()
    out = json.loads(ut.handle_remote({"command": "echo ok", "wait_s": 600}))
    assert time.time() - t < 2 and "`macbook`" in out["error"] and "mac-mini" in out["error"]
    assert not _outbox(base)


def test_courier_alive_but_never_claims_errors_fast(remote, monkeypatch):
    base, _ = remote
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    monkeypatch.setattr(ut, "PICKUP_S", 0.6)
    _beat(base, "macbook")
    t = time.time()
    out = json.loads(ut.handle_remote({"command": "echo ok", "wait_s": 600}))
    assert time.time() - t < 3 and "did not pick up" in out["error"] and not _outbox(base)


def test_several_couriers_need_a_configured_device(remote, monkeypatch):
    base, _ = remote
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    _beat(base, "macbook")
    _beat(base, "mac-mini")
    assert "user_terminal.device" in json.loads(ut.handle_remote({"command": "x"}))["error"]


def test_status_only_for_the_origin_gateway(remote, monkeypatch, tmp_path):
    monkeypatch.setattr(ut, "open_window", lambda job: "fake")
    jid = ut.start_relayed("true", "", str(tmp_path), ORIGIN)["job_id"]
    assert ut.relayed_status(jid, "nous-1", 0)["status"] == "pending"
    assert ut.relayed_status(jid, "hermes-hub", 0)["ok"] is False
    assert ut.relayed_status(jid, "", 0)["ok"] is False
    local = ut.create_job("true", "", str(tmp_path))  # a local (non-relayed) job is never readable remotely
    assert ut.relayed_status(local.name, "nous-1", 0)["ok"] is False


# Round trip: remote tool -> outbox -> fake Desktop courier (real claim rule) -> Mac-side local path -> result.
class FakeCourier(threading.Thread):
    """Plays a Mac's Desktop courier: claims with relay_files' real rule, runs the request through the
    user-terminal Mac routes' functions (start_relayed / relayed_status) and posts the result."""

    def __init__(self, base: Path, device: str, gateway_key: str = "nous-1", aliases=()):
        super().__init__(daemon=True)
        self.base, self.device, self.gw, self.aliases = base, device, gateway_key, list(aliases) or [device]
        self.stop, self.claimed = threading.Event(), []

    def run(self):
        while not self.stop.is_set():
            for req in rf.claim_pending(self.base, home=False, courier=self.device, reach=["nous-1"], aliases=self.aliases):
                self.claimed.append(req)
                a = req["args"]
                if a.get("job_id"):
                    res = ut.relayed_status(a["job_id"], self.gw, a.get("wait_s") or 0)
                else:
                    origin = {"gateway": self.gw, "profile": req["sender"].get("profile"),
                              "session_id": req["sender"].get("session_id"), "title": req["sender"].get("title")}
                    res = ut.start_relayed(a["command"], a.get("reason"), a.get("cwd"), origin)
                rf.write_result(req["id"], {**res, "device": self.device}, self.base)
            time.sleep(0.1)


@needs_bsd_script
def test_round_trip_remote_to_mac_and_back(remote, monkeypatch, tmp_path):
    base, mac = remote
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    monkeypatch.setenv("HERMES_USER_TERMINAL_DEVICE", "david-mbp")  # an alias resolves to the device
    monkeypatch.setattr(ut, "POLL_S", 1)
    screens = []

    def fake_window(job):  # default confirm=off: it runs at once, the owner only closes the window
        threading.Thread(target=lambda: screens.append(_drive(job, ["\n"])), daemon=True).start()
        return "fake"

    monkeypatch.setattr(ut, "open_window", fake_window)
    mbp, mini = FakeCourier(base, "macbook", aliases=["macbook", "david-mbp"]), FakeCourier(base, "mac-mini")
    _beat(base, "macbook", ["macbook", "david-mbp"])
    _beat(base, "mac-mini")
    mbp.start()
    mini.start()
    try:
        out = json.loads(ut.handle_remote({"command": "echo ok-$((40+2))", "reason": "test", "cwd": str(tmp_path),
                                           "wait_s": 20}, session_id="s1"))
    finally:
        mbp.stop.set()
        mini.stop.set()
    assert out["ok"] and out["status"] == "ran" and out["exit_code"] == 0, out
    assert "ok-42" in out["output"] and out["device"] == "macbook" and out["terminal"] == "fake"
    assert mbp.claimed and not mini.claimed  # routed to the named Mac only
    assert mbp.claimed[0]["action"] == "user_terminal" and mbp.claimed[0]["route"] == "macbook"
    assert "Gateway nous-1 · profile" in screens[0].decode(errors="replace")


def test_round_trip_pending_then_resume_with_job_id(remote, monkeypatch, tmp_path):
    base, _ = remote
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    monkeypatch.setattr(ut, "POLL_S", 1)
    monkeypatch.setattr(ut, "open_window", lambda job: "fake")
    c = FakeCourier(base, "macbook")
    _beat(base, "macbook")
    c.start()
    try:
        first = json.loads(ut.handle_remote({"command": "true", "cwd": str(tmp_path), "wait_s": 1}))
        assert first["ok"] and first["status"] == "pending" and "job_id" in first["note"]
        job = ut.jobs_dir() / first["job_id"]
        (job / "status.json").write_text(json.dumps({"status": "ran", "exit_code": 0}))
        again = json.loads(ut.handle_remote({"job_id": first["job_id"], "wait_s": 5}))
    finally:
        c.stop.set()
    assert again["ok"] and again["status"] == "ran" and again["job_id"] == first["job_id"]


def test_register_picks_remote_handler_without_a_screen(monkeypatch):
    got = {}

    class Ctx:
        def register_tool(self, **kw):
            got.update(kw)

        def register_hook(self, *a):
            pass

    monkeypatch.setattr(ut, "has_screen", lambda: False)
    ut.register(Ctx())
    assert got["handler"] is ut.handle_remote and got["schema"]["parameters"] == ut.SCHEMA["parameters"]
    monkeypatch.setattr(ut, "has_screen", lambda: True)
    ut.register(Ctx())
    assert got["handler"] is ut.handle


def test_dashboard_routes_start_and_status(remote, monkeypatch, tmp_path):
    """The Mac-side REST routes the courier calls (dashboard/plugin_api.py), end to end over HTTP."""
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location("ut_api", Path(__file__).with_name("dashboard") / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    mod = api._ut()  # the route's own fresh copy of the plugin module
    monkeypatch.setattr(mod, "open_window", lambda job: "fake")
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/user-terminal")
    c = TestClient(app)
    assert c.get("/api/plugins/user-terminal/ping").json()["relay"] is True
    r = c.post("/api/plugins/user-terminal/start", json={"command": "echo hi", "reason": "r", "cwd": str(tmp_path),
                                                       "origin": ORIGIN}).json()
    assert r["ok"] and r["status"] == "waiting_for_user" and r["terminal"] == "fake"
    st = c.get(f"/api/plugins/user-terminal/status/{r['job_id']}?gateway=nous-1&wait_s=0").json()
    assert st["status"] == "pending"
    assert c.get(f"/api/plugins/user-terminal/status/{r['job_id']}?gateway=other").json()["ok"] is False
    assert c.post("/api/plugins/user-terminal/start", json={"command": "x"}).json()["ok"] is False  # no origin


@pytest.mark.parametrize("out", [
    "daveey@desk.local: Permission denied (publickey,password,keyboard-interactive).",
    "Permission denied (publickey,keyboard-interactive).",
    # nested: laptop -> hop -> host, the inner ssh's failure arrives on its own line
    "Warning: Permanently added 'desk.local' (ED25519) to the list of known hosts.\r\n"
    "you@desk.local: Permission denied (publickey,password,keyboard-interactive).\nexit 255",
    "Enter passphrase for key '/Users/you/.ssh/id_ed25519':",
    "you@desk.local's password: ",
    "/usr/bin/ssh-copy-id: ERROR: Permission denied (publickey).",
])
def test_ssh_password_auth_gets_ssh_copy_id_hint(out):
    new = ut.transform_terminal_output(command="ssh -o BatchMode=yes desk.local true", output=out)
    assert new and "run_in_user_terminal" in new and "ssh-copy-id -i ~/.ssh/id_ed25519.pub" in new
    assert "hand it back" in new
    assert ut.transform_terminal_output(command="ssh x", output=new) is None  # idempotent


@pytest.mark.parametrize("out", [
    "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
    "security: SecKeychainUnlock: User interaction is not allowed.",
])
def test_git_https_and_keychain_get_hint(out):
    new = ut.transform_terminal_output(command="git pull", output=out)
    assert new and "run_in_user_terminal" in new and "ssh-copy-id" not in new


@pytest.mark.parametrize("out", [
    "Permission denied (publickey).",                         # key-only server: a password can't help
    "73 sudo: a password is required",                        # a log/grep that mentions it, not sudo itself
    "grep: Permission denied (publickey,password) appears in auth.log",
    "ok",
])
def test_no_hint_for_mentions_or_key_only_servers(out):
    assert ut.transform_terminal_output(command="x", output=out) is None


# ---- launchers must not pass a Kanban worker's identity to the terminal app

WORKER_ENV = {"HERMES_KANBAN_TASK": "t_dead", "HERMES_KANBAN_RUN_ID": "5045", "HERMES_KANBAN_WORKER_TASK": "t_dead"}


@pytest.fixture
def worker(monkeypatch, tmp_path):
    for k, v in WORKER_ENV.items():
        monkeypatch.setenv(k, v)
    calls = []

    def fake_run(argv, **kw):
        calls.append((list(argv), kw))
        return subprocess.CompletedProcess(argv, 0, stdout="workspace:7", stderr="")

    monkeypatch.setattr(ut.subprocess, "run", fake_run)
    monkeypatch.setattr(ut.time, "sleep", lambda s: None)
    job = tmp_path / "job"
    job.mkdir()
    return calls, job


@pytest.mark.parametrize("which", ["cmux", "terminal", "iterm"])
def test_launchers_pass_scrubbed_env(worker, monkeypatch, which):
    calls, job = worker
    monkeypatch.setattr(ut, "terminal_order", lambda: [which])
    ut.open_window(job)
    assert calls
    for argv, kw in calls:
        env = kw.get("env")
        assert env is not None, argv
        assert "HERMES_KANBAN_TASK" not in env and "HERMES_KANBAN_RUN_ID" not in env
        assert "HERMES_KANBAN_WORKER_TASK" not in env


def test_cmux_command_unsets_identity_first(worker, monkeypatch):
    calls, job = worker
    monkeypatch.setattr(ut, "terminal_order", lambda: ["cmux"])
    ut.open_window(job)
    cmd = next(a for a, _ in calls if "new-workspace" in a)[-1]
    assert cmd.startswith("unset ") and "HERMES_KANBAN_TASK" in cmd.split(";")[0] and "HERMES_KANBAN_RUN_ID" in cmd.split(";")[0]


def test_terminal_app_command_unsets_identity_first(worker, monkeypatch):
    calls, job = worker
    monkeypatch.setattr(ut, "terminal_order", lambda: ["terminal"])
    ut.open_window(job)
    script = calls[0][0][2]
    assert "do script \"unset " in script and "HERMES_KANBAN_RUN_ID" in script.split("runner.sh")[0]


def test_iterm_command_env_unsets_identity(worker, monkeypatch):
    calls, job = worker
    monkeypatch.setattr(ut, "terminal_order", lambda: ["iterm"])
    ut.open_window(job)
    script = calls[0][0][2]
    pre = script.split("runner.sh")[0]
    assert "env -u HERMES_KANBAN_TASK" in pre and "-u HERMES_KANBAN_RUN_ID" in pre



# ---- every opener failed: the job is persisted as failed (never left waiting_for_user) and waits end at once

def _all_openers_fail(monkeypatch):
    monkeypatch.setattr(ut, "terminal_order", lambda: ["iterm", "terminal"])

    def boom(name):
        def f(*_a, **_k):
            raise RuntimeError(f"{name} refused")
        return f

    monkeypatch.setattr(ut, "_open_iterm", boom("iterm"))
    monkeypatch.setattr(ut, "_open_terminal_app", boom("terminal"))


def _only_job(d: Path) -> Path:
    jobs = [p for p in d.iterdir() if p.is_dir()]
    assert len(jobs) == 1, jobs
    return jobs[0]


def _assert_failed_status(job: Path, *needles: str) -> dict:
    st = json.loads((job / "status.json").read_text())
    assert st["status"] == "failed" and st["job_id"] == job.name, st
    for n in needles:
        assert n in st["error"], st
    assert not list(job.glob("*.tmp")) and not list(job.glob(".status*"))  # no torn/temp write left behind
    return st


def test_handle_all_openers_fail_persists_failed_and_resume_returns_at_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    _all_openers_fail(monkeypatch)
    t = time.time()
    out = json.loads(ut.handle({"command": "true", "cwd": str(tmp_path), "wait_s": 3}))
    assert time.time() - t < 1.5
    job = _only_job(tmp_path / "jobs")
    _assert_failed_status(job, "iterm refused", "terminal refused")  # on disk first: not left waiting_for_user
    assert out["ok"] is False and out["status"] == "failed" and out["job_id"] == job.name, out
    assert "iterm refused" in out["error"] and "terminal refused" in out["error"]
    t = time.time()
    again = json.loads(ut.handle({"job_id": job.name, "wait_s": 3}))  # resume: no 3 s wait on a dead job
    assert time.time() - t < 1.5
    assert again["ok"] is False and again["status"] == "failed" and again["job_id"] == job.name
    assert "iterm refused" in again["error"]


def test_start_relayed_all_openers_fail_persists_failed_and_status_returns_at_once(remote, monkeypatch, tmp_path):
    _, mac = remote
    _all_openers_fail(monkeypatch)
    res = ut.start_relayed("true", "why", str(tmp_path), ORIGIN)
    job = _only_job(mac)
    _assert_failed_status(job, "terminal refused")  # on disk first: not left waiting_for_user
    assert res["ok"] is False and res["status"] == "failed" and res["job_id"] == job.name, res
    assert "on the Mac" in res["error"] and "terminal refused" in res["error"]
    t = time.time()
    st = ut.relayed_status(job.name, "nous-1", 3)  # the courier's resume path
    assert time.time() - t < 1.5
    assert st["ok"] is False and st["status"] == "failed" and st["job_id"] == job.name


def test_wait_for_treats_failed_as_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("true", "", str(tmp_path))
    ut.fail_job(job, "x broke")
    t = time.time()
    res = ut.wait_for(job, 3)
    assert time.time() - t < 1.5
    assert res == {**res, "ok": False, "status": "failed", "error": "x broke", "job_id": job.name}
    assert "note" in res and "pending" not in res["note"]


def test_fail_job_writes_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("true", "", str(tmp_path))
    seen = []
    real = ut.os.replace
    monkeypatch.setattr(ut.os, "replace", lambda a, b: (seen.append((Path(a).parent, Path(b).name)), real(a, b)))
    ut.fail_job(job, "e")
    assert seen == [(job, "status.json")]


@needs_bsd_script
def test_runner_refuses_a_job_already_marked_failed(tmp_path, monkeypatch):
    """A window that opens late (osascript timed out, then the user approved Automation) must not run a job the
    agent was already told failed: it would run twice if the agent retried."""
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    marker = tmp_path / "RAN"
    job = ut.create_job(f"touch {marker}", "", str(tmp_path))
    ut.fail_job(job, "timed out")
    screen = _drive(job, [], timeout=5)
    assert not marker.exists() and b"not running it" in screen
    assert ut.read_status(job)["status"] == "failed"


def test_remote_round_trip_reports_mac_open_failure(remote, monkeypatch, tmp_path):
    base, mac = remote
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "desktop")
    _all_openers_fail(monkeypatch)
    c = FakeCourier(base, "macbook")
    _beat(base, "macbook")
    c.start()
    try:
        out = json.loads(ut.handle_remote({"command": "true", "cwd": str(tmp_path), "wait_s": 5}))
    finally:
        c.stop.set()
    job = _only_job(mac)
    _assert_failed_status(job, "terminal refused")
    assert out["ok"] is False and out["status"] == "failed" and out["job_id"] == job.name, out


# ---- osascript timeout: name the TCC-responsible process when the OS tells us, labelled unknown otherwise

def _osa_timeout(monkeypatch):
    def fake_run(argv, **kw):
        if argv and argv[0] == "osascript":
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(ut.subprocess, "run", fake_run)


def test_osascript_timeout_names_responsible_process(tmp_path, monkeypatch):
    _osa_timeout(monkeypatch)
    monkeypatch.setattr(ut, "_responsible_pid", lambda pid: 4242)
    monkeypatch.setattr(ut, "_proc_info", lambda pid: {4242: (1, "/Applications/Example Host.app/Contents/MacOS/Example Host"),
                                                       ut.os.getpid(): (777, "python3"),
                                                       777: (4242, "zsh")}.get(pid))
    monkeypatch.setattr(ut, "terminal_order", lambda: ["terminal"])
    job = tmp_path / "job"
    job.mkdir()
    with pytest.raises(RuntimeError) as ei:
        ut.open_window(job)
    msg = str(ei.value)
    assert "osascript timed out after 20s" in msg
    assert "TCC responsible process: Example Host (pid 4242" in msg and "responsibility_get_pid_responsible_for_pid" in msg
    assert "not definitive" in msg and "zsh" in msg  # ancestry is shown, but labelled as not the TCC answer
    assert "tell application" not in msg  # the AppleScript body is not dumped into the error


def test_osascript_timeout_responsible_unknown_is_labelled(tmp_path, monkeypatch):
    _osa_timeout(monkeypatch)
    monkeypatch.setattr(ut, "_responsible_pid", lambda pid: None)
    monkeypatch.setattr(ut, "_proc_info", lambda pid: None)
    monkeypatch.setattr(ut, "terminal_order", lambda: ["iterm"])
    job = tmp_path / "job"
    job.mkdir()
    with pytest.raises(RuntimeError) as ei:
        ut.open_window(job)
    msg = str(ei.value)
    assert "iterm: osascript timed out" in msg and "TCC responsible process: unknown" in msg


def test_osascript_timeout_persists_diagnosis_in_failed_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    _osa_timeout(monkeypatch)
    monkeypatch.setattr(ut, "_responsible_pid", lambda pid: 4242)
    monkeypatch.setattr(ut, "_proc_info", lambda pid: (1, "/x/Example Host") if pid == 4242 else None)
    monkeypatch.setattr(ut, "terminal_order", lambda: ["terminal"])
    out = json.loads(ut.handle({"command": "true", "cwd": str(tmp_path), "wait_s": 3}))
    st = _assert_failed_status(_only_job(tmp_path / "jobs"), "osascript timed out")
    assert out["status"] == "failed" and st["diagnosis"]["responsible_process"]["name"] == "Example Host"
    assert st["diagnosis"]["responsible_process"]["source"] == "responsibility_get_pid_responsible_for_pid"


def test_osascript_error_includes_stderr(tmp_path, monkeypatch):
    def fake_run(argv, **kw):
        raise subprocess.CalledProcessError(1, argv, output="", stderr="execution error: Not authorized to send Apple events to Terminal. (-1743)")
    monkeypatch.setattr(ut.subprocess, "run", fake_run)
    monkeypatch.setattr(ut, "terminal_order", lambda: ["terminal"])
    job = tmp_path / "job"
    job.mkdir()
    with pytest.raises(RuntimeError) as ei:
        ut.open_window(job)
    assert "-1743" in str(ei.value)


def test_responsible_pid_lookup_is_real_or_none():
    """Bounded real OS read (no window): a pid on macOS, None where the call does not exist."""
    r = ut._responsible_pid(os.getpid())
    assert r is None or (isinstance(r, int) and r > 0)
    if sys.platform == "darwin":
        assert r is not None and ut._proc_info(r) is not None


# ---- single-start gate: an opener that errors after its window started must not cause a second run or a false
# "failed / nothing ran"; fail_job and every runner contend for the same atomic gate (mkdir start.lock).

def _spawn_runner(job: Path):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["SHELL"] = "/bin/bash"
        os.execv("/bin/bash", ["/bin/bash", str(job / "runner.sh"), str(job)])
    return pid, fd


def _read_until(fd: int, needle: bytes, timeout: float = 10, buf: bytes = b"") -> bytes:
    deadline = time.time() + timeout
    while needle not in buf and time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
    return buf


def _finish(pid: int, fd: int, answers=(), timeout: float = 15) -> bytes:
    """Answer each 'Press Enter' with the next answer and reap the runner."""
    buf, pending, deadline = b"", list(answers), time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        if pending and b"Press Enter" in buf:
            os.write(fd, pending.pop(0).encode())
    try:  # never hang the suite on a runner still waiting at a prompt
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return buf


def _wait_status(job: Path, wanted: tuple, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    st = ut.read_status(job)
    while st.get("status") not in wanted and time.time() < deadline:
        time.sleep(0.05)
        st = ut.read_status(job)
    return st


def _simulate_runner_started(job: Path, status: str) -> None:
    """What a runner leaves behind once it took the start gate (no pty: works on every platform)."""
    # a live pid: resume paths now reconcile, and a dead runner pid would (correctly) become "abandoned"
    (job / "opened").write_text(f"{os.getpid()}\n")
    (job / "start.lock").mkdir()
    (job / "start.lock" / "owner").write_text(f"runner {os.getpid()}\n")
    st = {"status": status} if status == "running" else {"status": "ran", "exit_code": 0}
    (job / "status.json").write_text(json.dumps(st))


def _openers(monkeypatch, first):
    """iterm = `first(job)`; terminal = counts calls and fails. Returns the terminal call list."""
    calls = []
    monkeypatch.setattr(ut, "terminal_order", lambda: ["iterm", "terminal"])
    monkeypatch.setattr(ut, "_open_iterm", lambda shell_cmd: first(Path(shell_cmd.split()[-1].strip("'"))))

    def term(shell_cmd):
        calls.append(shell_cmd)
        raise RuntimeError("terminal refused")
    monkeypatch.setattr(ut, "_open_terminal_app", term)
    return calls


def _timeout_after(status):
    def opener(job):
        _simulate_runner_started(job, status)
        raise ut.OpenFailed("osascript timed out after 20s. TCC responsible process: unknown (x).",
                            {"responsible_process": {"status": "unknown"}})
    return opener


@pytest.mark.parametrize("status", ["running", "ran"])
def test_handle_opener_raises_after_runner_started_preserves_status_and_no_fallback(tmp_path, monkeypatch, status):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    calls = _openers(monkeypatch, _timeout_after(status))
    out = json.loads(ut.handle({"command": "true", "cwd": str(tmp_path), "wait_s": 1}))
    job = _only_job(tmp_path / "jobs")
    assert calls == []  # no second opener once a runner holds the gate
    assert ut.read_status(job)["status"] == status  # not overwritten with failed
    assert out["ok"] is True and out["status"] == status and out["terminal"] == "iTerm", out
    assert "osascript timed out" in out["opener_warning"]
    assert "nothing ran" not in json.dumps(out)
    again = json.loads(ut.handle({"job_id": job.name, "wait_s": 1}))  # resume path
    assert again["ok"] is True and again["status"] == status


@pytest.mark.parametrize("status", ["running", "ran"])
def test_start_relayed_opener_raises_after_runner_started_preserves_status(remote, monkeypatch, tmp_path, status):
    _, mac = remote
    calls = _openers(monkeypatch, _timeout_after(status))
    res = ut.start_relayed("true", "why", str(tmp_path), ORIGIN)
    job = _only_job(mac)
    assert calls == [] and ut.read_status(job)["status"] == status
    assert res["ok"] is True and res["job_id"] == job.name and res["status"] == status, res
    st = ut.relayed_status(job.name, "nous-1", 1)  # the courier's resume path
    assert st["ok"] is True and st["status"] == status and "osascript timed out" in st["opener_warning"]


def test_opener_raises_then_window_appears_within_grace_stops_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(ut, "OPEN_GRACE_S", 2.0, raising=False)

    def late(job):  # osascript gave up, but the window shows up a moment later
        threading.Timer(0.3, lambda: (job / "opened").write_text("1\n")).start()
        raise ut.OpenFailed("osascript timed out after 20s.")
    calls = _openers(monkeypatch, late)
    out = json.loads(ut.handle({"command": "true", "cwd": str(tmp_path), "wait_s": 1}))
    job = _only_job(tmp_path / "jobs")
    assert calls == [] and out["ok"] is True and out["status"] == "pending", out
    assert ut.read_status(job)["status"] == "waiting_for_user" and not (job / "start.lock").exists()


def test_all_openers_fail_takes_the_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    _all_openers_fail(monkeypatch)
    out = json.loads(ut.handle({"command": "true", "cwd": str(tmp_path), "wait_s": 1}))
    job = _only_job(tmp_path / "jobs")
    _assert_failed_status(job, "iterm refused", "terminal refused")
    assert (job / "start.lock").is_dir() and "fail_job" in (job / "start.lock" / "owner").read_text()
    assert out["ok"] is False and out["status"] == "failed" and "nothing ran" in out["note"]


@pytest.mark.parametrize("status", ["running", "ran", "declined"])
def test_fail_job_never_overwrites_a_started_or_decided_job(tmp_path, monkeypatch, status):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("true", "", str(tmp_path))
    if status == "declined":
        (job / "start.lock").mkdir()
        (job / "status.json").write_text(json.dumps({"status": "declined"}))
    else:
        _simulate_runner_started(job, status)
    st = ut.fail_job(job, "late error")
    assert st["status"] == status and ut.read_status(job)["status"] == status
    assert "late error" not in json.dumps(ut.read_status(job))


@needs_bsd_script
def test_real_runner_started_then_opener_raises_runs_once(tmp_path, monkeypatch):
    """Real runner.sh in a pty plays the window the opener created before osascript timed out (Finder centring)."""
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(ut, "confirm_mode", lambda: "off")
    marker = tmp_path / "RAN"
    screens = []

    def opener(job):
        threading.Thread(target=lambda: screens.append(_drive(job, ["\n"])), daemon=True).start()
        _wait_status(job, ("running", "ran"))
        raise ut.OpenFailed("osascript timed out after 20s.")

    def term(shell_cmd):  # a fallback would start a second runner on the same job
        job = Path(shell_cmd.split("runner.sh' ")[-1].split(";")[0].strip("'"))
        calls.append(1)
        threading.Thread(target=lambda: screens.append(_drive(job, ["\n"])), daemon=True).start()
        return "Terminal"

    calls = []
    monkeypatch.setattr(ut, "terminal_order", lambda: ["iterm", "terminal"])
    monkeypatch.setattr(ut, "_open_iterm", lambda c: opener(Path(c.split()[-1].strip("'"))))
    monkeypatch.setattr(ut, "_open_terminal_app", term)
    out = json.loads(ut.handle({"command": f"echo once >> {marker}", "cwd": str(tmp_path), "wait_s": 15}))
    time.sleep(1)
    assert calls == [] and out["status"] == "ran" and out["exit_code"] == 0, out
    assert marker.read_text() == "once\n"


@needs_bsd_script
@pytest.mark.parametrize("answer", ["y\n", "n\n"])
def test_confirm_waiting_then_fail_job_then_answer_never_runs(tmp_path, monkeypatch, answer):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    marker = tmp_path / "RAN"
    job = ut.create_job(f"touch {marker}", "", str(tmp_path), confirm=True)
    pid, fd = _spawn_runner(job)
    screen = _read_until(fd, b"Run it?")
    assert b"Run it?" in screen
    st = ut.fail_job(job, "osascript timed out after 20s.")  # Hermes gives up while the prompt waits
    assert st["status"] == "failed"
    os.write(fd, answer.encode())
    screen += _finish(pid, fd, timeout=8)
    assert not marker.exists() and not (job / "output.log").exists()
    assert ut.read_status(job)["status"] == "failed" and b"not running it" in screen


@needs_bsd_script
def test_duplicate_runners_run_the_command_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    marker = tmp_path / "RAN"
    job = ut.create_job(f"echo once >> {marker}; sleep 1", "", str(tmp_path), confirm=False)
    a, b = _spawn_runner(job), _spawn_runner(job)
    sa, sb = _finish(*a, answers=["\n"]), _finish(*b, answers=["\n"])
    assert marker.read_text() == "once\n"
    assert ut.read_status(job) == {"status": "ran", "exit_code": 0}
    assert len((job / "opened").read_text().split()) == 2  # both windows announced themselves
    assert (b"not running it" in sa) != (b"not running it" in sb)


def test_center_is_best_effort_inside_the_open_script():
    for script in (ut._center("Terminal"), ut._center("iTerm")):
        assert script.strip().startswith("try") and "with timeout of" in script and script.strip().endswith("end try")



# ---------------------------------------------------------------- owned prompts: withdrawal, max age, dead runner

def _watch(job: Path, secs: float = 3.0, env: dict | None = None) -> None:
    """The job-local watchdog as it really runs: this plugin file executed with --watchdog (bounded)."""
    e = {**os.environ, **(env or {})}
    subprocess.run([sys.executable, str(Path(ut.__file__)), "--watchdog", str(job), str(secs), "0.2"], env=e,
                   timeout=secs + 20, capture_output=True)


def _board(tmp_path: Path, status="blocked", run=None) -> Path:
    import sqlite3
    db = tmp_path / "kanban.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, current_run_id INTEGER)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT, "
                "payload TEXT, created_at INTEGER)")
    con.execute("INSERT INTO tasks VALUES ('t_card', ?, ?)", (status, run))
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_card', 'blocked', ?)",
                (int(time.time()) - 600,))
    con.commit()
    con.close()
    return db


def _owned_job(tmp_path, monkeypatch, db: Path, run_id="7") -> Path:
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", run_id)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.setattr(ut, "open_window", lambda job: "Terminal")
    monkeypatch.setattr(ut, "spawn_watchdog", lambda job: None, raising=False)
    res = json.loads(ut.handle({"command": "echo hi", "reason": "r", "cwd": str(tmp_path), "wait_s": 1}))
    return Path(tmp_path / "jobs" / res["job_id"])


@pytest.mark.parametrize("key", ["HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"])
def test_owner_is_the_worker_card_and_run(tmp_path, monkeypatch, key):
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    monkeypatch.setenv(key, "t_card")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "42")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    seen = {}
    monkeypatch.setattr(ut, "open_window", lambda job: seen.setdefault("env", ut.launch_env()) and "Terminal")
    monkeypatch.setattr(ut, "spawn_watchdog", lambda job: None, raising=False)
    res = json.loads(ut.handle({"command": "echo hi", "cwd": str(tmp_path), "wait_s": 1}, session_id="s1"))
    owner_file = tmp_path / res["job_id"] / "owner.json"
    assert owner_file.exists(), "job records no owner"
    owner = json.loads(owner_file.read_text())
    assert owner == {"kind": "kanban", "task": "t_card", "run_id": 42, "board": "default",
                     "db": str(tmp_path / "kanban.db")}
    assert key not in seen["env"]  # the launched window still gets the scrubbed env


def test_owner_is_the_chat_session_without_a_card(tmp_path, monkeypatch):
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    monkeypatch.setattr(ut, "open_window", lambda job: "Terminal")
    spawned = []
    monkeypatch.setattr(ut, "spawn_watchdog", spawned.append, raising=False)
    res = json.loads(ut.handle({"command": "echo hi", "cwd": str(tmp_path), "wait_s": 1}, session_id="chat-123"))
    assert [p.name for p in spawned] == [res["job_id"]]  # every local job gets its own watchdog
    owner_file = tmp_path / res["job_id"] / "owner.json"
    assert owner_file.exists(), "job records no owner"
    assert json.loads(owner_file.read_text()) == {"kind": "session", "session_id": "chat-123"}


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@needs_bsd_script
def test_withdraw_kills_running_command_group_and_window_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    marker = tmp_path / "after"
    job = ut.create_job(f"echo $$ > {tmp_path}/shell; sleep 300 & echo $! > {tmp_path}/child; sleep 300; touch {marker}",
                        "r", str(tmp_path))
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("screen", _drive(job, ["\n"], timeout=30)))
    th.start()
    deadline = time.time() + 15
    while time.time() < deadline and not (tmp_path / "child").exists():
        time.sleep(0.1)
    child = int((tmp_path / "child").read_text())
    assert not _gone(child)
    try:
        res = json.loads(ut.handle({"action": "withdraw", "job_id": job.name, "reason": "card finished", "wait_s": 1}))
        th.join(10)
        alive_after = not _gone(child)
    finally:  # never leave a sleep 300 (or a hung pty driver) behind when this fails
        for pid in (child,):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        subprocess.run(["pkill", "-9", "-P", (tmp_path / "shell").read_text().strip()], check=False)
        th.join(35)
    assert not alive_after, "background sleep 300 survived the withdrawal"
    assert res.get("status") == "withdrawn"
    assert not marker.exists()
    assert b"Hermes withdrew this request: card finished. Nothing else to do here." in out["screen"]
    st = ut.wait_for(job, 0)
    assert st["status"] == "withdrawn" and st["reason"] == "card finished"  # never overwritten by "ran"
    assert (job / "withdraw").read_text().strip() == "card finished"


@needs_bsd_script
def test_runner_refuses_when_withdraw_marker_preexists(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    ran = tmp_path / "ran"
    job = ut.create_job(f"touch {ran}", "r", str(tmp_path))
    (job / "withdraw").write_text("no longer needed")
    screen = _drive(job, ["\n"])
    assert not ran.exists()
    assert b"Hermes withdrew this request: no longer needed. Nothing else to do here." in screen
    assert ut.read_status(job).get("status") != "ran"


def test_watchdog_keeps_same_blocked_run_then_withdraws_on_unblock(tmp_path, monkeypatch):
    import sqlite3
    db = _board(tmp_path, "blocked", None)
    job = _owned_job(tmp_path, monkeypatch, db)
    _watch(job, 1.5)
    assert ut.read_status(job)["status"] == "waiting_for_user"  # still blocked, same run: kept
    con = sqlite3.connect(db)
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_card', 'unblocked', ?)",
                (int(time.time()),))
    con.execute("UPDATE tasks SET status='ready'")
    con.commit()
    con.close()
    _watch(job, 3)
    st = ut.wait_for(job, 0)
    assert st["status"] == "withdrawn" and "unblocked" in st["reason"]


@pytest.mark.parametrize("status,run", [("done", None), ("archived", None), ("running", 9)])
def test_watchdog_withdraws_when_card_finished_or_newer_run(tmp_path, monkeypatch, status, run):
    db = _board(tmp_path, status, run)
    job = _owned_job(tmp_path, monkeypatch, db)
    _watch(job, 3)
    assert ut.wait_for(job, 0)["status"] == "withdrawn"


def test_watchdog_max_age(tmp_path, monkeypatch):
    # Max age is resolved when the job is created and persisted in meta.json: the detached watchdog may not be
    # able to import Hermes config, so it gets no env here and must still expire the job.
    monkeypatch.setenv("HERMES_USER_TERMINAL_MAX_WAIT_MINUTES", "2")
    db = _board(tmp_path, "blocked", None)
    job = _owned_job(tmp_path, monkeypatch, db)
    meta = json.loads((job / "meta.json").read_text())
    assert meta["max_wait_minutes"] == 2
    meta["created"] = time.time() - 3 * 60
    (job / "meta.json").write_text(json.dumps(meta))
    (job / "opened").write_text("")  # a window announced itself (no launch-grace failure)
    _watch(job, 3, {"HERMES_USER_TERMINAL_MAX_WAIT_MINUTES": ""})
    st = ut.wait_for(job, 0)
    assert st["status"] == "withdrawn" and st["reason"] == "expired after 2 min; ask again if still needed"


def test_watchdog_dead_runner_becomes_abandoned_but_not_before_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    _watch(job, 1)
    assert ut.read_status(job)["status"] == "waiting_for_user"  # no runner yet (launch race): kept
    p = subprocess.Popen(["true"])
    p.wait()
    (job / "opened").write_text(f"{p.pid}\n")
    ut.wait_for  # noqa: B018
    (job / "status.json").write_text(json.dumps({"status": "running"}))
    (job / "start.lock").mkdir()
    (job / "start.lock" / "owner").write_text(f"runner {p.pid}\n")
    _watch(job, 3)
    st = ut.wait_for(job, 0)
    assert st["status"] == "abandoned" and st["ok"] is False


def test_remote_withdraw_is_refused_not_resumed(remote, monkeypatch):
    monkeypatch.setattr(ut, "owner_refusal", lambda sid: None)
    res = json.loads(ut.handle_remote({"action": "withdraw", "job_id": "x", "reason": "r", "wait_s": 1}))
    assert res["ok"] is False and "withdraw is not available" in res["error"]



# ---------------------------------------------------------------- owned prompts: contract corrections

def test_create_job_records_card_owner_in_meta_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "5")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    job = ut.create_job("echo hi", "r", str(tmp_path))  # no owner= : create_job resolves it itself
    meta = json.loads((job / "meta.json").read_text())
    assert meta["owner"] == {"kind": "card", "board": "default", "task_id": "t_card", "run_id": 5}
    assert meta["board_db"] == str(tmp_path / "kanban.db")
    legacy = json.loads((job / "owner.json").read_text())  # sidecar kept for compatibility
    assert legacy["kind"] == "kanban" and legacy["task"] == "t_card"


def test_create_job_without_card_owns_by_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SESSION_ID", "sess-9")
    job = ut.create_job("echo hi", "r", str(tmp_path))
    assert json.loads((job / "meta.json").read_text())["owner"] == {"kind": "session", "session_id": "sess-9"}


def test_non_default_board_db_resolves_under_kanban_home(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_db", None)  # no core: the fallback resolver
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "ops")
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "root"))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    meta = json.loads((job / "meta.json").read_text())
    assert meta["owner"]["board"] == "ops"
    assert meta["board_db"] == str(tmp_path / "root" / "kanban" / "boards" / "ops" / "kanban.db")


def test_board_db_prefers_core_resolver(tmp_path, monkeypatch):
    import types
    core = types.ModuleType("hermes_cli.kanban_db")
    core.kanban_db_path = lambda board=None: tmp_path / f"core-{board}.db"
    monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_db", core)
    assert ut.board_db_path("ops") == str(tmp_path / "core-ops.db")


def test_watch_interval_defaults_to_15s():
    import inspect
    assert ut.WATCH_INTERVAL_S == 15
    assert inspect.signature(ut.watchdog).parameters["interval"].default == ut.WATCH_INTERVAL_S


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-3", "abc", "1e400"])
def test_max_wait_minutes_must_be_finite_and_positive(monkeypatch, raw):
    monkeypatch.setenv("HERMES_USER_TERMINAL_MAX_WAIT_MINUTES", raw)
    assert ut.max_wait_minutes() == 120.0


def test_expiry_reason_is_exact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_USER_TERMINAL_MAX_WAIT_MINUTES", "30")
    job = ut.create_job("echo hi", "r", str(tmp_path))
    meta = json.loads((job / "meta.json").read_text())
    meta["created"] -= 31 * 60
    (job / "meta.json").write_text(json.dumps(meta))
    (job / "opened").write_text("")
    st = ut.watch_once(job, {})
    assert st["status"] == "withdrawn" and st["reason"] == "expired after 30 min; ask again if still needed"


def _age(job: Path, secs: float, drop_new_keys: bool = False) -> None:
    meta = json.loads((job / "meta.json").read_text())
    meta["created"] = time.time() - secs
    if drop_new_keys:  # a job written before owners existed (e.g. 20261031-090513-2e0275)
        meta = {k: meta[k] for k in ("command", "reason", "cwd", "origin", "created")}
        (job / "owner.json").unlink(missing_ok=True)
    (job / "meta.json").write_text(json.dumps(meta))


def test_resume_reconciles_job_whose_window_never_started(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    young = ut.create_job("echo hi", "r", str(tmp_path))
    old = ut.create_job("echo hi", "r", str(tmp_path))
    _age(old, ut.LAUNCH_GRACE_S + 5)
    res = json.loads(ut.handle({"job_id": old.name, "wait_s": 1}))
    # Contract change: no window after the launch grace is "abandoned" with the start gate fenced first ("failed"
    # stays for real opener errors, test_opener_error_still_reports_failed).
    assert res["status"] == "abandoned" and res["ok"] is False
    assert (old / "start.lock").is_dir()  # a window that opens late refuses
    res = json.loads(ut.handle({"job_id": young.name, "wait_s": 1}))
    assert res["status"] == "pending"  # inside the launch grace: kept


def test_any_call_sweeps_legacy_stale_jobs_without_watchdog(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    legacy = ut.create_job("echo hi", "r", str(tmp_path))
    _age(legacy, 7 * 3600, drop_new_keys=True)
    windowed = ut.create_job("echo hi", "r", str(tmp_path))
    _age(windowed, 7 * 3600, drop_new_keys=True)
    (windowed / "opened").write_text("")  # a window announced itself but never ran it
    monkeypatch.setattr(ut, "open_window", lambda job: "Terminal")
    json.loads(ut.handle({"command": "echo new", "cwd": str(tmp_path), "wait_s": 1}))
    assert ut.read_status(legacy)["status"] == "abandoned"  # fenced then abandoned (was "failed")
    assert json.loads((legacy / "status.json").read_text())["status"] == "abandoned"
    st = ut.read_status(windowed)
    assert st["status"] == "withdrawn" and st["reason"] == "expired after 120 min; ask again if still needed"


@pytest.fixture
def victim():
    """A process in its own group that dies on SIGTERM/SIGUSR1/SIGKILL: proof of any stray signal."""
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    yield p
    p.kill()
    p.wait()


def test_withdraw_never_signals_legacy_pid_only_files(tmp_path, monkeypatch, victim):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    (job / "opened").write_text(f"{victim.pid}\n")
    (job / "cmd.pgid").write_text(f"{victim.pid}\n")
    ut.withdraw_job(job, "gone")
    time.sleep(0.3)
    assert victim.poll() is None, "a PID-only (unverified) process was signalled"


def test_withdraw_never_signals_pid_whose_start_time_differs(tmp_path, monkeypatch, victim):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    stale = "Mon Jan  1 00:00:00 2001"
    (job / "runners").write_text(f"{victim.pid} {stale}\n")
    (job / "opened").write_text(f"{victim.pid}\n")
    (job / "cmd.pgid").write_text(f"{victim.pid} {stale}\n")
    ut.withdraw_job(job, "gone")
    time.sleep(0.3)
    assert victim.poll() is None, "a reused pid (start time mismatch) was signalled"


def test_withdraw_signals_verified_command_group(tmp_path, monkeypatch, victim):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    (job / "cmd.pgid").write_text(f"{victim.pid} {ut._proc_start(victim.pid)}\n")
    ut.withdraw_job(job, "gone")
    assert victim.wait(5) is not None


@pytest.mark.parametrize("end", ["withdraw", "abandon"])
def test_status_json_reads_back_terminal_after_kill(tmp_path, monkeypatch, end):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    # the runner passed its gate just before the final gate was taken and writes "running" during the kill
    monkeypatch.setattr(ut, "_kill_command", lambda j, secs=3.0: (j / "status.json").write_text('{"status":"running"}'))
    st = ut.withdraw_job(job, "gone") if end == "withdraw" else ut.mark_abandoned(job, "window closed")
    on_disk = json.loads((job / "status.json").read_text())
    assert on_disk["status"] == st["status"] and on_disk["status"] in ("withdrawn", "abandoned")


def test_one_watchdog_per_job(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    cmd = [sys.executable, str(Path(ut.__file__)), "--watchdog", str(job), "30", "0.2"]
    first = subprocess.Popen(cmd, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not (job / "watchdog.lock" / "owner").exists():
            time.sleep(0.05)
        second = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert second.returncode == 0 and "already watched" in second.stderr
        assert first.poll() is None
        ut.withdraw_job(job, "done")
        assert first.wait(10) == 0
        assert not (job / "watchdog.lock").exists()  # released on exit
    finally:
        first.kill()


def test_watchdog_crash_is_logged_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    (job / "meta.json").write_text("[1, 2]")  # corrupt: not an object
    r = subprocess.run([sys.executable, str(Path(ut.__file__)), "--watchdog", str(job), "2", "0.2"],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode != 0 and "Traceback" in r.stderr


def test_spawn_failure_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))

    def boom(*a, **k):
        raise OSError("no python here")
    monkeypatch.setattr(ut.subprocess, "Popen", boom)
    _REAL_SPAWN(job)
    assert "no python here" in (job / "watchdog.log").read_text()


def test_spawned_watchdog_logs_to_job(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    (job / "meta.json").write_text("[1, 2]")
    _REAL_SPAWN(job)
    deadline = time.time() + 15
    while time.time() < deadline and "Traceback" not in ((job / "watchdog.log").read_text()
                                                         if (job / "watchdog.log").exists() else ""):
        time.sleep(0.1)
    assert "Traceback" in (job / "watchdog.log").read_text()


def test_board_check_logs_unreadable_board(tmp_path, caplog):
    db = tmp_path / "kanban.db"
    db.write_bytes(b"not a sqlite file at all" * 100)
    with caplog.at_level("WARNING"):
        assert ut._board_check({"task": "t_card", "db": str(db)}, time.time()) is None
    assert "board" in caplog.text.lower()


@needs_bsd_script
def test_runner_starts_one_watchdog_before_the_command(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_USER_TERMINAL_RUNNER_WATCHDOG", raising=False)
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("", "r", str(tmp_path))  # create_job() direct: no handle() spawn
    (job / "cmd.sh").write_text(f"[ -d {job}/watchdog.lock ] && cat {job}/watchdog.lock/owner > {tmp_path}/seen\n")
    try:
        _drive(job, ["\n"])
        seen = (tmp_path / "seen").read_text().split() if (tmp_path / "seen").exists() else []
        assert seen and seen[0].isdigit(), "no watchdog held the job when its command ran"
    finally:
        for line in [(job / "watchdog.lock" / "owner")]:
            try:
                os.kill(int(line.read_text().split()[0]), 9)
            except (OSError, ValueError, IndexError):
                pass


# ---------------------------------------------------------------- abandonment, identities, legacy runners

# The runner.sh shipped before start/final gates, runners identities and the withdraw trap existed (the shape of
# job 20261031-090513-2e0275): it never writes opened/runners, ignores start.lock and the withdraw marker.
LEGACY_RUNNER = r"""#!/bin/bash
JOB="$1"
status() { printf '%s' "$1" > "$JOB/status.json.tmp" && mv "$JOB/status.json.tmp" "$JOB/status.json"; }
status '{"status":"running"}'
cd "$(cat "$JOB/cwd.txt")" || exit 1
/usr/bin/script -q "$JOB/output.log" /bin/bash -c "/bin/bash \"$JOB/cmd.sh\"; echo \$? > \"$JOB/rc\""
status "{\"status\":\"ran\",\"exit_code\":$(cat "$JOB/rc" 2>/dev/null || echo 1)}"
"""


def _legacy_job(tmp_path, age_s: float) -> Path:
    job = ut.create_job("echo hi", "r", str(tmp_path))
    (job / "runner.sh").write_text(LEGACY_RUNNER)
    (job / "watchdog.sh").unlink()
    _age(job, age_s, drop_new_keys=True)
    return job


def test_unstarted_job_past_grace_is_fenced_then_abandoned_not_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    _age(job, ut.LAUNCH_GRACE_S + 5)
    st = ut.watch_once(job, {})
    assert st["status"] == "abandoned" and "nothing ran" in st["reason"]
    assert (job / "start.lock" / "owner").read_text().split()[0] == "fence"  # a late window refuses
    assert json.loads((job / "status.json").read_text())["status"] == "abandoned"
    assert ut.wait_for(job, 0)["ok"] is False


@needs_bsd_script
def test_late_window_after_fence_never_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    ran = tmp_path / "ran"
    job = ut.create_job(f"touch {ran}", "r", str(tmp_path))
    _age(job, ut.LAUNCH_GRACE_S + 5)
    assert ut.watch_once(job, {})["status"] == "abandoned"
    screen = _drive(job, [])
    assert not ran.exists()
    assert b"Hermes already gave up" in screen
    assert ut.read_status(job)["status"] == "abandoned"


def test_opener_error_still_reports_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))

    def boom(job):
        raise ut.OpenFailed("osascript exited 1: nope")
    monkeypatch.setattr(ut, "open_window", boom)
    res = json.loads(ut.handle({"command": "echo hi", "cwd": str(tmp_path), "wait_s": 1}))
    assert res["status"] == "failed" and "nope" in res["error"]  # the real opener error, not "abandoned"


def test_legacy_stale_2e0275_shape_becomes_abandoned(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    legacy = _legacy_job(tmp_path, 7 * 3600)  # waiting_for_user, no opened/start.lock/owner, older than max age
    monkeypatch.setattr(ut, "open_window", lambda job: "Terminal")
    ut.handle({"command": "echo new", "cwd": str(tmp_path), "wait_s": 1})  # any call sweeps
    st = ut.read_status(legacy)
    assert st["status"] == "abandoned", st
    assert (legacy / "start.lock").is_dir()


def test_dead_runner_is_abandoned_before_owner_or_expiry(tmp_path, monkeypatch):
    db = _board(tmp_path, "done", None)  # the card is finished AND the job is past max age
    job = _owned_job(tmp_path, monkeypatch, db)
    _age(job, 7 * 3600)
    p = subprocess.Popen(["true"])
    p.wait()
    (job / "runners").write_text(f"{p.pid} Mon Jan  1 00:00:00 2001\n")
    (job / "opened").write_text(f"{p.pid}\n")
    (job / "start.lock").mkdir()
    (job / "start.lock" / "owner").write_text(f"runner {p.pid}\n")
    (job / "status.json").write_text(json.dumps({"status": "running"}))
    st = ut.watch_once(job, {})
    assert st["status"] == "abandoned", st


@needs_bsd_script
def test_runner_and_python_identities_match_across_locale_and_tz(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    monkeypatch.setenv("TZ", "America/Los_Angeles")  # the Hermes process
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    job = ut.create_job("sleep 4", "r", str(tmp_path))
    pid, fd = pty.fork()
    if pid == 0:  # the terminal window: another zone and locale
        os.environ.update({"SHELL": "/bin/bash", "TZ": "Asia/Kolkata", "LC_ALL": "fr_FR.UTF-8", "LANG": "fr_FR.UTF-8"})
        os.execv("/bin/bash", ["/bin/bash", str(job / "runner.sh"), str(job)])
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not (job / "cmd.pgid").exists():
            if select.select([fd], [], [], 0.1)[0]:
                os.read(fd, 4096)
        runners = ut.runner_idents(job)
        assert runners and all(ut._same_process(p, s) for p, s in runners.items()), (runners, ut._proc_start(pid))
        ident = ut._cmd_ident(job)
        assert ident and ut._same_process(*ident), (ident, ut._proc_start(ident[0]) if ident else None)
        assert ut.watch_once(job, {}) is None  # a live runner is never judged dead
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        ident = ut._cmd_ident(job)
        if ident:
            subprocess.run(["pkill", "-9", "-g", str(ident[0])], check=False)


def test_board_check_quotes_uri_path(tmp_path):
    d = tmp_path / "we?ird#dir %41"
    d.mkdir()
    db = _board(d, "done", None)
    assert ut._board_check({"task_id": "t_card", "db": str(db)}, time.time()) == "card t_card is done"


def test_board_check_falls_back_to_rw_query_only_when_ro_fails(tmp_path, monkeypatch):
    import sqlite3
    db = _board(tmp_path, "done", None)
    before = db.read_bytes()
    real, uris = sqlite3.connect, []

    def connect(database, *a, **k):
        uris.append(str(database))
        if "mode=ro" in str(database):  # what a WAL db does while its -wal/-shm sidecars are gone
            raise sqlite3.OperationalError("unable to open database file")
        return real(database, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", connect)
    assert ut._board_check({"task_id": "t_card", "db": str(db)}, time.time()) == "card t_card is done"
    assert any(u.endswith("mode=rw") for u in uris) and not any("rwc" in u for u in uris)
    assert db.read_bytes() == before


def test_ro_connect_is_query_only_and_never_creates(tmp_path):
    import sqlite3
    db = _board(tmp_path, "done", None)
    c = ut._ro_connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("INSERT INTO tasks VALUES ('x', 'ready', NULL)")
    finally:
        c.close()
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.Error):
        ut._ro_connect(missing)
    assert not missing.exists()


def test_withdraw_without_command_does_not_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    t = time.time()
    assert ut.withdraw_job(job, "gone")["status"] == "withdrawn"
    assert time.time() - t < 1.0  # no cmd.pgid after the final gate: no command can still be launching


def test_withdraw_escalates_to_sigkill_for_verified_group(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    p = subprocess.Popen(["/bin/bash", "-c", "trap '' TERM; while :; do sleep 0.1; done"], start_new_session=True)
    try:
        time.sleep(0.3)
        (job / "cmd.pgid").write_text(f"{p.pid} {ut._proc_start(p.pid)}\n")
        ut.withdraw_job(job, "gone")
        assert p.wait(5) == -9
    finally:
        try:
            os.killpg(p.pid, 9)
        except (ProcessLookupError, PermissionError):  # already gone (macOS reports EPERM for a reaped group)
            pass


def test_kill_command_never_signals_own_group(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = ut.create_job("echo hi", "r", str(tmp_path))
    me = os.getpgrp()
    (job / "cmd.pgid").write_text(f"{me} {ut._proc_start(me)}\n")
    sent = []
    monkeypatch.setattr(ut.os, "killpg", lambda pg, sig: sent.append((pg, sig)))
    ut._kill_command(job, 0.3)
    assert sent == []


def _fake_legacy_runner(job: Path) -> subprocess.Popen:
    """A process whose argv is the legacy window's (bash runner.sh JOB) without running the command."""
    return subprocess.Popen(["/bin/bash", "-c", "sleep 30; :", str(job / "runner.sh"), str(job)],
                            start_new_session=True)


def test_withdraw_live_legacy_runner_is_unsupported_not_withdrawn(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path))
    job = _legacy_job(tmp_path, 60)
    (job / "status.json").write_text(json.dumps({"status": "running"}))
    p = _fake_legacy_runner(job)
    try:
        time.sleep(0.3)
        res = json.loads(ut.handle({"action": "withdraw", "job_id": job.name, "reason": "card done"}))
        assert res["ok"] is False and res["status"] == "running" and "legacy" in res["error"], res
        assert not (job / "withdraw").exists() and not (job / "final.lock").exists()
        _age(job, 7 * 3600)  # past max age: still must not claim withdrawn while the old window lives
        assert ut.watch_once(job, {}) is None or ut.read_status(job)["status"] == "running"
        assert ut.read_status(job)["status"] == "running"
    finally:
        os.killpg(p.pid, 9)
        p.wait()
    st = ut.watch_once(job, {})  # the old window is gone: abandoned
    assert st and st["status"] == "abandoned", st


def test_unblock_in_same_second_before_request_keeps_prompt(tmp_path, monkeypatch):
    import sqlite3
    db = _board(tmp_path, "blocked", None)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_card', 'unblocked', ?)",
                (int(time.time()) + 1,))  # same (or a later) second as the request, but written BEFORE it
    con.commit()
    con.close()
    job = _owned_job(tmp_path, monkeypatch, db)
    meta = json.loads((job / "meta.json").read_text())
    assert isinstance(meta.get("board_event_cursor"), int)
    assert ut.watch_once(job, {}) is None
    con = sqlite3.connect(db)
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_card', 'unblocked', ?)",
                (int(time.time()),))
    con.commit()
    con.close()
    st = ut.watch_once(job, {})
    assert st and st["status"] == "withdrawn" and "unblocked" in st["reason"]


def test_legacy_owner_without_cursor_keeps_timestamp_fallback(tmp_path):
    import sqlite3
    db = _board(tmp_path, "blocked", None)
    created = time.time() - 60
    owner = {"kind": "card", "task_id": "t_card", "db": str(db)}
    assert ut._board_check(owner, created) is None
    con = sqlite3.connect(db)
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_card', 'unblocked', ?)",
                (int(time.time()),))
    con.commit()
    con.close()
    assert ut._board_check(owner, created) == "card t_card was unblocked after this request"


def test_identity_recorded_by_pre_fix_runner_still_verifies(tmp_path, monkeypatch, victim):
    # A window started before runner.sh pinned LC_ALL=C TZ=UTC recorded lstart in its own locale/zone; that live
    # runner must not read as dead (abandoned) after the upgrade. A different start time still never matches.
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    old = " ".join(subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(victim.pid)], capture_output=True,
                                  text=True).stdout.split())
    assert old and old != ut._proc_start(victim.pid)
    assert ut._same_process(victim.pid, old)
    assert not ut._same_process(victim.pid, "Mon Jan  1 00:00:00 2001")
