"""The isolated core fixture must ship cross-plugin runtime dependencies."""
import importlib.util
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
        'assert callable(runpy.run_path(str(helper))["board_db_files"]); '
        'test = runpy.run_path(str(plugins / "user-steps/tests/test_integration_layout.py")); '
        'test["test_f3_staging_imports_real_user_steps_without_core"](plugins.parent / "layout-probe")'
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


def test_f3_staging_imports_real_user_steps_without_core(tmp_path):
    mods = Path(__file__).resolve().parents[3]
    path = mods / "tests/integration_f3/conftest.py"
    spec = importlib.util.spec_from_file_location("f3_layout_fixture", path)
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    home = tmp_path / "home"
    fixture.stage_plugins(mods, home)
    helper = home / "plugins/kanban_inventory.py"
    assert helper.read_bytes() == (mods / "plugins/kanban_inventory.py").read_bytes()
    # Import both real consumers from the staged layout, not from this checkout.
    for name in ("core", "cards"):
        staged = home / "plugins/user-steps" / (name + ".py")
        spec = importlib.util.spec_from_file_location("f3_staged_" + name, staged)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.__file__ is not None
        assert Path(module.__file__).resolve() == staged.resolve()
        assert module._board_db_files(home / "kanban/boards") == []
