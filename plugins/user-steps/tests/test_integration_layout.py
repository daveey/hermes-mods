"""The isolated core fixture must ship cross-plugin runtime dependencies."""
import os
from pathlib import Path
import subprocess
import shlex
import sys


def test_core_integration_copies_shared_board_inventory(tmp_path):
    core = tmp_path / "core"
    (core / "tests/hermes_cli").mkdir(parents=True)
    (core / "scripts").mkdir()
    runner = core / "scripts/run_tests.sh"
    # Exercise the real fixture copier; replace only the expensive core runner.
    check = (
        'import pathlib, runpy, sys; '
        'plugins = pathlib.Path(sys.argv[1]).parents[1]; '
        'helper = plugins / "kanban_inventory.py"; '
        'assert helper.read_bytes() == pathlib.Path(sys.argv[2]).read_bytes(); '
        'assert callable(runpy.run_path(str(helper))["board_db_files"])'
    )
    runner.write_text(
        '#!/usr/bin/env bash\nset -eu\n'
        + 'exec "$LAYOUT_PYTHON" -c ' + shlex.quote(check)
        + ' "$1" "$LAYOUT_HELPER"\n'
    )
    runner.chmod(0o755)
    plugin = Path(__file__).resolve().parents[1]
    helper = plugin.parent / "kanban_inventory.py"
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), HERMES_HOME=str(home),
               LAYOUT_PYTHON=sys.executable, LAYOUT_HELPER=str(helper))
    env.pop("HERMES_HUB_CLIENT_REAL", None)
    result = subprocess.run(
        ["bash", str(plugin / "run-core-integration.sh"), str(core)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list((core / "tests/hermes_cli").iterdir())
