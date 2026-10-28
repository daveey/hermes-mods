"""Detection logic for cpu-load-guard (pure functions; no Hermes imports)."""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

# hermes-hub is the shared kanban host. It used to be the sanctioned place to
# generate load, which put per-core burners on the fleet. Protect it by default.
# A cpu-load-guard.yaml protected_hosts list still replaces this whole list.
DEFAULT_PROTECTED = ["David-MBP", "MacBook-Pro-177", "hermes-hub"]

# Expressions that evaluate to "number of cores".
NCPU_RE = re.compile(
    r"\bnproc\b|hw\.(?:logical|physical)?cpu\b|hw\.ncpu|_NPROCESSORS_(?:ONLN|CONF)|"
    r"\bcpu_count\s*\(|\bos\.sched_getaffinity\b|\bnum_cpus\b|\bavailableProcessors\b",
    re.I,
)
STRESS_RE = re.compile(r"(?<![\w./-])stress(?:-ng)?\b")
DEVNULL_SRC = r"/dev/(?:zero|urandom)"
# Shell burners: any of these, fanned out to ~one per core, saturates the machine.
SHELL_BURNER_RE = re.compile(
    r"(?<![\w./-])(?:"
    r"yes\b[^;&|\n)]*?(?:>\s*/dev/null|&>\s*/dev/null|&(?!&))"
    r"|dd\s+if=" + DEVNULL_SRC + r"\s+of=/dev/null"
    r"|cat\s+" + DEVNULL_SRC + r"\s*>\s*/dev/null"
    r"|(?:sha\d*sum|md5sum|gzip|bzip2|xz)\s+(?:-\w+\s+)*" + DEVNULL_SRC +
    r"|(?:sysbench|cpuburn|burnP6|burnK7|lookbusy)\b"
    r"|while\s*(?::|true|\[\s*1\s*\])\s*;?\s*do\s*(?::|true)?\s*;?\s*done"
    r")",
    re.I,
)
# Backgrounded burners, counted literally (no loop needed to reach the threshold).
BG_BURNER_RE = re.compile(
    r"(?<![\w./-])(?:yes\b[^;&|\n)]*?(?:>\s*/dev/null|&>\s*/dev/null)\s*&(?!&)"
    r"|dd\s+if=" + DEVNULL_SRC + r"\s+of=/dev/null[^;&|\n]*?&(?!&)"
    r"|cat\s+" + DEVNULL_SRC + r"\s*>\s*/dev/null[^;&|\n]*?&(?!&)"
    r"|while\s*(?::|true|\[\s*1\s*\])\s*;?\s*do\s*(?::|true)?\s*;?\s*done\s*&(?!&))",
    re.I,
)
PY_BUSY_RE = re.compile(
    r"while\s+(?:True|1)\s*:\s*(?:pass|continue)\b|while\s+(?:True|1)\s*:\s*\n\s+(?:pass|continue)\b"
)
_PY_BURNERS = r"(?:yes|stress|stress-ng|sha256sum|md5sum|lookbusy|sysbench)"
# A burner name counts only when it is launched: the first argument of a spawn call ...
PY_SPAWN_CALL_RE = re.compile(
    r"\b(?:Popen|run|call|check_call|check_output|system|popen|getoutput|getstatusoutput|"
    r"spawn\w*|exec[lv]p?e?|create_subprocess_(?:exec|shell))\s*\(\s*\[?\s*[\"']" + _PY_BURNERS + r"\b"
)
# ... or the head of an argv list in code that spawns processes at all.
PY_ARGV_RE = re.compile(r"\[\s*[\"']" + _PY_BURNERS + r"[\"']\s*[,\]]")
PY_SPAWNS_RE = re.compile(r"\bsubprocess\b|\bos\.(?:system|popen|spawn|exec)|\bPopen\b|create_subprocess_")

SCRIPT_EXTS = {"", ".sh", ".bash", ".zsh", ".ksh", ".command", ".py"}
MAX_SCRIPT_BYTES = 256 * 1024
MAX_DEPTH = 2
_TOKEN = r"([^\s;&|<>()'\"`]+)"
# A shell word that may be single- or double-quoted (quoted paths can contain spaces). Three groups;
# _word() returns the one that matched.
_QTOKEN = r"(?:'([^']+)'|\"([^\"]+)\"|" + _TOKEN + ")"
# `bash x.sh`, `sh -x x`, `source x`, `. x`, `python3 x.py` anywhere in the command (also inside
# terminal('...') strings in execute_code).
INTERP_RE = re.compile(
    r"(?:^|(?<=[\s;&|(`'\"]))(?:bash|sh|zsh|dash|ksh|source|\.)(?:\s+-[-\w]+)*\s+" + _QTOKEN, re.M
)
PY_FILE_RE = re.compile(
    r"(?:^|(?<=[\s;&|(`'\"]))python[\d.]*(?:\s+-[-\w]+)*\s+"
    r"(?:'([^']+\.py)'|\"([^\"]+\.py)\"|([^\s;&|<>()'\"`]+\.py)\b)",
    re.M,
)
# A path in command position: `./x.sh`, `/abs/load-suite`, `nohup ~/x &`, `"/abs/x"`.
# A quote still counts as a boundary so `terminal('"/abs/x" &')` inside execute_code is
# found. A path that is only an argument of a reader is dropped later, not here.
# sudo [-E] [-u user] /x and env [-i] [-] [-u NAME] [-C DIR] [VAR=val ...] /x run /x
# as argv0. env -u/--unset take a name (not a path, so the command is not swallowed).
# env -C/--chdir take a directory, including -C/tmp and --chdir=/tmp; that value is
# not executed. -S and --split-string= values are executed, so they are scanned, not
# consumed. A lone `-` is -i, and only when the next character is whitespace, so it
# does not steal -u / -C. The attached alternative is first: otherwise
# `env -C /tmp -S/x` backtracks to a hit on /tmp and the script is never scanned.
# `env -S=` prints the environment and is not a command. A lone `-` or a NAME=VALUE
# ends option parsing, so an attached -S after either is a command name, not a split.
_CMD_PATH = (
    r"(?:'((?:~|\.{1,2})?/[^']+)'"
    r"|\"((?:~|\.{1,2})?/[^\"]+)\""
    r"|((?:~|\.{1,2})?/[^\s;&|<>()'\"`]+))"
)
_CMD_ENV_SPLIT = (
    r"(?:^|[;&|(\n`'\"])\s*(?:(?:nohup|exec|time|nice(?:\s+-n\s*-?\d+)?|setsid|caffeinate(?:\s+-\w+)*"
    r"|sudo(?:\s+-[ugCDhpRrt]\s+[^\s/.~-]\S*|\s+-[-\w=]+)*)\s+)*"
    r"env(?:\s+(?:-u|--unset)\s+[^\s/.~-]\S*|\s+-C(?:\s+)?\S+|\s+--chdir(?:=\S+|\s+\S+)|\s+-[-\w=]+)*"
    r"\s+(?:-[0iv]*S|--split-string=)"
)
CMD_POS_RE = re.compile(
    _CMD_ENV_SPLIT
    + _CMD_PATH
    + r"|"
    + r"(?:^|[;&|(\n`'\"])\s*(?:(?:nohup|exec|time|nice(?:\s+-n\s*-?\d+)?|setsid|caffeinate(?:\s+-\w+)*"
    + r"|sudo(?:\s+-[ugCDhpRrt]\s+[^\s/.~-]\S*|\s+-[-\w=]+)*"
    + r"|env(?:\s+(?:-u|--unset)\s+[^\s/.~-]\S*|\s+-C(?:\s+)?\S+|\s+--chdir(?:=\S+|\s+\S+)"
    + r"|\s+-[-\w=]+|\s+-(?!\S))*(?:\s+\w+=\S*)*)\s+)*"
    + _CMD_PATH
)
# Operands that a command hands to exec: `git bisect run ./x`, `xargs ./x`, `find . -exec ./x {} \;`.
EXEC_OPERAND_RE = re.compile(
    r"(?:^|(?<=[\s;&|(`'\"]))(?:git\s+bisect\s+run|xargs(?:\s+-[-\w=]+)*|"
    r"find\b[^;&|\n]*?\s-exec(?:dir)?)\s+" + _QTOKEN,
    re.M,
)
# What sits before a quote/paren that makes a path "data": an assignment (`src = "/x.txt"`) or
# the argument of a call that reads or inspects a file (`open("/x")`, `Path("/x").read_text()`).
_DATA_PREFIX_RE = re.compile(
    r"(?:(?<![=!<>])=|\b(?:open|read_text|read_bytes|load|loads|Path|PurePath|exists|isfile|isdir|"
    r"getsize|getmtime|stat|abspath|realpath|basename|dirname|splitext|readlines|read_file|"
    r"fromfile|loadtxt|read_csv|read_json|expanduser|glob|iglob)\s*\()\s*$"
)

# Readers and file tools. A path they are given is data, never a script this command runs, even
# when an interpreter name happens to sit in their arguments (`grep python burn.py`).
_READERS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag",
    "cat", "sed", "awk", "gawk",
    "head", "tail", "less", "more", "wc",
    "diff", "cmp", "git",
    "cp", "mv", "ln", "ls",
    "echo", "printf",
    "cut", "sort", "uniq", "tr", "tee",
    "stat", "file", "basename", "dirname", "realpath", "readlink",
})
_WRAPPERS = frozenset({
    "nohup", "exec", "time", "nice", "setsid", "caffeinate",
    "command", "sudo", "env", "stdbuf", "ionice", "chrt",
})
_CMD_SEP_RE = re.compile(r"&&|\|\||[;&|\n(`]")

# `cd DIR` / `pushd DIR`: later relative script paths in the same command resolve against DIR.
CD_RE = re.compile(r"(?:^|(?<=[\s;&|(`'\"]))(?:cd|pushd)(?:\s+-[LPe@]+)*\s+" + _QTOKEN, re.M)


def _word(m: re.Match) -> tuple[int, str]:
    """(offset, text) of whichever quoted/unquoted alternative matched."""
    for i in range(1, (m.re.groups or 0) + 1):
        if m.group(i) is not None:
            return m.start(i), m.group(i)
    return m.start(), ""


def _hostnames() -> set[str]:
    names = set()
    try:
        full = socket.gethostname()
        names.update({full.lower(), full.split(".")[0].lower()})
    except Exception:
        pass
    return names


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _flat_host_list(text: str) -> list[str] | None:
    """A ``protected_hosts:`` file that is only a flat list, or None if it isn't that shape.

    The Hermes venv has no PyYAML. A missing yaml module must not be treated as a missing
    file: this parser is what makes a list replace the default on that interpreter.
    """
    hosts: list[str] = []
    seen = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not seen:
            if line.strip() != "protected_hosts:":
                return None
            seen = True
            continue
        item = line.strip()
        if not item.startswith("- "):
            return None
        value = item[2:].strip().strip("'\"")
        if not value:
            return None
        hosts.append(value)
    return hosts if seen else None


def protected_hosts() -> list[str]:
    env = os.environ.get("CPU_LOAD_GUARD_HOSTS")
    if env is not None:
        return [h.strip() for h in env.split(",") if h.strip()]
    cfg = _home() / "cpu-load-guard.yaml"
    try:
        text = cfg.read_text()
    except OSError:
        return list(DEFAULT_PROTECTED)
    flat = _flat_host_list(text)
    if flat is not None:
        return flat
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        hosts = data.get("protected_hosts")
        if isinstance(hosts, list):
            return [str(h) for h in hosts]
    except Exception:  # no yaml module, or a shape this parser does not understand
        pass
    return list(DEFAULT_PROTECTED)


def enabled() -> bool:
    if os.environ.get("CPU_LOAD_GUARD") == "off":
        return False
    mine = _hostnames()
    for h in protected_hosts():
        h = h.lower()
        if h in mine or any(m.startswith(h) for m in mine):
            return True
    return False


def ncpu() -> int:
    try:
        return int(os.environ.get("CPU_LOAD_GUARD_NCPU") or os.cpu_count() or 8)
    except ValueError:
        return os.cpu_count() or 8


def threshold() -> int:
    return max(2, ncpu() // 2)


def _num(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def _multipliers(text: str) -> list[int]:
    """Literal fan-out widths found in loops / xargs / parallel / seq / brace ranges."""
    out = []
    for m in re.finditer(r"\bseq\s+(?:-\w+\s+)*(\d+)(?:\s+(\d+))?(?:\s+(\d+))?", text):
        a, b, c = m.groups()
        if c:  # seq first step last
            out.append((_num(c) - _num(a)) // max(1, _num(b)) + 1)
        elif b:
            out.append(_num(b) - _num(a) + 1)
        else:
            out.append(_num(a))
    for m in re.finditer(r"\{(\d+)\.\.(\d+)\}", text):
        out.append(abs(_num(m.group(2)) - _num(m.group(1))) + 1)
    for m in re.finditer(r"<\s*=?\s*(\d+)\s*;", text):
        out.append(_num(m.group(1)))
    for m in re.finditer(r"\bxargs\b[^|;\n]*?(?:-P\s*|--max-procs[= ])(\d+)", text):
        out.append(_num(m.group(1)))
    for m in re.finditer(r"\bparallel\b[^|;\n]*?(?:-j\s*|--jobs[= ])(\d+)", text):
        out.append(_num(m.group(1)))
    for m in re.finditer(r"\brange\(\s*(?:\d+\s*,\s*)?(\d+)\s*\)", text):
        out.append(_num(m.group(1)))
    return out


def _fan_out_wide(text: str) -> bool:
    """Is there a loop/pool whose width is a per-core expression or >= threshold?"""
    if NCPU_RE.search(text):
        return True
    t = threshold()
    return any(n >= t for n in _multipliers(text))


def _count_flag(text: str, flags: str) -> list[str]:
    # `--cpu 16`, `--cpu=16`, and an argv list `'--cpu', '16'`
    return re.findall(r"(?:" + flags + r")(?:[= ]|['\"]\s*,\s*['\"])?\s*(\$\(\s*[^)]*\)|`[^`]*`|\$\{?\w+\}?|\d+)", text)


def _wide_arg(arg: str) -> bool:
    arg = arg.strip()
    if arg.isdigit():
        n = int(arg)
        return n == 0 or n >= threshold()  # stress-ng --cpu 0 = all cores
    return True  # $(nproc), $N, `...`: unknown count, treat as per-core


def check_shell(text: str, cwd: str | None = None, depth: int = 0) -> str | None:
    """Check shell text, inline Python in it, and local scripts it runs (size- and depth-capped)."""
    if not text or not text.strip():
        return None
    why = _check_shell_text(text) or _check_py_text(text)  # python3 -c '...', python3 - <<EOF
    if why:
        return why
    return _check_scripts_run(text, cwd, depth)


def _check_shell_text(text: str) -> str | None:
    t = threshold()
    if STRESS_RE.search(text):
        for arg in _count_flag(text, r"--cpu|-c"):
            if _wide_arg(arg):
                return f"stress/stress-ng with {arg.strip()} CPU workers (>= {t} of {ncpu()} cores)"
    if re.search(r"\bopenssl\s+speed\b", text):
        for arg in _count_flag(text, r"-multi"):
            if _wide_arg(arg):
                return f"openssl speed -multi {arg.strip()} (>= {t} of {ncpu()} cores)"
    n_bg = len(BG_BURNER_RE.findall(text))
    if n_bg >= t:
        return f"{n_bg} backgrounded busy loops (>= {t} of {ncpu()} cores)"
    if SHELL_BURNER_RE.search(text) and _fan_out_wide(text):
        return f"a CPU burner fanned out to about one per core (>= {t} of {ncpu()} cores)"
    return None


def _check_py_text(code: str) -> str | None:
    spawns = bool(PY_SPAWN_CALL_RE.search(code)) or (bool(PY_ARGV_RE.search(code)) and bool(PY_SPAWNS_RE.search(code)))
    if (spawns or PY_BUSY_RE.search(code)) and _fan_out_wide(code):
        return f"a Python/subprocess CPU burner fanned out to about one per core (>= {threshold()} of {ncpu()} cores)"
    return None


def check_python(code: str, cwd: str | None = None, depth: int = 0) -> str | None:
    if not code or not code.strip():
        return None
    # terminal("..."), subprocess.run("stress-ng ..."), os.system, terminal("bash load.sh")
    return check_shell(code, cwd, depth)


def _resolve(token: str, cwd: str | None) -> Path | None:
    if not token or token.startswith("-") or "$" in token:
        return None
    p = Path(os.path.expanduser(token))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    try:
        if p.is_file() and p.stat().st_size <= MAX_SCRIPT_BYTES:
            return p
    except OSError:
        pass
    return None


def _is_python(path: Path, content: str) -> bool:
    return path.suffix == ".py" or bool(re.match(r"#!\S*(?:\s+\S+)?\s*python", content))


def check_file(path: Path, content: str, cwd: str | None = None, depth: int = 0) -> str | None:
    if _is_python(path, content):
        return check_python(content, cwd, depth)
    return check_shell(content, cwd, depth)


def _cwd_at(pos: int, cds: list[tuple[int, str]], base: str) -> str:
    """The shell's cwd at offset *pos*: *base* with every earlier `cd`/`pushd` applied."""
    cwd = base
    for start, target in cds:
        if start >= pos:
            break
        if target == "-" or "$" in target:
            continue  # unknown target; keep the last known directory
        cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(target)))
    return cwd


def _words_before(text: str, pos: int) -> list[str]:
    """Shell words of the simple command before *pos* (quotes and separators stripped)."""
    start = 0
    for m in _CMD_SEP_RE.finditer(text, 0, pos):
        start = m.end()
    segment = re.sub(r"'[^']*'|" + r'"[^"]*"', " ", text[start:pos])
    return [w.strip("'\"") for w in re.findall(r"[^\s;&|<>()]+", segment) if w.strip("'\"")]


def _operand_of_reader(text: str, pos: int) -> bool:
    """True when the file at *pos* is data to grep/cat/sed/git/cp/... rather than a script being run.

    Checks both the simple command's argv0 (`git log -S python3 -- x.py`) and the nearest word
    (`terminal('grep x.py')`, where the shell command is nested in a Python string).
    """
    words = _words_before(text, pos)
    if not words:
        return False
    if words[:3] == ["git", "bisect", "run"]:
        return False  # git bisect run executes its operand
    i = 0
    while i < len(words) and words[i] in _WRAPPERS:
        i += 1
        while i < len(words) and (words[i].startswith("-") or "=" in words[i] or words[i].lstrip("-").isdigit()):
            i += 1
    argv0 = words[i] if i < len(words) else ""
    j = len(words) - 1
    while j >= 0 and words[j].startswith("-"):
        j -= 1
    nearest = words[j] if j >= 0 else ""
    return argv0 in _READERS or nearest in _READERS


def _is_data_path(text: str, m: re.Match) -> bool:
    """A CMD_POS_RE hit whose quote/paren boundary is not a command position.

    `path = "/x.txt"` and `open("/x.txt")` name a file without running it. Only the ambiguous
    boundaries (a quote or an opening paren) are checked; `;`, `&`, `|`, newline and a bare line
    start stay command positions.
    """
    b = m.start()
    if b >= len(text) or text[b] not in "'\"(":
        return False
    return bool(_DATA_PREFIX_RE.search(text[: b + 1] if text[b] == "(" else text[:b]))


def _check_scripts_run(text: str, cwd: str | None, depth: int) -> str | None:
    if depth >= MAX_DEPTH:
        return None
    base = cwd or os.getcwd()
    cds = [(m.start(), _word(m)[1]) for m in CD_RE.finditer(text)]
    hits = sorted(
        [_word(m) + (False,) for r in (INTERP_RE, PY_FILE_RE) for m in r.finditer(text)]
        + [_word(m) + (False,) for m in CMD_POS_RE.finditer(text) if not _is_data_path(text, m)]
        + [_word(m) + (True,) for m in EXEC_OPERAND_RE.finditer(text)]
    )
    seen = set()
    for pos, tok, execs in hits:
        # grep/cat/sed/git/cp/... never execute the file they are handed. An interpreter word in
        # their arguments (`grep python burn.py`, `echo "python3 x.py"`) is data too.
        if not execs and _operand_of_reader(text, pos):
            continue
        here = _cwd_at(pos, cds, base)
        p = _resolve(tok, here)
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            content = p.read_text(errors="replace")
        except OSError:
            continue
        # bash resolves relative paths inside the script against the caller's cwd; the script's own
        # directory is checked too, for scripts that cd to their own dir first.
        why = check_file(p, content, here, depth + 1)
        if not why and str(p.parent) != here:
            why = check_file(p, content, str(p.parent), depth + 1)
        if why:
            return f"{why}, in script {p.name}"
    return None


def is_script_path(path: str, content: str = "") -> bool:
    return Path(path).suffix.lower() in SCRIPT_EXTS  # docs (.md/.txt) quoting a burner are not run


def check_write(path: str, content: str) -> str | None:
    """Check text about to be written to a script file (write_file, patch)."""
    if not content or not is_script_path(path, content):
        return None
    p = Path(os.path.expanduser(path or "x"))
    why = check_file(p, content, str(p.parent))
    return f"{why}, written to {p.name}" if why else None


def message(why: str) -> str:
    return (
        f"cpu-load-guard: refused on this protected host: {why}. Hermes automation must not saturate "
        "a shared host (this machine, the laptop, or hermes-hub). Do not fan out yes, stress, or "
        "Python busy loops toward one per core. Make the race deterministic (inject the delay or "
        "ordering with an Event, mock, or fake clock) instead of relying on host load. A bounded "
        "test run such as `pytest -n 2`, or an ordinary pytest/CI command, is fine."
    )
