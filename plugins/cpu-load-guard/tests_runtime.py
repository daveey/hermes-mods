"""Worker-runtime failure injection for cpu-load-guard. Does not start busy loops.

Proves the plugin the worker actually loads (discover_plugins, not a direct import)
rejects the 40-loop payload on terminal and execute_code, allows a bounded pytest
command, and still does so when tools.override is not granted.

Run under the Hermes interpreter from this directory, with a relative path. An absolute
path in the shell command is scanned as a script and refused, because this file quotes
the burner it tests:

  python -I -c "import hermes_bootstrap, runpy; runpy.run_path('tests_runtime.py', run_name='__main__')"
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAYLOAD = (
    'cd ~/code/_wt/flaky-t01a183; PY=~/.hermes/hermes-agent/.venv/bin/python; '
    'for i in $(seq 1 40); do (timeout 120 $PY -c "while True: pass" &) ; done; '
    'sleep 1; for i in 1 2 3; do $PY -m pytest -q -p no:cacheprovider '
    'tests/hermes_cli/test_config_rmw_lock.py '
    'tests/hermes_cli/test_update_stale_dashboard_review.py '
    'tests/tui_gateway/test_connector_accounts_rpc.py --durations=4 '
    '2>&1 | grep -E "s call|passed|failed|^E  " ; done; '
    'pkill -f "while True: pass"; true'
)
BOUNDED = (
    "cd ~/code/_wt/flaky-t01a183; PY=~/.hermes/hermes-agent/.venv/bin/python; "
    "$PY -m pytest -q -p no:cacheprovider "
    "tests/hermes_cli/test_config_rmw_lock.py "
    "tests/hermes_cli/test_update_stale_dashboard_review.py "
    "tests/tui_gateway/test_connector_accounts_rpc.py --durations=4"
)
EXEC = "from hermes_tools import terminal\nterminal(" + repr(PAYLOAD) + ", timeout=400)"
EXEC_OK = "from hermes_tools import terminal\nterminal(" + repr(BOUNDED) + ")"


def _busy_count() -> int:
    # Count only. Never signal these processes.
    proc = subprocess.run(
        ["pgrep", "-fc", "while True: pass"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stdout or "").strip()
    return int(text) if text.isdigit() else 0


def main() -> None:
    if os.environ.get("CPU_LOAD_GUARD") == "off":
        raise SystemExit("CPU_LOAD_GUARD=off")
    os.environ.pop("CPU_LOAD_GUARD_HOSTS", None)

    from hermes_cli.plugin_capabilities import plugin_capability_granted
    from hermes_cli.plugins import (
        discover_plugins,
        get_plugin_manager,
        get_pre_tool_call_block_message,
        iter_hook_callbacks,
    )

    before = _busy_count()
    discover_plugins()
    manager = get_plugin_manager()
    loaded = next(
        (p for p in manager._plugins.values() if p.manifest.name == "cpu-load-guard"),
        None,
    )
    if loaded is None or not loaded.enabled or loaded.error or loaded.module is None:
        raise SystemExit(f"cpu-load-guard not loaded: {loaded}")
    module_file = Path(loaded.module.__file__ or "").resolve()
    expected = (HERE / "__init__.py").resolve()
    if module_file != expected:
        raise SystemExit(f"loaded module {module_file} != {expected}")
    if "pre_tool_call" not in loaded.hooks_registered:
        raise SystemExit(f"hooks={loaded.hooks_registered}")

    callbacks = iter_hook_callbacks("pre_tool_call")
    own = [cb for cb in callbacks if getattr(cb, "__code__", None) and Path(cb.__code__.co_filename).resolve() == expected]
    if not own:
        raise SystemExit("cpu-load-guard pre_tool_call callback missing from worker hook list")

    # Routine load probe. It does not gate pre_tool_call.
    override = plugin_capability_granted("cpu-load-guard", "tools.override")
    if override:
        raise SystemExit("tools.override unexpectedly granted; this test expects the deny-and-still-block path")

    term = get_pre_tool_call_block_message("terminal", {"command": PAYLOAD, "timeout": 400})
    exec_block = get_pre_tool_call_block_message("execute_code", {"code": EXEC})
    direct = own[0](tool_name="terminal", args={"command": PAYLOAD, "timeout": 400})
    direct_exec = own[0](tool_name="execute_code", args={"code": EXEC})
    allow_term = get_pre_tool_call_block_message("terminal", {"command": BOUNDED, "timeout": 400})
    allow_exec = get_pre_tool_call_block_message("execute_code", {"code": EXEC_OK})
    allow_direct = own[0](tool_name="terminal", args={"command": BOUNDED})
    allow_direct_exec = own[0](tool_name="execute_code", args={"code": EXEC_OK})
    after = _busy_count()

    problems = []
    if not (isinstance(term, str) and "cpu-load-guard" in term and "refused" in term):
        problems.append(f"terminal dispatcher: {term!r}")
    if not (isinstance(exec_block, str) and "refused" in exec_block):
        problems.append(f"execute_code dispatcher: {exec_block!r}")
    if not (isinstance(direct, dict) and direct.get("action") == "block"):
        problems.append(f"terminal hook: {direct!r}")
    if not (isinstance(direct_exec, dict) and direct_exec.get("action") == "block"):
        problems.append(f"execute_code hook: {direct_exec!r}")
    if allow_term is not None:
        problems.append(f"bounded terminal blocked: {allow_term!r}")
    if allow_exec is not None:
        problems.append(f"bounded execute_code blocked: {allow_exec!r}")
    if allow_direct is not None or allow_direct_exec is not None:
        problems.append(f"bounded hook: {allow_direct!r} {allow_direct_exec!r}")
    if after != before:
        problems.append(f"busy-loop count changed {before} -> {after}")
    if problems:
        raise SystemExit("FAIL\n" + "\n".join(problems))
    print(
        "PASS"
        f" module={module_file}"
        " tools.override=deny"
        " terminal=block"
        " execute_code=block"
        " bounded_pytest=allow"
        f" busy_delta=0 count={after}"
    )


if __name__ == "__main__":
    main()


def test_worker_runtime_blocks_40_loop_and_allows_pytest(tmp_path, monkeypatch):
    """Collected only when pytest can see this file. Skips without the worker runtime."""
    import pytest

    pytest.importorskip("hermes_cli")
    # Discover this worktree through the real loader, not the installed copy.
    # Keep main() unchanged so standalone execution still verifies live identity.
    home = tmp_path / 'home'
    (home / 'plugins').mkdir(parents=True)
    (home / 'plugins' / 'cpu-load-guard').symlink_to(HERE, target_is_directory=True)
    (home / 'config.yaml').write_text('plugins:\n  enabled: [cpu-load-guard]\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    from hermes_cli.plugins import get_plugin_manager
    manager = get_plugin_manager()
    try:
        main()
    finally:
        manager.unload()
