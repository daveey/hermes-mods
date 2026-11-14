"""Offline process-boundary contract for native carry verification authentication."""
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("carry_credentials_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


@pytest.mark.parametrize("shallow", [False, True])
def test_native_carry_passes_ephemeral_credential_helper_to_every_git_process(tmp_path, monkeypatch, shallow):
    merge, ours, theirs, base, tree = (digit * 40 for digit in "12345")
    home = tmp_path / "home"
    home.mkdir()
    calls = []
    scratch_dirs = []
    helper = "credential.helper=!gh auth git-credential"
    outputs = {
        "init": "", "fetch": "", "show": f"{ours} {theirs}",
        "merge-base": base, "rev-list": f"{ours}\n{theirs}",
        "merge-tree": tree, "rev-parse": tree,
    }

    def run(argv, *, cwd, env, capture_output, text, timeout):
        # Stop at the process boundary: neither Git nor gh nor a network runs.
        # Strip options generically so a missing helper reaches our assertions,
        # rather than failing in fixture dispatch before testing the contract.
        args = list(argv[1:])
        options = []
        while args and args[0] == "-c":
            options.append(args[1])
            del args[:2]
        assert argv[0] == "git"
        assert options.count(helper) == 1, f"missing command-scoped helper: {argv!r}"
        assert options == ["core.hooksPath=" + os.devnull, helper]
        assert capture_output and text and timeout == 30
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert "GIT_CONFIG_COUNT" not in env
        assert "GIT_CONFIG_KEY_0" not in env
        assert "GIT_CONFIG_VALUE_0" not in env
        command = args[0]
        assert command in outputs, f"unexpected process (including persistent config write): {argv!r}"
        calls.append(args)
        scratch_dirs.append(Path(cwd))
        if command == "fetch":
            assert args == ["fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--depth=128",
                            "https://github.com/example/protected.git", merge, ours, theirs]
            if shallow:
                Path(cwd, "shallow").write_text(base + "\n")
        return subprocess.CompletedProcess(argv, 0, outputs[command], "")

    monkeypatch.setattr(g.subprocess, "run", run)
    result = g._git_merge_tree_refusal(merge, ours, theirs, "example/protected", str(tmp_path), {
        "HOME": str(home), "HERMES_HOME": str(home),
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "store",
    })
    assert result == ""
    expected = ["init", "fetch", "show", "merge-base"]
    if shallow:
        expected.append("rev-list")
    assert [args[0] for args in calls] == expected + ["merge-tree", "rev-parse"]
    assert len(set(scratch_dirs)) == 1
    assert not scratch_dirs[0].exists()
    # No helper/auth configuration or credentials are persisted under isolated HOME.
    assert list(home.iterdir()) == [home / "cache"]
    assert list((home / "cache").iterdir()) == []
