"""Tests for stash-worktree-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("stash_worktree_guard_plugin", "__init__.py")
guard = plugin.guard

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=GIT_ENV)


def init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True)
    git("init", "-q", "-b", branch, cwd=path)
    (path / "f.txt").write_text("base\n")
    git("add", ".", cwd=path)
    git("commit", "-qm", "base", cwd=path)


@pytest.fixture
def multi(tmp_path, monkeypatch):
    """Main checkout on ``main`` plus a linked worktree on ``other``. A stash sits on ``main``."""
    monkeypatch.delenv("STASH_WORKTREE_GUARD_DISABLE", raising=False)
    monkeypatch.delenv("STASH_WORKTREE_GUARD", raising=False)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    main = tmp_path / "repo"
    init_repo(main)
    git("branch", "other", cwd=main)
    wt = tmp_path / "wt"
    git("worktree", "add", "-q", str(wt), "other", cwd=main)
    (main / "f.txt").write_text("stashed\n")
    git("stash", "push", "-q", "-m", "from-main", cwd=main)
    return main, wt


@pytest.fixture
def single(tmp_path, monkeypatch):
    monkeypatch.delenv("STASH_WORKTREE_GUARD_DISABLE", raising=False)
    monkeypatch.delenv("STASH_WORKTREE_GUARD", raising=False)
    repo = tmp_path / "only"
    init_repo(repo)
    git("checkout", "-q", "-b", "other", cwd=repo)
    git("checkout", "-q", "main", cwd=repo)
    (repo / "f.txt").write_text("stashed\n")
    git("stash", "push", "-q", cwd=repo)
    git("checkout", "-q", "other", cwd=repo)
    return repo


def block(tool, **args):
    return plugin._pre_tool_call(tool_name=tool, args=args)


def test_warns_on_stash_list_in_a_multi_worktree_repo(multi):
    main, _wt = multi
    out = plugin.transform_terminal_output(
        command=f"git -C {main} stash list", output="stash@{{0}}: On main: from-main\n")
    assert out is not None
    assert guard.MARK in out
    assert "refs/stash" in out
    assert "2 worktrees" in out
    assert plugin.transform_terminal_output(command=f"git -C {main} stash list", output=out) is None


def test_warns_on_push_pop_apply_and_noarg(multi):
    main, _wt = multi
    for cmd in (
        f"git -C {main} stash",
        f"git -C {main} stash push -- f.txt",
        f"git -C {main} stash -q",
        f"git -C {main} stash pop",
        f"git -C {main} stash apply",
        f"cd {main} && git stash list",
    ):
        out = plugin.transform_terminal_output(command=cmd, output="")
        assert out and guard.MARK in out, cmd


def test_no_warning_for_a_single_worktree_or_unrelated_git(single, multi):
    repo = single
    main, _wt = multi
    assert plugin.transform_terminal_output(command=f"git -C {repo} stash list", output="x") is None
    assert plugin.transform_terminal_output(command=f"git -C {repo} stash pop", output="") is None
    assert plugin.transform_terminal_output(command=f"git -C {main} status", output="") is None
    assert plugin.transform_terminal_output(command=f"git -C {main} stash --help", output="help") is None
    assert plugin.transform_terminal_output(command="echo git stash list", output="") is None


def test_refuses_pop_and_apply_when_stash_branch_differs(multi):
    main, wt = multi
    for cmd in (
        f"git -C {wt} stash pop",
        f"git -C {wt} stash apply",
        f"cd {wt} && git stash pop",
        f"timeout 60 git -C {wt} stash pop",
        f"bash -c 'git -C {wt} stash pop'",
    ):
        got = block("terminal", command=cmd)
        assert got and got["action"] == "block", cmd
        assert "other" in got["message"] and "main" in got["message"]
        assert "refs/stash" in got["message"]
    got = block("terminal", command="git stash pop", workdir=str(wt))
    assert got and got["action"] == "block"
    got = block("execute_code", code=f'terminal("git -C {wt} stash pop")')
    assert got and got["action"] == "block"
    got = block("execute_code", code=f'subprocess.run(["git", "-C", "{wt}", "stash", "pop"])')
    assert got and got["action"] == "block"
    got = block("tool_call", calls=[{"name": "terminal", "arguments": {"command": f"git -C {wt} stash pop"}}])
    assert got and got["action"] == "block"


def test_allows_pop_when_the_stash_branch_matches_or_the_repo_has_one_worktree(multi, single):
    main, _wt = multi
    assert block("terminal", command=f"git -C {main} stash pop") is None
    assert block("terminal", command=f"git -C {main} stash push") is None
    assert block("terminal", command=f"git -C {main} stash list") is None
    # Same-branch pop still warns: the ref is shared even when the names match.
    warned = plugin.transform_terminal_output(command=f"git -C {main} stash pop", output="ok")
    assert warned and guard.MARK in warned
    # One worktree: pop across branches is the ordinary stash workflow.
    assert block("terminal", command=f"git -C {single} stash pop") is None
    assert "On main:" in guard.stash_subject(str(main)) or "WIP on main:" in guard.stash_subject(str(main))


def test_off_switch_skips_both_paths(multi):
    _main, wt = multi
    cmd = f"STASH_WORKTREE_GUARD=off git -C {wt} stash pop"
    assert block("terminal", command=cmd) is None
    assert plugin.transform_terminal_output(command=cmd, output="") is None


def test_unparseable_stash_subject_fails_closed(multi, monkeypatch):
    _main, wt = multi
    monkeypatch.setattr(guard, "stash_subject", lambda repo, ref="stash@{0}": "custom message with no branch")
    got = block("terminal", command=f"git -C {wt} stash pop")
    assert got and got["action"] == "block"
    assert "does not name a branch" in got["message"]


def test_assignment_cd_push_pop_is_checked_before_background_promotion(multi, monkeypatch):
    main, wt = multi
    git("checkout", "--detach", "-q", cwd=wt)
    monkeypatch.setenv("TMPDIR", str(wt.parent))
    command = 'WT=$TMPDIR/wt; cd $WT; git stash -q -- f.txt; git status; git stash pop -q'
    got = block("terminal", command=command, timeout=900)
    assert got and got["action"] == "block"
    assert "main" in got["message"] and "HEAD" in got["message"]


@pytest.mark.parametrize("target", ['$UNKNOWN_STASH_REPO', '"$(mktemp -d)"', '`mktemp -d`'])
@pytest.mark.parametrize("op", ["pop", "apply"])
def test_unknown_target_fails_closed(multi, target, op, monkeypatch):
    main, _wt = multi
    monkeypatch.delenv("UNKNOWN_STASH_REPO", raising=False)
    got = block("terminal", command=f'cd {target}; git stash {op}', workdir=str(main))
    assert got and got["action"] == "block"
    assert "resolve" in got["message"]
    assert "Save a patch instead" in got["message"]


@pytest.mark.parametrize("op", ["list", "push", "show"])
def test_unknown_target_nonrestoring_stash_is_allowed(multi, op):
    main, _wt = multi
    assert block("terminal", command=f'cd "$(mktemp -d)"; git stash {op}', workdir=str(main)) is None


def test_assigned_git_C_and_safe_expansions(multi, single, monkeypatch):
    main, wt = multi
    monkeypatch.setenv("HOME", str(wt.parent))
    monkeypatch.setenv("TMPDIR", str(wt.parent))
    for target in ('"$HOME/wt"', '"${TMPDIR}/wt"', '~/wt'):
        got = block("terminal", command=f'M={target}; git -C "$M" stash apply')
        assert got and got["action"] == "block"
        assert "other" in got["message"] and "main" in got["message"]
    for repo in (main, single):
        assert block("terminal", command=f'M="{repo}"; git -C "$M" stash pop') is None


def test_unknown_cwd_cannot_fall_back_to_guard_cwd(multi):
    main, _wt = multi
    assert block("terminal", command='cd "$MISSING"; cd relative; git stash pop', workdir=str(main))
    # An absolute -C restores a known target; a same-branch pop remains permitted.
    assert block("terminal", command=f'cd "$MISSING"; git -C "{main}" stash pop') is None


def test_assignment_scope_and_tracked_pwd(multi, single, monkeypatch):
    main, wt = multi
    monkeypatch.setenv("PWD", str(single))
    monkeypatch.setenv("WT", str(single))
    for cmd in (
        'cd "$PWD"; git stash pop',
        f'export WT={wt}; cd "$WT"; git stash pop',
        f'WT={wt}; WT={single} true; cd "$WT"; git stash pop',
        f'HOME={wt.parent}; cd ~/wt; git stash pop',
    ):
        assert block("terminal", command=cmd, workdir=str(wt)), cmd
    # Allow-direction controls prove assignments resolve, rather than merely fail closed.
    for cmd in (
        f'export WT="{single}"; cd "$WT"; git stash pop',
        f'HOME="{single.parent}"; cd ~/only; git stash pop',
    ):
        assert block("terminal", command=cmd, workdir=str(wt)) is None, cmd


def test_quoted_literal_variable_is_not_expanded(multi, single):
    main, wt = multi
    # Shell sees the literal directory $M, not the single-worktree value of M.
    literal = wt.parent / "$M"
    literal.symlink_to(wt, target_is_directory=True)
    cmd = f"M={single}; cd '$M'; git stash pop"
    assert block("terminal", command=cmd, workdir=str(wt.parent))


def test_missing_stash_is_not_a_block(multi):
    main, wt = multi
    git("stash", "clear", cwd=main)
    assert block("terminal", command=f"git -C {wt} stash pop") is None
