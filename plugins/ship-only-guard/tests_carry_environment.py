"""Real-child regression for the carry verifier's GIT_* environment boundary."""
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from tests_carry import GIT_VERIFY, R, git_history  # reuse real merge history, not policy stubs


@pytest.mark.parametrize("source", ["ambient", "caller", "both"])
def test_carry_git_children_discard_hostile_environment(monkeypatch, tmp_path, git_history, source):
    repo, heads = git_history
    real_git = shutil.which("git")
    assert real_git
    # The shim records what an actual exec'd child receives, then delegates every
    # operation to real Git. Only the network URL is replaced with our local repo.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "children.jsonl"
    shim = bin_dir / "git"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(log)!r}, 'a') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ), "
        "'cwd': os.getcwd()}) + '\\n')\n"
        f"args = [{str(repo)!r} if a == 'https://github.com/{R}.git' else a for a in sys.argv[1:]]\n"
        f"os.execv({real_git!r}, [{real_git!r}, *args])\n"
    )
    shim.chmod(0o755)
    home = tmp_path / "home"
    caller = {"HERMES_HOME": str(home), "CARRY_ENV_CONTROL": "preserved",
              "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    hostile = {
        "GIT_DIR": str(tmp_path / "nonexistent.git"),
        "GIT_WORK_TREE": str(tmp_path / "wrong-worktree"),
        "GIT_INDEX_FILE": str(tmp_path / "wrong-index"),
        "GIT_OBJECT_DIRECTORY": str(tmp_path / "wrong-objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(tmp_path / "wrong-alternates"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.repositoryFormatVersion",
        "GIT_CONFIG_VALUE_0": "999",
        "GIT_CONFIG_PARAMETERS": "invalid config parameters",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "wrong-global"),
        "GIT_CONFIG_NOSYSTEM": "0",
        "GIT_TERMINAL_PROMPT": "1",
        "GIT_FUTURE_HOSTILE_OPTION": "must also be filtered",
    }
    # Fixture creation finishes before pollution; monkeypatch restores the parent
    # environment, and every path (including the deliberately bad ones) is owned.
    if source in ("ambient", "both"):
        for key, value in hostile.items():
            monkeypatch.setenv(key, value)
    if source in ("caller", "both"):
        caller.update(hostile)
    why = GIT_VERIFY(*heads["clean"], R, None, caller)
    children = [json.loads(line) for line in log.read_text().splitlines()]
    assert children, "no real Git child observed"
    safe_git = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0"}
    for child in children:
        inherited_git = {k: v for k, v in child["env"].items() if k.startswith("GIT_")}
        assert inherited_git == safe_git, "hostile GIT_* reached a real carry child"
        assert child["env"]["CARRY_ENV_CONTROL"] == "preserved"
        assert child["env"]["HERMES_HOME"] == str(home)
        assert Path(child["cwd"]).parent == home / "cache"
    assert why == "", f"valid carry merge refused: {why}"
    for step in ("init", "fetch", "show", "merge-base", "merge-tree", "rev-parse"):
        assert any(step in child["argv"] for child in children), step
    assert not list((home / "cache").iterdir())
