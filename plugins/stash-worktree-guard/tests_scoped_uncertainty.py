"""Scoped uncertainty: no phantom `git stash pop` from text outside a Git argv.

Run: python3 -m pytest -q -p no:cacheprovider tests_scoped_uncertainty.py

Reported 2026-11-15 (6th false-refusal card of this class): a read-only awk/grep diagnostic with no
Git call was refused as `git stash pop`. Root cause: one locally scoped uncertainty (an unknown
loop/`read` value at the head of a hypothetical argv suffix of `stat`) set whole-command uncertainty,
and the whole-command fallback then scanned every byte of the command, so `--git a/.../{` inside an
awk regex read as `git <dynamic subcommand>`. Each case below is refused by the guard at 3381108f and
pins one of the four mechanisms that keep a refusal tied to a Git argv at command position:

* A: a non-splitting unknown head of a suffix reading keeps its uncertainty local to that argv
  (plus the stdin text it may read);
* B: a loop that does not change a variable (`for f in a b` around a growing inner loop) keeps its
  value known per partition instead of joining it to unknown;
* C: whole-text `git <dynamic word>` evidence requires `git` as a command word, not `--git`/`.git`;
* D: a command word whose directory alone is unknown (`~/bin/tool`, HOME unknown) names its program.

Every REFUSE case must stay refused, and the real-bash proofs show they really restore.
"""

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


plugin = _load("stash_worktree_guard_plugin_scoped", "__init__.py")
guard = plugin.guard

# The exact two-line command from the report (cwd was the scratch dir; the files need not exist).
REPORTED = (
    r"""awk '/^diff --git a\/home\/bin\/hermes-core-wt-land/{f=1;next} /^diff --git/{f=0} f && /^\+[^+]/' edbe39-3206-diff.patch | sed 's/^+//' | awk 'length($0)>45' | grep -v -E '^[[:space:]]*#' > edbe39-3206-added.txt"""
    "\n"
    r"""for f in ~/.hermes/bin/hermes-core-wt-land ~/.hermes/lib/core-tools/hermes-core-wt-land; do [ -e "$f" ] || continue; r=""; while IFS= read -r l; do grep -q -F -- "$l" "$f" && r="${r}H" || r="${r}M"; done < edbe39-3206-probe.txt; echo "hub $f $(stat -c %y "$f" | cut -c1-19) $r"; done"""
)

ALLOW = {
    "reported": REPORTED,
    # A: `stat` is unmodeled, so its argv gets suffix readings; "$l" stays one word.
    "A-suffix-head": "while read -r l; do stat -c %y \"$l\"; done < probe.txt; echo 'git stash pop'",
    "A-suffix-head-pipeline": "read -r f < list.txt; stat -c %y \"$f\" | cut -c1-19; "
                              "printf '%s\\n' 'git stash pop' > notes.txt",
    # B: the inner loop grows r forever; f is the same two values on entry and after.
    "B-loop-invariant": "for f in /bin/true /usr/bin/true; do r=''; while IFS= read -r l; do r=\"${r}H\"; "
                        "done < probe.txt; \"$f\"; done; echo 'git stash pop'",
    # C: `--git a/{` is an option and a path, not a Git command word.
    "C-option-not-command": ". ./env.sh; awk '/^diff --git a\\/{x}/' p.patch",
    # D: after `eval` of unknown text HOME is unknown, but the program is still hermes-core-wt.
    "D-fixed-basename": "eval \"$(~/.hermes/bin/hermes-core-wt env)\" && "
                        "timeout 500 ~/.hermes/bin/hermes-core-wt test tests/x.py",
}

REFUSE = {
    # An unquoted unknown suffix head may split into a command built elsewhere.
    "split-head": "X=$(printf 'git stash pop'); runit $X",
    # A quoted unknown head reads stdin: pipe, here-doc and an unread file all stay covered.
    "stdin-pipe": "G=$(printf git); echo stash pop | xargs \"$G\"",
    "stdin-heredoc": "G=$(printf git); xargs \"$G\" <<'EOF'\nstash pop\nEOF",
    "stdin-file": "printf 'stash pop' > args.txt; G=$(printf git); xargs \"$G\" < args.txt",
    # The loop really changes r: it stays unknown.
    "loop-changes": "for r in a b; do while read l; do r=\"$r $l\"; done < /dev/null; git -C $r status; done",
    # D never hides git itself, nor a directory part that may split.
    "fixed-git": "eval \"$(x)\" && ~/bin/git stash pop",
    "quoted-dir-git": "D=$(x); \"$D\"/git stash pop",
    "unquoted-dir": "D=$(printf 'git stash pop'); $D/tool",
    # C keeps a real dynamic Git subcommand after unseen code.
    "source-dynamic": ". ./env.sh; git ${sub}",
    "source-literal": ". ./env.sh; echo hi; git -C /x stash pop",
    "eval-then-pop": "eval \"$(x)\"; git stash pop",
}


@pytest.fixture
def wt(tmp_path, monkeypatch):
    """A main checkout with a stash plus a linked worktree on another branch; returns the worktree."""
    for key in ("STASH_WORKTREE_GUARD_DISABLE", "STASH_WORKTREE_GUARD", "TERMINAL_CWD"):
        monkeypatch.delenv(key, raising=False)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    main = tmp_path / "repo"
    main.mkdir()

    def git(*a, cwd=main):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=env)

    git("init", "-q", "-b", "main")
    (main / "f.txt").write_text("base\n")
    git("add", ".")
    git("commit", "-qm", "base")
    git("branch", "other")
    tree = tmp_path / "wt"
    git("worktree", "add", "-q", str(tree), "other")
    (main / "f.txt").write_text("stashed\n")
    git("stash", "push", "-q", "-m", "from-main")
    return tree


def _blocked(cmd, cwd):
    got = plugin._pre_tool_call(tool_name="terminal", args={"command": cmd, "workdir": str(cwd)})
    return bool(got and got["action"] == "block")


@pytest.mark.parametrize("name", sorted(ALLOW))
def test_no_phantom_restore_outside_a_git_argv(wt, name):
    cmd = ALLOW[name]
    assert guard.refuse_reason(cmd, str(wt)) is None, cmd
    assert not _blocked(cmd, wt), cmd


def test_reported_command_from_its_original_cwd(tmp_path):
    assert guard.refuse_reason(REPORTED, str(tmp_path)) is None
    assert not any(op == "pop" for _repo, op, _a, _n in guard._stash_invocations(REPORTED, str(tmp_path)))


@pytest.mark.parametrize("name", sorted(REFUSE))
def test_real_and_conservative_restores_stay_refused(wt, name):
    cmd = REFUSE[name]
    assert guard.refuse_reason(cmd, str(wt)) is not None, cmd
    assert _blocked(cmd, wt), cmd


def _bash(cmd, cwd, path_prefix, home):
    env = {"PATH": f"{path_prefix}:/usr/bin:/bin", "HOME": str(home), "LC_ALL": "C",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    subprocess.run(["bash", "-c", cmd], cwd=cwd, env=env, capture_output=True, timeout=30,
                   stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("name", ["split-head", "stdin-pipe", "stdin-heredoc", "stdin-file"])
def test_refused_suffix_shapes_really_restore(wt, tmp_path, name):
    """The refusal is not conservatism alone: run in bash, these pop main's stash into the worktree."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "runit").write_text('#!/bin/sh\nexec "$@"\n')  # an unmodeled program that runs its argv
    (fakebin / "runit").chmod(0o755)
    main = wt.parent / "repo"
    assert subprocess.run(["git", "stash", "list"], cwd=main, capture_output=True, text=True).stdout
    _bash(REFUSE[name], wt, fakebin, tmp_path)
    assert subprocess.run(["git", "stash", "list"], cwd=main, capture_output=True, text=True).stdout == ""
    assert (wt / "f.txt").read_text() == "stashed\n"  # another worktree's change landed here


@pytest.mark.parametrize("name", sorted(ALLOW))
def test_allowed_shapes_run_no_git(wt, tmp_path, name):
    """Soundness of each allow: in bash (with a recording fake git first on PATH) no git runs."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    mark = tmp_path / "git-argv.log"
    (fakebin / "git").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {mark}\n")
    (fakebin / "git").chmod(0o755)
    (wt / "env.sh").write_text("")
    (wt / "probe.txt").write_text("a\nb\n")
    (wt / "list.txt").write_text(f"{wt}/f.txt\n")
    _bash(ALLOW[name], wt, fakebin, tmp_path)
    assert not mark.exists() or mark.read_text() == "", mark.read_text()
    assert subprocess.run(["git", "stash", "list"], cwd=wt.parent / "repo",
                          capture_output=True, text=True).stdout != ""


@pytest.mark.parametrize("raw,base", [
    ("~/.hermes/bin/hermes-core-wt", "hermes-core-wt"),
    ("~user/bin/tool", "tool"),
    ('"$D"/bin/python3', "python3"),
    ('"$(cmd)"/x', "x"),
    ("$D/tool", None),            # unquoted: may split into a whole command
    ("/opt/*/bin/python3", None),  # glob: may expand to several words (or none, under nullglob)
    ("~/{a,b}/tool", None),        # brace expansion
    ('"$@"/tool', None),           # one word per positional parameter
    ('"$D/tool"', None),           # last component not a plain literal word
    ("~/bin/$T", None),            # last component unknown
    ("tool", None),                # no directory part
    ("\"$D/x\"y/tool", "tool"),
])
def test_fixed_basename(raw, base):
    assert guard._fixed_basename(raw) == base
