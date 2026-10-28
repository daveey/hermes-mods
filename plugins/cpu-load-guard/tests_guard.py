"""Tests for cpu-load-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cpu_load_guard_plugin", HERE / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


@pytest.fixture(autouse=True)
def protected(monkeypatch):
    monkeypatch.setenv("CPU_LOAD_GUARD_HOSTS", plugin.guard.socket.gethostname())
    monkeypatch.setenv("CPU_LOAD_GUARD_NCPU", "16")
    monkeypatch.delenv("CPU_LOAD_GUARD", raising=False)


def run(tool, args):
    return plugin._pre_tool_call(tool_name=tool, args=args)


BAD = [
    "for i in $(seq 1 $(nproc)); do yes >/dev/null & done",
    "for i in $(seq 1 16); do yes > /dev/null & done",
    "for i in {1..16}; do yes >/dev/null & done; sleep 60",
    "n=$(sysctl -n hw.ncpu); for i in $(seq $n); do yes >/dev/null & done",
    "stress-ng --cpu $(nproc) --timeout 60s",
    "stress-ng --cpu 0",
    "stress --cpu 16 --timeout 30",
    "openssl speed -multi 16 sha256",
    "openssl speed -multi $(nproc)",
    "seq 16 | xargs -P 16 -I{} yes >/dev/null",
    "for i in $(seq 1 12); do while :; do :; done & done",
    "for ((i=0;i<16;i++)); do cat /dev/zero >/dev/null & done",
    "yes>/dev/null & " * 8,
]
GOOD = [
    "pytest -n 2",
    "pytest -n $(nproc) tests/",
    "make -j$(nproc)",
    "yes | head -5",
    "yes >/dev/null &",
    "for i in 1 2; do yes >/dev/null & done",
    "stress-ng --cpu 2 --timeout 5s",
    "openssl speed -multi 2 sha256",
    "echo yes; grep -c yes file",
    "seq 1 100 | xargs -P 2 echo",
    "for i in $(seq 1 $(nproc)); do echo $i; done",
]


@pytest.mark.parametrize("cmd", BAD)
def test_terminal_blocks(cmd):
    r = run("terminal", {"command": cmd})
    assert r and r["action"] == "block" and "hermes-hub" in r["message"]


@pytest.mark.parametrize("cmd", BAD)
def test_background_terminal_blocks(cmd):
    r = run("terminal", {"command": cmd, "background": True, "notify": True})
    assert r and r["action"] == "block"


@pytest.mark.parametrize("cmd", GOOD)
def test_terminal_allows(cmd):
    assert run("terminal", {"command": cmd}) is None
    assert run("terminal", {"command": cmd, "background": True}) is None


EXEC_BAD = [
    "import subprocess\nsubprocess.Popen('stress-ng --cpu 16', shell=True)",
    "from hermes_tools import terminal\nterminal('for i in $(seq 1 $(nproc)); do yes >/dev/null & done', background=True)",
    "import subprocess, os\nprocs=[subprocess.Popen(['yes'], stdout=subprocess.DEVNULL) for _ in range(os.cpu_count())]",
    "import multiprocessing as mp\ndef burn():\n    while True:\n        pass\nps=[mp.Process(target=burn) for _ in range(mp.cpu_count())]",
    "import subprocess\nfor _ in range(16):\n    subprocess.Popen(['yes'], stdout=subprocess.DEVNULL)",
]


@pytest.mark.parametrize("code", EXEC_BAD)
def test_execute_code_blocks(code):
    r = run("execute_code", {"code": code})
    assert r and r["action"] == "block"


def test_execute_code_allows():
    assert run("execute_code", {"code": "import subprocess\nsubprocess.run(['pytest','-n','2'])"}) is None
    assert run("execute_code", {"code": "import os\nprint(os.cpu_count())"}) is None
    assert run("execute_code", {"code": "x=[i*i for i in range(1000)]"}) is None


def test_wrapped_tool_call_and_process_write():
    r = run("tool_call", {"calls": [{"name": "mcp__terminal", "arguments": {"command": "stress-ng --cpu 16"}}]})
    assert r and r["action"] == "block"
    assert run("process_manage", {"action": "submit", "data": "stress-ng --cpu 16"})


def test_unprotected_host_allows(monkeypatch):
    monkeypatch.setenv("CPU_LOAD_GUARD_HOSTS", "some-other-host")
    assert run("terminal", {"command": "stress-ng --cpu $(nproc)"}) is None


def test_off_switch(monkeypatch):
    monkeypatch.setenv("CPU_LOAD_GUARD", "off")
    assert run("terminal", {"command": "stress-ng --cpu $(nproc)"}) is None


def test_default_protects_laptop(monkeypatch):
    monkeypatch.delenv("CPU_LOAD_GUARD_HOSTS")
    monkeypatch.setenv("HERMES_HOME", "/nonexistent")
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "David-MBP.local")
    assert plugin.guard.enabled()
    monkeypatch.setattr(plugin.guard.socket, "gethostname", lambda: "hermes-hub")
    assert not plugin.guard.enabled()
