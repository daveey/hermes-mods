"""Tests for stash-worktree-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import os
import shutil
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


# --- Scalar argv and unsupported expansion boundaries ---

@pytest.mark.parametrize("template", [
    'S=stash; git -C {wt} $S pop',
    'OP=pop; git -C {wt} stash "$OP"',
    'G=git; S=stash; OP=pop; $G -C {wt} "$S" "$OP"',
    'git() {{ command git -C {wt} "$@"; }}; git stash pop',
    'C="git -C {wt} stash pop"; $C',
])
def test_expanded_restore_argv(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt)
    assert _blocked(cmd, main)
    # The hook is non-mutating, and the refused command is a real restore in bash.
    assert _stash_count(main) == 1
    assert (wt / "f.txt").read_text() == "base\n"
    subprocess.run(["bash", "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
    assert _stash_count(main) == 0
    assert (wt / "f.txt").read_text() == "stashed\n"


@pytest.mark.parametrize("where", ["main", "single"])
def test_scalar_argv_allow_controls(multi, single, where):
    main, wt = multi
    repo = {"main": main, "single": single}[where]
    cmd = f'G=git; S=stash; OP=pop; $G -C {repo} "$S" "$OP" 2>&1'
    assert not _blocked(cmd, wt)


@pytest.mark.parametrize("template", [
    'env -C {wt} git stash pop',
    'nice -n 5 git -C {wt} stash pop',
    'stdbuf -oL git -C {wt} stash pop',
    'command -p git -C {wt} stash pop',
    'exec -a git git -C {wt} stash pop',
    'coproc git -C {wt} stash pop; wait',
    '/usr/bin/env -C {wt} git stash pop',
    'W=env; $W -C {wt} git stash pop',
])
def test_wrapper_options_are_not_silently_dropped(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt)
    assert _blocked(cmd, main)
    # The hook is non-mutating, and the refused command is a real restore in bash.
    assert _stash_count(main) == 1
    assert (wt / "f.txt").read_text() == "base\n"
    subprocess.run(["bash", "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
    assert _stash_count(main) == 0
    assert (wt / "f.txt").read_text() == "stashed\n"


def test_ansi_c_quote_cannot_hide_substitution(multi):
    main, wt = multi
    cmd = "echo $'it\\'s'\"$(git -C " + str(wt) + " stash pop)\"$'it\\'s'"
    assert _blocked(cmd, main)
    # ANSI-C quoting is outside the scalar grammar, even when the apparent restore
    # would otherwise be same-branch. Do not trust boundaries from the ordinary lexer.
    assert _blocked(f"echo $'plain'; git -C {main} stash pop", wt)


@pytest.mark.parametrize("prefix", ["export", "declare -x", "typeset -x"])
def test_exported_git_selection_overrides_even_absolute_C(multi, prefix):
    main, wt = multi
    gitdir = (wt / '.git').read_text().strip().removeprefix('gitdir: ')
    cmd = f'{prefix} GIT_DIR={gitdir} GIT_WORK_TREE={wt}; git -C {main} stash pop'
    assert _blocked(cmd, main)


def test_prefix_git_selection_overrides_even_absolute_C(multi):
    main, wt = multi
    gitdir = (wt / '.git').read_text().strip().removeprefix('gitdir: ')
    assert _blocked(f'GIT_DIR={gitdir} GIT_WORK_TREE={wt} git -C {main} stash pop', main)


def test_cdpath_relative_target_is_unknown(multi, single):
    main, wt = multi
    (single / 'target').mkdir()
    (wt.parent / 'target').symlink_to(wt, target_is_directory=True)
    cmd = f'CDPATH={wt.parent}; cd target; git stash pop'
    assert _blocked(cmd, single)
    assert not _blocked(f'CDPATH={wt.parent}; cd {main}; git stash pop', wt)


def test_expansion_globbing_is_not_a_scalar(multi):
    main, wt = multi
    (main / 'stash').touch()
    assert _blocked(f"S='sta*'; git -C {wt} $S pop", main)


def test_prefix_cdpath_is_not_ignored(multi, single):
    main, wt = multi
    (single / 'target').mkdir()
    (wt.parent / 'target').symlink_to(wt, target_is_directory=True)
    assert _blocked(f'CDPATH={wt.parent} cd target; git stash pop', single)


# --- Composition: fallback order, function precedence, sticky alternate selection ---

def _gitdir(wt):
    return (wt / ".git").read_text().strip().removeprefix("gitdir: ")


def _assert_real_restore(cmd, main, wt):
    """Refused, guard left the fixture untouched, and bash really restores main's stash in wt."""
    assert _blocked(cmd, main), cmd
    assert _stash_count(main) == 1
    assert (wt / "f.txt").read_text() == "base\n"
    proc = subprocess.run(["bash", "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV)
    assert proc.returncode == 0, proc.stderr
    assert _stash_count(main) == (1 if "apply" in cmd else 0)  # apply keeps the entry
    assert (wt / "f.txt").read_text() == "stashed\n"


@pytest.mark.parametrize("template", [
    # Uncertain fragments fall back to text evidence; it must not depend on word order.
    'OP=pop; S=stash; env -C {wt} git "$S" "$OP"',
    'OP=apply; S=stash; nice -n 5 git -C {wt} "$S" "$OP"',
    'OP=pop; S=stash; stdbuf -oL git -C {wt} "$S" "$OP"',
    "OP=pop; S=stash; echo $'hi'; git -C {wt} \"$S\" \"$OP\"",
    "OP=pop; S=stash; git -C {wt} \"$S\" \"$OP\"; echo $'hi'",
    # Equivalent evidence spelled through split scalars, quotes, or ANSI-C escapes.
    'A=sta; B=sh; OP=pop; env -C {wt} git "$A$B" "$OP"',
    "A=sta; B=sh; OP=pop; echo $'hi'; git -C {wt} \"${{A}}${{B}}\" \"$OP\"",
    "echo $'hi'; git -C {wt} st'a'sh p\\op",
    "echo $'hi'; git -C {wt} $'\\x73tash' $'\\160op'",
])
def test_uncertain_fallback_is_order_and_spelling_independent(multi, template):
    main, wt = multi
    _assert_real_restore(template.format(wt=wt), main, wt)


@pytest.mark.parametrize("template", [
    # Bash resolves functions before PATH, so wrapper names are ordinary function names.
    'env() {{ git -C {wt} stash pop; }}; env true',
    'timeout() {{ git -C {wt} stash pop; }}; timeout 5 true',
    'nice() {{ git -C {wt} stash pop; }}; nice -n 5 true',
    'function nohup {{ git -C {wt} stash pop; }}; nohup true',
    'stdbuf() {{ git -C {wt} stash pop; }}; stdbuf -oL true',
    'sudo() {{ git -C {wt} stash pop; }}; sudo true',
    # Non-POSIX bash lets a function shadow even `command` and `exec`.
    'command() {{ git -C {wt} stash pop; }}; command true',
    'exec() {{ git -C {wt} stash pop; }}; exec true',
    'E=env; env() {{ git -C {wt} stash pop; }}; $E true',
    # `command` still bypasses a shadowing function and runs the real restore.
    'git() {{ :; }}; command git -C {wt} stash pop',
])
def test_functions_resolve_before_wrapper_names(multi, template):
    main, wt = multi
    _assert_real_restore(template.format(wt=wt), main, wt)


def test_function_precedence_allow_controls(multi, single):
    main, wt = multi
    # The function shadows the wrapper: the wrapped restore never runs in bash.
    cmd = f'env() {{ :; }}; env git -C {wt} stash pop'
    assert not _blocked(cmd, main)
    subprocess.run(["bash", "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
    assert _stash_count(main) == 1
    # Same-branch and single-worktree restores through a shadowing function stay allowed.
    assert not _blocked(f'timeout() {{ git -C {main} stash pop; }}; timeout 5 true', wt)
    assert not _blocked(f'env() {{ git -C {single} stash pop; }}; env true', wt)
    assert not _blocked(f'git() {{ :; }}; command git -C {main} stash pop', wt)
    # Non-restoring function bodies are not refused.
    assert not _blocked(f'env() {{ git -C {wt} stash list; }}; env true', main)


@pytest.mark.parametrize("template", [
    "git --git-dir={gitdir} --work-tree={wt} -C {main} stash pop",
    "git --git-dir {gitdir} --work-tree {wt} -C {main} stash pop",
    "git --work-tree={wt} --git-dir={gitdir} -C {main} stash apply",
    "git --git-dir={gitdir} -C {main} --work-tree {wt} stash pop",
    "git -C {main} --git-dir={gitdir} --work-tree={wt} -C {main} stash pop",
])
def test_cli_alternate_selection_is_sticky_across_C(multi, template):
    main, wt = multi
    cmd = template.format(gitdir=_gitdir(wt), wt=wt, main=main)
    _assert_real_restore(cmd, main, wt)


def test_alternate_selection_controls(multi, single):
    main, wt = multi
    # Plain absolute -C stays trusted; redirection and non-restoring ops are unaffected.
    assert not _blocked(f"git -C {main} stash pop 2>&1 >/dev/null", wt)
    assert not _blocked(f"git -C {single} stash pop", wt)
    assert not _blocked(f"git --git-dir={_gitdir(wt)} --work-tree={wt} -C {main} stash list", main)


# --- Unknown wrapper/Git argv, brace expansion, builtin and logical cwd ---

def _symlink_into_main(main, wt):
    """wt/l -> main/d: logically wt/l/.. is wt (other), physically it is main."""
    (main / "d").mkdir()
    (wt / "l").symlink_to(main / "d", target_is_directory=True)


UNKNOWN_ARGV_RESTORES = [
    # Long timeout options are parsed, not mistaken for the command.
    "timeout --foreground 10 git -C {wt} stash pop",
    "timeout --signal=TERM 10 git -C {wt} stash pop",
    "timeout --kill-after=5 --preserve-status 10 git -C {wt} stash pop",
    "timeout -v -s KILL 10s git -C {wt} stash apply",
    # Unquoted brace expansion is not a literal word.
    "git -C {wt} {{stash,pop}}",
    # builtin/command run the cd builtin.
    "builtin cd {wt}; git stash pop",
    "command cd {wt}; git stash pop",
    "builtin pushd {wt} >/dev/null; git stash pop",
    # Unknown op values (printf -v, read, $(...), arrays, "$@", ${X:-y}) with and
    # without wrappers or ANSI-C text.
    'printf -v OP %s%s%s p o p; git -C {wt} stash "$OP"',
    'printf -v OP %s%s%s p o p; git -C {wt} stash "$OP" -q',
    'printf -v OP %s%s%s p o p; nice -n 5 git -C {wt} stash "$OP"',
    "printf -v OP %s%s%s p o p; echo $'hi'; git -C {wt} stash \"$OP\"",
    "printf -v OP %s%s%s p o p; echo $'hi'; nice -n 5 git -C {wt} stash \"$OP\"",
    'printf -v OP %s%s%s p o p; timeout --foreground 5 git -C {wt} stash "$OP"',
    'printf -v S %s%s st ash; printf -v OP %s%s%s p o p; git -C {wt} "$S" "$OP"',
    'printf -v S %s%s st ash; printf -v OP %s%s%s p o p; stdbuf -oL git -C {wt} "$S" "$OP"',
    'read -r OP <<< pop; git -C {wt} stash "$OP"',
    'OP=$(echo pop); env -C {wt} git stash "$OP"',
    'A=(stash pop); git -C {wt} "${{A[@]}}"',
    'set -- stash pop; git -C {wt} "$@"',
    'git -C {wt} stash "${{OP:-pop}}"',
    # Git global options with a separate argument, and config-injected aliases.
    "git --namespace foo -C {wt} stash pop",
    "git --no-optional-locks -C {wt} stash pop",
    "git -c alias.p='stash pop' -C {wt} p",
    # Generic command runners.
    "echo pop | xargs git -C {wt} stash",
    "flock {main}/.lk git -C {wt} stash pop",
    "setsid -w git -C {wt} stash pop",
    "find {main}/f.txt -exec git -C {wt} stash pop \\;",
    # Temporary bindings read by cd.
    "cd {main}; OLDPWD={wt} cd - >/dev/null; git stash pop",
    "HOME={wt} cd; git stash pop",
]


@pytest.mark.parametrize("template", UNKNOWN_ARGV_RESTORES)
def test_unknown_argv_and_wrapper_restores_are_refused(multi, template):
    main, wt = multi
    _assert_real_restore(template.format(wt=wt, main=main), main, wt)


@pytest.mark.parametrize("template", [
    "cd {wt}/l/..; git stash pop",
    "cd {wt}/l; cd ..; git stash pop",
    "pushd {wt}/l/.. >/dev/null; git stash pop",
])
def test_logical_cd_through_symlink_is_not_resolved_physically(multi, template):
    main, wt = multi
    _symlink_into_main(main, wt)
    _assert_real_restore(template.format(wt=wt, main=main), main, wt)


@pytest.mark.parametrize("template", [
    # Not executed: bash would fail or the shape is a usage error, but argv is unknown.
    "git -C {wt} stash{{,}} pop",
    "git -C {wt} st{{a,}}sh pop",
    "git -C {wt} stash p{{o,}}p",
    'printf -v OP %s%s%s p o p; git -C {wt} stash -q "$OP"',
    "git --shallow-file x -C {wt} stash pop",
    "git --attr-source HEAD -C {wt} stash pop",
    "git --frobnicate foo -C {wt} stash pop",
    "timeout --frobnicate 10 git -C {wt} stash pop",
    "timeout 1e3 git -C {wt} stash pop",
    'git -C {wt} -c "$CFG" p',
    "W=x; $UNKNOWN -C {wt} git stash \"$OP\"",
])
def test_unsupported_argv_forms_fail_closed(multi, template):
    main, wt = multi
    assert _blocked(template.format(wt=wt, main=main), main)


_BASES = [
    'printf -v OP %s%s%s p o p; {deco}git -C {wt} stash "$OP"',
    'printf -v S %s%s st ash; printf -v OP %s%s%s p o p; {deco}git -C {wt} "$S" "$OP"',
    "{deco}git -C {wt} {{stash,pop}}",
    "{deco}git --namespace foo -C {wt} stash pop",
    "OP=pop; S=stash; {deco}git -C {wt} \"$S\" \"$OP\"",
]
_DECORATIONS = [
    "", "echo $'hi'; ", "nice -n 5 ", "timeout --foreground 5 ", "stdbuf -oL ",
    "env -i PATH=\"$PATH\" ", "echo $'hi'; nice -n 5 ", "flock /tmp/lk ", "command -p ",
]


@pytest.mark.parametrize("deco", _DECORATIONS)
@pytest.mark.parametrize("base", _BASES)
def test_added_uncertainty_never_turns_a_refusal_into_allow(multi, base, deco):
    main, wt = multi
    assert _blocked(base.format(wt=wt, deco=""), main)
    assert _blocked(base.format(wt=wt, deco=deco), main), deco


def test_round3_allow_controls(multi, single):
    main, wt = multi
    for cmd in (
        # same-branch / single-worktree restores through the newly modeled forms
        f"timeout 10 git -C {main} stash pop",
        f"timeout --foreground -k 5 10 git -C {main} stash pop",
        f"builtin cd {main}; git stash pop",
        f"builtin cd {single}; git stash pop",
        f"git --namespace foo -C {main} stash pop",
        f"git --no-pager -C {main} stash pop 2>&1 >/dev/null",
        f"git -C {main} stash pop stash@{{0}}",
        f"git -c core.editor=true -C {main} stash pop",
        f"pushd {single} >/dev/null; git stash pop",
        f"cd {main}/../repo; git stash pop",
    ):
        assert not _blocked(cmd, wt), cmd
    for cmd in (
        # non-restoring commands with unknown or wrapped argv
        f"timeout --foreground 10 git -C {wt} stash list",
        f"git -C {wt} stash list stash@{{0}}",
        f"printf -v OP %s%s%s l i st; git -C {wt} stash list",
        f"nice -n 5 git -C {wt} stash list",
        f"printf $'x\\ty\\n'; git -C {wt} stash list",
        f"echo pop | xargs -n1 git -C {wt} log -1",
        f"find {main} -name '*.txt' -print",
        f"git -c alias.p='stash pop' -C {main} log -1",
        'git log --format="%H" -1 "$X"',
        'git commit -m "$MSG"',
        f'git -C {wt} stash -m "$MSG"',
        f"echo $'x'; git -C {wt} stash push -m \"$MSG\"",
        f"echo $'x'; git -C {wt} stash -m \"$MSG\"",
    ):
        assert not _blocked(cmd, main), cmd


def test_logical_cd_allow_controls(multi):
    main, wt = multi
    _symlink_into_main(main, wt)
    # Physical cd lands in main (same branch); a symlink without `..` is unambiguous.
    assert not _blocked(f"cd -P {wt}/l/..; git stash pop", wt)
    assert not _blocked(f"cd {wt}/l; cd {main}; git stash pop", wt)
    assert not _blocked(f"cd {wt}/l; git stash list", main)
    # The tracked logical cwd is still exact: wt/l is inside main's checkout.
    assert not _blocked(f"cd {wt}/l; git stash pop", wt)


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


# --- Review round 4: shell entry points, splitting, program names, config ---

ROUND4_RESTORES = [
    # A hashed name runs its path; unset does not clear the hash table; a function wins.
    "hash -p /usr/bin/git g; unset g; g -C {wt} stash pop",
    "g() {{ git -C {wt} stash pop; }}; hash -p /bin/true g; g",
    # IFS changes how an unquoted scalar splits.
    "IFS=x; A=stashxpop; git -C {wt} $A",
    "IFS=_; A=stash_pop; git -C {wt} $A",
    "read -r IFS <<< x; A=stashxpop; git -C {wt} $A",
    # Backslash-newline is removed before tokenizing.
    "git -C {wt} sta\\\nsh pop",
    "git -C {wt} \\\nstash pop",
    # Shell option clusters and `--` before the -c script.
    "bash -ec 'git -C {wt} stash pop'",
    "bash -lc 'git -C {wt} stash pop'",
    "sh -c -- 'git -C {wt} stash pop'",
    "bash -o pipefail -c 'git -C {wt} stash pop'",
    # A shell reading its script from stdin or a file the guard does not see.
    "echo 'git -C {wt} stash pop' | bash",
    "cat <<'EOF' | bash\ngit -C {wt} stash pop\nEOF",
    "bash <(echo 'git -C {wt} stash pop')",
    "echo 'git -C {wt} stash pop' > {main}/.git/s.sh; bash {main}/.git/s.sh",
    # set -a exports every later assignment.
    "set -a; GIT_DIR={gd}; GIT_WORK_TREE={wt}; git stash pop",
    "set -o allexport; GIT_DIR={gd}; GIT_WORK_TREE={wt}; git stash pop",
    "set -a; GIT_DIR={gd}; GIT_WORK_TREE={wt}; bash -c 'git stash pop'",
    # Programs that are git under another name.
    "cd {wt} && $(git --exec-path)/git-stash pop",
    "cd {wt} && /usr/lib/git-core/git-stash pop",
    "hash -p /usr/bin/git g; g -C {wt} stash pop",
    # Unlisted programs that run their argv.
    "prlimit --nofile=1024 git -C {wt} stash pop",
    "setpriv --inh-caps=-all git -C {wt} stash pop",
    # Config injected through include files or config-file environment.
    "printf '[alias]\\n\\tp = stash pop\\n' > {main}/.git/inc; git -c include.path={main}/.git/inc -C {wt} p",
    "printf '[alias]\\n\\tp = stash pop\\n' > {main}/.git/inc; GIT_CONFIG_GLOBAL={main}/.git/inc git -C {wt} p",
    "mkdir -p {main}/.git/h; printf '[alias]\\n\\tp = stash pop\\n' > {main}/.git/h/.gitconfig; HOME={main}/.git/h git -C {wt} p",
]


def _gitdir(wt):
    return (wt / ".git").read_text().strip().removeprefix("gitdir: ")


@pytest.mark.parametrize("template", ROUND4_RESTORES)
def test_round4_restores_are_refused(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt, main=main, gd=_gitdir(wt))
    if "/usr/lib/git-core/" in cmd and not Path("/usr/lib/git-core/git-stash").exists():
        cmd = cmd.replace("/usr/lib/git-core/git-stash", "$(git --exec-path)/git-stash")
    for prog in ("prlimit", "setpriv"):
        if cmd.startswith(prog) and not shutil.which(prog):
            assert _blocked(cmd, main), cmd
            return
    _assert_real_restore(cmd, main, wt)


def test_round4_allow_controls(multi, single):
    main, wt = multi
    for cmd in (
        f"bash -ec 'git -C {main} stash pop'",
        f"bash -lc 'cd {single} && git stash pop'",
        f"sh -c -- 'git -C {main} stash pop'",
        f"IFS=x; A=stash; git -C {main} $A pop",
        f"git -C {main} sta\\\nsh pop",
        f"set -a; X=1; git -C {main} stash pop",
        f"cd {main} && /usr/lib/git-core/git-stash pop",
        f"git -c include.path=/dev/null -C {main} log -1",
        f"git -c include.path=/dev/null -C {main} stash pop",
        f"echo hi # comment \\\ngit -C {main} stash pop",
    ):
        assert not _blocked(cmd, wt), cmd
    for cmd in (
        f"bash -ec 'git -C {wt} stash list'",
        f"echo 'git -C {wt} log' | bash",
        "bash scripts/test.sh",
        "cat f | bash",
        f"IFS=x; A=stash; git -C {wt} $A list",
        f"prlimit --nofile=1024 git -C {wt} stash list",
        f"hash -p /usr/bin/git g; g -C {wt} stash list",
        f"git -c include.path=/dev/null -C {wt} log -1",
        f"HOME=/tmp git -C {wt} log -1",
        'git commit -m "fix stash pop handling"',
        'grep -rn "stash pop" .',
        "echo git stash pop",
        f"set -a; GIT_PAGER=cat; git -C {wt} stash list",
    ):
        assert not _blocked(cmd, main), cmd
    # Documented conservative refusal: a restore behind an unmodeled program runs in an
    # unknown repo, even when that repo would be the same branch.
    assert _blocked(f"prlimit --nofile=1024 git -C {main} stash pop", wt)


def test_round4_conservative_refusals(multi):
    main, wt = multi
    # Static only: these programs may be absent, or run elsewhere (ssh).
    for cmd in (
        f"numactl --localalloc git -C {wt} stash pop",
        f"eatmydata git -C {wt} stash pop",
        f"ssh host git -C {wt} stash pop",
        # Same-branch restores next to a shell reading an unseen script.
        "git stash pop; cat f | bash",
        f"bash x.sh; git -C {main} stash pop",
    ):
        assert _blocked(cmd, main), cmd


def test_round4_comment_does_not_continue_line(multi):
    main, wt = multi
    # In bash a backslash inside a comment does not join the next line: the git line runs.
    _assert_real_restore(f"echo hi # note \\\ngit -C {wt} stash pop", main, wt)


# --- Review round 5: hash table, allexport/keyword, namerefs, pipes, interpreters ---

ROUND5_RESTORES = [
    # A hashed name is forgotten by hash -r/-d, an assignment to PATH, or set +h.
    "hash -p /bin/echo git; hash -r; git -C {wt} stash pop",
    "hash -p /bin/echo git; hash -d git; git -C {wt} stash pop",
    "hash -p /bin/echo git; PATH=$PATH; git -C {wt} stash pop",
    "set +h; hash -p /bin/echo git; git -C {wt} stash pop",
    "P=$(command -v git); hash -p $P g; g -C {wt} stash pop",
    "hash -p$(command -v git) g; g -C {wt} stash pop",
    "BASH_CMDS[g]=$(command -v git); g -C {wt} stash pop",
    # allexport exports only later assignments; `set x -a` sets positionals, not -a.
    "cd {wt}; D={main}; set -a; bash -c 'git -C \"$D\" stash pop'",
    "cd {wt}; set x -a; D={main}; bash -c 'git -C \"$D\" stash pop'",
    "set -eo allexport; GIT_DIR={gd}; GIT_WORK_TREE={wt}; git stash pop",
    "shopt -so allexport; GIT_DIR={gd}; GIT_WORK_TREE={wt}; git stash pop",
    "bash -a -c 'GIT_DIR={gd}; GIT_WORK_TREE={wt}; git stash pop'",
    "bash -o allexport -c 'GIT_DIR={gd}; GIT_WORK_TREE={wt}; git stash pop'",
    "set -k; git GIT_DIR={gd} GIT_WORK_TREE={wt} stash pop",
    "declare -n R=IFS; R=x; A=stashxpop; git -C {wt} $A",
    # Shell input the guard does not read as a script.
    "printf 'git -C %s stash pop\\n' {wt} | bash 3<<<x",
    "printf 'git -C {wt} %s%s pop\\n' sta sh | bash",
    "source <(echo 'git -C {wt} stash pop')",
    ". /dev/stdin <<< 'git -C {wt} stash pop'",
    "printf 'git -C %s stash pop' {wt} > {main}/.git/s; bash -- {main}/.git/s",
    # Programs that run git or a shell from their argv.
    "busybox sh -c 'g\"\"it -C {wt} stash pop'",
    "perl -e 'system(\"git -C {wt} stash pop\")'",
    "python3 -c 'import os; os.system(\"git -C {wt} stash pop\")'",
    "G=git; nice $G -C {wt} stash pop",
    "G=git; prlimit --nofile=1024 $G -C {wt} stash pop",
    "S=git-stash; cd {wt} && setpriv --inh-caps=-all $(git --exec-path)/$S pop",
    # An alias written earlier in the same command.
    "git config alias.p 'stash pop' && git -C {wt} p",
]


@pytest.mark.parametrize("template", ROUND5_RESTORES)
def test_round5_restores_are_refused(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt, main=main, gd=_gitdir(wt))
    needs = [p for p in ("prlimit", "setpriv", "busybox", "perl", "python3") if p in cmd.split()
             or f"{p} " in cmd]
    if any(not shutil.which(p) for p in needs):
        assert _blocked(cmd, main), cmd
        return
    _assert_real_restore(cmd, main, wt)


def test_round5_allow_controls(multi):
    main, wt = multi
    for cmd in (
        f"hash -p /bin/echo e; git -C {main} stash pop",
        f"set -a; D={main}; bash -c 'git -C \"$D\" stash pop'",
        f"shopt -so allexport; X=1; git -C {main} stash pop",
        f"set -k; git -C {main} stash pop",
        f"python3 -m pytest -q && git -C {main} stash pop",
        f"python3 -c 'print(1)' && git -C {main} stash pop",
        f"make \"$TARGET\" && git -C {main} stash pop",
        f"declare -n ref=arr; git -C {main} stash pop",
        f"git config alias.st status && git -C {main} st",
        f"echo pop | bash -c cat",
        f"printf 'hello\\n' | bash; git -C {main} stash list",
        f"git config user.name x && git -C {main} stash pop",
        f"git -c core.editor=true -c core.pager=cat -C {main} stash pop",
        f"x=\"$(git -C {main} stash list)\"; PS4='+ '; git -C {main} stash pop",
        f'gh pr create --title x --body "then run git -C {wt} stash pop"',
        f"sed -i 's|old|git -C {wt} stash pop|' notes.md",
    ):
        assert not _blocked(cmd, main), cmd


# --- Review round 6: programs and settings that run a string, implicit bash code ---

ROUND6_RESTORES = [
    # Programs that run a string argument as shell code.
    "echo x | sed 'e git -C {wt} stash pop'",
    "echo 'git -C {wt} stash pop' | sed 's/^//e'",
    "make -f /dev/null --eval 'all:;git -C {wt} stash pop' all",
    "vim -es -u NONE -c '!git -C {wt} stash pop' -c 'qa!'",
    "echo 'system(q(git -C {wt} stash pop))' | perl",
    # BASH_CMDS set without a plain assignment.
    "declare BASH_CMDS[g]=$(command -v git); g -C {wt} stash pop",
    "printf -v 'BASH_CMDS[g]' %s $(command -v git); g -C {wt} stash pop",
    # Code bash runs implicitly.
    "command_not_found_handle() {{ git -C {wt} stash pop; }}; nosuchcmd_xyz; true",
    "PS4='$(git -C {wt} stash pop >&2)'; set -x; true",
    "x='$(git -C {wt} stash pop)'; echo \"${{x@P}}\"",
    "x='a[$(git -C {wt} stash pop >/dev/null; echo 0)]'; (( x )); true",
    "printf 'a\\n' | mapfile -t -C 'git -C {wt} stash pop; :' -c 1 arr",
    "printf 'cd {wt}\\n' > {main}/.git/env.sh; BASH_ENV={main}/.git/env.sh bash -c 'git stash pop'",
    "printf 'cd {wt}\\n' > {main}/.git/rc; bash --rcfile {main}/.git/rc -ic 'git stash pop'",
    # Git running a program from config or the environment.
    "git -c diff.external='git -C {wt} stash pop #' diff --ext-diff 'stash@{{0}}^1' 'stash@{{0}}'",
    "GIT_EXTERNAL_DIFF='git -C {wt} stash pop #' git diff --ext-diff 'stash@{{0}}^1' 'stash@{{0}}'",
    "mkdir -p {main}/.git/hk && printf '#!/bin/sh\\nunset GIT_DIR GIT_INDEX_FILE\\n"
    "git -C {wt} stash pop\\n' > {main}/.git/hk/pre-commit"
    " && chmod +x {main}/.git/hk/pre-commit && git -c core.hooksPath={main}/.git/hk commit -q"
    " --allow-empty -m x",
    # Escape-encoded text piped to a shell.
    "printf '\\x67it -C {wt} \\x73tash \\x70op' | bash",
    "printf 'git -C {wt} st\\141sh p\\157p' | bash",
    # A shell alias defined in the same command.
    "shopt -s expand_aliases\nalias g='git -C {wt}'\ng stash pop",
]


@pytest.mark.parametrize("template", ROUND6_RESTORES)
def test_round6_restores_are_refused(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt, main=main, gd=_gitdir(wt))
    first = cmd.split()[0]
    needs = [p for p in ("vim", "perl", "make", "sed") if p in cmd.split()]
    if any(not shutil.which(p) for p in needs) or first == "vim":
        assert _blocked(cmd, main), cmd  # static only: vim may be absent or interactive
        return
    _assert_real_restore(cmd, main, wt)
