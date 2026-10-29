"""Tests for mac-heavy-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("mac_heavy_guard_plugin", "__init__.py")
guard = plugin.guard


@pytest.fixture(autouse=True)
def on_mac(monkeypatch):
    monkeypatch.setattr(guard.sys, "platform", "darwin")
    monkeypatch.delenv("MAC_HEAVY_OK", raising=False)
    monkeypatch.delenv("MAC_HEAVY_GUARD", raising=False)


def blocked(tool, **args):
    return plugin._pre_tool_call(tool_name=tool, args=args) is not None


HEAVY = [
    "cargo build --release",
    "cd ~/code/app && cargo build --release --target x86_64-apple-darwin",
    "cargo test -r",
    "cargo bench",
    "cargo test --workspace",
    "docker build -t x .",
    "docker buildx build --platform linux/amd64 .",
    "docker compose up --build",
    "pytest",
    "python -m pytest -q",
    "uv run pytest -x -q",
    "pytest -n auto tests/",
    "python3 -m pytest -n8",
    "npm test",
    "pnpm run test",
    "npx vitest run",
    "vitest",
    "npm install",
    "npm ci",
    "pnpm i",
    "yarn",
    "uv sync",
    "pip install -r requirements.txt",
    "poetry install",
    "make test",
    "nice -n 5 cargo build --release",
    "timeout 600 pytest",
    "FOO=1 cargo build --release",
    "bash -lc 'cd repo && npm ci'",
    "git pull && pnpm install && pnpm test",
]

LIGHT = [
    "cargo build",
    "cargo check",
    "cargo test -p foo some_test",
    "pytest tests/test_x.py -q",
    "pytest tests/tools/ -k guard",
    "python -m pytest test_mac_load_recorder.py",
    "pytest -k slow_case",
    "uv run pytest tests/test_a.py::test_b",
    "npx vitest run src/foo.test.ts",
    "vitest -t 'renders'",
    "npm test -- src/foo.test.ts",
    "npm install left-pad",
    "pnpm add -D typescript",
    "pip install requests",
    "docker ps",
    "make lint",
    "ssh ubuntu@hermes-hub 'cd repo && cargo build --release'",
    "echo 'cargo build --release'",
    "git commit -m 'run pytest and npm install'",
    "rg 'docker build' docs/",
    "ls -la",
]


@pytest.mark.parametrize("cmd", HEAVY)
def test_blocks_heavy_terminal(cmd):
    assert blocked("terminal", command=cmd), cmd


@pytest.mark.parametrize("cmd", LIGHT)
def test_allows_light_terminal(cmd):
    assert not blocked("terminal", command=cmd), cmd


def test_opt_out_prefix_and_env(monkeypatch):
    assert not blocked("terminal", command="MAC_HEAVY_OK=1 cargo build --release")
    assert not blocked("terminal", command="env MAC_HEAVY_OK=1 pytest")
    assert blocked("terminal", command="MAC_HEAVY_OK=0 cargo build --release")
    monkeypatch.setenv("MAC_HEAVY_OK", "1")
    assert not blocked("terminal", command="cargo build --release")


def test_off_switch_and_non_mac(monkeypatch):
    monkeypatch.setenv("MAC_HEAVY_GUARD", "0")
    assert not blocked("terminal", command="cargo build --release")
    monkeypatch.delenv("MAC_HEAVY_GUARD")
    monkeypatch.setattr(guard.sys, "platform", "linux")
    assert not blocked("terminal", command="cargo build --release")


def test_message_names_routes_and_opt_out():
    res = plugin._pre_tool_call(tool_name="terminal", args={"command": "cargo build --release"})
    assert res["action"] == "block"
    for needle in ("cargo build --release", "worker-1", "hermes-hub", "MAC_HEAVY_OK=1", "heavy_route"):
        assert needle in res["message"]


def test_tool_call_batch_and_other_tools():
    assert blocked("tool_call", calls=[{"name": "terminal", "arguments": {"command": "uv sync"}}])
    assert not blocked("read_file", path="Cargo.toml")
    assert not blocked("terminal", command="echo 'unbalanced")


def _hook(cmd, env_extra=None):
    import os
    env = dict(os.environ, **(env_extra or {}))
    env.pop("MAC_HEAVY_OK", None)
    return subprocess.run([sys.executable, str(HERE / "claude_hook.py")], input=json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": cmd}}), capture_output=True, text=True, env=env)


@pytest.mark.skipif(sys.platform != "darwin", reason="guard only acts on macOS")
def test_claude_hook_blocks_and_allows():
    r = _hook("cargo build --release")
    assert r.returncode == 2 and "MAC_HEAVY_OK=1" in r.stderr
    assert _hook("pytest tests/test_x.py").returncode == 0
    assert _hook("MAC_HEAVY_OK=1 npm ci").returncode == 0
