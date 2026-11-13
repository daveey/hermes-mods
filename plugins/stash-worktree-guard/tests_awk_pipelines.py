"""Awk inside pipelines: reported false refusals plus a seeded generator over pipeline shapes.

Run: python3 -m pytest -q -p no:cacheprovider tests_awk_pipelines.py

The guard may treat an awk invocation as unable to run commands only when every word up to and
including the program passed an allowlist (``_awk_program_inert``). The generator below runs every
sampled shape in real bash, with a fake ``git`` that records its argv, and asserts that a shape whose
payload really ran a stash restore is refused whenever the recorded main verdict refused it.
"""

import importlib.util
import itertools
import json
import os
import random
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


plugin = _load("stash_worktree_guard_plugin_awk", "__init__.py")
guard = plugin.guard

# Reported 2026-11-13 (stash guard refused it, which also blocked the Desktop relaunch in front).
REPORTED = (
    "/usr/bin/open /Applications/Hermes.app; sleep 8; ps -axo pid,command | grep -i 'Hermes.app' | grep -v grep; "
    'grep -aE "apply window lost|no build serves" ~/.hermes/logs/hermes-sync.log | cut -c1-60 '
    "| awk '{print $1}' | sort | uniq -c"
)

ALLOW = [
    REPORTED,
    "grep -aE \"apply window lost|no build serves\" log.txt | awk '{print $1}'",
    "grep -aE \"stash pop|apply\" log.txt | awk -F: '{print $1}' | sort | uniq -c",
    "grep -aE \"stash pop|apply\" log.txt | awk -F ':' -v n=2 '{print $n}'",
    "printf 'git stash pop\\n' | gawk -- '{print NF}'",
    "printf 'git stash pop\\n' | mawk 'NF > 1 {print $2}'",
]

# Must stay refused: the program (or a later shell) can run the restore.
REFUSE = [
    "grep -aE \"apply window lost|no build serves\" log.txt | awk '{system(\"git stash pop\")}'",
    "grep -aE \"apply|x\" log.txt | awk '{print | \"git stash pop\"}'",
    "grep -aE \"apply|x\" log.txt | awk '{print $1 | \"git stash apply\"}' | sort",
    "printf 'git stash pop\\n' | awk '{system($0)}'",
    "printf 'git stash pop\\n' | awk '{print}' | sh",
    "printf 'git stash pop\\n' | awk -F: '{print $1}' | bash",
    "awk 'BEGIN{\"git stash pop\" | getline x}'",
    "gawk 'BEGIN{f=\"system\"; @f(\"git stash pop\")}'",
    "printf '%s' 'BEGIN{system(\"git stash pop\")}' | awk -f -",
    "printf 'git stash pop\\n' | awk -v x=1 '{system($0)}'",
]


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
    (tree / "log.txt").write_text("apply window lost: a\nno build serves b\n")
    (tree / "prog.awk").write_text("{system($0)}\n")
    return tree


def _blocked(cmd, cwd):
    got = plugin._pre_tool_call(tool_name="terminal", args={"command": cmd, "workdir": str(cwd)})
    return bool(got and got["action"] == "block")


@pytest.mark.parametrize("cmd", ALLOW)
def test_inert_awk_in_pipelines_is_allowed(wt, cmd):
    assert guard.refuse_reason(cmd, str(wt)) is None, cmd
    assert not _blocked(cmd, wt), cmd


@pytest.mark.parametrize("cmd", REFUSE)
def test_awk_that_can_run_a_restore_stays_refused(wt, cmd):
    assert guard.refuse_reason(cmd, str(wt)) is not None, cmd
    assert _blocked(cmd, wt), cmd


@pytest.mark.parametrize("words,inert", [
    (["awk", "'{print $1}'"], True),
    (["awk", "-F:", "'{print $1}'", "f"], True),
    (["awk", "-F", "':'", "-v", "x=1", "--", "'{print}'", "$unknown_operand"], True),
    (["gawk", "-vx=1", "NF"], True),
    (["awk", "'{system($0)}'"], False),
    (["awk", "'{print | \"sh\"}'"], False),
    (["gawk", "'{@f()}'"], False),
    (["awk", "-f", "prog.awk"], False),
    (["awk", "-e", "'{print}'"], False),
    (["gawk", "-i", "inc", "'{print}'"], False),
    (["gawk", "-l", "ext", "'{print}'"], False),
    (["mawk", "-W", "exec", "'{print}'"], False),
    (["awk", "-v", "x=$y", "'{print}'"], False),       # unquoted unknown value may split into a program
    (["awk", "$PROG"], False),                         # program not a fixed word
    (["awk", "sys{tem,}"], False),                     # brace expansion can build a primitive
    (["awk", '"{print $1}"'], False),                  # `$` inside double quotes expands
    (["awk", "-F"], False),                            # option value missing
    (["awk", "-v", "x", "'{print}'"], False),          # -v needs NAME=VAL
    (["awk", "-v", "'{print}'"], False),               # -v NAME=VAL missing: the next word is no value
    (["awk", "-"], False),                             # an option-looking word is never the program
    (["awk", "--"], False),                            # `--` with no program after it
    (["awk", "-W", "'{print}'"], False),
    (["awk", "'{print}'", "-f", "prog.awk"], True),    # words after the program are operands
    (["awk", "-F''", "'{print}'"], False),             # argv `-F`: the next word is the separator
    (["awk"], False),
])
def test_awk_program_inert_allowlist(words, inert):
    assert guard._awk_program_inert(words) is inert, words


# ---- seeded generator over pipeline shapes ------------------------------------------------------
SEED = 20261113
UPSTREAM = ["", 'grep -aE "apply window lost|no build serves" log.txt | ', "printf 'git stash pop\\n' | ",
            "printf '%s\\n' 'BEGIN{system(\"git stash pop\")}' | "]
AWKS = ["awk", "gawk", "mawk"]
OPTIONS = ["", "-F:", "-F ' '", "-v x=1", "--", "-f prog.awk", "-f -"]
PROGRAMS = ["'{print $1}'", "'{print}'", "'{system($0)}'", "'BEGIN{system(\"git stash pop\")}'",
            "'{print | \"git stash pop\"}'", "'BEGIN{\"git stash pop\" | getline x}'",
            "'BEGIN{f=\"system\"; @f(\"git stash pop\")}'"]
OPERANDS = ["", " log.txt"]
DOWNSTREAM = ["", " | sort | uniq -c", " | sh"]
FIXTURE = HERE / "fixtures" / "awk_pipeline_main_verdicts.json"


def generated_cases():
    cases = set()
    for up, awk, opt, prog, operand, down in itertools.product(UPSTREAM, AWKS, OPTIONS, PROGRAMS, OPERANDS, DOWNSTREAM):
        program = "" if opt.startswith("-f") else " " + prog
        cases.add(f"{up}{awk}{' ' + opt if opt else ''}{program}{operand}{down}")
    cases = sorted(cases)
    limit = int(os.environ.get("AWK_GENERATOR_CASES", "700"))
    return sorted(random.Random(SEED).sample(cases, min(limit, len(cases))))


def _payload_ran(cmd, cwd, fakebin):
    mark = cwd / "git-argv.log"
    mark.write_text("")
    env = {"PATH": f"{fakebin}:/usr/bin:/bin", "MARK": str(mark), "HOME": str(cwd), "LC_ALL": "C"}
    subprocess.run(["bash", "-c", cmd], cwd=cwd, env=env, capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
    return any("stash" in line and ("pop" in line or "apply" in line) for line in mark.read_text().splitlines())


def test_generated_awk_pipelines_never_run_a_restore_main_refused(wt, tmp_path):
    """Rule: payload ran and main refused => head refuses. Reports the violation count (expected 0)."""
    if not all(shutil.which(a) for a in AWKS):
        pytest.skip("needs awk, gawk and mawk")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake = fakebin / "git"
    fake.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$MARK"\n')
    fake.chmod(0o755)
    main = json.loads(FIXTURE.read_text())["verdicts"]
    violations, ran_count, missing = [], 0, []
    for cmd in generated_cases():
        if cmd not in main:
            missing.append(cmd)
            continue
        ran = _payload_ran(cmd, wt, fakebin)
        ran_count += ran
        if ran and main[cmd] and guard.refuse_reason(cmd, str(wt)) is None:
            violations.append(cmd)
    print(f"awk generator: seed={SEED} cases={len(generated_cases())} payload_ran={ran_count} "
          f"violations={len(violations)}")
    assert not missing, missing[:5]
    assert ran_count > 50  # the generator really exercises restores
    assert violations == []


def test_generated_inert_awk_never_runs_a_restore(wt, tmp_path):
    """Soundness of the permissive exit itself: whenever the allowlist judged the awk program inert, the
    awk process itself ran no restore (a later `| sh` stage is analyzed separately and may)."""
    if not all(shutil.which(a) for a in AWKS):
        pytest.skip("needs awk, gawk and mawk")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "git").write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$MARK"\n')
    (fakebin / "git").chmod(0o755)
    checked = 0
    for up, awk, opt, prog, operand in itertools.product(UPSTREAM, AWKS, OPTIONS, PROGRAMS, OPERANDS):
        program = [] if opt.startswith("-f") else [prog]
        words = [awk, *(opt.split(" ", 1) if opt else [])] + program + ([operand.strip()] if operand else [])
        if opt == "-F ' '":
            words = [awk, "-F", "' '"] + program + ([operand.strip()] if operand else [])
        if guard._awk_program_inert(words):
            checked += 1
            cmd = f"{up}{' '.join(words)}"
            assert not _payload_ran(cmd, wt, fakebin), cmd
    assert checked > 20
