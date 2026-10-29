"""Decide whether a shell command is heavy work that should not run on a laptop.

Shared by the Hermes pre_tool_call hook (__init__.py) and the Claude Code PreToolUse hook
(claude_hook.py). Heavy = release builds (`cargo build --release`), container builds
(`docker build`), whole test suites (`pytest` with no test path or with `-n`, bare `npm test`,
`vitest run` with no filter, `make test`) and whole-repo dependency installs (`npm install`,
`pnpm install`, `uv sync`, `pip install -r`). Targeted work stays allowed (`pytest tests/test_x.py`,
`cargo build`, `npm install left-pad`), as does anything sent over `ssh`.

Opt-out: prefix the command with `MAC_HEAVY_OK=1` (or export it). Off entirely: `MAC_HEAVY_GUARD=0`.
The guard only acts on macOS.
"""

from __future__ import annotations

import os
import re
import shlex
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
    lex = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=";&|()")
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
        if t in SEPARATORS or (t and set(t) <= set(";&|()")):
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


def check_argv(argv: list[str]) -> Optional[str]:
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


def check_shell(text: str) -> Optional[str]:
    if os.environ.get(OPT_OUT, "0") not in ("", "0"):
        return None
    for argv, env in commands(text):
        if OPT_OUT in env:
            continue
        why = check_argv(argv)
        if why:
            return why
    return None


def message(why: str) -> str:
    return (
        f"mac-heavy-guard: blocked {why} on this Mac. Heavy builds, whole test suites and whole-repo installs "
        "pile up across agents here (load 150-270) and make Hermes Desktop slow, so they run on the hub or "
        "worker-1 by default.\n"
        "- Hermes: put the work on a card; cards that name tests/builds go to worker-1 automatically "
        "(kanban.placement.heavy_route), or set runs_on=[machine:worker-1].\n"
        "- Anywhere: run it on the hub, e.g. ssh ubuntu@hermes-hub 'cd <repo> && nice -n 10 <cmd>'.\n"
        "- Narrow it: a single test file/dir or a debug build is allowed here.\n"
        f"- Needs this Mac (Xcode/macOS-only, a Mac-only checkout)? Re-run with {OPT_OUT}=1 <cmd>; "
        "CARGO_BUILD_JOBS / PYTEST_XDIST_AUTO_NUM_WORKERS / VITEST_MAX_WORKERS cap it at 4 workers."
    )
