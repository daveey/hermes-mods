"""Reject malformed carry inputs before starting any Git subprocess."""
import importlib.util
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("carry_inputs_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

MERGE, OURS, THEIRS, BASE, TREE = (c * 40 for c in "abcde")


@pytest.fixture
def run(monkeypatch):
    # No real Git/network calls, even if validation regresses. Recording the
    # boundary catches a late refusal just as well as an accidental acceptance.
    recorder = Mock(return_value=CompletedProcess([], 1, "", "fixture Git failure"))
    monkeypatch.setattr(g.subprocess, "run", recorder)
    return recorder


def refuse(tmp_path, run, repo="example/project", merge=MERGE, ours=OURS, theirs=THEIRS):
    reason = g._git_merge_tree_refusal(
        merge, ours, theirs, repo, str(tmp_path), {"HERMES_HOME": str(tmp_path / "home")})
    run.assert_not_called()
    assert reason == "merge-tree inputs are unreadable"


@pytest.mark.parametrize("repo", [
    "", "example", "/project", "example/", "example/project/extra",
    "https://github.com/example/project", "example project/repo",
    "example/project\n", "example/project?ref=main", "example/project#ref",
    "example/project;true", "example\\project", "exämple/project",
], ids=["empty", "no-slash", "no-owner", "no-name", "extra-slash",
        "url", "space", "newline", "query", "fragment", "shell", "backslash", "unicode"])
def test_invalid_repository_refused_before_subprocess(tmp_path, run, repo):
    refuse(tmp_path, run, repo=repo)


@pytest.mark.parametrize("slot", ["merge", "ours", "theirs"])
@pytest.mark.parametrize("sha", [None, 123, b"a" * 40, "", "a" * 39, "a" * 41,
                                "g" * 40, "a" * 39 + "\n", "HEAD", "--all"],
                         ids=["none", "integer", "bytes", "empty", "short", "long",
                              "nonhex", "newline", "ref", "option"])
def test_invalid_sha_refused_before_subprocess(tmp_path, run, slot, sha):
    refuse(tmp_path, run, **{slot: sha})


@pytest.mark.parametrize("repo", ["example/project", "Example_1/project.name-2"])
def test_valid_inputs_reach_git_and_accept_clean_tree(tmp_path, run, repo):
    run.side_effect = [CompletedProcess([], 0, output, "") for output in
                       ("", "", f"{OURS} {THEIRS}\n", BASE + "\n", TREE + "\n", TREE + "\n")]
    assert g._git_merge_tree_refusal(
        MERGE, OURS, THEIRS, repo, str(tmp_path), {"HERMES_HOME": str(tmp_path / "home")}) == ""
    assert run.call_count == 6
    commands = [call.args[0][5:] for call in run.call_args_list]
    assert [command[0] for command in commands] == [
        "init", "fetch", "show", "merge-base", "merge-tree", "rev-parse"]
    assert commands[1][-4:] == ["https://github.com/" + repo + ".git", MERGE, OURS, THEIRS]
