"""Tests for push-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("push_guard_plugin", "__init__.py")
guard = plugin.guard
GUARDS = [{"remote": "owner/repo", "branch": "main", "allow": ["docs/*", "*.md"], "use": "land <branch> --wait"}]


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    bare = tmp_path / "owner" / "repo.git"
    bare.mkdir(parents=True)
    git("init", "-q", "--bare", "-b", "main", cwd=bare)
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    git("remote", "add", "origin", str(bare), cwd=work)
    (work / "a.py").write_text("x")
    git("add", ".", cwd=work)
    git("commit", "-qm", "base", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("checkout", "-q", "-b", "feat", cwd=work)
    cfg = tmp_path / "push-guards.json"
    cfg.write_text(json.dumps({"guards": GUARDS}))
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(cfg))
    return work


def change(work, name):
    p = work / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("y")
    git("add", ".", cwd=work)
    git("commit", "-qm", name, cwd=work)


def block(tool, args):
    return plugin._pre_tool_call(tool_name=tool, args=args)


def test_code_push_to_main_is_blocked_with_the_land_command(repo):
    change(repo, "hub/x.py")
    got = block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)})
    assert got and got["action"] == "block"
    assert "land <branch> --wait" in got["message"] and "hub/x.py" in got["message"]


def test_docs_only_push_to_main_is_allowed(repo):
    change(repo, "docs/plans/x.md")
    change(repo, "README.md")
    assert block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)}) is None


def test_push_to_a_feature_branch_is_allowed(repo):
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "git push -u origin feat", "workdir": str(repo)}) is None
    assert block("terminal", {"command": "git push --force-with-lease origin HEAD:feat", "workdir": str(repo)}) is None


def test_cd_and_dash_C_and_refs_heads_forms_are_seen(repo, tmp_path):
    change(repo, "hub/x.py")
    assert block("terminal", {"command": f"cd {repo} && git fetch && git push origin HEAD:refs/heads/main",
                              "workdir": str(tmp_path)})
    assert block("terminal", {"command": f"git -C {repo} push origin feat:main", "workdir": str(tmp_path)})


def test_pushing_local_main_by_name_or_bare_push_on_main_is_blocked(repo):
    git("checkout", "-q", "main", cwd=repo)
    git("branch", "--set-upstream-to=origin/main", cwd=repo)
    change(repo, "hub/y.py")
    assert block("terminal", {"command": "git push origin main", "workdir": str(repo)})
    assert block("terminal", {"command": "git push", "workdir": str(repo)})


def test_other_remotes_are_not_guarded(repo, tmp_path):
    other = tmp_path / "else" / "thing.git"
    other.mkdir(parents=True)
    git("init", "-q", "--bare", "-b", "main", cwd=other)
    git("remote", "add", "two", str(other), cwd=repo)
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "git push two HEAD:main", "workdir": str(repo)}) is None


def test_override_skips_the_check(repo):
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "PUSH_GUARD=off git push origin HEAD:main", "workdir": str(repo)}) is None


def test_execute_code_subprocess_strings_and_argv_lists(repo):
    change(repo, "hub/x.py")
    code = f'import subprocess\nsubprocess.run("git -C {repo} push origin HEAD:main", shell=True)\n'
    assert block("execute_code", {"code": code})
    code = f'subprocess.run(["git", "-C", "{repo}", "push", "origin", "HEAD:main"])\n'
    assert block("execute_code", {"code": code})


def test_no_config_is_a_no_op(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(tmp_path / "missing.json"))
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)}) is None


def test_nested_tool_call_is_checked(repo):
    change(repo, "hub/x.py")
    args = {"calls": [{"name": "terminal", "arguments": {"command": "git push origin HEAD:main", "workdir": str(repo)}}]}
    assert block("mcp__tool_call", args)
