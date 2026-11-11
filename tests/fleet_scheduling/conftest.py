"""Fleet fixture containment. No production guards are disabled here.

The launcher supplies a disposable root outside any inherited Kanban fence.
HERMES_KANBAN_DB is intentionally absent: core fences any explicit worker pin,
even a scratch one. The resolved default board is asserted before initialization.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket

import sys
from urllib.parse import urlsplit

import pytest


def owned(root: Path, path: Path) -> Path:
    root, path = root.resolve(), path.resolve()
    if path == root or root not in path.parents:
        raise PermissionError(f"not a fixture-owned child: {path}")
    return path


def loopback_url(url: str) -> str:
    value = urlsplit(url)
    if value.scheme != "http" or value.username or value.password or not value.port:
        raise PermissionError("provider must be an explicit loopback HTTP endpoint")
    try:
        valid = ipaddress.ip_address(value.hostname or "").is_loopback
    except ValueError:
        valid = False
    if not valid:
        raise PermissionError("non-loopback provider denied")
    return url


def identity(pid: int) -> str:
    # Fields after comm start with field 3; starttime is field 22.
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    return fields[19]


class OwnedProcesses:
    """Only an observed, fixture-rooted process with unchanged birth may be signalled."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.births: dict[int, str] = {}

    def register(self, pid: int) -> None:
        if pid in (1, os.getpid(), os.getppid()):
            raise PermissionError("runner/parent registration denied")
        birth = identity(pid)
        raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        env = dict(part.split(b"=", 1) for part in raw if b"=" in part)
        if env.get(b"FLEET_FIXTURE_ROOT") != os.fsencode(self.root):
            raise PermissionError("foreign process registration denied")
        owned(self.root, Path(os.fsdecode(env[b"HERMES_HOME"])))
        if identity(pid) != birth:
            raise PermissionError("PID changed during fixture registration")
        self.births[pid] = birth

    def send(self, pid: int, sig: int) -> None:
        if pid not in self.births or identity(pid) != self.births[pid]:
            raise PermissionError("unregistered or recycled PID")
        # pidfd binds the signal to this process, including the race after identity read.
        fd = os.pidfd_open(pid)
        try:
            if identity(pid) != self.births[pid]:
                raise PermissionError("PID identity changed before signalling")
            signal.pidfd_send_signal(fd, sig)
        finally:
            os.close(fd)


def load_board(core: Path):
    source = core / "tests/e2e/core/kanban/_helpers.py"
    spec = importlib.util.spec_from_file_location("fleet_core_board", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Board


def fixture_env(root: Path, core: Path, inherited=None) -> dict[str, str]:
    inherited = os.environ if inherited is None else inherited
    # Deliberate allowlist: never inherit provider credentials, routing, proxies,
    # session identities, worker pins or production registration information.
    env = {key: inherited[key] for key in ("PATH", "LANG", "TZ") if key in inherited}
    for key in ("HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_DELEGATED_CHILD_OWNER"):
        if key in inherited:
            env[key] = inherited[key]
    env.update({
        "HOME": str(root / "home"), "HERMES_HOME": str(root / "runtime"),
        "HERMES_KANBAN_HOME": str(root / "board"),
        "HERMES_HUB_STATE": str(root / "hub"), "TMPDIR": str(root / "tmp"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_RUNTIME_DIR": str(root / "ipc"),
        "XDG_STATE_HOME": str(root / "state"), "PYTHONPATH": str(core),
        "PYTEST_VERSION": "fleet", "FLEET_FIXTURE_ROOT": str(root),
        "NO_PROXY": "127.0.0.1,::1", "NO_COLOR": "1", "TERM": "dumb",
        "OPENAI_API_KEY": "fixture-not-a-credential",
    })
    return env


def preflight(root: Path) -> dict:
    from agent.delegation_context import kanban_path_is_fenced
    from hermes_cli import kanban_db as kb
    from tui_gateway import kanban_remote_notices as notices

    root = root.resolve()
    paths = {name: owned(root, Path(os.environ[name])) for name in (
        "HOME", "HERMES_HOME", "HERMES_KANBAN_HOME", "HERMES_HUB_STATE")}
    assert "HERMES_KANBAN_DB" not in os.environ, "explicit DB pins are forbidden in fixtures"
    assert not os.environ.get("HERMES_STATE_DB_GUARD_BYPASS"), "state guard bypass forbidden"
    paths.update({"resolved_db": owned(root, kb.kanban_db_path()),
                  "resolved_ledger": owned(root, notices.ledger_path()),
                  "resolved_replica": owned(root, notices.hub_db_path()),
                  "resolved_workspaces": owned(root, kb.workspaces_root())})
    assert paths["resolved_db"] == paths["HERMES_KANBAN_HOME"] / "kanban.db"
    assert not kanban_path_is_fenced(paths["resolved_db"]), "fixture falls under inherited fence"
    # Require kernel network isolation, not a Python mock that child processes evade.
    interfaces = sorted(name for _, name in socket.if_nameindex())
    assert interfaces == ["lo"], f"requires private loopback-only netns, got {interfaces}"
    assert not Path(f"/run/user/{os.getuid()}/bus").exists(), "production user manager visible"
    assert not Path("/run/dbus/system_bus_socket").exists(), "production system bus visible"
    marker = os.environ.get("HERMES_DELEGATED_CHILD_CONTEXT")
    if marker:
        assert marker != "1", "legacy all-board fence cannot be bypassed"
        assert kanban_path_is_fenced(Path(marker) / "kanban.db"), "inherited fence lost"
    return {"paths": {key: str(value) for key, value in paths.items()},
            "network_interfaces": interfaces, "explicit_db_pin": None,
            "scenario": os.environ.get("FLEET_SCENARIO"), "fixture_root": str(root),
            "descendant_fence": marker, "guard_bypass": False}


@pytest.fixture
def fleet_root():
    value = os.environ.get("FLEET_FIXTURE_ROOT")
    if not value:
        pytest.fail("use scripts/run-fleet-scheduling; no ambient-home test execution")
    return Path(value)


@pytest.fixture
def containment(fleet_root):
    return preflight(fleet_root)


@pytest.fixture
def board_factory(fleet_root, containment):
    core = Path(os.environ["FLEET_CORE_SOURCE"])
    Base = load_board(core)


    class SafeBoard(Base):
        def __post_init__(self):
            loopback_url(self.base_url)
            owned(fleet_root, self.root)
            super().__post_init__()
            # Preserve the generated fixture config, but avoid HOME/.hermes/state.db
            # (correctly rejected as a default home by the core test guard).
            self.hermes_home.rename(self.root / "runtime")
            self.hermes_home = self.root / "runtime"
            self.registry = OwnedProcesses(self.root)

        def env(self):
            env = fixture_env(self.root, core)
            for name in ("HOME", "HERMES_KANBAN_HOME", "HERMES_HUB_STATE", "TMPDIR"):
                Path(env[name]).mkdir(parents=True, exist_ok=True)
            env["HERMES_BIN"] = str(self.hermes_bin)
            env.update(self.env_extra)
            assert not env.get("HERMES_STATE_DB_GUARD_BYPASS")
            assert "HERMES_KANBAN_DB" not in env
            for key in ("HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_DELEGATED_CHILD_OWNER"):
                assert env.get(key) == os.environ.get(key)
            return env

        @property
        def db_path(self):
            return self.root / "board" / "kanban.db"

        def kill_workers(self):
            # No raw Board.kill_workers: its PID set does not prove birth identity.
            for pid in tuple(self.registry.births):
                try:
                    self.registry.send(pid, signal.SIGKILL)
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    pass

        def dispatch(self, *args):
            out = super().dispatch(*args)
            for pid in self.spawned_pids:
                if pid not in self.registry.births:
                    self.registry.register(pid)
            return out

    boards = []
    def create(name, url, **kwargs):
        result = SafeBoard(fleet_root / name, url, **kwargs)
        boards.append(result)
        return result
    try:
        yield create
    finally:
        for board in boards:
            board.kill_workers()
