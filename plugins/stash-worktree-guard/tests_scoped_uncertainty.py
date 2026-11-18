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

# These commands may run Git, but no stash/protected-ref mutation; unlike ALLOW they
# are not inputs to the no-Git-execution proof below.
WRAPPER_DATA = {
    "broker-refspec": "token-broker exec --scope user/github --reason x -- git push origin HEAD:fix/stash-guard-scoped-uncertainty",
    "broker-colon-refspec": "token-broker exec --scope user/github --reason x -- git push origin HEAD:stash-topic",
    "unknown-wrapper-refspec": "launcher exec --unknown -- git push origin HEAD:fix/stash-topic",
    "suffix-wrapper-prose": r'''git commit -m "proof used \`git archive\`"; token-broker exec --scope user/github --reason push -- env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper "GIT_CONFIG_VALUE_0=!gh auth git-credential" git push origin HEAD:topic''',
    "suffix-wrapper-awk": r'''awk '/git archive`/' patch; token-broker exec --scope user/github --reason push -- env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper "GIT_CONFIG_VALUE_0=!gh auth git-credential" git push origin HEAD:topic''',
}


@pytest.mark.parametrize("cmd", WRAPPER_DATA.values(), ids=WRAPPER_DATA.keys())
def test_wrapper_resolution_uncertainty_is_local(wt, cmd):
    assert guard.refuse_reason(cmd, str(wt)) is None
    assert not _blocked(cmd, wt)


REFUSE = {
    "broker-literal-pop": 'token-broker exec --scope user/github --reason x -- git stash pop',
    "broker-literal-push": 'token-broker exec --scope user/github --reason x -- git stash push',
    "broker-joined-stash": 'token-broker exec --scope user/github --reason x -- git st""ash drop',
    "broker-ansi-stash": r"token-broker exec --scope user/github --reason x -- git $'\x73tash' pop",
    "broker-parameter-stash": 'S=refs/stash; token-broker exec --scope user/github --reason x -- git "${S#refs/}" pop',
    "broker-env-string": "token-broker exec --scope user/github --reason x -- env -S 'git stash pop'",
    "broker-dashed-stash": 'token-broker exec --scope user/github --reason x -- /usr/lib/git-core/git-stash pop',
    "broker-alias": "token-broker exec --scope user/github --reason x -- git -c alias.undo='stash pop' undo",
    "broker-protected-ref": 'token-broker exec --scope user/github --reason x -- git push origin HEAD:refs/stash',
    "truncated-case-dynamic-op": 'echo "$(case a in a) git stash $OP;; esac)"',
    "wrapper-dynamic-mutation": 'G=$(command -v git); S=$(printf st""ash); token-broker exec --scope user/github --reason test -- "$G" "$S" drop',
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

# Review round 1 (PR #252 @64209eb2): a quoted unknown head of a wrapper's argv may be git itself.
# Scoping its uncertainty must still read that argv as a Git argv, not as text alone.
_GSO = "G=$(command -v git); S=$(printf stash); O=$(printf pop); "
WRAPPED_GIT = {
    "wrapped-flock": _GSO + 'flock /tmp/l "$G" "$S" "$O"',
    "wrapped-flock-cmdsub": 'S=$(printf stash); O=$(printf pop); flock /tmp/l "$(command -v git)" "$S" "$O"',
    "wrapped-xargs-herestring": 'G=$(printf git); S=$(printf stash); xargs "$G" "$S" <<< pop',
    "wrapped-setsid": _GSO + 'setsid -w "$G" "$S" "$O"',
    "wrapped-ionice": _GSO + 'ionice -c3 "$G" "$S" "$O"',
    "wrapped-taskset": _GSO + 'taskset -c 0 "$G" "$S" "$O"',
    "wrapped-strace": _GSO + 'strace -o /dev/null "$G" "$S" "$O"',
    "wrapped-chronic": _GSO + 'chronic "$G" "$S" "$O"',
    "wrapped-unbuffer": _GSO + 'unbuffer "$G" "$S" "$O"',
    "wrapped-uv-run": _GSO + 'uv run "$G" "$S" "$O"',
    "wrapped-systemd-run": _GSO + 'systemd-run --user --wait "$G" "$S" "$O"',
    "wrapped-unknown-program": _GSO + 'foo "$G" "$S" "$O"',
    "wrapped-parallel": _GSO + 'parallel "$G" ::: "$S"',
    "wrapped-escaped-stash": "G=$(command -v git); S=$(printf '\\x73tash'); flock /tmp/l \"$G\" \"$S\" pop",
    "wrapped-split-quotes": "G=$(command -v git); S=$(printf st\"\"ash); O=$(printf pop); flock /tmp/l \"$G\" \"$S\" \"$O\"",
}
# Round 2: preserve every mutation fallback, not only pop/apply or raw stash text.
WRAPPED_MUTATIONS = {
    f"mutation-{encoding}-{op}": f'G=$(command -v git); S=$(printf {encoded}); flock /tmp/l "$G" "$S" {op}'
    for encoding, encoded in [("quotes", 'st""ash'), ("escape", r"st\ash"),
                              ("hex", r"'\x73tash'")]
    for op in ("drop", "clear", "push", "save")
}
WRAPPED_MUTATIONS["mutation-reflog"] = (
    'G=$(command -v git); C=$(printf reflog); echo "reflog expire --expire=now --all"; '
    'flock /tmp/l "$G" "$C" expire --expire=now --all'
)
# Review round 4 (PR #252 @1dfbb5a9): a wrapper suffix whose resolution fails stays local, but its
# stdin is part of that argv's input (an appending runner turns it into Git words), and unread
# input (`< file`, `xargs -a file`) is never local evidence.
WRAPPER_STDIN = {
    "wrapper-stdin-pipe": "printf 'stash pop' | flock /tmp/l env A=1 xargs git",
    "wrapper-stdin-nice": "printf 'stash pop' | flock /tmp/l nice -n 1 env A=1 xargs git",
    "wrapper-stdin-heredoc": "flock /tmp/l env A=1 xargs git <<'EOF'\nstash pop\nEOF",
    "wrapper-stdin-herestring": "flock /tmp/l env A=1 xargs git <<< 'stash pop'",
    "wrapper-stdin-token-broker": "printf 'stash pop' | token-broker exec --scope user/github "
                                  "--reason t -- env A=1 xargs git",
    "wrapper-stdin-file": "printf 'stash pop' > args.txt; flock /tmp/l env A=1 xargs git < args.txt",
    "wrapper-xargs-a-file": "printf 'stash pop' > args.txt; flock /tmp/l env A=1 xargs -a args.txt git",
}
REFUSE.update(WRAPPED_GIT)
REFUSE.update(WRAPPED_MUTATIONS)
REFUSE.update(WRAPPER_STDIN)
# Review round 5 (PR #252 @63314c7c): an unknown quoted head may be consumed by its wrapper
# (`env "$A"` with A=X=1, `flock L "$A"` with A=env), so the rest of the argv is the real
# command. Its suffix reading is not split again, so a nested runner or interpreter there
# (`xargs -a args git`) runs input the guard never analyzed: never scoped locally.
_ARGS = "printf 'stash pop' > args.txt; "
WRAPPER_DEFERRED = {
    "deferred-env-assign-xargs-a": "A=$(printf A=1); " + _ARGS + 'flock /tmp/l env "$A" xargs -a args.txt git',
    "deferred-env-assign-xargs-stdin": "A=$(printf A=1); " + _ARGS + 'flock /tmp/l env "$A" xargs git < args.txt',
    "deferred-env-assign-xargs-long": "A=$(printf A=1); " + _ARGS
                                      + 'flock /tmp/l env "$A" xargs --arg-file=args.txt git',
    "deferred-env-assign-xargs-path": "A=$(printf A=1); " + _ARGS + 'flock /tmp/l env "$A" /usr/bin/xargs -a args.txt git',
    "deferred-env-assign-dashdash": "A=$(printf A=1); " + _ARGS + 'flock /tmp/l env -- "$A" xargs -a args.txt git',
    "deferred-env-assign-nice": "A=$(printf A=1); " + _ARGS + 'flock /tmp/l env "$A" nice xargs -a args.txt git',
    "deferred-env-assign-timeout": "A=$(printf A=1); " + _ARGS + 'flock /tmp/l env "$A" timeout 5 xargs -a args.txt git',
    "deferred-flock-consumed-head": "A=$(printf env); " + _ARGS + 'flock /tmp/l "$A" xargs -a args.txt git',
    "deferred-env-assign-parallel": "A=$(printf A=1); " + _ARGS + 'flock /tmp/l env "$A" parallel -a args.txt git',
    "deferred-env-assign-python": "A=$(printf A=1); printf 'import os; os.system(\"git stash pop\")' > s.py; "
                                  'flock /tmp/l env "$A" python3 s.py',
    # The runner or interpreter named through a known variable: classified by the value
    # dispatch sees, not by its literal spelling (round-6 review 5948911889).
    "deferred-known-var-xargs": "X=xargs; A=$(printf A=1); " + _ARGS + 'flock /tmp/l env "$A" "$X" -a args.txt git',
    "deferred-known-var-xargs-braced": "X=xargs; A=$(printf A=1); " + _ARGS
                                       + 'flock /tmp/l env "$A" "${X}" -a args.txt git',
    "deferred-known-var-xargs-unquoted": "X=xargs; A=$(printf A=1); " + _ARGS
                                         + 'flock /tmp/l env "$A" $X -a args.txt git',
    "deferred-known-var-xargs-path": "X=/usr/bin/xargs; A=$(printf A=1); " + _ARGS
                                     + 'flock /tmp/l env "$A" "$X" -a args.txt git',
    "deferred-known-var-xargs-after-nice": "X=xargs; A=$(printf A=1); " + _ARGS
                                           + 'flock /tmp/l env "$A" nice "$X" -a args.txt git',
    "deferred-known-var-python": "X=python3; A=$(printf A=1); "
                                 "printf 'import os; os.system(\"git stash pop\")' > s.py; "
                                 'flock /tmp/l env "$A" "$X" s.py',
    "deferred-known-var-flock-head": "X=xargs; A=$(printf env); " + _ARGS
                                     + 'flock /tmp/l "$A" "$X" -a args.txt git',
}
REFUSE.update(WRAPPER_DEFERRED)
# A word naming a shell function defers too: its body is not analyzed at this depth.
# (Conservative only: env cannot run a function, so there is no real-bash proof.)
REFUSE["deferred-function-name"] = ("x() { xargs \"$@\"; }; A=$(printf A=1); " + _ARGS
                                    + 'flock /tmp/l env "$A" x -a args.txt git')
ALLOW["deferred-control-known-var-grep"] = ("X=grep; A=$(printf A=1); " + _ARGS
                                            + 'flock lock env "$A" "$X" -c pop args.txt')
ALLOW["deferred-control-known-var-cat"] = ("X=cat; A=$(printf A=1); " + _ARGS
                                           + 'flock lock env "$A" "$X" args.txt')
# Controls: the same consumed head before a program that runs nothing stays allowed.
ALLOW["deferred-control-grep"] = "A=$(printf A=1); " + _ARGS + 'flock lock env "$A" grep -c pop args.txt'
ALLOW["deferred-control-cat"] = "A=$(printf A=1); " + _ARGS + 'flock lock env "$A" cat args.txt'


@pytest.mark.parametrize("name", [n for n in WRAPPED_MUTATIONS if n.endswith("-drop")])
def test_refused_mutation_really_drops_shared_stash(wt, tmp_path, name):
    import shutil
    if not shutil.which("flock"):
        pytest.skip("flock not installed")
    main = wt.parent / "repo"
    assert guard.refuse_reason(REFUSE[name], str(wt)) is not None
    result = subprocess.run(
        ["bash", "-c", REFUSE[name].replace("/tmp/l", str(tmp_path / "lock"))],
        cwd=wt, capture_output=True, text=True, check=True)
    assert "Dropped refs/stash@{0}" in result.stdout
    assert subprocess.run(["git", "stash", "list"], cwd=main,
                          capture_output=True, text=True, check=True).stdout == ""

# Wrappers from util-linux/findutils run for real in the restore proof below (skipped if absent).
_REAL_PROG = {"wrapped-flock": "flock", "wrapped-flock-cmdsub": "flock",
              "wrapped-xargs-herestring": "xargs", "wrapped-setsid": "setsid",
              "wrapped-ionice": "ionice", "wrapped-taskset": "taskset",
              **{n: "flock" for n in WRAPPER_STDIN if n != "wrapper-stdin-token-broker"},
              **{n: "flock" for n in WRAPPER_DEFERRED if n != "deferred-env-assign-parallel"}}


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


def test_truncated_case_dynamic_operation_really_restores(wt):
    cmd = REFUSE["truncated-case-dynamic-op"]
    assert guard.refuse_reason(cmd, str(wt)) is not None
    subprocess.run(["bash", "-c", cmd], cwd=wt, env={**os.environ, "OP": "pop"},
                   check=True, capture_output=True, text=True)
    assert (wt / "f.txt").read_text() == "stashed\n"
    assert subprocess.run(["git", "stash", "list"], cwd=wt.parent / "repo",
                          check=True, capture_output=True, text=True).stdout == ""


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


@pytest.mark.parametrize("name", ["split-head", "stdin-pipe", "stdin-heredoc", "stdin-file",
                                  *_REAL_PROG])
def test_refused_suffix_shapes_really_restore(wt, tmp_path, name):
    """The refusal is not conservatism alone: run in bash, these pop main's stash into the worktree."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "runit").write_text('#!/bin/sh\nexec "$@"\n')  # an unmodeled program that runs its argv
    (fakebin / "runit").chmod(0o755)
    main = wt.parent / "repo"
    prog = _REAL_PROG.get(name)
    if prog and not any(os.access(os.path.join(d, prog), os.X_OK) for d in ("/usr/bin", "/bin")):
        pytest.skip(f"{prog} not installed")
    assert subprocess.run(["git", "stash", "list"], cwd=main, capture_output=True, text=True).stdout
    _bash(REFUSE[name].replace("/tmp/l", str(tmp_path / "lock")), wt, fakebin, tmp_path)
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
