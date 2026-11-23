"""Real procfs regression controls for check-all-before-signal cleanup.

FLEET_CLEANUP_RUNNER selects historical source for the identical RED run.
No procfs responses are mocked; the send seam controls only owned producers.
"""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import OwnedProcesses, identity


def runner_module():
    source = os.environ.get("FLEET_CLEANUP_RUNNER", str(Path(__file__).parents[2] / "scripts/run-fleet-scheduling"))
    loader = importlib.machinery.SourceFileLoader("cleanup_phases_under_test", source)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


PROGRAM = """
import ctypes, os, sys, threading

def commands():
    print('ready', flush=True)
    for line in sys.stdin:
        if line.strip() == 'exit':
            os._exit(0)
        if line.strip() == 'private':
            assert ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) == 0
            print('private', flush=True)
            continue
        if line.strip() == 'threads':
            threading.Thread(target=lambda: threading.Event().wait(60)).start()
            print('threaded', flush=True)
            ctypes.CDLL(None).pthread_exit(None)
        os.chdir(line.strip())
        print('changed', flush=True)

if sys.argv[1] == 'threads':
    t = threading.Thread(target=commands)
    t.start()
    ctypes.CDLL(None).pthread_exit(None)
else:
    commands()
"""


@pytest.fixture
def box(tmp_path, monkeypatch):
    runner = runner_module()
    root = tmp_path / "fx"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    processes = []
    calls = []
    real_send = OwnedProcesses.send
    assert "OwnedProcesses" in runner.cleanup_processes.__code__.co_names
    # cleanup imports this exact conftest class, never a test replacement registry.
    assert sys.modules["conftest"].OwnedProcesses is OwnedProcesses

    kernel_signals = []
    runner._kernel_signals = kernel_signals
    pidfd_send = signal.pidfd_send_signal

    def kernel_spy(*args, **kwargs):
        kernel_signals.append(args)
        return pidfd_send(*args, **kwargs)

    def spy(registry, pid, sig):
        assert type(registry) is sys.modules["conftest"].OwnedProcesses
        calls.append(pid)
        return real_send(registry, pid, sig)

    monkeypatch.setattr(signal, "pidfd_send_signal", kernel_spy)
    monkeypatch.setattr(OwnedProcesses, "send", spy)
    real_inventory = runner.inventory
    monkeypatch.setattr(runner, "inventory", lambda path: sorted(real_inventory(path), key=lambda row: row[0]))

    def spawn(marker=False, home="runtime", foreign=False, threads=False):
        env = {"PATH": os.defpath, "HERMES_HOME": str(root / home)}
        if marker:
            env["FLEET_FIXTURE_ROOT"] = str(root if marker is True else tmp_path / "other")
        proc = subprocess.Popen([sys.executable, "-c", PROGRAM, "threads" if threads else "normal"],
                                env=env, cwd=tmp_path if foreign else workspace,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        processes.append(proc)
        assert proc.stdout.readline() == b"ready\n"
        return proc

    yield runner, root, spawn, calls, real_send
    for proc in processes:
        # Kill only these exact children, and join/reap before monkeypatch teardown.
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        proc.stdin.close()
        proc.stdout.close()


def exited(proc, reap=False):
    proc.stdin.write(b"exit\n")
    proc.stdin.flush()
    fd = os.pidfd_open(proc.pid)
    try:
        assert select.select([fd], [], [], 5)[0], "producer did not exit"
    finally:
        os.close(fd)
    if reap:
        assert proc.wait(timeout=5) == 0


@pytest.mark.parametrize("marker,reap", [(False, False), (False, True), (True, False)])
def test_owner_signal_child_exit_boundary(box, monkeypatch, marker, reap):
    runner, root, spawn, calls, send = box
    parent, child = spawn(marker=True), spawn(marker=marker)

    def seam(registry, pid, sig):
        calls.append(pid)
        send(registry, pid, sig)
        if pid == parent.pid:
            exited(child, reap)
    monkeypatch.setattr(OwnedProcesses, "send", seam)
    result = runner.cleanup_processes(root)
    child.wait(timeout=5)
    assert result["errors"] == [], result
    assert result["fixture_processes_remaining"] == []


def test_thread_group_with_exited_leader_is_killed(box, monkeypatch):
    runner, root, spawn, calls, send = box
    parent, child = spawn(marker=True), spawn()

    def seam(registry, pid, sig):
        calls.append(pid)
        send(registry, pid, sig)
        if pid == parent.pid:
            child.stdin.write(b"threads\n")
            child.stdin.flush()
            assert child.stdout.readline() == b"threaded\n"
            import time
            deadline = time.monotonic() + 5
            while Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert len(list(Path(f"/proc/{child.pid}/task").iterdir())) == 2
    monkeypatch.setattr(OwnedProcesses, "send", seam)
    result = runner.cleanup_processes(root)
    assert not result["errors"], result
    assert child.wait(timeout=5) == -signal.SIGKILL
    assert child.pid in calls


def test_cwd_change_after_registration_is_declared_relaxation(box, monkeypatch, tmp_path):
    runner, root, spawn, calls, send = box
    parent, child = spawn(marker=True), spawn()

    def seam(registry, pid, sig):
        calls.append(pid)
        send(registry, pid, sig)
        if pid == parent.pid:
            child.stdin.write(os.fsencode(tmp_path) + b"\n")
            child.stdin.flush()
            assert child.stdout.readline() == b"changed\n"
    monkeypatch.setattr(OwnedProcesses, "send", seam)
    result = runner.cleanup_processes(root)
    assert result["errors"] == [], result
    assert child.wait(timeout=5) == -signal.SIGKILL
    assert set(calls) == {parent.pid, child.pid}


def test_registers_every_candidate_before_first_signal(box, monkeypatch):
    runner, root, spawn, calls, send = box
    first, last = spawn(marker=True), spawn()
    registered = []
    register = OwnedProcesses.register

    def checked(registry, pid):
        register(registry, pid)
        registered.append(pid)

    def seam(registry, pid, sig):
        assert registered == [first.pid, last.pid]
        calls.append(pid)
        send(registry, pid, sig)
    monkeypatch.setattr(OwnedProcesses, "register", checked)
    monkeypatch.setattr(OwnedProcesses, "send", seam)
    result = runner.cleanup_processes(root)
    assert not result["errors"], result


@pytest.mark.parametrize("kind", ["foreign_cwd", "wrong_home", "foreign_marker"])
def test_live_invalid_custody_retains_exact_errors_every_pass(box, kind):
    runner, root, spawn, calls, send = box
    proc = spawn(foreign=kind == "foreign_cwd", home="not-runtime" if kind == "wrong_home" else "runtime",
                 marker="foreign" if kind == "foreign_marker" else False)
    result = runner.cleanup_processes(root)
    assert result["errors"] == [f"fixture descendant {proc.pid} lost ownership marker; not signalled"] * 5 + [
        f"fixture descendants remain: [{proc.pid}]"]
    assert calls == [] and runner._kernel_signals == [] and proc.poll() is None
    diagnostics = result["ownership_diagnostics"]
    assert len(diagnostics) == 5
    assert [item["pass"] for item in diagnostics] == list(range(1, 6))
    assert all(item["phase"] == 1 and item["signals_sent"] == 0 for item in diagnostics)
    assert all(item["before"]["stat"]["start_time"] == identity(proc.pid) for item in diagnostics)


def test_reaped_after_inventory_still_fails_closed_with_diagnostics(box, monkeypatch):
    runner, root, spawn, calls, send = box
    proc = spawn()
    inventory = runner.inventory

    def seam(path):
        rows = inventory(path)
        if rows:
            exited(proc, reap=True)
        return rows
    monkeypatch.setattr(runner, "inventory", seam)
    result = runner.cleanup_processes(root)
    assert result["errors"] == [f"fixture descendant {proc.pid} lost ownership marker; not signalled"]
    assert calls == [] and runner._kernel_signals == []
    entry = result["ownership_diagnostics"][0]
    assert entry["before"]["cwd"]["errno"] == 2
    assert entry["after"]["stat"]["errno"] == 2


def test_registered_identity_mismatch_remains_refused(box, monkeypatch):
    runner, root, spawn, calls, send = box
    proc = spawn(marker=True)

    def seam(registry, pid, sig):
        registry.births[pid] = str(int(registry.births[pid]) + 1)
        send(registry, pid, sig)
    monkeypatch.setattr(OwnedProcesses, "send", seam)
    result = runner.cleanup_processes(root)
    assert result["errors"] == [f"pid {proc.pid} refused: unregistered or recycled PID"] * 5 + [
        f"fixture descendants remain: [{proc.pid}]"]
    assert proc.poll() is None and result["signalled"] == [] and runner._kernel_signals == []
    assert len(result["ownership_diagnostics"]) == 5
    assert all(item["phase"] == 2 for item in result["ownership_diagnostics"])


def test_unreadable_live_cwd_after_inventory_remains_error(box, monkeypatch):
    runner, root, spawn, calls, send = box
    proc = spawn()
    inventory = runner.inventory
    triggered = []

    def seam(path):
        rows = inventory(path)
        if rows and not triggered:
            triggered.append(True)
            proc.stdin.write(b"private\n")
            proc.stdin.flush()
            assert proc.stdout.readline() == b"private\n"
            with pytest.raises(PermissionError):
                os.readlink(f"/proc/{proc.pid}/cwd")
        return rows
    monkeypatch.setattr(runner, "inventory", seam)
    result = runner.cleanup_processes(root)
    assert result["errors"] == [f"fixture descendant {proc.pid} lost ownership marker; not signalled"]
    assert proc.poll() is None and calls == [] and runner._kernel_signals == []
    diagnostics = result["ownership_diagnostics"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["before"]["cwd"]["errno"] == 13
    assert diagnostics[0]["after"]["cwd"]["errno"] == 13
    assert diagnostics[0]["exception_type"] == "PermissionError"


def test_stale_inventory_foreign_live_pid_never_signalled(box, monkeypatch):
    runner, root, spawn, calls, send = box
    proc = spawn(home="foreign", foreign=True)
    stale_env = {b"HERMES_HOME": os.fsencode(root / "runtime")}
    # A stale observation names a real live foreign process, not a mocked procfs.
    monkeypatch.setattr(runner, "inventory", lambda path: [(proc.pid, stale_env)])
    result = runner.cleanup_processes(root)
    assert result["errors"] == [f"fixture descendant {proc.pid} lost ownership marker; not signalled"] * 5 + [
        f"fixture descendants remain: [{proc.pid}]"]
    assert proc.poll() is None and calls == [] and runner._kernel_signals == []
    assert len(result["ownership_diagnostics"]) == 5


def test_owned_live_set_is_unchanged(box):
    runner, root, spawn, calls, send = box
    first, second = spawn(marker=True), spawn()
    result = runner.cleanup_processes(root)
    assert not result["errors"], result
    assert {row["pid"] for row in result["signalled"]} == {first.pid, second.pid}
    assert set(calls) == {first.pid, second.pid}
