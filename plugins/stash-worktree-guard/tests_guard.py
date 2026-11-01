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


# --- Bounded shell state: groups, pipelines, background, mutations, substitutions ---

def _blocked(cmd, cwd):
    got = block("terminal", command=cmd, workdir=str(cwd))
    return bool(got and got["action"] == "block")


# (command template, workdir key, expected blocked). {wt}/{main}/{single} are real repos:
# main is on the stash's branch, wt is a second worktree on `other`, single has one worktree.
SHELL_STATE_CASES = [
    # Grouping: a subshell's cd scopes its own commands, never the parent.
    ("(cd {wt} && git stash pop)", "main", True),
    ("(cd {wt}; git stash apply)", "main", True),
    ("(cd {main} && git stash pop)", "wt", False),
    ("(cd {single} && git stash pop)", "wt", False),
    ("(cd {main}); git stash pop", "wt", True),
    ("{{ cd {wt}; git stash pop; }}", "main", True),
    ("{{ cd {wt}; }}; git stash pop", "main", True),
    # Background and pipeline elements run in subshells: their cd does not leak.
    ("cd {single} & git stash pop", "wt", True),
    ("cd {single} | true; git stash pop", "wt", True),
    ("cd {single} |& cat; git stash pop", "wt", True),
    ("git -C {wt} stash list |& git -C {wt} stash pop", "main", True),
    # Redirections are not control separators.
    ("git -C {main} stash pop 2>&1 | tail -3", "wt", False),
    ("git -C {wt} stash pop 2>&1 | tail -3", "main", True),
    ("cd {single} >/dev/null 2>&1; git stash pop", "wt", False),
    ("cd {single} &>/dev/null; git stash pop", "wt", False),
    ("cd {wt} &>/dev/null; git stash pop", "main", True),
    # Variable mutations the guard does not model invalidate the binding.
    ("WT={single}; WT+=/../wt; cd \"$WT\"; git stash pop", "main", True),
    ("WT={single}; unset WT; cd \"$WT\"; git stash pop", "wt", True),
    ("WT={single}; declare -x WT={wt}; cd \"$WT\"; git stash pop", "main", True),
    ("WT={single}; read WT </dev/null; cd \"$WT\"; git stash pop", "wt", True),
    ("WT={single}; eval \"cd {wt}\"; git stash pop", "main", True),
    ("f() {{ cd {wt}; }}; f; git stash pop", "main", True),
    # Directory stack: popd's destination is not inferred.
    ("pushd {single}; popd; git stash pop", "wt", True),
    ("pushd {single} >/dev/null && git stash pop", "wt", False),
    # Command substitutions are inspected (never executed) in an inherited, isolated scope.
    ("x=$(git stash pop)", "wt", True),
    ("x=\"$(git stash pop)\"", "wt", True),
    ("x=`git stash pop`", "wt", True),
    ("echo \"$(cd {wt} && git stash pop)\"", "main", True),
    ("WT={wt}; x=\"$(cd \"$WT\" && git stash pop)\"", "main", True),
    ("cat <(git -C {wt} stash pop)", "main", True),
    ("x=\"$(cd {single} && git stash pop)\"", "wt", False),
    ("x=$(cd {single}); git stash pop", "wt", True),
    # Case/compound separators: ;; ;& ;;& and if/then are command boundaries.
    ("case x in x) git -C {wt} stash pop;; esac", "main", True),
    ("case x in x) true;& y) git -C {wt} stash pop;; esac", "main", True),
    ("case x in x) true;;& *) git -C {wt} stash pop;; esac", "main", True),
    ("case x in x) git -C {single} stash pop;; esac", "wt", False),
    ("if true; then git -C {wt} stash pop; fi", "main", True),
    ("cd {single}; case \"$1\" in a) cd {wt};; esac; git stash pop", "wt", True),
    ("false && cd {single}; git stash pop", "wt", True),
    ("cd {single} || exit 1; git stash pop", "wt", False),
]


@pytest.mark.parametrize("template,where,expected", SHELL_STATE_CASES,
                         ids=[c[0] for c in SHELL_STATE_CASES])
def test_bounded_shell_state(multi, single, template, where, expected):
    main, wt = multi
    cmd = template.format(wt=wt, main=main, single=single)
    cwd = {"main": main, "wt": wt}[where]
    assert _blocked(cmd, cwd) is expected, cmd


def test_nested_shell_exhaustion_fails_closed(multi):
    import shlex
    main, wt = multi
    cmd = f"git -C {wt} stash pop"
    for _ in range(12):
        cmd = "bash -c " + shlex.quote(cmd)
    assert _blocked(cmd, main)
    deep = f"git -C {wt} stash pop"
    for _ in range(12):
        deep = f"echo \"$({deep})\""
    assert _blocked(deep, main)
    # Function recursion: every analyzed level is same-branch, but the exhausted level
    # still contains a restore, so it is refused rather than dropped.
    assert _blocked(f"f() {{ git -C {main} stash pop; f; }}; f", wt)
    assert _blocked("f() { f; }; f; git stash pop", main)
    # A shallow same-branch nesting keeps working.
    assert not _blocked("bash -c " + shlex.quote(f"git -C {main} stash pop"), wt)


@pytest.mark.parametrize("template,where,expected", [
    ("bash <<EOF\ncd {wt}\ngit stash pop\nEOF", "main", True),
    ("cat <<'EOF'\ngit stash pop\nEOF\ngit -C {main} stash pop", "wt", False),
    ("G=git; $G stash pop", "wt", True),
    ("$(echo git) stash pop", "main", True),
    ("trap 'git stash pop' EXIT", "main", True),
    ("source ./env.sh; git stash pop", "main", True),
    ("for d in {wt}; do cd $d; done; git stash pop", "main", True),
    ("git stash pop ; )", "main", True),  # unparseable: fail closed
])
def test_other_shell_entry_points(multi, template, where, expected):
    main, wt = multi
    cmd = template.format(wt=wt, main=main)
    assert _blocked(cmd, {"main": main, "wt": wt}[where]) is expected, cmd


def test_execute_code_shell_state(multi, single, monkeypatch):
    main, wt = multi
    monkeypatch.setenv("TERMINAL_CWD", str(main))
    got = block("execute_code", code=f'terminal("(cd {wt} && git stash pop)")')
    assert got and got["action"] == "block"
    assert block("execute_code", code=f'terminal("cd {single} & git stash pop")') is None
    monkeypatch.setenv("TERMINAL_CWD", str(wt))
    got = block("execute_code", code='terminal(\'x="$(git stash pop)"\')')
    assert got and got["action"] == "block"
    got = block("execute_code", code=f'terminal("cd {single} & git stash pop")')
    assert got and got["action"] == "block"
    assert block("execute_code", code=f'terminal("cd {single} >/dev/null 2>&1; git stash pop")') is None


# --- Uncertainty: a parse failure in a fragment taints the whole command ---

CASE_IN_SUBST = 'echo "$(case a in a) git -C {wt} stash pop;; esac)"'


def _stash_count(repo):
    out = subprocess.run(["git", "-C", str(repo), "stash", "list"], capture_output=True, text=True, check=True)
    return len(out.stdout.splitlines())


def test_quoted_case_substitution_fails_closed_terminal(multi):
    main, wt = multi
    cmd = CASE_IN_SUBST.format(wt=wt)
    got = block("terminal", command=cmd, workdir=str(main))
    assert got and got["action"] == "block", cmd
    assert "Save a patch instead" in got["message"]
    # The shape is a real restore: bash pops main's stash into the `other` worktree.
    assert _stash_count(main) == 1
    subprocess.run(["bash", "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
    assert _stash_count(main) == 0
    assert (wt / "f.txt").read_text() == "stashed\n"


def test_quoted_case_substitution_fails_closed_execute_code(multi, monkeypatch):
    main, wt = multi
    monkeypatch.setenv("TERMINAL_CWD", str(main))
    code = "terminal('" + CASE_IN_SUBST.format(wt=wt) + "')"
    got = block("execute_code", code=code)
    assert got and got["action"] == "block", code


def test_fragment_parse_failure_without_restore_is_not_refused(multi):
    main, wt = multi
    # Uncertainty refuses only textual restores: a non-restoring stash is still allowed.
    assert not _blocked(f'echo "$(case a in a) git -C {wt} stash list;; esac)"', main)


# --- Bounds: state cap, step budget, unexpected analyzer exception ---

def _subdirs(repo, n):
    for k in range(n):
        (repo / f"d{k}").mkdir()
    return [repo / f"d{k}" for k in range(n)]


def _case_cd(single, dirs):
    arms = " ".join(f"{k}) cd {d};;" for k, d in enumerate(dirs))
    return f'cd {single}; case "$1" in {arms} esac; git stash pop'


def test_state_cap_collapses_to_unknown(multi, single, monkeypatch):
    _main, wt = multi
    dirs = _subdirs(single, guard.MAX_STATES + 4)
    # Every possible cwd is inside the single-worktree repo; only the cap makes cwd unknown.
    assert not _blocked(_case_cd(single, dirs[:3]), wt)
    assert _blocked(_case_cd(single, dirs), wt)
    monkeypatch.setattr(guard, "MAX_STATES", 1000)
    assert not _blocked(_case_cd(single, dirs), wt)


def test_step_budget_fails_closed(multi):
    main, wt = multi
    same_branch = f"git -C {main} stash pop"
    assert not _blocked("true; " * 10 + same_branch, wt)
    assert _blocked("true; " * guard.MAX_STEPS + same_branch, wt)
    assert not _blocked("true; " * guard.MAX_STEPS + f"git -C {main} stash list", wt)


def test_unexpected_analyzer_exception_fails_closed(multi, monkeypatch):
    main, wt = multi
    same_branch = f"git -C {main} stash pop"
    assert not _blocked(same_branch, wt)

    def boom(*_a, **_k):
        raise RuntimeError("analyzer defect")

    monkeypatch.setattr(guard._Analyzer, "simple", boom)
    assert _blocked(same_branch, wt)
    assert not _blocked(f"git -C {main} stash list", wt)
