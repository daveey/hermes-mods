"""Aggregate preflight deadline and the real host's bounded policy dispatch."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest


def load_plugin():
    spec = importlib.util.spec_from_file_location("ship_budget_plugin", Path(__file__).with_name("__init__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registered_callback_has_finite_budget():
    p = load_plugin()
    hooks = []
    p.register(SimpleNamespace(register_hook=lambda *args: hooks.append(args)))
    assert hooks == [("pre_tool_call", p._pre_tool_call)]
    assert p.guard.PREFLIGHT_TIMEOUT == 120
    assert p._pre_tool_call.hermes_hook_timeout == 150


def test_aggregate_budget_clips_each_probe_and_never_allows_late(monkeypatch):
    g = load_plugin().guard
    clock = [0.0]
    monkeypatch.setattr(g.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(g, "PREFLIGHT_TIMEOUT", 5)
    timeouts = []
    def run(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock[0] += 3
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(g.subprocess, "run", run)
    def check():
        g._run_probe(["git", "version"], timeout=15)
        g._run_probe(["git", "version"], timeout=15)
    assert "deadline exceeded" in g.bounded_preflight(check)
    assert timeouts == [5, 2]
    assert g._PREFLIGHT_DEADLINE.get() is None


def test_expired_budget_starts_no_probe_and_resets_on_exception(monkeypatch):
    g = load_plugin().guard
    monkeypatch.setattr(g, "PREFLIGHT_TIMEOUT", 0)
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: pytest.fail("started after deadline"))
    assert "deadline exceeded" in g.bounded_preflight(lambda: g._git(".", "status"))
    assert g._PREFLIGHT_DEADLINE.get() is None
    def broken():
        raise RuntimeError("broken")
    with pytest.raises(RuntimeError, match="broken"):
        g.bounded_preflight(broken)
    assert g._PREFLIGHT_DEADLINE.get() is None


def test_real_subprocess_expiry_blocks_adapter(monkeypatch):
    p = load_plugin()
    monkeypatch.setattr(p.guard, "PREFLIGHT_TIMEOUT", 0.1)
    def check(*args):
        p.guard._run_probe([sys.executable, "-c", "import time; time.sleep(10)"], timeout=15)
    monkeypatch.setattr(p.guard, "check_tool", check)
    start = time.monotonic()
    result = p._pre_tool_call("terminal", {"command": "ignored fixture"})
    assert result["action"] == "block" and "deadline exceeded" in result["message"]
    assert time.monotonic() - start < 3


def test_nested_budget_does_not_extend_parent(monkeypatch):
    g = load_plugin().guard
    parent = time.monotonic() + 0.1
    token = g._PREFLIGHT_DEADLINE.set(parent)
    try:
        assert g.bounded_preflight(lambda: None) is None
        assert g._PREFLIGHT_DEADLINE.get() == parent
    finally:
        g._PREFLIGHT_DEADLINE.reset(token)


def test_standalone_deadline_returns_failure(monkeypatch, capsys):
    g = load_plugin().guard
    monkeypatch.setattr(g, "PREFLIGHT_TIMEOUT", 0)
    monkeypatch.setattr(g, "check_argv", lambda *a: None)
    assert g.main(["--gh-args", "pr", "merge", "12"]) == 1
    assert "deadline exceeded" in capsys.readouterr().err


def test_real_dispatch_honors_callback_budget_and_timeout_stays_closed(monkeypatch):
    plugins = pytest.importorskip("hermes_cli.plugins")
    dispatch = pytest.importorskip("hermes_cli.plugins_dispatch")
    p = load_plugin()
    manager = plugins.PluginManager()
    manager._hooks["pre_tool_call"] = [p._pre_tool_call]
    monkeypatch.setattr(plugins, "_resolve_hook_callback_timeout", lambda: 0.01)
    observed = []
    def slow(*args):
        observed.append(dispatch.current_hook_call().timeout)
        time.sleep(0.05)
        return None
    monkeypatch.setattr(p.guard, "check_tool", slow)
    assert not manager.invoke_hook("pre_tool_call", tool_name="terminal", args={})
    assert observed == [150]
    assert not manager._hook_timeout_suppressed_until
    # Scale only this fixture's declared budget down; exercise real abandonment.
    release = threading.Event()
    finished = threading.Event()
    def hung(*args):
        try:
            release.wait(3)
        finally:
            finished.set()
    monkeypatch.setattr(p.guard, "check_tool", hung)
    monkeypatch.setattr(p._pre_tool_call, "hermes_hook_timeout", 0.02)
    try:
        result = manager.invoke_hook("pre_tool_call", tool_name="terminal", args={})
        assert any(r.get("action") == "block" for r in result)
        assert manager._hook_timeout_suppressed_until
        result = manager.invoke_hook("pre_tool_call", tool_name="terminal", args={})
        assert any(r.get("action") == "block" for r in result)
    finally:
        release.set()
        assert finished.wait(3)
