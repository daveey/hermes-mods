"""The lane's generated commands must survive test-home pollution in a persistent terminal."""
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coach_inbox_contract", ROOT / "self_heal.py")
sh = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sh
spec.loader.exec_module(sh)


def briefs():
    return [sh.investigation_brief({"fingerprint": "desktop-12345678"}),
            sh.fix_brief("desktop-12345678", {})]


@pytest.mark.parametrize("index", [0, 1], ids=["investigation", "fix"])
def test_post_and_preflight_pin_the_lane_home(index):
    text = briefs()[index]
    commands = re.findall(r"`([^`]*hermes inbox[^`]*)`", text)
    assert len(commands) == 2, commands
    for command in commands:
        assert command.startswith('env HERMES_HOME="$HOME/.hermes" hermes inbox ')
    assert commands[0].endswith("post --help")
    assert "Do not pipe" in text
    assert "inbox_outcome" in text


@pytest.mark.skipif(not shutil.which("hermes"), reason="installed Hermes CLI required")
@pytest.mark.parametrize("index", [0, 1], ids=["investigation", "fix"])
def test_real_generated_preflight_with_contaminated_home(tmp_path, index):
    # Real discovery, not an import of inbox.register or a mocked subprocess. Never posts data.
    text = briefs()[index]
    commands = re.findall(r"`([^`]*hermes inbox[^`]*)`", text)
    preflight = next(command for command in commands if command.endswith("post --help"))
    env = dict(os.environ, HERMES_HOME=str(tmp_path / "diagnostic-home"))
    env.pop("HERMES_PLUGINS_DEBUG", None)
    run = subprocess.run(["/bin/sh", "-c", preflight], env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "usage: hermes inbox post" in run.stdout
    assert "--field" in run.stdout
