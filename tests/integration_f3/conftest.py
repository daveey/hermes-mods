"""Opt-in, checkout-explicit integration with no live Hermes profile."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

import pytest


def pytest_addoption(parser):
    parser.addoption("--hermes-core", help="Explicit Hermes core source checkout (or HERMES_CORE_CHECKOUT)")


def pytest_configure(config):
    value = config.getoption("--hermes-core") or os.environ.get("HERMES_CORE_CHECKOUT")
    if not value:
        raise pytest.UsageError("integration_f3 requires --hermes-core=/path/to/core or HERMES_CORE_CHECKOUT; no live checkout fallback")
    root = Path(value).expanduser().resolve()
    for relative in ("run_agent.py", "hermes_cli/plugins.py", "agent/pre_final_gate.py"):
        if not (root / relative).is_file():
            raise pytest.UsageError(f"Not a compatible core checkout: {root} (missing {relative})")
    # Set before importing core: import-time config/logging must never use a live home.
    sandbox = tempfile.TemporaryDirectory(prefix="f3-integration-")
    old_env = dict(os.environ)
    os.environ.clear()
    for key in ("PATH", "LANG", "TMPDIR", "SYSTEMROOT"):
        if key in old_env:
            os.environ[key] = old_env[key]
    os.environ.update(HOME=sandbox.name, HERMES_HOME=sandbox.name,
                      HERMES_RUNTIME_DIR=str(Path(sandbox.name) / "runtime"),
                      ASK_GATE_NO_PREWARM="1", HERMES_VERIFY_ON_STOP="0")
    sys.path.insert(0, str(root))
    config._f3_core = root
    network = {"active": True, "attempts": []}

    def deny_network(event, args):
        if network["active"] and event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            network["attempts"].append(event)
            raise RuntimeError("F3 integration forbids network access")
    sys.addaudithook(deny_network)
    config._f3_network = network

    def cleanup():
        network["active"] = False
        sys.path.remove(str(root))
        os.environ.clear()
        os.environ.update(old_env)
        sandbox.cleanup()
    config.add_cleanup(cleanup)


@pytest.fixture
def harness(tmp_path, monkeypatch, request):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_cli import plugins
    from run_agent import AIAgent
    assert Path(sys.modules["run_agent"].__file__).resolve() == request.config._f3_core / "run_agent.py"

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_USER_STEPS_FRAME", "1")
    enabled = getattr(request, "param", ["ask-gate", "user-steps"])
    home.joinpath("config.yaml").write_text(
        f"plugins:\n  enabled: [{', '.join(enabled)}]\nagent:\n  pre_final_gate_max: 1\n"
        "model:\n  default: test/model\n  provider: openai-compat\n"
        "  base_url: https://example.invalid/v1\n  context_length: 128000\n"
        "auxiliary:\n  title_generation:\n    enabled: false\n",
        encoding="utf-8")
    mods = Path(__file__).resolve().parents[2]
    for name in ("ask-gate", "user-steps"):
        shutil.copytree(mods / "plugins" / name, home / "plugins" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
    token = set_hermes_home_override(home)
    manager = plugins.get_plugin_manager()
    from hermes_state import SessionDB
    db = SessionDB(db_path=home / "state.db")
    try:
        manager.discover_and_load()
        gate_callbacks = manager.iter_hook_callbacks("pre_final_gate")
        transforms = manager.iter_hook_callbacks("transform_llm_output")
        assert len(transforms) == 1, manager._plugins
        ask_callbacks = [cb for cb in gate_callbacks if cb.__name__ == 'pre_final_gate']
        ask = sys.modules[ask_callbacks[0].__module__] if 'ask-gate' in enabled else None
        steps = sys.modules[transforms[0].__module__]
        if ask is not None:
            assert Path(ask.__file__).is_relative_to(home)
        assert Path(steps.__file__).is_relative_to(home)
        agent = AIAgent(session_id="f3-portable", api_key="test-only",
                        base_url="https://example.invalid/v1", provider="openai-compat",
                        model="test/model", enabled_toolsets=[], max_iterations=6,
                        quiet_mode=True, skip_context_files=True, skip_memory=True,
                        platform="cli", save_trajectories=False, session_db=db,
                        skip_background_review=True)
        agent._cached_system_prompt = "Isolated integration test."
        agent.compression_enabled = False
        yield SimpleNamespace(home=home, manager=manager, agent=agent, ask=ask, steps=steps, db=db)
        assert request.config._f3_network["attempts"] == [], "Unexpected network attempt"
    finally:
        db.close()
        manager.unload()
        reset_hermes_home_override(token)
