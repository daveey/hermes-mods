"""All mutations below are classifications; Git fixtures have no network access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

g = load("ship_guard_test", HERE / "guard.py")
plugin = load("ship_plugin_test", HERE / "__init__.py")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "ship-only-guard.json").write_text(json.dumps({"repos": ["example/protected"]}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in (g.MARKER, "SHIP_ONLY_GUARD_REPOS", "GH_REPO", "TERMINAL_CWD"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/example/protected.git"], check=True)
    return root


def test_profile_policy_inheritance(repo, monkeypatch):
    root = Path(os.environ["HERMES_HOME"])
    profile = root / "profiles" / "reviewer"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    assert g.check_shell("gh pr merge 12", str(repo))


def test_python_terminal_positional(repo):
    assert g.check_python('terminal("gh pr merge 12 -R example/protected")', str(repo))
    assert g.check_python('terminal("gh pr merge 12 -R example/other")', str(repo)) is None


@pytest.mark.parametrize("command", [
    "gh pr merge 12", "gh pr merge --admin --squash 12", "gh --repo example/protected pr merge 12",
    "gh pr --repo=example/protected merge --auto 12", "gh pr merge -Rexample/protected 12",
    "gh pr merge https://github.com/example/protected/pull/12 --admin",
    "GH_REPO=example/protected gh pr merge 12", "env GH_REPO=example/protected gh pr merge 12",
    "gh api -X PUT repos/example/protected/pulls/12/merge",
    "gh api /repos/example/protected/merges -f head=topic",
    "gh api --method=POST https://api.github.com/repos/example/protected/merges",
    "git push origin HEAD:main", "git push origin +HEAD:refs/heads/main", "git push origin main",
    "git push --force origin topic:main", "git push origin --all", "git push --mirror origin",
    "git push", "git push origin", "git push origin HEAD", "git -c push.default=matching push origin", "git push origin :main",
    "gh pr merge --repo github.com/example/protected 12",
    "git push --repo=origin HEAD:main", "git push origin refs/heads/*:refs/heads/*",
    "bash -lc 'gh pr merge 12'", "command gh pr merge 12", "env -u GH_REPO gh pr merge 12",
])
def test_blocked_shapes(repo, command):
    assert g.check_shell(command, str(repo)), command


@pytest.mark.parametrize("command", [
    "gh pr view 12", "gh pr merge -R example/unrelated 12", "GH_REPO=example/unrelated gh pr merge 12",
    "gh pr merge https://github.com/example/unrelated/pull/12",
    "gh api repos/example/unrelated/merges -f head=topic", "gh api repos/example/protected/pulls/12",
    "git push origin HEAD:topic", "git push origin --tags", "git push --dry-run origin HEAD:main", "git -c push.default=nothing push",
    "git push https://github.com/example/unrelated.git HEAD:main",
    "echo 'gh pr merge 12'", "echo HERMES_CORE_WT_LAND=1", "git status",
])
def test_allowed_shapes(repo, command):
    assert g.check_shell(command, str(repo)) is None


@pytest.mark.parametrize("command", [
    "HERMES_CORE_WT_LAND=1 gh pr merge 12", "env HERMES_CORE_WT_LAND=1 gh api repos/example/protected/merges",
    "HERMES_CORE_WT_LAND=1 git push origin HEAD:main",
    "HERMES_CORE_WT_LAND=1 bash -c 'gh pr merge 12; git push origin main'",
])
def test_land_child(repo, command):
    assert g.check_shell(command, str(repo)) is None


@pytest.mark.parametrize("command", [
    "echo HERMES_CORE_WT_LAND=1; gh pr merge 12", "gh pr merge 12 # HERMES_CORE_WT_LAND=1",
    "HERMES_CORE_WT_LAND=1 true && gh pr merge 12",
    "HERMES_CORE_WT_LAND=1 gh pr merge 12; git push origin main",
    "HERMES_CORE_WT_LAND=1; gh pr merge 12", "HERMES_CORE_WT_LAND=10 gh pr merge 12",
    "env HERMES_CORE_WT_LAND=1 true | gh pr merge 12",
])
def test_marker_is_command_scoped(repo, command):
    assert g.check_shell(command, str(repo))


def test_context(repo, tmp_path, monkeypatch):
    assert g.check_shell(f"cd '{repo}' && gh pr merge 12", str(tmp_path))
    assert g.check_shell(f"git -C '{repo}' push origin main", str(tmp_path))
    assert g.check_shell(f"env -C '{repo}' git push origin main", str(tmp_path))
    monkeypatch.setenv("TERMINAL_CWD", str(repo))
    assert g.check_shell("gh pr merge 12")
    monkeypatch.setenv(g.MARKER, "1")
    assert g.check_shell("gh pr merge 12") is None
    assert g.check_shell("env -u HERMES_CORE_WT_LAND gh pr merge 12")


def test_configured_push_default(repo):
    subprocess.run(["git", "-C", str(repo), "config", "remote.origin.push", "HEAD:main"], check=True)
    assert g.check_shell("git push", str(repo))


def test_missing_and_override_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SHIP_ONLY_GUARD_REPOS", raising=False)
    monkeypatch.delenv(g.MARKER, raising=False)
    assert g.check_shell("gh pr merge -R example/protected 12") is None
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", '["example/protected"]')
    assert g.check_shell("gh pr merge -R example/protected 12")
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", '["bad value"]')
    assert not g.load_repos()


@pytest.mark.parametrize("code", [
    'subprocess.run(["gh", "pr", "merge", "12"])',
    'subprocess.run("gh pr merge 12", shell=True)',
    'os.system("git push origin main")',
    'argv = ["gh", "pr", "merge", "12"]\nsubprocess.check_call(argv)',
    'terminal(command="gh pr merge 12")',
    'terminal({"command": "git push origin main"})',
    'tool_call(calls=[{"name":"terminal", "arguments":{"command":"gh pr merge 12"}}])',
])
def test_python_literals(repo, code):
    assert g.check_python(code, str(repo))


@pytest.mark.parametrize("code", [
    '# HERMES_CORE_WT_LAND=1\nsubprocess.run(["gh", "pr", "merge", "12"])',
    'note = "HERMES_CORE_WT_LAND=1"\nterminal(command="gh pr merge 12")',
])
def test_python_comment_does_not_authorize(repo, code):
    assert g.check_python(code, str(repo))


@pytest.mark.parametrize("code", [
    'print("gh pr merge 12")', 'note = "git push origin main"',
    'subprocess.run(["echo", "gh pr merge 12"])',
    'terminal(command="HERMES_CORE_WT_LAND=1 gh pr merge 12")',
    'subprocess.run(["gh", "pr", "merge", "12"], env={"HERMES_CORE_WT_LAND":"1"})',
    'subprocess.run(["gh", "pr", "merge", "12", "-R", "example/unrelated"])',
])
def test_python_allow_direction(repo, code):
    assert g.check_python(code, str(repo)) is None


def test_python_cwd(repo, tmp_path):
    assert g.check_python(f'subprocess.run(["git", "push", "origin", "main"], cwd={str(repo)!r})', str(tmp_path))


def test_registered_hooks(repo, monkeypatch):
    hooks = {}
    class Context:
        def register_hook(self, name, fn):
            hooks[name] = fn
    plugin.register(Context())
    hook = hooks["pre_tool_call"]
    for name, args in [
        ("terminal", {"command": "gh pr merge 12", "workdir": str(repo)}),
        ("execute_code", {"code": f'subprocess.run(["git","push","origin","main"], cwd={str(repo)!r})'}),
        ("tool_call", {"calls": [{"name": "functions.terminal", "arguments": {"command": "gh pr merge 12", "workdir": str(repo)}}]}),
        ("tool_call", {"calls": [{"name": "execute_code", "arguments": {"code": 'terminal(command="gh pr merge -R example/protected 12")'}}]}),
    ]:
        assert hook(tool_name=name, args=args)["action"] == "block"
    assert hook(tool_name="terminal", args={"command": "gh pr merge -R example/unrelated 12"}) is None


def test_direct_argv(repo):
    assert g.check_argv(["git", "push", "origin", "+HEAD:main"], cwd=str(repo))
    assert g.check_argv(["gh", "pr", "merge", "12"], cwd=str(repo))
    assert g.check_argv(["gh", "pr", "merge", "12"], cwd=str(repo), env={g.MARKER: "1"}, repos={"example/protected"}) is None


def test_other_local_repository(repo, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-b", "main", str(other)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "remote", "add", "origin", "git@github.com:example/unrelated.git"], check=True)
    assert g.check_shell("gh pr merge 12; git push origin main", str(other)) is None
    assert g.check_shell(f"cd {other} && gh pr merge 12", str(repo)) is None
    assert g.check_shell(f"git -C {other} push origin main", str(repo)) is None
    assert g.check_shell(f"git -C {repo} push origin main", str(other))


def test_python_assignment_order(repo):
    code = 'argv = ["gh", "pr", "merge", "12"]\nsubprocess.run(argv)\nargv = ["echo", "safe"]'
    assert g.check_python(code, str(repo))


def test_standalone_cli(repo):
    env = dict(os.environ, SHIP_ONLY_GUARD_REPOS='["example/protected"]')
    blocked = subprocess.run([sys.executable, str(HERE / "guard.py"), "--gh-args", "pr", "merge", "-R", "example/protected", "12"], env=env, cwd=repo, capture_output=True, text=True)
    assert blocked.returncode == 1
    assert "ship-only-guard" in blocked.stderr
    env[g.MARKER] = "1"
    allowed = subprocess.run([sys.executable, str(HERE / "guard.py"), "--gh-args", "pr", "merge", "12"], env=env, cwd=repo, capture_output=True, text=True)
    assert allowed.returncode == 0
