"""Exercise conftest under a genuinely inherited worker environment."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_inherited_worker_context_is_isolated(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copyfile(Path(__file__).with_name("conftest.py"), suite / "conftest.py")
    # conftest's autouse gateway reaper imports this sibling helper.
    shutil.copyfile(Path(__file__).with_name("gateway_reaper.py"), suite / "gateway_reaper.py")
    shutil.copyfile(Path(__file__).with_name("home_guard.py"), suite / "home_guard.py")
    (suite / "test_probe.py").write_text('''
import os
from pathlib import Path


def test_isolated(tmp_path, monkeypatch):
    assert "HERMES_DELEGATED_CHILD_CONTEXT" not in os.environ
    assert "HERMES_KANBAN_TASK" not in os.environ
    assert "HERMES_KANBAN_WORKSPACE" not in os.environ
    assert "HERMES_KANBAN_BOARD" not in os.environ
    assert "HERMES_KANBAN_FUTURE_PIN" not in os.environ
    assert Path(os.environ["HERMES_KANBAN_HOME"]) == tmp_path
    assert Path(os.environ["HERMES_KANBAN_DB"]) == tmp_path / "kanban.db"
    assert Path(os.environ["HERMES_HOME"]) == tmp_path
    assert Path(os.environ["HERMES_RUNTIME_DIR"]) == tmp_path / "tools"
    assert os.environ["COACH_LESSONS"] == "0"
    # A test can still opt into a synthetic worker/board context explicitly.
    monkeypatch.setenv("HERMES_DELEGATED_CHILD_CONTEXT", "1")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "test-task")
    assert os.environ["HERMES_KANBAN_TASK"] == "test-task"
''')
    env = dict(os.environ, HERMES_DELEGATED_CHILD_CONTEXT="1",
               HERMES_HOME="unchanged-profile-home", HERMES_KANBAN_TASK="outer-worker",
               HERMES_KANBAN_WORKSPACE="outer-workspace", HERMES_KANBAN_BOARD="live-board",
               HERMES_KANBAN_DB="live.db", HERMES_KANBAN_HOME="live-home",
               HERMES_KANBAN_FUTURE_PIN="future-pin", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(suite)],
                            env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
