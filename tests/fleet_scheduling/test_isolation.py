"""Containment must pass before scenario code may inject a fault."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

import pytest

from conftest import OwnedProcesses, fixture_env, identity, loopback_url, owned, preflight


def test_isolation_resolved_paths_and_preserved_fence(fleet_root, containment):
    from hermes_cli import kanban_db_fence
    from hermes_state import SessionDB
    marker = os.environ.get("HERMES_DELEGATED_CHILD_CONTEXT")
    if marker:
        with pytest.raises(PermissionError, match="cannot mutate"):
            kanban_db_fence.assert_not_fenced(Path(marker) / "kanban.db")
    with SessionDB(Path(os.environ["HERMES_HOME"]) / "state.db") as db:
        db.create_session("fleet-isolation", "desktop")
        assert db.get_session("fleet-isolation")
    output = Path(os.environ["FLEET_OUTPUT"])
    scenario = os.environ["FLEET_SCENARIO"]
    assert containment["scenario"] == scenario and containment["fixture_root"] == str(fleet_root)
    (output / f"{scenario}-preflight.json").write_text(json.dumps(containment, indent=2), encoding="utf-8")


def test_isolation_explicit_pin_rejected(fleet_root, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(fleet_root / "not-the-resolved-board.db"))
    with pytest.raises(AssertionError, match="explicit DB pins"):
        preflight(fleet_root)


def test_isolation_state_guard_not_disabled(fleet_root, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DB_GUARD_BYPASS", "1")
    with pytest.raises(AssertionError, match="bypass forbidden"):
        preflight(fleet_root)


def test_isolation_escape_and_symlink_refused(fleet_root):
    with pytest.raises(PermissionError, match="not a fixture"):
        owned(fleet_root, fleet_root.parent / "foreign")
    link = fleet_root / "escape"
    link.symlink_to(fleet_root.parent, target_is_directory=True)
    with pytest.raises(PermissionError, match="not a fixture"):
        owned(fleet_root, link / "foreign")


@pytest.mark.parametrize("url", ["https://example.invalid/v1", "http://192.0.2.1:123/v1",
                                    "http://localhost:123/v1", "http://user@127.0.0.1:123/v1"])
def test_isolation_nonloopback_provider_rejected(url):
    with pytest.raises(PermissionError):
        loopback_url(url)


def test_isolation_kernel_network_denies_external(containment):
    assert loopback_url("http://127.0.0.1:123/v1")
    with socket.socket() as sock:
        sock.settimeout(0.2)
        with pytest.raises(OSError):
            sock.connect(("192.0.2.1", 9))
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.create_connection(server.getsockname(), timeout=1) as client:
            peer, _ = server.accept()
            peer.close()
            assert client.getpeername() == server.getsockname()


def test_isolation_environment_drops_credentials_not_fences(fleet_root):
    source = {"PATH": os.defpath, "OPENAI_API_KEY": "not-real", "AWS_SECRET_ACCESS_KEY": "not-real",
              "HERMES_KANBAN_DB": "/foreign/board.db", "HTTP_PROXY": "http://foreign:123",
              "HERMES_DELEGATED_CHILD_CONTEXT": "/foreign", "HERMES_DELEGATED_CHILD_OWNER": "123:456",
              "HERMES_SESSION_ID": "foreign", "HERMES_KANBAN_WORKER_TASK": "t_foreign"}
    env = fixture_env(fleet_root, Path(os.environ["FLEET_CORE_SOURCE"]), source)
    assert env["HERMES_DELEGATED_CHILD_CONTEXT"] == source["HERMES_DELEGATED_CHILD_CONTEXT"]
    assert env["HERMES_DELEGATED_CHILD_OWNER"] == source["HERMES_DELEGATED_CHILD_OWNER"]
    assert env["OPENAI_API_KEY"] == "fixture-not-a-credential"
    for name in ("AWS_SECRET_ACCESS_KEY", "HERMES_KANBAN_DB", "HTTP_PROXY", "HERMES_SESSION_ID",
                 "HERMES_KANBAN_WORKER_TASK", "HERMES_STATE_DB_GUARD_BYPASS"):
        assert name not in env


def test_isolation_real_board_cli_uses_fixture_only(board_factory, fleet_root):
    board = board_factory("cli", "http://127.0.0.1:9/v1")
    tid = board.create("anonymous isolation fixture", "--no-requires")
    assert tid and board.task(tid)["title"] == "anonymous isolation fixture"
    assert board.db_path.resolve().is_relative_to(fleet_root.resolve())
    assert board.task(tid)["worker_pid"] is None
    assert board.runs(tid) == []
    assert len(board.tasks()) == 1


def test_isolation_signal_requires_registered_birth(fleet_root, containment):
    registry = OwnedProcesses(fleet_root)
    with pytest.raises(PermissionError):
        registry.register(os.getpid())
    with pytest.raises(PermissionError):
        registry.send(os.getpid(), signal.SIGKILL)
    proc = subprocess.Popen([sys.executable, "-c", "import os,time; print(os.environ.get('FLEET_FIXTURE_ROOT'), flush=True); time.sleep(30)"],
                            env=os.environ.copy(), stdout=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert proc.stdout.readline().strip() == str(fleet_root)
        registry.register(proc.pid)
        real = identity(proc.pid)
        registry.births[proc.pid] = real + "wrong"
        with pytest.raises(PermissionError, match="recycled"):
            registry.send(proc.pid, signal.SIGTERM)
        assert proc.poll() is None
        registry.births[proc.pid] = real
        registry.send(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=5) == -signal.SIGTERM
    finally:
        if proc.poll() is None:
            registry.births[proc.pid] = identity(proc.pid)
            registry.send(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Implementation mutations of the unsafe signal / path oracles. The real
# pidfd_send_signal is replaced by a spy for every case: no process, foreign or
# otherwise, is ever signalled by these tests.
# ---------------------------------------------------------------------------

def signal_oracle(registry_cls, root, monkeypatch, foreign_pid):
    calls = []
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda fd, sig: calls.append(sig))
    monkeypatch.setattr(os, "pidfd_open", lambda pid: os.open(os.devnull, os.O_RDONLY))
    violations = []
    registry = registry_cls(root)

    def must_refuse(label, action):
        before = len(calls)
        try:
            action()
        except PermissionError:
            if len(calls) != before:
                violations.append(f"{label}: signalled before refusing")
        else:
            violations.append(f"{label}: not refused")
    must_refuse("unregistered foreign pid", lambda: registry.send(foreign_pid, signal.SIGKILL))
    must_refuse("foreign registration", lambda: registry.register(foreign_pid))
    registry.births[foreign_pid] = identity(foreign_pid) + "x"  # forced recycled-PID shape
    must_refuse("recycled identity", lambda: registry.send(foreign_pid, signal.SIGKILL))
    must_refuse("runner self", lambda: registry.register(os.getpid()))
    if calls:
        violations.append(f"signals delivered to refused pids: {calls}")
    return violations


class MutantNoBirth(OwnedProcesses):
    def send(self, pid, sig):  # drops the registered-birth check
        fd = os.pidfd_open(pid)
        try:
            signal.pidfd_send_signal(fd, sig)
        finally:
            os.close(fd)


class MutantNoMarker(OwnedProcesses):
    def register(self, pid):  # drops the fixture-marker environment check
        self.births[pid] = identity(pid)


class MutantRecycledAllowed(OwnedProcesses):
    def send(self, pid, sig):  # checks registration but not start identity
        if pid not in self.births:
            raise PermissionError("unregistered")
        fd = os.pidfd_open(pid)
        try:
            signal.pidfd_send_signal(fd, sig)
        finally:
            os.close(fd)


def path_oracle(fn, root):
    violations = []
    link = root / "mut-escape"
    if not link.exists():
        link.symlink_to(root.parent, target_is_directory=True)
    sibling = Path(str(root) + "-sibling")
    for label, candidate in (("symlink escape", link / "x"), ("prefix sibling", sibling / "x"),
                             ("fixture root itself", root), ("parent", root.parent)):
        try:
            fn(root, candidate)
        except PermissionError:
            continue
        violations.append(f"{label} accepted")
    return violations


def mutant_prefix_owned(root, path):  # lexical prefix, no resolve
    if not str(path).startswith(str(root)) or Path(path) == Path(root):
        raise PermissionError("not a fixture-owned child")
    return path


def mutant_no_resolve_owned(root, path):  # proper-parent test without symlink resolution
    if Path(root) not in Path(path).parents:
        raise PermissionError("not a fixture-owned child")
    return path


def test_isolation_signal_oracle_accepts_real_and_rejects_mutants(fleet_root, containment, monkeypatch):
    foreign = subprocess.Popen(["sleep", "30"], env={"PATH": os.defpath})  # no fixture marker
    try:
        assert signal_oracle(OwnedProcesses, fleet_root, monkeypatch, foreign.pid) == []
        for mutant in (MutantNoBirth, MutantNoMarker, MutantRecycledAllowed):
            assert signal_oracle(mutant, fleet_root, monkeypatch, foreign.pid), mutant.__name__
        assert foreign.poll() is None  # never signalled
    finally:
        foreign.kill()
        foreign.wait(timeout=5)


def test_isolation_path_oracle_accepts_real_and_rejects_mutants(fleet_root):
    assert path_oracle(owned, fleet_root) == []
    assert path_oracle(mutant_prefix_owned, fleet_root)
    assert path_oracle(mutant_no_resolve_owned, fleet_root)


# ---------------------------------------------------------------------------
# Production-registration exclusion with disposable sentinel stores/transports.
# ---------------------------------------------------------------------------

def test_isolation_production_registration_excluded(fleet_root, containment, tmp_path, monkeypatch):
    store = tmp_path / "sentinel-prod"
    for name in ("home", "hermes", "kanban", "hub"):
        (store / name).mkdir(parents=True)
        (store / name / "sentinel.db").write_text("sentinel", encoding="utf-8")
    sock_path = tmp_path / "sentinel.sock"
    accepted = []
    server = socket.socket(socket.AF_UNIX)
    monkeypatch.chdir(tmp_path)  # AF_UNIX path limit: bind by relative name
    server.bind(sock_path.name)
    server.listen()
    server.settimeout(0.05)
    inherited = {"PATH": os.defpath, "HOME": str(store / "home"), "HERMES_HOME": str(store / "hermes"),
                 "HERMES_KANBAN_HOME": str(store / "kanban"), "HERMES_HUB_STATE": str(store / "hub"),
                 "HERMES_KANBAN_DB": str(store / "kanban" / "kanban.db"),
                 "HERMES_GATEWAY_URL": "http://127.0.0.1:1", "HERMES_REGISTRATION_URL": f"unix:{sock_path}",
                 "DBUS_SESSION_BUS_ADDRESS": f"unix:path={sock_path}", "XDG_RUNTIME_DIR": str(store),
                 "XDG_CONFIG_HOME": str(store), "SSH_AUTH_SOCK": str(sock_path)}
    before = {p: p.read_bytes() for p in store.rglob("sentinel.db")}
    env = fixture_env(fleet_root, Path(os.environ["FLEET_CORE_SOURCE"]), inherited)
    for forbidden in ("HERMES_KANBAN_DB", "HERMES_GATEWAY_URL", "HERMES_REGISTRATION_URL",
                      "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK"):
        assert forbidden not in env
    assert not any(str(store) in value for value in env.values())
    probe = ("import json,os;from hermes_cli import kanban_db as kb;"
             "from tui_gateway import kanban_remote_notices as n;"
             "print(json.dumps([str(kb.kanban_db_path()),str(n.ledger_path()),str(n.hub_db_path())]))")
    for name in ("HOME", "HERMES_HOME", "HERMES_KANBAN_HOME", "HERMES_HUB_STATE", "TMPDIR"):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    env["PYTHONPATH"] = os.environ["FLEET_CORE_SOURCE"]
    out = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    resolved = json.loads(out.stdout.strip().splitlines()[-1])
    assert all(Path(item).resolve().is_relative_to(fleet_root.resolve()) for item in resolved), resolved
    try:
        server.accept()
        accepted.append(1)
    except (socket.timeout, BlockingIOError):
        pass
    server.close()
    assert accepted == []
    assert {p: p.read_bytes() for p in store.rglob("sentinel.db")} == before
