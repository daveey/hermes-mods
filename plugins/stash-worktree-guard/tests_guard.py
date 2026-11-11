"""Tests for stash-worktree-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import functools
import importlib.util
import os
import re
import shutil
import shlex

# Explicit Git execution slots; every unsafe execution below uses disposable repositories.
NESTED_RUNNERS = {
    "foreach": "git -c r.repo={target} for-each-repo --config=r.repo {implicit}",
    "bisect": "git bisect run {process}",
    "helper": "git submodule--helper foreach {shell}",
    "public": "git submodule foreach {shell}",
    "difftool": "git difftool -y --extcmd={diffshell}",
    "rebase": "git rebase --exec {shell} HEAD~1",
}


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


def _newest_bash():
    """(major, path) of the newest bash here. Restore proofs run in it, not in PATH's first
    bash: macOS's /bin/bash 3.2 (first on PATH in some sessions) lacks coproc, mapfile,
    BASH_CMDS, ${x@P} and `case` inside $(...)."""
    best = (0, "bash")
    for cand in dict.fromkeys(filter(None, (shutil.which("bash"), "/opt/homebrew/bin/bash",
                                            "/usr/local/bin/bash", "/bin/bash"))):
        try:
            out = subprocess.run([cand, "-c", 'echo "${BASH_VERSINFO[0]}"'], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if out.isdigit() and int(out) > best[0]:
            best = (int(out), cand)
    return best


BASH_MAJOR, BASH = _newest_bash()


def _proof_bash():
    """bash for proving a refused command really restores. Call it only after the refusal is
    asserted: without bash >= 4 the proof is skipped, never the refusal."""
    if BASH_MAJOR < 4:
        pytest.skip(f"restore proof needs bash >= 4 (newest here: {BASH}, major {BASH_MAJOR}); "
                    "refusal checked")
    return BASH


# String-running modes some builds lack: BSD sed has no `e` command or `s///e` flag, Xcode's
# GNU make 3.81 has no --eval, and macOS ships no flock or setsid. A case needing a mode the
# host lacks checks the refusal only, like a missing program.
HOST_PROBES = {
    "sed": "echo x | sed 'e true' && echo true | sed 's/^//e'",
    "make": "make -f /dev/null --eval 'all:;@true' all",
    "setsid": "setsid -w true",
}


@functools.cache
def _host_runs(prog):
    if not shutil.which(prog):
        return False
    probe = HOST_PROBES.get(prog)
    return probe is None or subprocess.run([BASH, "-c", probe], capture_output=True,
                                           timeout=30).returncode == 0


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=GIT_ENV)


def init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True)
    git("init", "-q", "-b", branch, cwd=path)
    (path / "f.txt").write_text("base\n")
    git("add", ".", cwd=path)
    git("commit", "-qm", "base", cwd=path)


# The report's abbreviated command was already allowed. The missing trigger was the
# word "alias" in Python output text: the conservative remapping pre-scan sees it,
# but an executable pathname must never become a hash/alias-selected git command.
# Adjacent literals split the SQLite URI mode below to keep the repo's source-code
# connection scanner out of inert shell data; the analyzer receives the original URI.
PATHNAME_DATA_ALLOW = {
    "quoted_non_git_argument": 'git stash list; echo alias; /usr/bin/python3 - "$X"',
    "quoted_heredoc": '''git stash list; /usr/bin/python3 - "$X" <<'EOF'
print("alias 'data'")
EOF
''',
    "parameter_default": 'git stash list; echo "${X:-default} alias"; /usr/bin/python3 - "$X"',
    "reported_shape": '''F=/scratch/fixture; for r in main other; do echo "fixture $r: stash entries $(git -C $F/$r stash list | wc -l | tr -d ' ') | head $(git -C $F/$r log --oneline -1) | changed files $(git -C $F/$r status --porcelain | wc -l | tr -d ' ')"; done; for r in main other; do git -C $F/$r worktree remove --force $F/$r-wt; done; rm -rf "$F"; L=$(find /scratch/cache -maxdepth 4 -name ledger.db -path '*chat-closeout*' 2>/dev/null | head -1); echo "ledger: ${L:-not found}"; [ -n "$L" ] && /usr/bin/python3 - "$L" <<'EOF'
import json, sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=''' '''ro", uri=True)
print(con.execute("select name from sqlite_master where type='table'").fetchall())
print("legacy alias ids with a canonical sibling")
EOF
''',
}


@pytest.mark.parametrize("case", PATHNAME_DATA_ALLOW)
def test_pathname_data_not_remapped(multi, case):
    assert guard.refuse_reason(PATHNAME_DATA_ALLOW[case], str(multi[1])) is None


@pytest.mark.parametrize("cmd", [
    'git $SUB pop', 'git stash $X',
    'echo alias; python3 - "$X"',
    'BASH_CMDS[python3]=$(command -v git); python3 stash pop',
    "alias g='git'; g stash pop",
    'echo alias; /usr/bin/git stash pop',
    'echo alias; P=/usr/bin/git; "$P" stash pop',
])
def test_pathname_fix_preserves_remapping_and_git_refusals(multi, cmd):
    assert guard.refuse_reason(cmd, str(multi[1])) is not None


@pytest.mark.parametrize("options", [
    "-p", "-l", "-a", "-o timing.txt", "-h", "--portability", "--append",
    "--output timing.txt", "--output=timing.txt", "--format %e", "--format=%e",
    "--verbose", "-f %e", "--quiet", "--help", "--version", "--unknown",
])
def test_external_time_preserves_caller_state(multi, options):
    cmd = f'/usr/bin/time {options} true; git -C $HOME/x log -1'
    assert guard.refuse_reason(cmd, str(multi[1])) is None


@pytest.mark.parametrize("option", ["--unknown", "-Z"])
def test_external_time_parse_error_keeps_opaque_alias_evidence(multi, option):
    wt = shlex.quote(str(multi[1]))
    wrapper = f'/usr/bin/time {option} true'
    # The time boundary already preserves caller state; EXTERNAL_WRAPPERS must also
    # retain opaque-child alias evidence for later non-builtin Git subcommands.
    reason = guard.refuse_reason(f'{wrapper}; git -C {wt} custom', str(multi[1]))
    assert reason is not None and "opaque child" in reason
    assert wrapper in reason and "custom" in reason
    assert guard.refuse_reason(f'{wrapper}; git -C {wt} status', str(multi[1])) is None


@pytest.mark.parametrize("option", ["-o", "-f", "--output", "--format"])
def test_external_time_option_operand_is_not_a_command(multi, option):
    # `git` is the output filename/format, not the executable. Leaving it in argv
    # invents a Git restore that the original command does not invoke.
    cmd = f'/usr/bin/time {option} git stash pop'
    assert guard.refuse_reason(cmd, str(multi[1])) is None


@pytest.mark.parametrize("option", ["-o", "-f", "--output", "--format"])
def test_external_time_option_operand_preserves_known_git_target(multi, option):
    main, wt = multi
    # Unlike pop, same-branch apply is allowed: a fallback's unresolved repo would
    # falsely refuse it, so a literal unsafe restore alone cannot pin option arity.
    allowed = f'/usr/bin/time {option} timing.txt git -C {shlex.quote(str(main))} stash apply'
    refused = f'/usr/bin/time {option} timing.txt git -C {shlex.quote(str(wt))} stash apply'
    assert guard.refuse_reason(allowed, str(wt)) is None
    assert guard.refuse_reason(refused, str(wt)) is not None


@pytest.mark.parametrize("wrapper,changes_parent", [
    ("/usr/bin/time -p", False),
    ("command time -p", False),
    ("/usr/bin/env /usr/bin/time -p", False),
    ("time -p", True),
    # Bash permits pathname function names; function lookup wins over wrapper parsing.
    ("/usr/bin/time() { cd /; }; /usr/bin/time -p", True),
    ("function time { cd /; }; command time -p", False),
])
def test_time_child_boundary_cwd_matches_bash(multi, wrapper, changes_parent):
    main, _wt = multi
    cmd = f'{wrapper} cd /; pwd'
    result = subprocess.run([BASH, "-c", cmd], cwd=main, env=GIT_ENV,
                            capture_output=True, text=True, timeout=30)
    expected = "/" if changes_parent else str(main)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    analyzer = guard._Analyzer(str(main))
    states = analyzer.run_text(cmd, [guard._State(str(main), {"PWD": str(main)}, set(), {})], 0)
    assert states  # inspect the same modeled state used by subsequent Git commands
    assert {state.cwd for state in states} == {expected}


@pytest.mark.parametrize("wrapper", ["/usr/bin/time -p", "command time -p"])
def test_time_child_boundary_preserves_unset_variable(multi, wrapper):
    main, wt = multi
    cmd = f'X={shlex.quote(str(wt))}; {wrapper} unset X; printf "%s\\n" "$X"'
    result = subprocess.run([BASH, "-c", cmd], cwd=main, env=GIT_ENV,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(wt)
    analyzer = guard._Analyzer(str(main))
    states = analyzer.run_text(cmd, [guard._State(str(main), {"PWD": str(main)}, set(), {})], 0)
    assert states and {state.vars.get("X") for state in states} == {str(wt)}


@pytest.mark.parametrize("operation", ["cd /", "unset X"])
def test_time_child_boundary_keeps_real_multiworktree_restore(multi, operation):
    main, wt = multi
    cmd = (f'X={shlex.quote(str(wt))}; command time -p {operation}; '
           'git -C "$X" stash pop')
    _assert_real_restore(cmd, main, wt)


def test_external_time_preserves_interpreter_path(multi):
    cmd = '''/usr/bin/time -p true -k plugins/other;
"$HOME/Applications/Hermes Python.app/Contents/MacOS/python3.14" - <<'EOF'
print(1)
EOF
'''
    assert guard.refuse_reason(cmd, str(multi[1])) is None


@pytest.mark.parametrize("wrapper", ["/usr/bin/time -p", "time -p"])
def test_external_time_keeps_stash_refusal(multi, wrapper):
    cmd = f'{wrapper} git -C "{multi[1]}" stash pop'
    assert guard.refuse_reason(cmd, str(multi[1])) is not None


@pytest.mark.parametrize("substitution", ["<(git -C $M show HEAD:a)",
                                           ">(git -C $M show HEAD:a)",
                                           "$(git -C $M show HEAD:a)"])
def test_process_substitution_inherits_shell_state(multi, substitution):
    cmd = f'M=/x; cmp -s y {substitution}'
    assert guard.refuse_reason(cmd, str(multi[1])) is None


@pytest.mark.parametrize("cmd", [
    'M=/x; cmp -s y <(git -C $M stash pop)',
    'cmp y <(git -C $UNSET show HEAD)',
])
def test_process_substitution_keeps_uncertain_and_restore_refusals(multi, cmd):
    assert guard.refuse_reason(cmd, str(multi[1])) is not None


def test_process_substitution_real_restore(multi):
    main, wt = multi
    # Drain to EOF and wait: cmp's early mismatch must not SIGPIPE git's output.
    cmd = (f'M={shlex.quote(str(wt))}; '
           'cmp -s /dev/null <(git -C "$M" stash pop >/dev/null); wait "$!"')
    _assert_real_restore(cmd, main, wt)


# Non-POSIX bash looks functions up by name even when the name contains a slash; only
# alias and hash remapping are skipped for pathnames.
SLASH_FUNCTION_RESTORES = [
    "/usr/bin/python3() {{ git -C {wt} stash pop; }}; /usr/bin/python3",
    "function /usr/bin/python3 {{ git -C {wt} stash pop; }}; /usr/bin/python3 - x",
    "./x() {{ git -C {wt} stash pop; }}; ./x",
    "function a/b() {{ git -C {wt} stash pop; }}; a/b",
    "F=/usr/bin/python3; /usr/bin/python3() {{ git -C {wt} stash pop; }}; \"$F\"",
    # `unset -v` reports an invalid identifier and leaves the function defined.
    "/usr/bin/python3() {{ git -C {wt} stash pop; }}; unset -v /usr/bin/python3; /usr/bin/python3",
]


@pytest.mark.parametrize("template", SLASH_FUNCTION_RESTORES)
def test_slash_named_functions_are_analyzed(multi, template):
    main, wt = multi
    assert guard.refuse_reason(template.format(wt=wt), str(wt)) is not None
    _assert_real_restore(template.format(wt=wt), main, wt)


def test_slash_named_function_controls(multi):
    main, wt = multi
    for cmd in (
        f"/usr/bin/python3() {{ git -C {wt} stash list; }}; /usr/bin/python3",
        f"/usr/bin/python3() {{ git -C {main} stash apply; }}; /usr/bin/python3",
        f"/usr/bin/python3() {{ git -C {wt} stash pop; }}; command /usr/bin/python3 -c pass",
        f"/usr/bin/python3() {{ git -C {wt} stash pop; }}; unset -f /usr/bin/python3; "
        "/usr/bin/python3 -c pass",
        f"./x() {{ git -C {wt} stash pop; }}; unset ./x; ./x",
        # The hash table is never consulted for a pathname: bash reports ./g not found.
        f"hash -p /usr/bin/git ./g; ./g -C {wt} stash pop",
    ):
        assert not _blocked(cmd, wt), cmd


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


def _git_version():
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=30).stdout
    return tuple(int(n) for n in re.search(r"(\d+)\.(\d+)\.(\d+)", out).groups())


@pytest.fixture
def nested_repo(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("GIT_", "STASH_WORKTREE_GUARD")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    env = {**os.environ, **{k: v for k, v in GIT_ENV.items()
                           if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}}

    def run(cwd, *args, check=True):
        p = subprocess.run(["git", *args], cwd=cwd, env=env, text=True,
                           capture_output=True, timeout=30)
        assert not check or p.returncode == 0, (args, p.stdout, p.stderr)
        return p

    def init(path):
        path.mkdir()
        run(path, "init", "-q", "-b", "main")
        (path / "f.txt").write_text("base\n")
        for n in range(3):
            (path / "g.txt").write_text(f"{n}\n")
            run(path, "add", ".")
            run(path, "commit", "-qm", f"commit-{n}")

    def make(site):
        main, target = tmp_path / "main", tmp_path / "other"
        init(main)
        run(main, "worktree", "add", "-qb", "other", str(target))
        origin = main
        cwd = main if site == "foreach" else target
        if site in ("helper", "public"):
            source = tmp_path / "source"
            init(source)
            run(main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub")
            run(main, "commit", "-qam", "submodule")
            cwd, target, origin = main, main / "sub", tmp_path / "origin"
            run(target, "worktree", "add", "-qb", "stash-origin", str(origin))
        (origin / "f.txt").write_text("stashed\n")
        run(origin, "stash", "push", "-qm", "origin")
        if site == "bisect":
            run(target, "bisect", "start", "--no-checkout", "HEAD", "HEAD~2")
        if site == "difftool":
            (target / "g.txt").write_text("trigger\n")
        assert run(target, "symbolic-ref", "--short", "HEAD").stdout != run(origin, "symbolic-ref", "--short", "HEAD").stdout
        assert run(target, "worktree", "list", "--porcelain").stdout.count("worktree ") == 2
        return cwd, target

    return make, run, env


@pytest.mark.parametrize("site,payload", [
    (site, payload) for site in NESTED_RUNNERS for payload in ("direct", "alias", "readonly")
    if (site, payload) != ("public", "alias")  # already refused by base name uncertainty
])
def test_nested_runner_real_execution(nested_repo, site, payload):
    make, run, env = nested_repo
    cwd, target = make(site)
    tail = "x" if payload == "alias" else "stash list" if payload == "readonly" else "stash pop"
    process = "git " + tail
    cmd = NESTED_RUNNERS[site].format(target=shlex.quote(str(target)), implicit=tail,
        process=process, shell=shlex.quote(process), diffshell=shlex.quote("sh -c " + shlex.quote(process)))
    if payload == "alias":
        cmd = cmd.replace("git ", "git -c alias.x='stash pop' ", 1)
    before = (run(target, "stash", "list").stdout, (target / "f.txt").read_text(),
              run(target, "status", "--porcelain").stdout)
    assert before[0].count("stash@{") == 1 and before[1] == "base\n"
    reason = guard.refuse_reason(cmd, str(cwd))
    hook = block("terminal", command=cmd, workdir=str(cwd))
    assert bool(reason) == (payload != "readonly"), (cmd, reason)
    assert bool(hook) == (payload != "readonly"), (cmd, hook)
    result = subprocess.run([BASH, "--noprofile", "--norc", "-c", cmd], cwd=cwd,
                            env=env, text=True, capture_output=True, timeout=30)
    after = (run(target, "stash", "list").stdout, (target / "f.txt").read_text(),
             run(target, "status", "--porcelain").stdout)
    if payload == "readonly":
        assert result.returncode == 0, result.stderr
        assert after == before
    else:
        assert after[0] == "" and after[1] == "stashed\n", (result, after)
        assert result.returncode == (1 if site == "rebase" else 0), result.stderr


NESTED_REFUSALS = [
    "git for-each-repo --config r.repo -- stash pop",
    "git for-each-repo --keep-going --no-keep-going --config=r.repo stash apply",
    'git for-each-repo --config="$KEY" x',
    "git for-each-repo --config=r.repo $COMMAND",
    "git for-each-repo --mystery value stash pop",
    "git bisect run sh -c 'git stash pop'",
    'git bisect run "$COMMAND"',
    "git rebase --exec '$COMMAND' HEAD~1",
    "git rebase --committer-date-is-author-date -x 'git stash pop' HEAD~1",
    "git submodule--helper foreach --quiet --recursive --super-prefix prefix -- 'git stash pop'",
    "git submodule foreach -q --recursive -- git stash pop",
    "git submodule foreach 'git stash pop; :' argument",
    "git submodule foreach --no-quiet --no-recursive 'git stash pop'",
    "git difftool --extcmd 'git stash pop'",
    "git difftool --extcmd='git stash pop'",
    "git difftool -x 'git stash pop'",
    "git difftool -x'git stash pop'",
    "git difftool --mystery -x 'git stash pop'",
    "git rebase --exec 'git stash pop' HEAD~1",
    "git rebase --exec='git stash pop' HEAD~1",
    "git rebase -x 'git stash pop' HEAD~1",
    "git rebase -x'git stash pop' HEAD~1",
    "git rebase -x 'git stash list' -x 'git stash pop' HEAD~1",
    "git rebase --exec \"$PROGRAM\" HEAD~1",
    "git -c alias.x='stash pop' for-each-repo --config=r.repo bisect run git x",
    "git -c alias.x='stash pop' rebase --exec 'git x' HEAD~1",
]


@pytest.mark.parametrize("cmd", NESTED_REFUSALS)
def test_nested_runner_parser_refusal(multi, cmd):
    main, _ = multi
    assert guard.refuse_reason(cmd, str(main)), cmd
    assert block("terminal", command=cmd, workdir=str(main)), cmd


@pytest.mark.parametrize("cmd", [
    "git bisect log", "git bisect run 'git stash pop'", "git bisect run stash pop",
    "git submodule--helper stash pop", "git log --grep='git stash pop'",
    "git difftool -- --extcmd='git stash pop'",
    "git rebase -- --exec='git stash pop'",
    "git difftool --tool '--extcmd=git stash pop'",
    "git rebase --onto '--exec=git stash pop' HEAD~1",
    "git -c alias.x='stash pop' for-each-repo --config=r.repo status --porcelain; git x",
    "git -c alias.hash-object='stash pop' hash-object f.txt",
])
def test_nested_runner_parser_allow(multi, cmd):
    main, _ = multi
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not block("terminal", command=cmd, workdir=str(main)), cmd


def test_nested_runner_budget_refusal(multi):
    main, _ = multi
    cmd = "git -c alias.x='stash pop' " + "for-each-repo --config=r.repo " * (guard.MAX_DEPTH + 2) + "x"
    assert guard.refuse_reason(cmd, str(main))
    assert block("terminal", command=cmd, workdir=str(main))


def test_nested_runner_existing_unknown_ref_control(multi):
    # Existing whole-command uncertainty refusal, not a new RED claim.
    cmd = 'git for-each-repo --config=r.repo stash pop "$REF"'
    assert guard.refuse_reason(cmd, str(multi[0]))
    assert block("terminal", command=cmd, workdir=str(multi[0]))


def test_nested_runner_step_budget_refusal(multi, monkeypatch):
    monkeypatch.setattr(guard, "MAX_STEPS", 1)
    cmd = "git -c alias.x='stash pop' for-each-repo --config=r.repo x"
    assert guard.refuse_reason(cmd, str(multi[0]))
    assert block("terminal", command=cmd, workdir=str(multi[0]))


@pytest.mark.parametrize("command", [
    "HOME=/missing git bisect run git x",
    "GIT_CONFIG_GLOBAL=/missing git bisect run git x",
    "git -c include.path=/missing bisect run git x",
    "git --config-env=alias.x=VALUE bisect run git x",
])
def test_nested_runner_inherited_taint_refusal(multi, command):
    assert guard.refuse_reason(command, str(multi[0]))
    assert block("terminal", command=command, workdir=str(multi[0]))


@pytest.mark.parametrize("command", [
    "git -c alias.x='stash pop' bisect run git status; git x",
    "git -c alias.x='stash pop' submodule--helper foreach 'git stash list'; git x",
    "git -c alias.x='stash pop' difftool -x 'true'; git x",
    "git -c alias.x='stash pop' rebase -x 'git stash list'; git x",
    "git for-each-repo --config=r.repo -- -c alias.x='stash pop' status; git x",
    "git bisect run sh -c 'cd /missing'; git stash apply",
])
def test_nested_runner_isolated_taint_allow(multi, command):
    assert guard.refuse_reason(command, str(multi[0])) is None
    assert not block("terminal", command=command, workdir=str(multi[0]))


def test_nested_runner_existing_public_alias_control(nested_repo):
    # Base already refuses the public script under alias uncertainty; not a new RED.
    test_nested_runner_real_execution(nested_repo, "public", "alias")


# difftool hands its tool two file names, so a `git stash pop|list` tool fails whatever the guard
# decides. What difftool exits with then depends on git: git 2.43 `git stash` prints the error but
# exits 0, so difftool exits 0; git 2.56 (ubuntu-latest CI) `git stash` exits 128 and difftool dies
# "external diff died, stopping at <file>" (128). A `--extcmd=false` probe can't stand in for it:
# difftool tolerates rc 1 (0 on 2.56 too). Rows marked TOOL_FAILED accept either status and check
# that the tool really ran and failed; the no-restore check is the real assertion.
TOOL_FAILED = (0, 128)


@pytest.mark.parametrize("site,command,refused,rc", [
    ("foreach", "git for-each-repo --config=missing.repo stash pop", True, 0),
    ("bisect", "git bisect run stash pop", False, 1),
    ("bisect", "git bisect run 'git stash pop'", False, 1),
    ("helper", "git submodule--helper stash pop", False, 129),
    ("difftool", "git difftool -y --extcmd='git stash pop'", True, TOOL_FAILED),
    ("difftool", "git difftool -y --extcmd='git stash list'", False, TOOL_FAILED),
    ("difftool-empty", "git difftool -y --extcmd='git stash pop'", True, 0),
    ("foreach", "git -c alias.x='stash pop' -c r.repo={target} for-each-repo --config=r.repo status --porcelain", False, 0),
    ("foreach", "git -c alias.hash-object='stash pop' hash-object f.txt", False, 0),
])
def test_nested_runner_real_nonrestoring_controls(nested_repo, site, command, refused, rc):
    make, run, env = nested_repo
    cwd, target = make(site)
    command = command.format(target=shlex.quote(str(target)))
    before = (run(target, "stash", "list").stdout, (target / "f.txt").read_text(),
              run(target, "status", "--porcelain").stdout)
    assert bool(guard.refuse_reason(command, str(cwd))) == refused
    assert bool(block("terminal", command=command, workdir=str(cwd))) == refused
    tool_failed = rc is TOOL_FAILED
    result = subprocess.run([BASH, "-c", command], cwd=cwd, env=env, capture_output=True, timeout=30)
    assert result.returncode in (rc if tool_failed else (rc,)), result.stderr
    if tool_failed:
        # The tool did run, on the changed file, and failed on the two names difftool appended.
        assert re.search(rb"Too many revisions specified|bad revision", result.stderr), result.stderr
    assert (run(target, "stash", "list").stdout, (target / "f.txt").read_text(),
            run(target, "status", "--porcelain").stdout) == before


@pytest.mark.parametrize("site,template", [
    ("foreach", "git -c r.repo={target} for-each-repo --config r.repo -- stash pop"),
    ("foreach", "git -c r.repo={target} for-each-repo --keep-going --no-keep-going --config=r.repo stash pop"),
    ("helper", "git submodule--helper foreach --quiet --recursive -- 'git stash pop'"),
    ("public", "git submodule foreach -q --recursive git stash pop"),
    ("public", "git submodule foreach 'git stash pop; :' unused"),
    ("helper", "git submodule--helper foreach --no-quiet --no-recursive 'git stash pop'"),
    ("difftool", "git difftool -y --extcmd \"sh -c 'git stash pop'\""),
    ("difftool", "git difftool -y -x \"sh -c 'git stash pop'\""),
    ("difftool", "git difftool -y -x\"sh -c 'git stash pop'\""),
    ("rebase", "git rebase --exec='git stash pop' HEAD~1"),
    ("rebase", "git rebase -x 'git stash pop' HEAD~1"),
    ("rebase", "git rebase -x'git stash pop' HEAD~1"),
    ("rebase", "git rebase -x 'git stash list' -x 'git stash pop' HEAD~1"),
])
def test_nested_runner_real_option_syntax(nested_repo, site, template):
    make, run, env = nested_repo
    cwd, target = make(site)
    command = template.format(target=shlex.quote(str(target)))
    assert guard.refuse_reason(command, str(cwd))
    assert block("terminal", command=command, workdir=str(cwd))
    assert run(target, "stash", "list").stdout.count("stash@{") == 1
    assert (target / "f.txt").read_text() == "base\n"
    if "--keep-going" in template and _git_version() < (2, 45, 3):
        # for-each-repo --keep-going first shipped in Git 2.45.3 (v2.45.2 rejects it); the guard
        # assertions above stay strict, only the real restore needs a Git that knows the option.
        pytest.skip("git for-each-repo --keep-going needs Git >= 2.45.3")
    result = subprocess.run([BASH, "-c", command], cwd=cwd, env=env, capture_output=True, timeout=30)
    assert (target / "f.txt").read_text() == "stashed\n", result.stderr
    assert run(target, "stash", "list").stdout == ""
    assert result.returncode == (1 if site == "rebase" else 0), result.stderr


# Git's use_shell runs `sh -c '<argv0> "$@"' <argv0> <operands...>` when argv0 has shell
# metacharacters, so later foreach operands reach the program as quoted data. Difftool's
# helper evals the field-split value, so a newline in --extcmd becomes a word separator.
@pytest.mark.parametrize("site,command,op", [
    ("helper", "git submodule--helper foreach 'git stash' pop", "pop"),
    ("public", "git submodule foreach 'exec git' stash pop", "pop"),
    ("public", "git submodule foreach 'git stash' apply", "apply"),
    ("helper", "git submodule--helper foreach 'GIT_TRACE=0 git stash' pop", "pop"),
    ("helper", "git -c alias.x='stash pop' submodule--helper foreach 'exec git' x", "pop"),
    ("helper", "git submodule--helper foreach 'git stash' -- pop", "pop"),
    ("helper", "git submodule--helper foreach 'git stash' --quiet pop", "pop"),
    ("difftool", "git difftool -y -x 'git stash\npop;:'", "pop"),
])
def test_nested_runner_real_positional_operands(nested_repo, site, command, op):
    make, run, env = nested_repo
    cwd, target = make(site)
    assert run(target, "stash", "list").stdout.count("stash@{") == 1
    assert (target / "f.txt").read_text() == "base\n"
    assert guard.refuse_reason(command, str(cwd)), command
    assert block("terminal", command=command, workdir=str(cwd)), command
    result = subprocess.run([BASH, "--noprofile", "--norc", "-c", command], cwd=cwd, env=env,
                            text=True, capture_output=True, timeout=30)
    assert (target / "f.txt").read_text() == "stashed\n", (result.stdout, result.stderr)
    assert run(target, "stash", "list").stdout.count("stash@{") == (1 if op == "apply" else 0)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("site,command", [
    ("helper", "git submodule--helper foreach 'git stash' list"),
    ("public", "git submodule foreach 'git stash list' --oneline"),
    ("public", "git submodule foreach 'echo' stash pop"),
    ("helper", "git submodule--helper foreach 'git stash' -- list"),
    ("helper", "git -c alias.x='stash pop' submodule--helper foreach 'git status' -- --porcelain"),
    ("difftool", "git difftool -y -x 'git stash\nlist;:'"),
])
def test_nested_runner_real_positional_readonly(nested_repo, site, command):
    make, run, env = nested_repo
    cwd, target = make(site)
    before = (run(target, "stash", "list").stdout, (target / "f.txt").read_text(),
              run(target, "status", "--porcelain").stdout)
    assert guard.refuse_reason(command, str(cwd)) is None, command
    assert not block("terminal", command=command, workdir=str(cwd)), command
    result = subprocess.run([BASH, "--noprofile", "--norc", "-c", command], cwd=cwd, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (run(target, "stash", "list").stdout, (target / "f.txt").read_text(),
            run(target, "status", "--porcelain").stdout) == before


@pytest.mark.parametrize("cmd", [
    "git submodule--helper foreach 'git stash' \"$OP\"",
    "git submodule foreach 'exec git' \"$CMD\"",
    "git submodule--helper foreach 'git stash $(' pop",
    "git submodule--helper foreach 'git stash \"$1\"' pop",
    "git difftool -y -x 'git stash'",
    "git -c alias.x='stash pop' difftool -y -x 'git stash list'",
    "git submodule--helper foreach 'git log' --oneline",
    "git submodule--helper foreach 'git stash' \"$X\" -- pop",
    "git submodule--helper foreach 'git stash \"$1\"' list",
    "git submodule foreach 'git stash' -- pop",
])
def test_nested_runner_positional_parser_refusal(multi, cmd):
    # Unknown operands stay unknown; difftool's LOCAL/REMOTE are unknown operands, so an
    # alias-tainted child Git call with them is refused like any alias-tainted unknown word.
    # Helper options after the command word are permuted options; unknown ones never guess.
    main, _ = multi
    assert guard.refuse_reason(cmd, str(main)), cmd
    assert block("terminal", command=cmd, workdir=str(main)), cmd


@pytest.mark.parametrize("cmd", [
    "git submodule--helper foreach 'git stash list \"$1\"' \"$X\"",
    "git submodule foreach 'echo' \"$X\" pop",
    "git submodule foreach 'git stash' 'list'",
    "git stash list \"$X\"; git difftool -y -x 'git status'",
    "git difftool -y --extcmd=\"sh -c 'git status'\"",
])
def test_nested_runner_positional_parser_allow(multi, cmd):
    main, _ = multi
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not block("terminal", command=cmd, workdir=str(main)), cmd


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
    # apply keeps the branch check: the refusal names both branches.
    for cmd in (
        f"git -C {wt} stash apply",
        f"cd {wt} && git stash apply",
        f"timeout 60 git -C {wt} stash apply",
        f"bash -c 'git -C {wt} stash apply'",
    ):
        got = block("terminal", command=cmd)
        assert got and got["action"] == "block", cmd
        assert "other" in got["message"] and "main" in got["message"]
        assert "refs/stash" in got["message"]
    # pop mutates the shared ref: refused in a multi-worktree repo whatever the branch.
    for cmd in (
        f"git -C {wt} stash pop",
        f"cd {wt} && git stash pop",
        f"timeout 60 git -C {wt} stash pop",
        f"bash -c 'git -C {wt} stash pop'",
    ):
        got = block("terminal", command=cmd)
        assert got and got["action"] == "block", cmd
        assert "refs/stash" in got["message"]
    got = block("terminal", command="git stash pop", workdir=str(wt))
    assert got and got["action"] == "block"
    got = block("execute_code", code=f'terminal("git -C {wt} stash pop")')
    assert got and got["action"] == "block"
    got = block("execute_code", code=f'subprocess.run(["git", "-C", "{wt}", "stash", "pop"])')
    assert got and got["action"] == "block"
    got = block("tool_call", calls=[{"name": "terminal", "arguments": {"command": f"git -C {wt} stash pop"}}])
    assert got and got["action"] == "block"


def test_allows_apply_when_the_stash_branch_matches_or_pop_when_the_repo_has_one_worktree(multi, single):
    main, _wt = multi
    assert block("terminal", command=f"git -C {main} stash apply") is None
    assert block("terminal", command=f"git -C {main} stash list") is None
    # Branch names cannot establish ownership of the shared stack: same-branch pop/push refuse.
    for op in ("pop", "push"):
        got = block("terminal", command=f"git -C {main} stash {op}")
        assert got and got["action"] == "block", op
    # Same-branch apply still warns: the ref is shared even when the names match.
    warned = plugin.transform_terminal_output(command=f"git -C {main} stash apply", output="ok")
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
    got = block("terminal", command=f"git -C {wt} stash apply")
    assert got and got["action"] == "block"
    assert "does not name a branch" in got["message"]


def test_assignment_cd_push_pop_is_checked_before_background_promotion(multi, monkeypatch):
    main, wt = multi
    git("checkout", "--detach", "-q", cwd=wt)
    monkeypatch.setenv("TMPDIR", str(wt.parent))
    command = 'WT=$TMPDIR/wt; cd $WT; git stash -q -- f.txt; git status; git stash pop -q'
    got = block("terminal", command=command, timeout=900)
    assert got and got["action"] == "block"
    assert "refs/stash" in got["message"]
    # The assigned cwd resolves to the detached worktree: apply's branch check names both.
    command = 'WT=$TMPDIR/wt; cd $WT; git status; git stash apply -q'
    got = block("terminal", command=command, timeout=900)
    assert got and got["action"] == "block"
    assert "main" in got["message"] and "HEAD" in got["message"]


@pytest.mark.parametrize("target", ['$UNKNOWN_STASH_REPO', '"$(mktemp -d)"', '`mktemp -d`'])
@pytest.mark.parametrize("op", ["pop", "apply", "push"])
def test_unknown_target_fails_closed(single, target, op, monkeypatch):
    # The fallback cwd (cd failed) is a one-worktree repo, so only the unknown target refuses.
    monkeypatch.delenv("UNKNOWN_STASH_REPO", raising=False)
    got = block("terminal", command=f'cd {target}; git stash {op}', workdir=str(single))
    assert got and got["action"] == "block"
    assert "resolve" in got["message"]
    assert "Save a patch instead" in got["message"]


@pytest.mark.parametrize("op", ["list", "create", "show"])
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
    for repo, op in ((main, "apply"), (single, "pop")):
        assert block("terminal", command=f'M="{repo}"; git -C "$M" stash {op}') is None


def test_unknown_cwd_cannot_fall_back_to_guard_cwd(multi):
    main, _wt = multi
    assert block("terminal", command='cd "$MISSING"; cd relative; git stash pop', workdir=str(main))
    # An absolute -C restores a known target; a same-branch apply remains permitted.
    assert block("terminal", command=f'cd "$MISSING"; git -C "{main}" stash apply') is None


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
    assert block("terminal", command=f"git -C {wt} stash apply") is None
    # An empty stack is no ownership proof: another worktree may push before the pop runs.
    assert block("terminal", command=f"git -C {wt} stash pop")


# --- A restore after a push in the same command ---
#
# The branch check reads refs/stash before the command runs. A command that pushes and then
# restores pops whatever stash is newest when the pop runs: its own, or one another worktree
# pushed in between. Seen live on the hub: `git stash -q; pytest ...; git stash pop -q` in a
# seven-worktree checkout passed because refs/stash was empty when it was checked.

PUSH_THEN_RESTORE_CASES = [
    # (command template, workdir key, clear the fixture's stash first)
    ("git stash -q; true; git stash pop -q", "wt", True),
    ("git stash && git stash apply", "wt", True),
    ("git stash save -q wip; git stash pop", "wt", True),
    ("git stash push -q -m x; git -C {wt} stash pop", "main", True),
    # The top stash is main's own, but the pop runs after this command's push.
    ("git stash -q; git stash pop -q", "main", False),
    # A pop before the push is checked as usual; the one after it is not deduplicated away.
    ("git stash pop; git stash -q; git stash pop", "main", False),
]


@pytest.mark.parametrize("template,where,clear", PUSH_THEN_RESTORE_CASES)
def test_restore_after_same_command_push_refuses(multi, template, where, clear):
    main, wt = multi
    if clear:
        git("stash", "clear", cwd=main)
    cmd = template.format(main=main, wt=wt)
    got = block("terminal", command=cmd, workdir=str({"main": main, "wt": wt}[where]))
    assert got and got["action"] == "block", cmd
    assert "refs/stash" in got["message"], got["message"]


PUSH_THEN_RESTORE_ALLOWED = [
    # One worktree: nobody else shares refs/stash.
    ("git stash -q; true; git stash pop -q", "single"),
    # No push in the command: the top stash is main's own.
    ("git stash list; git stash show; git stash apply", "main"),
    # A push into another repository leaves this one's refs/stash alone.
    ("git -C {single} stash -q; git -C {main} stash apply", "wt"),
]


@pytest.mark.parametrize("template,where", PUSH_THEN_RESTORE_ALLOWED)
def test_restore_after_push_elsewhere_or_in_one_worktree_is_allowed(multi, single, template, where):
    main, wt = multi
    cmd = template.format(main=main, wt=wt, single=single)
    cwd = {"main": main, "wt": wt, "single": single}[where]
    assert block("terminal", command=cmd, workdir=str(cwd)) is None, cmd


def test_push_then_restore_really_takes_a_stash_pushed_in_between(multi):
    main, wt = multi
    git("stash", "clear", cwd=main)
    (wt / "f.txt").write_text("wt edit\n")
    (main / "f.txt").write_text("main edit\n")
    # The main worktree's push stands in for a second worker stashing between push and pop.
    cmd = f"cd {wt} && git stash -q && git -C {main} stash -q && git stash pop -q"
    assert _blocked(cmd, wt)
    subprocess.run([_proof_bash(), "-c", cmd], check=True, capture_output=True, env=GIT_ENV)
    assert (wt / "f.txt").read_text() == "main edit\n"


# --- `git stash branch` restores (and drops) a stash too ---

STASH_BRANCH_CASES = [
    # (command template, workdir key, expected blocked)
    ("git stash branch newb", "wt", True),
    ("git -C {wt} stash branch newb stash@{{0}}", "main", True),
    ("git stash push -q -m x; git stash branch newb", "main", True),
    # `stash branch` drops the entry: refused in a multi-worktree repo even on its own branch.
    ("git stash branch newb", "main", True),
    # One worktree: nobody else shares refs/stash.
    ("git -C {single} stash branch newb", "wt", False),
]


@pytest.mark.parametrize("template,where,expected", STASH_BRANCH_CASES)
def test_stash_branch_is_checked_like_pop(multi, single, template, where, expected):
    main, wt = multi
    cmd = template.format(main=main, wt=wt, single=single)
    assert _blocked(cmd, {"main": main, "wt": wt}[where]) is expected, cmd


def test_stash_branch_really_takes_another_worktrees_stash(multi):
    main, wt = multi
    cmd = f"git -C {wt} stash branch newb"
    assert _blocked(cmd, main)
    subprocess.run([_proof_bash(), "-c", cmd], check=True, capture_output=True, env=GIT_ENV)
    assert (wt / "f.txt").read_text() == "stashed\n"
    assert _stash_count(main) == 0  # dropped: main's worker has lost it


# --- Functions exported to a child Bash (`export -f`, `declare -fx`, `set -a`) ---
#
# `timeout` cannot run a shell function, so `f() {...}; export -f f; timeout 60 bash -c f` is
# the usual idiom. A child Bash imports exported functions from its environment, but the
# analyzer gave every child an empty function table, so the restore inside `f` went unseen.

EXPORTED_FUNCTION_CASES = [
    # (command template, workdir key, expected blocked)
    ("f() {{ git stash pop; }}; export -f f; timeout -k 1 5 bash -c f", "wt", True),
    ("f() {{ git -C {wt} stash pop; }}; export -f f; bash -c f", "main", True),
    ("f() {{ git -C {wt} stash pop; }}; declare -fx f; bash -c 'f'", "main", True),
    ("f() {{ git -C {wt} stash pop; }}; typeset -f -x f; sh -c f", "main", True),
    ("set -a; f() {{ git -C {wt} stash pop; }}; bash -c f", "main", True),
    # A grandchild imports it too: the function stays in the environment.
    ("f() {{ git -C {wt} stash pop; }}; export -f f; bash -c 'bash -c f'", "main", True),
    # Controls: an unexported function is not visible in the child.
    ("f() {{ git -C {wt} stash pop; }}; bash -c f", "main", False),
    # The exported function applies this worktree's own stash.
    ("f() {{ git stash apply; }}; export -f f; bash -c f", "main", False),
]


@pytest.mark.parametrize("template,where,expected", EXPORTED_FUNCTION_CASES)
def test_exported_function_runs_in_child_shell(multi, template, where, expected):
    main, wt = multi
    cmd = template.format(main=main, wt=wt)
    assert _blocked(cmd, {"main": main, "wt": wt}[where]) is expected, cmd


def test_exported_function_really_restores_in_child_bash(multi):
    main, wt = multi
    cmd = f"f() {{ git -C {wt} stash pop -q; }}; export -f f; bash -c f"
    assert _blocked(cmd, main)
    subprocess.run([_proof_bash(), "-c", cmd], check=True, capture_output=True, env=GIT_ENV)
    assert (wt / "f.txt").read_text() == "stashed\n"


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
    ("(cd {main} && git stash apply)", "wt", False),
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
    ("git -C {main} stash apply 2>&1 | tail -3", "wt", False),
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
    assert not _blocked("bash -c " + shlex.quote(f"git -C {main} stash apply"), wt)


@pytest.mark.parametrize("template,where,expected", [
    ("bash <<EOF\ncd {wt}\ngit stash pop\nEOF", "main", True),
    ("cat <<'EOF'\ngit stash pop\nEOF\ngit -C {main} stash apply", "wt", False),
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
    assert block("execute_code", code=f'terminal("cd {single} & git stash apply")') is None
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
    subprocess.run([_proof_bash(), "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
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
    subprocess.run([_proof_bash(), "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
    assert _stash_count(main) == 0
    assert (wt / "f.txt").read_text() == "stashed\n"


@pytest.mark.parametrize("where", ["main", "single"])
def test_scalar_argv_allow_controls(multi, single, where):
    main, wt = multi
    repo = {"main": main, "single": single}[where]
    op = "apply" if where == "main" else "pop"  # a same-branch pop mutates the shared ref
    cmd = f'G=git; S=stash; OP={op}; $G -C {repo} "$S" "$OP" 2>&1'
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
    subprocess.run([_proof_bash(), "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
    assert _stash_count(main) == 0
    assert (wt / "f.txt").read_text() == "stashed\n"


# An external wrapper program (env, nice, nohup, stdbuf, sudo, timeout) runs its argv in a
# child process: only that argv is uncertain, never the caller's cwd or variables. A
# git-free agent command (`PY=...; env A=1 $PY ...; $PY - <<'EOF'`) was refused as an
# unresolved `git stash pop` because the caller's state collapsed to unknown, so `$PY`
# might have been git and, with HOME unknown, any git word an alias (2026-11-01).
@pytest.mark.parametrize("template", [
    "PY=/usr/bin/python3; env A=1 $PY -c 1; $PY - <<'EOF'\nprint(1)\nEOF",
    "PY=/usr/bin/python3; env -u X $PY -c 1; $PY x.py",
    "PY=/usr/bin/python3; nice -n 5 $PY -c 1; $PY x.py",
    "T=/usr/bin/true; stdbuf -oL $T; $T x",
    "T=/usr/bin/true; sudo -n $T; $T x",
    "T=/usr/bin/true; /usr/bin/env A=1 $T; $T x",
])
def test_external_wrapper_keeps_caller_state(multi, template):
    main, _wt = multi
    assert not _blocked(template, main), template


@pytest.mark.parametrize("template", [
    # The wrapped argv itself stays uncertain.
    "env A=1 git -C {wt} stash pop",
    # The caller's state is still tracked after the wrapper.
    "env A=1 true; cd {wt}; git stash pop",
    "G=git; nice -n 5 true; $G -C {wt} stash pop",
    # Config the wrapped child writes is shared state: a later alias call stays refused.
    "env A=1 git config alias.p 'stash pop'; git -C {wt} p",
    # The child may also write Git config, an include or a `git-*` program with no Git text
    # the guard can see (base64 of `[alias] p = stash pop`, `[include] path = ../../inc`,
    # `exec git stash pop`): a later non-builtin subcommand may run it (2026-11-02).
    "env A=1 sh -c 'echo W2FsaWFzXQoJcCA9IHN0YXNoIHBvcAo= | base64 -d >> .git/config'; "
    "git -C {wt} p",
    "echo W2FsaWFzXQoJcCA9IHN0YXNoIHBvcAo= | base64 -d > ../inc; env -u X sh -c "
    "'echo W2luY2x1ZGVdCglwYXRoID0gLi4vLi4vaW5jCg== | base64 -d >> .git/config'; git -C {wt} p",
    "mkdir -p ../bin; nice -n 5 sh -c 'echo IyEvYmluL3NoCmV4ZWMgZ2l0IHN0YXNoIHBvcAo= | base64 -d "
    "> ../bin/git-p && chmod +x ../bin/git-p'; PATH=$PWD/../bin:$PATH git -C {wt} p",
    # Builtins and keywords run in the caller: its state stays unknown after them.
    "command -p cd {wt}; git stash pop",
    "time -p cd {wt}; git stash pop",
])
def test_external_wrapper_state_refusal_controls(multi, template):
    main, wt = multi
    _assert_real_restore(template.format(wt=wt), main, wt)


@pytest.mark.parametrize("cmd", [
    'TP=$(echo x); env -i A=1 $TP -c x; $TP -m pip list',
    '''cd ~/code/_wt/v111-verify; TP=$(timeout 60 scripts/run-in-hermes-env /usr/bin/env 2>/dev/null | sed -n 's/^__HERMES_TEST_PYTHON=//p'); echo "TP=$TP"; $TP -c 'import sys, pytest, httpx; print(sys.version.split()[0], "pytest", pytest.__version__)'; S=~/.hermes/cache/scratch/v111_vf/pt92; env -i PYTHONPATH=$S/site $TP -c 'import pytest, pluggy; print("overlay", pytest.__version__, pytest.__file__.split("/")[-3], "pluggy", pluggy.__version__)'; $TP -m pip list 2>/dev/null | grep -iE '^pytest|pluggy|anyio' | head -8 || ls $(dirname $TP)/../lib/python3*/site-packages | grep -iE '^pytest' | head''',
    'TP=$(echo x); S=/tmp; env -i PYTHONPATH=$S/site "$TP" -c x; "$TP" -m pip list',
], ids=["A1", "A2", "A3"])
def test_opaque_child_unknown_interpreter_allow(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main)) is None


@pytest.mark.parametrize("cmd", [
    "env A=1 sh -c 'echo W2FsaWFzXQoJcCA9IHN0YXNoIHBvcAo= | base64 -d >> .git/config'; G=$(echo x); $G p",
    'TP=$(echo x); env -i A=1 $TP -c x; $TP stash pop',
    'TP=$(echo x); env -i A=1 $TP -c x; git p',
], ids=["R2", "R3", "R4"])
def test_opaque_child_unknown_interpreter_refuse(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main))


@pytest.mark.parametrize("evidence", [
    'stash', 'pop', 'apply', '.GiT/CoNfIg', 'GiT CoNfIg', 'ALIAS.p', '[ALIAS]', 'INCLUDE', 'git_config',
])
def test_opaque_child_unknown_name_keeps_whole_text_evidence(multi, evidence):
    main, _wt = multi
    cmd = f'TP=$(echo x); env -i A=1 $TP -c x; $TP p; echo {shlex.quote(evidence)}'
    assert guard.refuse_reason(cmd, str(main))


@pytest.mark.parametrize("later", [
    '$TP "$ARGS"',  # independently unknown argv must still refuse
    'HOME=/somewhere $TP p',  # explicit environment uncertainty, not just the child
    '$TP git p',  # literal Git in a suffix is not the unknown-name-as-git reading
])
def test_opaque_child_does_not_relax_other_uncertainty(multi, later):
    main, _wt = multi
    assert guard.refuse_reason(f'TP=$(echo x); env -i A=1 $TP -c x; {later}', str(main))


@pytest.mark.parametrize("evidence,refused", [('', False), ('include', True)])
def test_child_alias_unknown_name_evidence_gate(tmp_path, evidence, refused):
    analyzer = guard._Analyzer(evidence)
    analyzer.child_alias = True
    analyzer.child_alias_source = 'opaque runner child'
    state = guard._State(str(tmp_path), {'HOME': analyzer.home0}, {'HOME'}, {})
    analyzer.shadow_argv(['$TP', 'p'], state, 0, as_git=True, unknown_command='$TP p')
    restores = [c for c in analyzer.calls if c[1] == 'stash']
    assert bool(restores) is refused
    if refused:
        assert 'opaque runner child' in restores[0][3]
        assert '$TP p' in restores[0][3]


def test_opaque_child_alias_diagnostic(multi):
    main, _wt = multi
    reason = guard.refuse_reason('TP=$(echo x); env -i A=1 $TP -c x; git p', str(main))
    assert 'env -i A=1 $TP -c x' in reason
    assert 'git p' in reason
    assert 'alias' in reason
    assert 'Quote it' not in reason


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
    assert not _blocked(f'CDPATH={wt.parent}; cd {main}; git stash apply', wt)


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
    proc = subprocess.run([_proof_bash(), "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV)
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
    subprocess.run([BASH, "-c", cmd], cwd=main, capture_output=True, env=GIT_ENV, check=True)
    assert _stash_count(main) == 1
    # Same-branch and single-worktree restores through a shadowing function stay allowed.
    assert not _blocked(f'timeout() {{ git -C {main} stash apply; }}; timeout 5 true', wt)
    assert not _blocked(f'env() {{ git -C {single} stash pop; }}; env true', wt)
    assert not _blocked(f'git() {{ :; }}; command git -C {main} stash apply', wt)
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
    assert not _blocked(f"git -C {main} stash apply 2>&1 >/dev/null", wt)
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
    cmd = template.format(wt=wt, main=main)
    assert _blocked(cmd, main), cmd
    assert _stash_count(main) == 1
    assert (wt / "f.txt").read_text() == "base\n"
    if "timeout" in cmd.split() and shutil.which("timeout") is None:
        pytest.skip("real restore proof needs timeout, absent from PATH; "
                    "guard refusal and unchanged stash/file checked")
    if any(not _host_runs(p) for p in ("flock", "setsid") if p in cmd.split()):
        return
    _assert_real_restore(cmd, main, wt)


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
        f"timeout 10 git -C {main} stash apply",
        f"timeout --foreground -k 5 10 git -C {main} stash apply",
        f"builtin cd {main}; git stash apply",
        f"builtin cd {single}; git stash pop",
        f"git --namespace foo -C {main} stash apply",
        f"git --no-pager -C {main} stash apply 2>&1 >/dev/null",
        f"git -C {main} stash apply stash@{{0}}",
        f"git -c core.editor=true -C {main} stash apply",
        f"pushd {single} >/dev/null; git stash pop",
        f"cd {main}/../repo; git stash apply",
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
        # A push is a shared-ref mutation in main/wt; -m "$MSG" parsing is checked in single.
        f'git -C {single} stash -m "$MSG"',
        f"echo $'x'; git -C {single} stash create",
        f"echo $'x'; git -C {single} stash list",
    ):
        assert not _blocked(cmd, main), cmd


def test_logical_cd_allow_controls(multi):
    main, wt = multi
    _symlink_into_main(main, wt)
    # Physical cd lands in main (same branch); a symlink without `..` is unambiguous.
    assert not _blocked(f"cd -P {wt}/l/..; git stash apply", wt)
    assert not _blocked(f"cd {wt}/l; cd {main}; git stash apply", wt)
    assert not _blocked(f"cd {wt}/l; git stash list", main)
    # The tracked logical cwd is still exact: wt/l is inside main's checkout.
    assert not _blocked(f"cd {wt}/l; git stash apply", wt)


# --- Bounds: state cap, step budget, unexpected analyzer exception ---

def _subdirs(repo, n):
    for k in range(n):
        (repo / f"d{k}").mkdir()
    return [repo / f"d{k}" for k in range(n)]


def _case_cd(single, dirs):
    arms = " ".join(f"{k}) cd {d};;" for k, d in enumerate(dirs))
    return f'cd {single}; case "$1" in {arms} esac; git stash pop'


@pytest.fixture(autouse=True)
def assert_cap_states_survive_copy(request, monkeypatch):
    if not request.node.name.startswith("test_state_cap"):
        return
    original = guard._union

    def checked(*groups):
        result = original(*groups)
        for state in result:
            assert state.copy().key() == state.key()
        return result

    monkeypatch.setattr(guard, "_union", checked)


@pytest.mark.parametrize("count", [16, 17, 30])
@pytest.mark.parametrize("shape", ["tilde", "stable", "quoted", "absolute", "status"])
def test_state_cap_join_readonly(multi, monkeypatch, count, shape):
    main, wt = multi
    monkeypatch.setenv("HOME", str(main.parent))
    loop = "for s in " + " ".join(map(str, range(count))) + "; do echo $s; done; "
    commands = {
        "tilde": loop + "C=~/repo; git -C $C log --oneline -1",
        "stable": f"C={main}; " + loop + "git -C $C log --oneline -1",
        "quoted": f"C={main}; " + loop + 'git -C "$C" log --oneline -1',
        "absolute": loop + f"C={main}; git -C $C log --oneline -1",
        "status": loop + "git status --short",
    }
    assert guard.refuse_reason(commands[shape], str(wt)) is None


@pytest.mark.parametrize("shape", ["cwd", "variable", "nonconvergent", "nameref"])
def test_state_cap_join_restore_controls(multi, shape):
    main, wt = multi
    # Distinct spellings resolve to the same real worktree in the shell.
    paths = " ".join(str(wt) + "/." * i for i in range(1, 21))
    nums = " ".join(map(str, range(20)))
    dirs = " ".join(map(str, _subdirs(wt, 20)))
    commands = {
        "cwd": f"for d in {dirs}; do cd $d; done; git stash pop",
        "variable": f"for X in {paths}; do echo $X; done; git -C $X stash pop",
        "nonconvergent": f"X={wt}; for s in {nums}; do X=$X/.; done; git -C $X stash pop",
        "nameref": f"declare -n R=HOME; for s in {paths}; do R=$s; done; git -C {wt} stash pop",
    }
    command = commands[shape]
    reason = guard.refuse_reason(command, str(wt))
    assert reason
    _assert_real_restore(command, main, wt)


def test_state_cap_join_unknown_word_diagnostic(multi):
    _main, wt = multi
    values = " ".join(map(str, range(20)))
    reason = guard.refuse_reason(
        f"for X in {values}; do echo $X; done; git -C $X log --oneline -1", str(wt))
    assert reason and "$X" in reason


def test_state_cap_join_consensus_fields():
    states = [guard._State("/same", {"HOME": "/home/test", "X": str(i)},
                           {"X"}, {}, "/logical")
              for i in range(17)]
    states[0].vars["PARTIAL"] = "value"
    joined, = guard._union(states)
    assert joined.cwd == "/same"
    assert joined.lcwd == "/logical"
    assert joined.vars == {"HOME": "/home/test", "X": None, "PARTIAL": None}
    assert joined.exported == {"X"}
    states[-1].lcwd = "/different"
    joined, = guard._union(states)
    assert joined.lcwd is None


@pytest.mark.parametrize("shape", ["nameref-loop", "nameref-no-loop", "export", "function"])
def test_state_cap_join_adversarial_restores(multi, shape):
    main, wt = multi
    values = " ".join(str(wt) + "/." * i for i in range(1, 21))
    loop = f"for s in {values}; do V=$s; done; "
    commands = {
        "nameref-loop": f"X={main}; declare -n R=X; for s in {values}; do R=$s; done; git -C $X stash pop",
        "nameref-no-loop": f"X={main}; declare -n R=X; R={wt}; git -C $X stash pop",
        "export": f"cd {wt}; X=../repo; if [ -d {main} ]; then :; else export X; fi; "
                  + loop + "bash -c 'cd ./$X && git stash pop'",
        "function": f"cd {main}; if [ -d {main} ]; then f() {{ cd ../wt; }}; else f() {{ :; }}; fi; "
                    + loop + "f; git stash pop",
    }
    _assert_real_restore(commands[shape], main, wt)


@pytest.mark.parametrize("declaration", ["declare -n", "declare -xn", "typeset -nx", "local -n", "local -xn"])
@pytest.mark.parametrize("count", [0, 20])
def test_state_cap_join_nameref_flags(multi, declaration, count):
    main, wt = multi
    loop = ("for s in " + " ".join(map(str, range(count))) + "; do echo $s; done; ") if count else ""
    command = (f"X={main}; f() {{ {declaration} R=X; R={wt}; "
               + loop + 'git -C "$X" stash pop; }; f')
    _assert_real_restore(command, main, wt)


@pytest.mark.parametrize("template", [
    'X={main}; declare -n R=X; R={wt}; cd "$X"; bash -c "git stash pop"',
    'export X={main}; declare -n R=X; R={wt}; bash -c \'git -C "$X" stash pop\'',
    'X={main}; N=-n; declare $N R=X; R={wt}; git -C "$X" stash pop',
    'X={main}; N=-n; declare "$N" R=X; R={wt}; git -C "$X" stash pop',
    'F=-x; declare $F GIT_DIR={gitdir} GIT_WORK_TREE={wt}; git -C {main} stash pop',
    'X={main}; Y={wt}; declare -n R=X; for R in Y; do git -C "$R" stash pop; done',
])
def test_state_cap_join_nameref_boundaries(multi, template):
    main, wt = multi
    _assert_real_restore(template.format(main=main, wt=wt, gitdir=_gitdir(wt)), main, wt)


def test_state_cap_join_delayed_function(multi):
    main, wt = multi
    command = ("for i in 1 2 3; do g; h; h(){ g(){ f(){ "
               + f"git -C {wt} stash pop; " + "}; }; }; done; f")
    _assert_real_restore(command, main, wt)


def test_state_cap_join_logical_cwd_survives_copy(multi):
    main, wt = multi
    (wt / "m").symlink_to(main, target_is_directory=True)
    (wt / "repo").symlink_to(".", target_is_directory=True)
    values = " ".join(str(wt) + "/." * i for i in range(1, 21))
    command = (f"if [ -d {main} ]; then cd {wt}/m; else cd {main}; fi; "
               f"for s in {values}; do V=$s; done; W=1; cd ../repo; git stash pop")
    _assert_real_restore(command, main, wt)


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
    same_branch = f"git -C {main} stash apply"
    assert not _blocked("true; " * 10 + same_branch, wt)
    assert _blocked("true; " * guard.MAX_STEPS + same_branch, wt)
    assert not _blocked("true; " * guard.MAX_STEPS + f"git -C {main} stash list", wt)


def test_unexpected_analyzer_exception_fails_closed(multi, monkeypatch):
    main, wt = multi
    same_branch = f"git -C {main} stash apply"
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
        f"bash -ec 'git -C {main} stash apply'",
        f"bash -lc 'cd {single} && git stash pop'",
        f"sh -c -- 'git -C {main} stash apply'",
        f"IFS=x; A=stash; git -C {main} $A apply",
        f"git -C {main} sta\\\nsh apply",
        f"set -a; X=1; git -C {main} stash apply",
        f"cd {main} && /usr/lib/git-core/git-stash apply",
        f"git -c include.path=/dev/null -C {main} log -1",
        f"git -c include.path=/dev/null -C {main} stash apply",
        f"echo hi # comment \\\ngit -C {main} stash apply",
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
        f"hash -p /bin/echo e; git -C {main} stash apply",
        f"set -a; D={main}; bash -c 'git -C \"$D\" stash apply'",
        f"shopt -so allexport; X=1; git -C {main} stash apply",
        f"set -k; git -C {main} stash apply",
        f"python3 -m pytest -q && git -C {main} stash apply",
        f"python3 -c 'print(1)' && git -C {main} stash apply",
        f"make \"$TARGET\" && git -C {main} stash apply",
        f"declare -n ref=arr; git -C {main} stash apply",
        f"git config alias.st status && git -C {main} st",
        f"echo pop | bash -c cat",
        f"printf 'hello\\n' | bash; git -C {main} stash list",
        f"git config user.name x && git -C {main} stash apply",
        f"git -c core.editor=true -c core.pager=cat -C {main} stash apply",
        f"x=\"$(git -C {main} stash list)\"; PS4='+ '; git -C {main} stash apply",
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
    if any(not _host_runs(p) for p in needs) or first == "vim":
        assert _blocked(cmd, main), cmd  # static only: vim may be absent or interactive
        return
    _assert_real_restore(cmd, main, wt)


# --- `[` test builtin: a lone `[` / `]` is literal, not a bracket glob ---

# Refused on hermes-hub as `git stash pop` (no git, no stash in it): the unquoted `[` of
# `[ -f $db ]` was read as a glob, the command name became unknown and was re-read as git.
SQLITE_LOOP_REPORTED = (
    "cd ~/.hermes/kanban/boards; for b in */; do db=$b/kanban.db; [ -f $db ] || continue; "
    "sqlite3 -readonly $db \"select '$b',r.task_id,... where e.kind='claimed' and e.payload "
    "like '%\\\"holder\\\":\\\"worker-1\\\"%' ...\" 2>/dev/null; done; "
    "sqlite3 -readonly ~/.hermes/kanban.db \"select ... title like '%portable%'\""
)
SQLITE_LOOP_FULL = (
    "cd ~/.hermes/kanban/boards; for b in */; do db=$b/kanban.db; [ -f $db ] || continue; "
    "sqlite3 -readonly $db \"select '$b',r.task_id,r.status from task_runs r join task_events e "
    "on e.task_id=r.task_id where e.kind='claimed' and e.payload like "
    "'%\\\"holder\\\":\\\"worker-1\\\"%' order by r.id desc limit 5\" 2>/dev/null; done; "
    "sqlite3 -readonly ~/.hermes/kanban.db \"select id,title,status from tasks "
    "where title like '%portable%'\""
)


@pytest.mark.parametrize("cmd", [SQLITE_LOOP_REPORTED, SQLITE_LOOP_FULL,
                                 "for b in */; do [ -f $b/x ] || continue; done",
                                 "x=$(ls); [ -n $x ] && echo ok"])
def test_test_bracket_with_unknown_operand_is_not_a_restore(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not _blocked(cmd, main), cmd


@pytest.mark.parametrize("template", [
    "for b in */; do [ -f $b/x ] || continue; git -C {wt} stash pop; done",
    "[ -f nothing ] || git -C {wt} stash pop",
    "g[i]t -C {wt} stash pop",
    "x=$(ls); [ -n $x ] && git -C $x stash pop",
    "for b in */; do [ -d $b ] && $b -C {wt} stash pop; done",
])
def test_bracket_controls_still_refuse(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt)
    assert _blocked(cmd, main), cmd


# --- `for NAME in WORDS`: literal loop words give the variable known values ---

# Refused as `git stash pop` (no stash in it): an unquoted for-loop variable was an unknown,
# splittable word, so git's subcommand position after `-C $T/$w` became unknown.
FOR_LOOP_ALLOWED = [
    "T=/tmp; for w in a b; do git -C $T/$w status; done",                 # card case 1
    'T=/tmp; for w in a b; do git -C "$T/$w" status; done',               # case 2
    "for w in /tmp/a /tmp/b; do git -C $w status; done",                  # case 3
    'for w in /tmp/a /tmp/b; do git -C "$w" status; done',                # case 4
    "C=/tmp/x; git -C $C worktree prune -v",                              # case 7
    "T=/tmp; C=/tmp/x; for w in core-fix core-hub; do d=$(git -C $T/$w status --porcelain"
    ' | wc -l); [ "$d" = 0 ] && git -C $C worktree remove $T/$w; done',   # reported, C set
    "for w in 'a' \"b\" c\\\\d; do git -C /tmp/$w status; done",            # quoted literal words
]


@pytest.mark.parametrize("cmd", FOR_LOOP_ALLOWED)
def test_for_loop_literal_words_are_known(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, "/Users/you") is None, cmd
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not _blocked(cmd, main), cmd


def test_unknown_word_refusal_names_the_word(multi):
    """Card case 6: no stash text, so the refusal names the word and says to quote it."""
    main, _wt = multi
    for cmd, word in (("git -C $C worktree prune -v", "$C"),
                      ("for w in core-fix core-hub; do git -C $C worktree remove $T/$w; done",
                       "$C"),
                      # A loop word with a space splits again when the variable is unquoted.
                      ("for w in 'a b'; do git -C /tmp/$w status; done", "/tmp/$w")):
        why = guard.refuse_reason(cmd, "/Users/you")
        assert why is not None, cmd
        assert f"`{word}`" in why and f'"{word}"' in why and "Quote it" in why, why
        assert "git stash pop`:" not in why, why
        assert _blocked(cmd, main), cmd


@pytest.mark.parametrize("cmd", [
    'for w in a b; do git -C "$w" stash pop; done',      # card controls
    "git -C $C stash apply",
    "T=/x; for w in a; do git -C $T/$w stash pop; done",
])
def test_for_loop_card_controls_still_refuse(cmd):
    why = guard.refuse_reason(cmd, "/Users/you")
    assert why is not None and "`git stash" in why, (cmd, why)


@pytest.mark.parametrize("template", [
    "for w in {wt}; do git -C $w stash pop; done",
    "for w in {main} {wt}; do git -C $w stash pop; done",
    "for w in {main} {wt}; do cd $w; done; git stash pop",
    "for w in {main}; do w={wt}; git -C $w stash pop; done",
    "for w in {main}; do git -C $w/../wt stash pop; done",
    # Non-literal loop words keep the variable unknown.
    "for w in $(echo {main}); do git -C $w stash pop; done",
    "for w in {main} $X; do git -C $w stash pop; done",
    "for w in {main}*; do git -C $w stash pop; done",
    "for w in {{{main},{wt}}}; do git -C $w stash pop; done",
    "for w in `echo {main}`; do git -C $w stash pop; done",
    "for w; do git -C $w stash pop; done",
    "select w in {main}; do git -C $w stash pop; done",
    "for w in {main}; do :; done; git -C $w/../wt stash pop",
])
def test_for_loop_restores_still_refuse(multi, template):
    main, wt = multi
    cmd = template.format(main=main, wt=wt)
    assert _blocked(cmd, main), cmd


# --- `command -v`/`-V` only describes names: it runs nothing and keeps the shell state ---

# Refused as `git stash pop` (no stash in it): `command -v gh` read as an unsupported wrapper
# option, so the whole shell state collapsed and a later unquoted `-C $r` became a word that
# could split into `stash pop`. Reported shape, host and paths anonymized.
COMMAND_LOOKUP_REPORTED = r'''timeout 90 ssh -o BatchMode=yes host 'ls -l ~/x 2>&1 | cut -c1-160; command -v gh python3; for r in ~/a ~/b; do [ -d $r/.git ] || [ -f $r/.git ] || { echo "$r: not a git repo"; continue; }; echo "$r: HEAD $(git -C $r rev-parse --short=10 HEAD) branch $(git -C $r rev-parse --abbrev-ref HEAD)"; git -C $r cat-file -e 0123abcd 2>/dev/null && echo has || echo lacks; done' 2>&1 | grep -v x'''
COMMAND_LOOKUP_ALLOWED = [
    COMMAND_LOOKUP_REPORTED,
    "command -v gh python3; for r in /tmp/a /tmp/b; do git -C $r rev-parse HEAD; done",
    "command -v gh; git -C $HOME rev-parse HEAD",
    "command -V git; C=/tmp/x; git -C $C log -1",
    "command -pv git >/dev/null; git -C $HOME/a status",
    "command -v -- git; X=/tmp/x; git -C $X status",
    "builtin command -v git; X=/tmp/x; git -C $X status",
    # A lookup names words; it never runs them.
    "command -v git stash pop",
]


@pytest.mark.parametrize("cmd", COMMAND_LOOKUP_ALLOWED)
def test_command_lookup_keeps_shell_state(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, "/Users/you") is None, cmd
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not _blocked(cmd, main), cmd


@pytest.mark.parametrize("template", [
    "command -v gh; git -C {wt} stash pop",
    "command -v gh python3; for r in {wt}; do git -C $r stash pop; done",
    "command -V git; cd {wt}; git stash pop",
    "command -pv git; C={wt}; git -C $C stash apply",
    "command -v gh; git -C $C stash pop",
    # Running forms and unknown options stay uncertain, as before.
    "command -p git -C {wt} stash pop",
    "command git -C {wt} stash pop",
    "command -x git -C {wt} stash pop",
    "command -v$X git -C {wt} stash pop",
])
def test_command_lookup_controls_still_refuse(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt)
    assert _blocked(cmd, main), cmd


def test_command_lookup_restore_control_really_restores(multi):
    main, wt = multi
    cmd = f"command -v gh >/dev/null; git -C {wt} stash pop -q"
    assert _blocked(cmd, main), cmd
    assert (wt / "f.txt").read_text() == "base\n"
    subprocess.run([_proof_bash(), "-c", cmd], cwd=main, env=GIT_ENV, check=True,
                   capture_output=True)
    assert (wt / "f.txt").read_text() == "stashed\n"


def test_command_lookup_allow_control_is_read_only(multi):
    main, wt = multi
    cmd = f"command -v gh python3 git; for r in {main} {wt}; do git -C $r rev-parse HEAD; done"
    assert not _blocked(cmd, main), cmd
    before = subprocess.run(["git", "stash", "list"], cwd=main, env=GIT_ENV, text=True,
                            capture_output=True, check=True).stdout
    subprocess.run([_proof_bash(), "-c", cmd], cwd=main, env=GIT_ENV, capture_output=True)
    after = subprocess.run(["git", "stash", "list"], cwd=main, env=GIT_ENV, text=True,
                           capture_output=True, check=True).stdout
    assert before == after and before.strip()
    assert (wt / "f.txt").read_text() == "base\n" and (main / "f.txt").read_text() == "base\n"


# Shared allow corpus: add each real false positive here, not only a one-off test.
AWK_DIFF = r"awk '/^diff --git a\/tools\/process_registry.py/{f=1} /^diff --git/ && !/tools\/process_registry.py/{f=0} f' v1101hr_598.diff"
# Reported command is analyzed only, never executed. Personal path anonymized.
BUILTIN_REPORTED = r'''R=/Users/you/code/hermes; P=/Users/you/.hermes/cache/scratch.noindex; S=$P/v1101is; rm -rf $S; mkdir -p $S/home $S/tmp; git -C $R worktree add -q --detach $S/wt 4406b1ef28bf && cd $S/wt && git show 4406b1ef28bf^1:hermes-sync > hermes-sync && echo "RED (corrected base cb19c73c5c) hermes-sync blob $(git hash-object hermes-sync | cut -c1-12)" && timeout 300 env -i HOME=$S/home TMPDIR=$S/tmp PATH=/usr/bin:/bin LANG=en_US.UTF-8 nice -n 10 /usr/bin/python3 -m unittest tests.test_core_requeue_budget > $S/RED.log 2>&1; echo "RED exit $? | $(grep -a -E '^(Ran |OK|FAILED)' $S/RED.log | tr '\n' ' ')"; grep -a -E '^(FAIL|ERROR):' $S/RED.log | cut -c1-170; cd /Users/you; git -C $R worktree remove --force $S/wt; rm -rf $S; echo "worktrees left: $(git -C $R worktree list | grep -c v1101is)"; git -C $R show cb19c73c5c:hermes-sync > /Users/you/.hermes/cache/scratch.noindex/v1101ir_old_hs && echo "old blob $(git hash-object /Users/you/.hermes/cache/scratch.noindex/v1101ir_old_hs | cut -c1-12)"'''
BUILTIN_WRAPPER = "timeout 5 env -i HOME=/Users/you/.hermes/cache/scratch.noindex/x PATH=/usr/bin:/bin nice -n 10 /usr/bin/python3 -m unittest t"
BUILTIN_ALLOW_CASES = [
    BUILTIN_REPORTED,
    BUILTIN_WRAPPER + "; cd /Users/you; git hash-object /abs/f",
    BUILTIN_WRAPPER + "; cd /Users/you; git cat-file -t HEAD",
    BUILTIN_WRAPPER + "; git -C /Users/you/code/hermes hash-object /abs/f",
]
READ_ONLY_ALLOW_CORPUS = [
    *BUILTIN_ALLOW_CASES,
    AWK_DIFF,
    AWK_DIFF.replace("awk ", "gawk ", 1),
    AWK_DIFF.replace("awk ", "mawk ", 1),
    # Public fixture retains the reported syntax, with personal paths/repo anonymized.
    r'''cd /Users/you/.hermes/cache/scratch.noindex && timeout 60 gh pr diff 598 -R example/repo > v1101hr_598.diff 2>&1; timeout 60 gh pr diff 608 -R example/repo > v1101hr_608.diff 2>&1; echo "598 diff lines $(wc -l < v1101hr_598.diff) | 608 diff lines $(wc -l < v1101hr_608.diff)"; echo "== #598 tools/process_registry.py hunks:"; ''' + AWK_DIFF + r''' | grep -E '^[@+-]' | grep -v -E '^(\+\+\+|---)' | cut -c1-170 | head -60''',
    SQLITE_LOOP_REPORTED, SQLITE_LOOP_FULL,
    *FOR_LOOP_ALLOWED[:4], FOR_LOOP_ALLOWED[-1],
    *COMMAND_LOOKUP_ALLOWED,
    "[ -f x ] && echo y", "sed -n '1,5p' f",
    "awk 'BEGIN { print \"git stash pop\" }'",
]


@pytest.mark.parametrize("cmd", READ_ONLY_ALLOW_CORPUS)
def test_read_only_allow_corpus(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not _blocked(cmd, main), cmd


@pytest.mark.parametrize("cmd", [
    "awk 'BEGIN{system(\"git stash pop\")}'",
    "awk 'BEGIN{\"git stash pop\" | getline x}'",
    "awk 'BEGIN{print \"\" | \"git stash pop\"}'",
    "gawk 'BEGIN{system(\"git -C \" ENVIRON[\"TARGET\"] \" stash pop\")}'",
    "perl -e 'system(\"git stash pop\")'",
    "mawk 'BEGIN{system(\"git stash apply\")}'",
    "gawk 'BEGIN{f=\"system\"; @f(\"git stash pop\")}'",
    "gawk 'BEGIN{f=\"sys\" \"tem\"; @f(\"git -C \" ENVIRON[\"TARGET\"] \" stash pop\")}'",
    "awk 'BEGIN{system (\"git \" \"stash \" \"pop\")}'",
])
def test_awk_command_primitive_refusal_controls(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main)) is not None, cmd
    assert _blocked(cmd, main), cmd


@pytest.mark.parametrize("template", [
    'awk "$(git -C {wt} stash pop)"',
    "awk 'BEGIN{{print \"git stash pop\"}}' | sh",
    "printf '%s' 'BEGIN{{system(\"git stash pop\")}}' | awk -f -",
    "awk -f - <<'EOF'\nBEGIN{{system(\"git stash pop\")}}\nEOF",
    "awk 'BEGIN{{system(\"git stash pop\")}} # | data'",
])
def test_awk_surrounding_shell_and_program_input_still_refuse(multi, template):
    main, wt = multi
    assert _blocked(template.format(wt=wt), main)


@pytest.mark.parametrize("program", [
    'BEGIN{system("git stash pop")}',
    'BEGIN{"git stash pop" | getline x}',
    'BEGIN{print "" | "git stash pop"}',
    'BEGIN{f="system"; @f("git stash pop")}',
])
def test_awk_primitives_really_restore_cross_branch(multi, program):
    main, wt = multi
    executable = "gawk" if "@" in program else "awk"
    cmd = f"{executable} '{program}'"
    assert _blocked(cmd, wt), cmd
    assert (wt / "f.txt").read_text() == "base\n"
    if not shutil.which(executable):
        pytest.skip(f"{executable} missing; refusal checked")
    subprocess.run([executable, program], cwd=wt, env=GIT_ENV,
                   check=True, capture_output=True, timeout=30)
    assert (wt / "f.txt").read_text() == "stashed\n"


@pytest.mark.parametrize("cmd", [
    BUILTIN_WRAPPER + "; cd /Users/you; git frobnicate",
    "git -c alias.x='stash pop' x",
    "git -c alias.whatchanged='stash pop' whatchanged",
    "git -c alias.pack-redundant='stash pop' pack-redundant",
])
def test_builtin_alias_refusal_controls(multi, cmd):
    _main, wt = multi
    assert guard.refuse_reason(cmd, str(wt)) is not None
    assert _blocked(cmd, wt)


def test_builtin_hash_object_alias_does_not_pop_real_stash(multi):
    _main, wt = multi
    def stashes():
        return subprocess.check_output(["git", "stash", "list"], cwd=wt, env=GIT_ENV)
    before = stashes()
    assert before
    git("-c", "alias.hash-object=stash pop", "hash-object", "f.txt", cwd=wt)
    assert stashes() == before
    assert (wt / "f.txt").read_text() == "base\n"


@pytest.mark.parametrize("name", sorted(guard._GIT_BUILTINS - {"stash"}))
def test_all_known_builtins_ignore_alias_uncertainty(multi, name):
    main, _wt = multi
    assert guard.refuse_reason(BUILTIN_WRAPPER + "; git " + name, str(main)) is None


@pytest.mark.parametrize("template", [
    "git -C {main} config core.worktree {wt}; git -C {main} stash pop",
    "git config -f {main}/.git/config core.worktree {wt}; git -C {main} stash pop",
    "printf '[core]\\n\\tworktree = {wt}\\n' >> {main}/.git/config; git -C {main} stash pop",
    "shopt -s expand_aliases; alias p='git -C {wt} stash pop'\np",
])
def test_round5_config_write_and_alias_real_restore(multi, template):
    main, wt = multi
    cmd = template.format(main=main, wt=wt)
    assert _blocked(cmd, main), cmd
    subprocess.run(["bash", "-c", cmd], cwd=main, env=GIT_ENV,
                   check=True, capture_output=True, timeout=30)
    assert (main / "f.txt").read_text() == "base\n"
    assert (wt / "f.txt").read_text() == "stashed\n"
    assert not subprocess.check_output(["git", "stash", "list"], cwd=main, env=GIT_ENV).strip()


@pytest.mark.parametrize("variant", ["trap_after", "trap_before", "fallback", "prompt"])
def test_unknown_export_selectors_and_deferred_restores(multi, variant):
    main, wt = multi
    gd = subprocess.check_output(["git", "rev-parse", "--absolute-git-dir"],
                                 cwd=wt, env=GIT_ENV, text=True).strip()
    export = f"export GIT_DIR={gd} GIT_WORK_TREE={wt}"
    pop = f"git -C {main} stash pop"
    branches = "; ".join(f"[ -e /nonexistent-swg-{i} ] && export Y{i}=1" for i in range(5))
    cmd = {
        "trap_after": f"{export}; trap '{pop}' EXIT",
        "trap_before": f"trap '{pop}' EXIT; {export}",
        "fallback": f"{export}; {branches}; {pop}",
        "prompt": f"PROMPT_COMMAND='{pop}'; {export}; eval \"$PROMPT_COMMAND\"",
    }[variant]
    assert _blocked(cmd, main), cmd
    subprocess.run(["bash", "-c", cmd], cwd=main, env=GIT_ENV,
                   check=True, capture_output=True, timeout=30)
    assert (main / "f.txt").read_text() == "base\n"
    assert (wt / "f.txt").read_text() == "stashed\n"
    assert not subprocess.check_output(["git", "stash", "list"], cwd=main, env=GIT_ENV).strip()


def test_state_cap_exported_repository_selectors(multi):
    main, wt = multi
    gd = subprocess.check_output(["git", "rev-parse", "--absolute-git-dir"],
                                 cwd=wt, env=GIT_ENV, text=True).strip()
    branches = "; ".join(f"[ -e /nonexistent-swg-{i} ] && x{i}=1" for i in range(5))
    cmd = f"export GIT_DIR={gd} GIT_WORK_TREE={wt}; {branches}; git -C {main} stash pop"
    assert _blocked(cmd, main), cmd
    subprocess.run(["bash", "-c", cmd], cwd=main, env=GIT_ENV,
                   check=True, capture_output=True, timeout=30)
    assert (main / "f.txt").read_text() == "base\n"
    assert (wt / "f.txt").read_text() == "stashed\n"
    assert not subprocess.check_output(["git", "stash", "list"], cwd=main, env=GIT_ENV).strip()


def test_for_loop_over_same_branch_checkout_allows_apply(multi):
    main, _wt = multi
    cmd = f"for w in {main}; do git -C $w stash apply; done"
    assert guard.refuse_reason(cmd, str(main)) is None, cmd


# A double-quoted command substitution never splits, even when its own text quotes an unknown
# variable (`"$(printf x:%s "$TOK")"`): the inner quotes do not close the outer ones.
AUTH_FETCH = ('set +x; source {env}; TOK="$GITHUB_TOKEN"; git -C {repo} -c http.extraHeader='
              '"Authorization: Basic $(printf x-access-token:%s "$TOK" | base64 -w0)" fetch origin main')


@pytest.mark.parametrize("cmd", [
    AUTH_FETCH,
    'git -C {repo} -c http.extraHeader="X: $(printf %s "$TOK")" fetch origin main',
    'git -C {repo} -c http.extraHeader="X: ${{TOK:-"$OTHER"}}" fetch origin main',
    'git -C {repo} -c http.extraHeader="X: `printf %s "$TOK"`" push origin HEAD',
])
def test_quoted_substitution_with_inner_quotes_allows_fetch(multi, cmd):
    main, wt = multi
    cmd = cmd.format(env=main / "missing.env", repo=wt)
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert block("terminal", command=cmd, workdir=str(main)) is None, cmd
    assert list(guard._git_calls_noted(cmd, str(main)))[-1][:2] == (os.path.realpath(wt), cmd.split()[-3])


@pytest.mark.parametrize("cmd", [
    # The real restore is still refused next to the same authenticated config.
    'TOK="$GITHUB_TOKEN"; git -C {wt} -c http.extraHeader="X: $(printf %s "$TOK")" stash pop',
    'git -C {wt} -c a="$(printf %s "$TOK")" stash apply',
    # An unquoted substitution may still split into the subcommand.
    'git -C {wt} -c a=$(printf %s "$TOK") fetch',
    'git -C {wt} -c a="x"$(printf %s "$TOK") fetch',
    # A restore inside the quoted substitution itself is still analyzed.
    'git -C {wt} -c a="$(git -C {wt} stash pop)" fetch',
])
def test_quoted_substitution_fix_keeps_restore_refusals(multi, cmd):
    main, wt = multi
    cmd = cmd.format(wt=wt)
    assert guard.refuse_reason(cmd, str(main)), cmd
    assert block("terminal", command=cmd, workdir=str(main)), cmd


@pytest.mark.parametrize("cmd", [
    'a=(a.b=c stash pop); git -C {wt} -c "${{a[@]}}"',
    'set -- a.b=c stash pop; git -C {wt} -c "$@"',
    'a=(a.b=c stash pop); git -C {wt} -c "${{a[@]:0}}"',
])
def test_quoted_array_expansion_in_git_option_fails_closed(multi, cmd):
    main, wt = multi
    cmd = cmd.format(wt=wt)
    assert guard.refuse_reason(cmd, str(main)), cmd
    assert block("terminal", command=cmd, workdir=str(main)), cmd
    # Proof: the quoted array really becomes `git -c a.b=c stash pop` and restores main's stash.
    bash = _proof_bash()
    def stashes():
        return subprocess.run(["git", "stash", "list"], cwd=main, env=GIT_ENV, check=True,
                              capture_output=True, text=True).stdout
    before = stashes()
    assert before
    subprocess.run([bash, "-c", cmd], cwd=main, env=GIT_ENV, capture_output=True, timeout=30)
    assert stashes() != before and (wt / "f.txt").read_text() == "stashed\n"


# --- A loop that does not converge in two iterations widens only what changed ---

# Refused as `git stash pop` (no git in it at all): `ARNS="$ARNS ..."` grows on every
# iteration, so the loop did not converge and the whole state collapsed to unknown. HOME was
# lost with it, `~/.local/bin/token-broker` became an unknown command name that might be git,
# and an unknown command run as git may be an alias for `stash pop`. Reported shape.
LOOP_ACCUMULATOR_REPORTED = r'''cat > $TMPDIR/decom_stop.py <<'EOF'
import boto3
ec2 = boto3.client("ec2", region_name="us-east-1")
resp = ec2.stop_instances(InstanceIds=["i-0abc", "i-0def"])
for s in resp["StoppingInstances"]:
    print(s["InstanceId"], s["CurrentState"]["Name"])
EOF
ARNS=""
for i in i-0abc i-0def; do ARNS="$ARNS --resource arn:aws:ec2:us-east-1:111111111111:instance/$i"; done
~/.local/bin/token-broker exec --help 2>&1 | grep -i -E "resource" | head -3'''
LOOP_ACCUMULATOR_ALLOWED = [
    LOOP_ACCUMULATOR_REPORTED,
    'A=""; for i in a b c; do A="$A x$i"; done; ~/bin/tool run --help',
    'A=""; for i in a b; do A="$A/$i"; done; git -C ~/x status',
    'n=0; while [ $n -lt 3 ]; do n=$((n+1)); done; ~/bin/tool --help',
    'A=; for i in a b; do A="$A$i"; done; cd {main} && git stash apply',
]


@pytest.mark.parametrize("cmd", LOOP_ACCUMULATOR_ALLOWED)
def test_non_converging_loop_keeps_unchanged_state(multi, cmd):
    main, _wt = multi
    cmd = cmd.format(main=main)
    assert guard.refuse_reason(cmd, "/Users/you") is None, cmd
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not _blocked(cmd, main), cmd


@pytest.mark.parametrize("template", [
    # Values that change across iterations stay unknown after the loop.
    'D={main}; for i in a b; do D="$D/.."; done; git -C "$D" stash pop',
    'D={main}; for i in a b; do D="$D/x"; cd {wt}; done; git stash pop',
    'for i in a b c; do cd ..; done; git stash pop',
    'A=; for i in a b; do A="$A s"; done; for d in {main}; do cd {wt}; done; git stash pop',
    # A value that changes only from the third iteration on is still widened.
    'A=; B=; C={main}; for i in a b c d; do C="$B"; B="$A"; A="$A x"; done; git -C "$C" stash pop',
    'A=; H={main}; for i in a b c d; do cd "$H"; H="$G"; G="$A"; A="$A {wt}"; done; git stash pop',
    # A changing value used as a command name inside the loop may be git.
    'A=; for i in a b; do A="$A stash"; $A pop; done',
])
def test_non_converging_loop_restores_still_refuse(multi, template):
    main, wt = multi
    cmd = template.format(main=main, wt=wt)
    assert _blocked(cmd, main), cmd


# Refused as `git stash pop` (no git in it at all): `env -u NAME` is an unsupported wrapper
# option, so any later non-builtin git subcommand may be an alias. `$(( $(date +%s) - s ))` was
# analyzed as the subshell `( $(date +%s) - s )`, whose unknown command name might be git, and
# `git - s` then looked like an alias run. Arithmetic text is not a command: only its own
# substitutions run. Reported shape, paths shortened.
ARITH_REPORTED = r'''P11=/opt/py/bin/python; D=/tmp/lcgfix/plugins/live-checkout-guard; TN=test_x
cd /tmp || exit 1
{ echo "== clean env: $(env -u HERMES_SESSION_KEY MAC_HEAVY_OK=1 timeout 100 "$P11" -B -m pytest -q "$D/tests_guard.py::$TN" 2>&1 | tail -1)"
  for f in tests_guard.py tests_update.py; do
    s=$(date +%s); r11=$(MAC_HEAVY_OK=1 timeout 150 "$P11" -B -m pytest -q "$D/$f" 2>&1 | tail -1); t11=$(( $(date +%s) - s ))
    echo "   $f [$t11 s]: $r11"
  done
} > "$TMPDIR/out.txt" 2>&1; cat "$TMPDIR/out.txt" | cut -c1-260'''
ARITH_ALLOWED = [
    ARITH_REPORTED,
    'env -u X true; t=$(( $(date +%s) - 1 )); echo "$t"',
    'env -u X true; echo $(( `date +%s` - 1 ))',
    'env -u X true; echo "$(( 2 * ($(date +%s) - 1) ))"',
    'env -u X true; (( n = $(date +%s) - 1 )); echo "$n"',
    'env -u X true; for (( i = $(date +%s); i < 0; i++ )); do :; done',
]


@pytest.mark.parametrize("cmd", ARITH_ALLOWED)
def test_arithmetic_operands_are_not_commands(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, "/Users/you") is None, cmd
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not _blocked(cmd, main), cmd


@pytest.mark.parametrize("template", [
    # Substitutions inside arithmetic still run, in every arithmetic form.
    'echo $(( $(git -C {wt} stash pop) + 1 ))',
    'echo "$(( `git -C {wt} stash pop` ))"',
    '(( n = $(git -C {wt} stash pop) ))',
    'for (( i = $(git -C {wt} stash pop); i < 1; i++ )); do :; done',
    'echo $(( 1 + $(( $(git -C {wt} stash pop) )) ))',
    'env -u X true; echo $(( $(git -C {wt} stash pop) ))',
    # Arithmetic text expands as if double-quoted: single quotes there do not stop `$(...)`.
    "echo $(( '$(git -C {wt} stash pop)' ))",
    # Unbalanced `$((...))` text is a command substitution of a subshell in Bash.
    'echo $((cd {wt}; git stash pop) )',
    'echo $((true) && (git -C {wt} stash pop))',
    # `((a) ; (b))` is two nested subshells in Bash, not an arithmetic command.
    '((true) ; (git -C {wt} stash pop))',
])
def test_arithmetic_substitutions_still_refuse(multi, template):
    main, wt = multi
    cmd = template.format(wt=wt)
    assert _blocked(cmd, main), cmd


# --- Phantom `stash pop` from trap handlers and SQL LIKE data (public, anonymized fixtures) ---
# Reported: an ssh script with `trap "...git -C $R worktree remove --force $W..." EXIT` was
# refused as `git stash pop` although the command never mentions stash. The double-quoted trap
# word is expanded when `trap` runs, so $R was known; the handler used to be analyzed with every
# variable lost, so `-C $R` could "split" and shift the subcommand.
TRAP_SSH_REPORTED = (
    'timeout 150 ssh -o ConnectTimeout=20 -o BatchMode=yes -o UserKnownHostsFile="$HOME/.hermes/cache/scratch/example_known_hosts" '
    "you@worker.example.com 'R=~/code/hermes-mods; "
    'for w in $(git -C $R worktree list --porcelain | sed -n "s|^worktree \\(/tmp/cc-wt-.*\\)|\\1|p"); '
    'do git -C $R worktree remove --force $w && echo "removed leftover $w"; done; git -C $R worktree prune; '
    "W=/tmp/sm-wt-$$; git -C $R worktree add -q --detach $W HEAD || exit 1; "
    'trap "cd /; git -C $R worktree remove --force $W; rm -f /tmp/sm-$$.*" EXIT; cd $W; '
    "timeout 100 env -i PATH=/usr/bin:/bin HOME=$HOME scripts/test-all --hermes-core $HOME/.hermes/hermes-agent "
    "-k plugins/session-mentions --exact --json /tmp/sm-$$.json > /tmp/sm-$$.log 2>&1; "
    'echo "worker session-mentions rc=$? | $(tail -1 /tmp/sm-$$.log | cut -c1-80)"; '
    'echo "leftover /tmp worktrees: $(git -C $R worktree list | grep -c /tmp/)"\'; echo "ssh rc=$?"'
)
TRAP_DQ_LOCAL = 'R=/srv/repo; W=/tmp/w-$$; git -C $R worktree add -q --detach $W HEAD; trap "git -C $R worktree remove --force $W" EXIT'
# SQL LIKE pattern naming a command: data for the database, not shell code.
SQL_LIKE_HEREDOC = """/usr/bin/python3 - <<'EOF'
import sqlite3
c = sqlite3.connect("x.db")
print(c.execute("select * from t where body LIKE '%`git stash pop`%'").fetchall())
EOF"""
SQL_LIKE_ARGV = """/usr/bin/python3 -c "import sqlite3; sqlite3.connect('x.db').execute(\\"select 1 where b LIKE '%git stash pop%'\\")\""""


@pytest.mark.parametrize("cmd", [TRAP_SSH_REPORTED, TRAP_DQ_LOCAL])
def test_phantom_stash_pop_false_positives_allowed(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main)) is None, cmd
    assert not _blocked(cmd, main), cmd


@pytest.mark.parametrize("cmd", [
    # Real restores in ssh/trap/substitution forms stay refused.
    "ssh you@worker.example.com 'git stash pop'",
    "ssh you@worker.example.com 'git -C $R stash pop'",
    'ssh you@worker.example.com \'R=/srv/repo; git -C "$R" stash apply\'',
    'R=/srv/repo; trap "git -C $R stash pop" EXIT',
    "R=/srv/repo; trap 'git -C $R stash pop' EXIT",
    'trap "git -C $VAR stash drop" EXIT',
    'R=/srv/repo; trap "git -C \\$R stash pop" EXIT; R=/other',
    'R=/srv/repo; trap "git -C $R $SUB pop" EXIT',
    'R=/srv/repo; trap "cd /; git -C $R worktree remove --force $W; git stash pop" EXIT',
    'git -C "$VAR" stash pop',
    'echo "$(git -C "$VAR" stash drop)"',
    # Deferred expansion stays unknown (single quotes), as before.
    "trap 'git -C $R $W' EXIT",
    # A real command after, or hidden in, SQL-LIKE-looking text is still refused.
    SQL_LIKE_HEREDOC.replace("print(", "import os; os.system('git stash pop'); print("),
    """/usr/bin/python3 - <<'EOF'
import os
os.system("echo LIKE '%'; git stash pop; echo '%'")
EOF""",
    """/usr/bin/python3 - <<'EOF'
import subprocess
subprocess.run("git stash apply", shell=True)  # LIKE '%x%'
EOF""",
])
def test_phantom_fix_keeps_real_restores_blocked(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main)) is not None, cmd
    assert _blocked(cmd, main), cmd


def test_single_quoted_trap_defers_variable_expansion(multi):
    main, wt = multi
    # W is harmless at installation time, but single quotes defer it until EXIT.
    cmd = (f'R={shlex.quote(str(wt))}; W=status; '
           'trap \'git -C $R $W\' EXIT; W="stash pop"')
    assert guard.refuse_reason(cmd, str(main)), cmd
    assert _blocked(cmd, main), cmd
    _assert_real_restore(cmd, main, wt)


def test_inferred_stash_pop_diagnostic_is_not_misleading(multi):
    main, _wt = multi
    # Nothing here says `stash`: the refusal must say it is inferred from an unresolved word.
    why = guard.refuse_reason("trap 'git -C $R worktree remove $W' EXIT", str(main))
    assert why and "inferred" in why and "no literal" in why, why
    assert 'git -C $R worktree remove $W' in why, why
    assert 'Quote it' not in why, why
    assert 'the unquoted word' not in why, why


def test_embedded_interpreter_diagnostic_exact_report(multi):
    main, _wt = multi
    cmd = (HERE / 'fixtures/sql_inventory.txt').read_text()
    # Deliberate bounded false refusal: never execute this personal-DB query.
    why = guard.refuse_reason(cmd, str(main))
    assert why and 'conservative embedded interpreter scanning' in why, why
    assert '/usr/bin/python3 -' in why and 'data' in why, why
    assert 'blocked `git stash pop`' not in why, why


@pytest.mark.parametrize('cmd', [SQL_LIKE_HEREDOC, SQL_LIKE_ARGV,
    """python3 -c 'import os; os.system("echo LIKE \\"%$(git stash pop)%\\"")'"""])
def test_sql_like_is_not_an_execution_exemption(multi, cmd):
    main, _wt = multi
    assert guard.refuse_reason(cmd, str(main)), cmd
    # A literal restore keeps the plain wording (no "inferred" claim).
    plain = guard.refuse_reason("git -C $R stash pop", str(main))
    assert plain and "no literal" not in plain, plain


@pytest.mark.parametrize('op', ['pop', 'apply', 'drop'])
@pytest.mark.parametrize('form', ['direct', 'quoted-C', 'ssh', 'substitution'])
def test_review_restore_matrix(multi, op, form):
    main, wt = multi
    literal = f'git -C "{wt}" stash {op}'
    cmd = {
        'direct': f'git stash {op}',
        'quoted-C': f'R={shlex.quote(str(wt))}; git -C "$R" stash {op}',
        'ssh': f'ssh you@worker.example.com {shlex.quote(literal)}',
        'substitution': f'echo "$({literal})"',
    }[form]
    before = subprocess.check_output(['git', 'rev-parse', 'refs/stash'], cwd=main, env=GIT_ENV)
    assert guard.refuse_reason(cmd, str(wt)), cmd
    assert _blocked(cmd, wt), cmd
    assert subprocess.check_output(['git', 'rev-parse', 'refs/stash'], cwd=main, env=GIT_ENV) == before
    assert (wt / 'f.txt').read_text() == 'base\n'


@pytest.mark.parametrize('when', ['before', 'after'])
def test_early_trap_expansion_keeps_runtime_exports_unknown(multi, when):
    main, wt = multi
    gd = _gitdir(wt)
    export = f'export GIT_DIR={shlex.quote(gd)} GIT_WORK_TREE={shlex.quote(str(wt))}'
    trap = f'R={shlex.quote(str(main))}; trap "git -C $R stash pop" EXIT'
    cmd = f'{export}; {trap}' if when == 'before' else f'{trap}; {export}'
    assert _blocked(cmd, main), cmd
    _assert_real_restore(cmd, main, wt)


def test_sql_looking_string_can_really_execute_shell(multi):
    main, wt = multi
    program = 'import os; s="LIKE \'%git stash pop%\'"; os.system(s.split("%")[1])'
    cmd = f'cd {shlex.quote(str(wt))}; python3 -c ' + shlex.quote(program)
    assert _blocked(cmd, wt), cmd
    _assert_real_restore(cmd, main, wt)
