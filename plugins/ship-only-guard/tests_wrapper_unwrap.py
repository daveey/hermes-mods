"""Literal wrapper classification only; no command or network mutation."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("wrapper_state", Path(__file__).with_name("tests_state.py"))
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)
g = state.g
REPO = "example/protected"
MERGE = f"gh pr merge 3050 -R {REPO} --squash"
BROKER = "token-broker exec --scope user/github --reason x -- "


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("unexpected subprocess/network call")
    monkeypatch.setattr(g.subprocess, "run", unexpected)
    monkeypatch.setattr(g, "_live_turns", lambda *a: [])
    monkeypatch.setattr(g, "_turn_acquire", lambda *a: "")


def check(command, tmp_path):
    return g.check_tool("terminal", {"command": command}, cwd=str(tmp_path),
                        repos={REPO}, env={"GH_TOKEN": ""})


@pytest.mark.parametrize("prefix", [
    BROKER, "/opt/tools/" + BROKER, "timeout 60 ", "nice ", "stdbuf -oL ", "/usr/bin/", "~/.local/bin/",
    # A wrapper argument that is the bare word gh/git must not end the scan.
    BROKER.replace("--reason x", "--reason gh"), BROKER.replace("--reason x", "--reason git"), "sudo -u git ",
])
def test_wrapped_merge_refused(prefix, tmp_path):
    command = prefix + MERGE
    assert check(command, tmp_path), command


@pytest.mark.parametrize("command", [
    'rg "gh pr merge" README.md', "grep -n gh file",
    BROKER + f"gh pr view 1 -R {REPO}",
])
def test_read_only_allowed(command, tmp_path):
    assert check(command, tmp_path) is None


@pytest.mark.parametrize("approved", [True, False])
def test_wrapped_state_preflight_matches_bare(monkeypatch, tmp_path, approved):
    data = state.payload(**({} if approved else {"reviews": []}))
    calls = state.probe(monkeypatch, data)
    command = f"gh pr merge 12 -R {REPO} --rebase --match-head-commit {state.SHA}"
    bare = check(command, tmp_path)
    bare_calls = [argv for argv, kwargs in calls]
    calls.clear()
    wrapped = check(BROKER + command, tmp_path)
    assert wrapped == bare
    assert [argv for argv, kwargs in calls] == bare_calls
    assert bare_calls[0][:3] == ["gh", "pr", "view"]
    assert (bare is None) == approved


def test_wrapped_git_preserves_context(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(g, "_push", lambda words, cwd, repos, config: calls.append((words, cwd, repos, config)) or "refused")
    assert check("timeout 60 /usr/bin/git -c push.default=matching push origin main", tmp_path) == "refused"
    assert calls == [(["origin", "main"], str(tmp_path), {REPO}, {"push.default": "matching"})]
