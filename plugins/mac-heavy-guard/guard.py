"""Decide whether a shell command is heavy work that should not run on a laptop.

Shared by the Hermes pre_tool_call hook (__init__.py) and the Claude Code PreToolUse hook
(claude_hook.py). Heavy = release builds (`cargo build --release`), container builds
(`docker build`), whole test suites (`pytest` with no test path or with `-n`, bare `npm test`,
`vitest run` with no filter, `make test`, Hermes `scripts/run_tests.sh` with no test path) and whole-repo dependency installs (`npm install`,
`pnpm install`, `uv sync`, `pip install -r`). Targeted work stays allowed (`pytest tests/test_x.py`,
`cargo build`, `npm install left-pad`), as does anything sent over `ssh`.

Hermes core worktrees: a multi-file Python test run there (pytest or scripts/run_tests.sh with 2+ test paths
or a directory) is allowed but gets a note pointing at `hermes-core-wt test <same args>`, which runs it on
hermes-hub; a blocked whole-suite run there lists that command first.

Opt-out: prefix the command with `MAC_HEAVY_OK=1` (or export it). Off entirely: `MAC_HEAVY_GUARD=0`.
The guard only acts on macOS.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from typing import Iterator, Optional

OPT_OUT = "MAC_HEAVY_OK"
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n", "|&", ";;"}
# Wrappers whose arguments are still the command to run here.
WRAPPERS = {"env", "nice", "time", "sudo", "command", "exec", "caffeinate", "nohup", "stdbuf", "taskpolicy"}
WRAPPER_VALUE_FLAGS = {"-n", "-u", "-c", "-b", "-d", "-g"}  # nice -n 10, sudo -u x, taskpolicy -c bg
PY = re.compile(r"^(?:.*/)?python(?:3(?:\.\d+)?)?$")
SHELLS = {"bash", "sh", "zsh", "dash"}
TEST_PATH = re.compile(r"(?:/|\.py$|::|^test_|_test\.py$|\.(?:test|spec)\.[jt]sx?$)")
PYTEST_VALUE_FLAGS = {"-m", "-p", "-c", "-o", "-W", "--rootdir", "--confcutdir", "--basetemp",
                      "--junitxml", "--maxfail", "--deselect", "--ignore", "--ignore-glob", "-r", "--tb",
                      "--durations", "--cov", "--cov-report", "--timeout", "--log-level"}


def enabled() -> bool:
    return sys.platform == "darwin" and os.environ.get("MAC_HEAVY_GUARD", "1") != "0"


def _tokens(text: str) -> Optional[list[str]]:
    lex = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = "#"
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: don't guess
        return None


def commands(text: str) -> Iterator[tuple[list[str], set[str]]]:
    """Yield (argv, env-assignment names) for each simple command, recursing into `bash -c`."""
    toks = _tokens(text)
    if toks is None:
        return
    cur: list[str] = []
    for t in toks + [";"]:
        if t in SEPARATORS or (t and set(t) <= set(";&|()\n")):
            if cur:
                yield from _unwrap(cur)
            cur = []
        else:
            cur.append(t)


def _unwrap(argv: list[str]) -> Iterator[tuple[list[str], set[str]]]:
    env: set[str] = set()
    i = 0
    while i < len(argv):
        a = argv[i]
        if ASSIGNMENT.match(a):
            if a.startswith(f"{OPT_OUT}=") and a.split("=", 1)[1] not in ("", "0"):
                env.add(OPT_OUT)
            i += 1
        elif os.path.basename(a) in WRAPPERS:
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 2 if argv[i] in WRAPPER_VALUE_FLAGS else 1
        elif os.path.basename(a) in ("timeout", "gtimeout"):
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
            i += 1  # the duration
        else:
            break
    rest = argv[i:]
    if not rest:
        return
    name = os.path.basename(rest[0])
    if name == "ssh":  # runs elsewhere
        return
    flag = next((k for k, a in enumerate(rest[1:], 1) if re.fullmatch(r"-[a-z]*c[a-z]*", a)), None)
    if name in SHELLS and flag is not None:
        payload = rest[flag + 1] if flag + 1 < len(rest) else ""
        for sub, sub_env in commands(payload):
            yield sub, sub_env | env
        return
    yield rest, env


def _strip_runner(argv: list[str]) -> list[str]:
    """`uv run pytest`, `poetry run pytest`, `python -m pytest`, `npx vitest`, `pnpm exec vitest` -> tool argv."""
    while argv:
        name = os.path.basename(argv[0])
        if name in ("uv", "poetry", "pdm", "hatch", "rye") and len(argv) > 1 and argv[1] == "run":
            argv = [a for a in argv[2:]]
            while argv and argv[0].startswith("-"):
                argv = argv[1:]
        elif PY.match(name) and len(argv) > 2 and argv[1] == "-m":
            argv = argv[2:]
        elif name in ("npx", "bunx") or (name in ("pnpm", "yarn", "bun") and len(argv) > 1 and argv[1] in ("exec", "dlx")):
            argv = argv[1:] if name in ("npx", "bunx") else argv[2:]
            while argv and argv[0].startswith("-"):
                argv = argv[1:]
        else:
            return argv
    return argv


def _pytest_full(args: list[str]) -> Optional[str]:
    if any(a == "-n" or a.startswith("-n=") or (a.startswith("-n") and a[2:].isdigit()) or a.startswith("--numprocesses")
           for a in args):
        return "pytest with -n (parallel workers)"
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in PYTEST_VALUE_FLAGS:
            skip = True
            continue
        if a == "-k" or a.startswith("-k") or a.startswith("--lf") or a.startswith("--last-failed"):
            return None
        if not a.startswith("-"):
            return None  # a test path, dir or node id
    return "the whole pytest suite (no test path)"


# Hermes core's per-file suite runner (scripts/run_tests.sh -> run_tests_parallel.py): positional args and
# --files/--files-from/--paths narrow it; with none it runs all ~6000 test files.
RUN_TESTS = ("run_tests.sh", "run_tests_parallel.py")
RUN_TESTS_VALUE_FLAGS = {"-j", "--jobs", "--file-timeout", "--file-retries", "--slice", "--generate-slices",
                         "--flaky-report"}
RUN_TESTS_SELECT_FLAGS = ("--files", "--files-from", "--paths")


def _run_tests_full(args: list[str]) -> Optional[str]:
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith(RUN_TESTS_SELECT_FLAGS):
            return None
        if a in RUN_TESTS_VALUE_FLAGS:
            skip = True
            continue
        if not a.startswith("-"):
            return None
    return "the whole Hermes test suite (run_tests.sh with no test paths)"


def _is_run_tests(token: str) -> bool:
    # No whitespace: `echo "... with scripts/run_tests.sh"` is prose, not the runner.
    return not any(c.isspace() for c in token) and os.path.basename(token) in RUN_TESTS


def check_argv(argv: list[str]) -> Optional[str]:
    first = os.path.basename(argv[0])
    if (PY.match(first) or first in SHELLS) and len(argv) > 1 and _is_run_tests(argv[1]):
        argv = argv[1:]
    if argv and _is_run_tests(argv[0]):
        return _run_tests_full(argv[1:])
    argv = _strip_runner(argv)
    if not argv:
        return None
    name, args = os.path.basename(argv[0]), argv[1:]
    sub = args[0] if args else ""
    if name == "cargo":
        if sub in ("build", "b", "test", "t", "bench", "run", "r", "install", "clippy") and (
                "--release" in args or "-r" in args[1:] or "--profile=release" in args):
            return f"cargo {sub} --release"
        if sub == "bench":
            return "cargo bench"
        if sub in ("test", "t", "build", "b") and ("--workspace" in args or "--all" in args):
            return f"cargo {sub} --workspace"
        return None
    if name in ("docker", "podman", "nerdctl"):
        if sub == "build" or args[:2] == ["buildx", "build"] or args[:2] == ["image", "build"]:
            return "docker build"
        if sub == "compose" and ("build" in args or "--build" in args):
            return "docker compose build"
        return None
    if name in ("pytest", "py.test"):
        return _pytest_full(args)
    if name in ("vitest", "jest"):
        rest = [a for a in args if a not in ("run", "--run")]
        if any(not a.startswith("-") for a in rest) or any(a.startswith(("-t", "--testNamePattern")) for a in rest):
            return None
        return f"the whole {name} suite (no file filter)"
    if name in ("npm", "pnpm", "yarn", "bun"):
        if sub in ("test", "t") or (sub == "run" and len(args) > 1 and args[1] in ("test", "test:all", "test:ci")):
            extra = args[2:] if sub == "run" else args[1:]
            extra = [a for a in extra if a != "--"]
            return None if any(not a.startswith("-") for a in extra) else f"{name} test (whole suite)"
        if sub in ("install", "i", "ci", "add") or (name == "yarn" and not args):
            pkgs = [a for a in args[1:] if not a.startswith("-")]
            return None if (pkgs and sub != "ci") else f"{name} {sub or 'install'} (whole-repo install)"
        return None
    if name == "uv" and sub == "sync":
        return "uv sync (whole-repo install)"
    if name in ("pip", "pip3") or (PY.match(name) and args[:2] == ["-m", "pip"]):
        pargs = args[2:] if PY.match(name) else args
        if pargs[:1] == ["install"] and ("-r" in pargs or any(a.startswith("--requirement") for a in pargs)
                                          or "-e" in pargs and "." in pargs):
            return "pip install of a whole requirements set"
        return None
    if name == "poetry" and sub == "install":
        return "poetry install (whole-repo install)"
    if name in ("make", "gmake") and any(a in ("test", "check", "test-all", "ci") for a in args):
        return f"make {' '.join(a for a in args if not a.startswith('-'))}"
    return None


SCRIPT_MAX_BYTES = 64 * 1024
SCRIPT_MAX_DEPTH = 2


def _script_arg(argv: list[str]) -> Optional[str]:
    """The local file this simple command runs as a shell script, if it names one."""
    name = os.path.basename(argv[0])
    if name in SHELLS:
        for a in argv[1:]:
            if not a.startswith("-"):
                return a
        return None
    if name in ("source", "."):
        return argv[1] if len(argv) > 1 else None
    return argv[0] if "/" in argv[0] else None


def _read_script(path: str, cwd: Optional[str]) -> Optional[str]:
    """Text of a local shell script (<= 64 KB), or None when missing, binary or not a shell script."""
    try:
        full = os.path.expanduser(path)
        if not os.path.isabs(full):
            full = os.path.join(cwd or os.getcwd(), full)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as fh:
            raw = fh.read(SCRIPT_MAX_BYTES)
    except OSError:
        return None
    if b"\0" in raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("#!"):
        words = text.split("\n", 1)[0][2:].split()
        interp = [os.path.basename(w) for w in words if not w.startswith("-")]
        if interp and interp[0] == "env":
            interp = interp[1:]
        if not interp or interp[0] not in SHELLS | {"ksh", "ash"}:
            return None
    return text


Hit = tuple[str, Optional[list[str]], Optional[str]]  # (why, Python-test args if a direct pytest/run_tests run, cwd)


def _scan(text: str, cwd: Optional[str], depth: int, in_script: bool) -> Optional[Hit]:
    for argv, env in commands(text):
        if argv[0] == "cd" and len(argv) > 1 and not argv[1].startswith("-"):
            cwd = _cd(cwd, argv[1])
            continue
        if OPT_OUT in env:
            continue
        why = check_argv(argv)
        if not why and in_script:  # `step pytest python3 -m pytest -q`: a heavy command passed as arguments
            why = next((w for w in (check_argv(argv[k:]) for k in range(1, len(argv))) if w), None)
        if why:
            test = None if in_script else _py_test(argv)
            return why, (test[1] if test else None), cwd
        ref = _script_arg(argv)
        if ref and depth < SCRIPT_MAX_DEPTH:
            if os.path.basename(argv[0]) not in SHELLS | {"source", "."}:
                # Direct execution requires an executable file; quoted data paths do not.
                full = _cd(cwd, ref)
                if not os.path.isfile(full) or not os.access(full, os.X_OK):
                    continue
            body = _read_script(ref, cwd)
            if body:
                inner = _scan(body, cwd, depth + 1, True)
                if inner:
                    return f"{ref} \u2192 {inner[0]}", None, cwd
    return None


def _cd(cwd: Optional[str], target: str) -> str:
    target = os.path.expanduser(target)
    return target if os.path.isabs(target) else os.path.join(cwd or os.getcwd(), target)


def _opted_out() -> bool:
    return os.environ.get(OPT_OUT, "0") not in ("", "0")


def check_shell(text: str, cwd: Optional[str] = None) -> Optional[str]:
    if _opted_out():
        return None
    hit = _scan(text, cwd, 0, False)
    return hit[0] if hit else None


# --- Hermes core worktrees: point multi-file test runs at `hermes-core-wt test` (runs on hermes-hub) -------------

CORE_ORIGIN = re.compile(r"[:/](?:daveey/hermes|NousResearch/hermes-agent)(?:\.git)?/?$", re.I)


def core_worktree(cwd: Optional[str]) -> Optional[str]:
    """Root of the Hermes core checkout holding cwd (origin daveey/hermes or NousResearch/hermes-agent, and
    scripts/run_tests.sh present), else None."""
    d = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    while not os.path.exists(os.path.join(d, ".git")):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    if not os.path.isfile(os.path.join(d, "scripts", "run_tests.sh")):
        return None
    try:
        url = subprocess.run(["git", "-C", d, "config", "--get", "remote.origin.url"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return d if CORE_ORIGIN.search(url) else None


def _py_test(argv: list[str]) -> Optional[tuple[str, list[str]]]:
    """("pytest" | "run_tests", args) when argv runs pytest or Hermes's run_tests.sh / run_tests_parallel.py."""
    first = os.path.basename(argv[0])
    if (PY.match(first) or first in SHELLS) and len(argv) > 1 and _is_run_tests(argv[1]):
        argv = argv[1:]
    if argv and _is_run_tests(argv[0]):
        return "run_tests", argv[1:]
    argv = _strip_runner(argv)
    if argv and os.path.basename(argv[0]) in ("pytest", "py.test"):
        return "pytest", argv[1:]
    return None


def _multi_file(kind: str, args: list[str], cwd: Optional[str]) -> bool:
    """2+ distinct test files, or any directory (run_tests.sh's --files-from list counts as many)."""
    value_flags = PYTEST_VALUE_FLAGS | {"-k"} | (RUN_TESTS_VALUE_FLAGS if kind == "run_tests" else set())
    paths: set[str] = set()
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--" and kind == "run_tests":
            break  # the rest goes to pytest as options
        if kind == "run_tests" and a.startswith(RUN_TESTS_SELECT_FLAGS):
            flag, eq, val = a.partition("=")
            if not eq:
                val = args[i] if i < len(args) else ""
                i += 1
            if flag == "--files-from":
                return True
            paths.update(v for v in re.split(r"[,\s]+", val) if v)
            continue
        if a in value_flags:
            i += 1
            continue
        if not a.startswith("-"):
            paths.add(a)
    files = set()
    for p in paths:
        f = p.split("::", 1)[0]
        if f.endswith("/") or os.path.isdir(os.path.join(cwd or os.getcwd(), os.path.expanduser(f))):
            return True
        files.add(f)
    return len(files) >= 2


def core_test_command(args: list[str]) -> str:
    return "hermes-core-wt test" + (" " + shlex.join(args) if args else "")


def note(text: str, cwd: Optional[str] = None) -> Optional[str]:
    """Non-blocking note for a multi-file Python test run inside a Hermes core worktree, else None."""
    if _opted_out():
        return None
    for argv, env in commands(text):
        if argv[0] == "cd" and len(argv) > 1 and not argv[1].startswith("-"):
            cwd = _cd(cwd, argv[1])
            continue
        if OPT_OUT in env:
            continue
        test = _py_test(argv)
        if test and _multi_file(test[0], test[1], cwd) and core_worktree(cwd):
            return (
                f"mac-heavy-guard note: `{shlex.join(argv)}` runs several test files on this MacBook, where "
                "parallel pytest runs from many agents pile up and slow Hermes Desktop. "
                f"`{core_test_command(test[1])}` runs the same tests on hermes-hub (under nice, on a snapshot of "
                "this worktree including uncommitted files) with the output streamed back; use it for multi-file "
                "runs. Not blocked: targeted runs are allowed here."
            )
    return None


def evaluate(text: str, cwd: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """(block message, note) for a shell command; at most one is set."""
    if _opted_out():
        return None, None
    hit = _scan(text, cwd, 0, False)
    if hit:
        why, test_args, hit_cwd = hit
        core = core_test_command(test_args) if test_args is not None and core_worktree(hit_cwd) else None
        return message(why, core), None
    return None, note(text, cwd)


def message(why: str, core_cmd: Optional[str] = None) -> str:
    core = (f"- This is a Hermes core worktree: {core_cmd} runs it on hermes-hub (under nice) and streams the "
            "output back.\n") if core_cmd else ""
    return (
        f"mac-heavy-guard: blocked {why} on this Mac. Heavy builds, whole test suites and whole-repo installs "
        "pile up across agents here (load 150-270) and make Hermes Desktop slow, so they run on the hub or "
        "worker-1 by default.\n"
        f"{core}"
        "- Hermes: put the work on a card; cards that name tests/builds go to worker-1 automatically "
        "(kanban.placement.heavy_route), or set runs_on=[machine:worker-1].\n"
        "- Anywhere: run it on the hub, e.g. ssh ubuntu@hermes-hub 'cd <repo> && nice -n 10 <cmd>'.\n"
        "- Narrow it: a single test file/dir or a debug build is allowed here.\n"
        f"- Needs this Mac (Xcode/macOS-only, a Mac-only checkout)? Re-run with {OPT_OUT}=1 <cmd>; "
        "CARGO_BUILD_JOBS / PYTEST_XDIST_AUTO_NUM_WORKERS / VITEST_MAX_WORKERS cap it at 4 workers."
    )
