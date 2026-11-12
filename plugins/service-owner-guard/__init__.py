"""service-owner-guard: an agent does not stop an owner-marked service with a raw command.

A service (launchd job, systemd unit, or process) can carry an owner card: ``HERMES_OWNER_CARD`` in its
environment, or an ``OWNER.md`` whose first line is ``card: t_<hex>[@host]`` in its working directory or the
script's directory (or an ancestor below $HOME). This ``pre_tool_call`` hook (plus ``claude_hook.py`` for Claude
Code Bash) refuses ``launchctl disable|bootout|unload|remove|stop``, ``systemctl stop|disable|mask|kill`` and
``kill|pkill|killall`` of such a service, at top level or inside ``ssh <host> '<cmd>'`` / ``bash -c``, and from
execute_code Python, unless the command carries ``owner-approved=<card id>``. Owner lookup reuses
machine-investigate's collect.py (``valid_owner``, ``read_owner_md``, ``owner``); nothing here parses markers.
Reloads (bootout then bootstrap of the same label) and kickstart/restart/start/enable/bootstrap are never refused.
Shell segmenting is vendored from backend-restart-guard. ``SERVICE_OWNER_GUARD=off`` disables it.
"""

from __future__ import annotations

import ast
import datetime
import glob
import importlib.util
import inspect
import json
import logging
import os
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}
SHELLS = {"sh", "bash", "zsh", "dash"}
LAUNCHCTL_STOP = {"disable", "bootout", "unload", "remove", "stop"}
SYSTEMCTL_STOP = {"stop", "disable", "mask", "kill"}
LOAD_VERBS = {"bootstrap", "load"}
REMOTE_TIMEOUT = 8
APPROVED = re.compile(r"owner-approved=(t_[0-9a-f]+)")
CHECKED_NONE = re.compile(r"owner-checked=none\b")
# A unit pinned to its own runtime by a ``*pinned-owner*.conf`` drop-in (Ask Eve owners). A raw agent stop
# of one is refused with no in-command override: stopping one coupled owner makes the operator checks refuse,
# and a core update no longer needs it (hermes update leaves a pinned gateway alone).
PINNED_DROPIN = re.compile(r"pinned-owner[^/\s]*\.conf$")
MAX_SCRIPT_BYTES = 65536


def enabled() -> bool:
    return os.environ.get("SERVICE_OWNER_GUARD", "on").lower() not in {"off", "0", "false", "no"}


# ───────────── single-sourced owner parsing: machine-investigate/scripts/collect.py ─────────────
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_path() -> Path:
    rel = Path("skills/devops/machine-investigate/scripts/collect.py")
    for base in (HERE.parents[1], Path.home() / "code/hermes-mods"):
        if (base / rel).exists():
            return base / rel
    raise FileNotFoundError("machine-investigate collect.py not found")


_collect = _lib = None


def libs():
    global _collect, _lib
    if _lib is None:
        _collect = _load("sog_collect", _collect_path())
        lib = _load("sog_lookup_lib", HERE / "lookup_lib.py")
        for name in ("valid_owner", "read_owner_md", "parse_ps", "chain", "env_tags", "owner"):
            setattr(lib, name, getattr(_collect, name))
        _lib = lib
    return _collect, _lib


def run_cmd(argv: list, timeout: float = 5) -> tuple:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def remote_script(spec: dict) -> str:
    collect, lib = libs()
    return "\n".join([
        "from __future__ import annotations\nimport json, os, plistlib, re, subprocess, sys\nfrom pathlib import Path",
        (HERE / "lookup_lib.py").read_text(),  # declares valid_owner = read_owner_md = None ...
        "import platform\nMAC = platform.system() == 'Darwin'",
        "TAG_KEYS = " + repr(collect.TAG_KEYS), "TASK_KEYS = " + repr(collect.TASK_KEYS),
        "TAG_RE = re.compile(" + repr(collect.TAG_RE.pattern) + ")",
        *[inspect.getsource(getattr(collect, name)) for name in (
            "valid_owner", "read_owner_md", "short_name", "parse_ps", "chain", "parse_env_tags", "env_tags", "owner")],
        "def run(argv, timeout=5):\n"
        "    try:\n"
        "        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)\n"
        "        return r.returncode, r.stdout\n"
        "    except Exception:\n"
        "        return 127, ''\n"
        "print(json.dumps(find_owner(json.loads(sys.argv[1]), run)))",
    ])


def remote_lookup(ssh_prefix: list, spec: dict) -> dict:
    """One bounded ssh call (<= 8 s) running the shared lookup on the target host."""
    try:
        script = remote_script(spec)
        res = subprocess.run(ssh_prefix + ["python3 - " + shlex.quote(json.dumps(spec))], input=script,
                             capture_output=True, text=True, timeout=REMOTE_TIMEOUT)
        if res.returncode != 0:
            return {"status": "failed", "error": f"ssh rc={res.returncode}: {res.stderr.strip()[:120]}"}
        return json.loads(res.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": "timeout"}
    except Exception as exc:
        return {"status": "failed", "error": repr(exc)[:160]}


def local_lookup(spec: dict) -> dict:
    return libs()[1].find_owner(spec, run_cmd)


# ───────────── shell parsing (vendored from backend-restart-guard) ─────────────
class _Unclosed(Exception):
    pass


def _expose_substitutions(text: str) -> str:
    """`text` with each command substitution moved out of its double quotes, and backticks spelled `$(...)`.

    Bash runs `"$(cmd)"` and "`cmd`" exactly like the unquoted forms, but shlex keeps a double-quoted string as one
    word, so `out="$(rm -f x 2>&1)"` hid the rm that `out=$(rm -f x 2>&1)` shows. `echo "a $(cmd) b"` becomes
    `echo "a "$(cmd)" b"` (`"$(cmd)"` becomes `$(cmd)`): the substitution is lexed as code, the text around it stays
    quoted data. Quotes inside a substitution start fresh, as in bash (plugins/coach/agent_rules.py `_mask_quoted`).
    Single quotes, `$'...'`, escaped `\\$(`/`` \\` ``, `$((...))` arithmetic and `#` comments are copied unchanged.
    Text whose quotes or substitutions do not close comes back unchanged."""
    n = len(text)

    def comment_at(i: int) -> bool:
        return text[i] == "#" and (i == 0 or text[i - 1] in " \t\n;&|()")

    def arithmetic(i: int) -> int:  # `$((...))` starting at i: index just past it
        depth, j = 0, i + 1
        while j < n:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise _Unclosed

    def single(i: int, ansi: bool) -> int:  # quote opening at i: index just past its close
        j = i + 1
        while j < n:
            if ansi and text[j] == "\\":
                j += 2
                continue
            if text[j] == "'":
                return j + 1
            j += 1
        raise _Unclosed

    def code(i: int, stop: str, level: int) -> tuple:
        if level > 32:
            raise _Unclosed
        out, depth = [], 0
        while i < n:
            c = text[i]
            if c == "\\":
                out.append(text[i:i + 2])
                i += 2
            elif stop == "`" and c == "`":
                return "".join(out), i + 1
            elif comment_at(i):
                j = text.find("\n", i)
                j = n if j < 0 else j
                out.append(text[i:j])
                i = j
            elif c == "'" or text.startswith("$'", i):
                j = single(i + (c == "$"), c == "$")
                out.append(text[i:j])
                i = j
            elif c == '"':
                s, i = dquote(i + 1, level)
                out.append(s)
            elif text.startswith("$((", i):
                j = arithmetic(i + 1)
                out.append(text[i:j])
                i = j
            elif text.startswith("$(", i):
                s, i = code(i + 2, ")", level + 1)
                out.append("$(" + s + ")")
            elif c == "`":
                s, i = code(i + 1, "`", level + 1)
                out.append("$(" + s + ")")
            else:
                if stop == ")" and c == "(":
                    depth += 1
                elif stop == ")" and c == ")":
                    if depth == 0:
                        return "".join(out), i + 1
                    depth -= 1
                out.append(c)
                i += 1
        if stop:
            raise _Unclosed
        return "".join(out), n

    def dquote(i: int, level: int) -> tuple:  # body of a "..." opened just before i
        parts, data = [], []
        while i < n:
            c = text[i]
            if c == "\\":
                data.append(text[i:i + 2])
                i += 2
            elif c == '"':
                if not parts:
                    return '"' + "".join(data) + '"', i + 1
                if data:
                    parts.append('"' + "".join(data) + '"')
                return "".join(parts), i + 1
            elif text.startswith("$((", i):
                j = arithmetic(i + 1)
                data.append(text[i:j])
                i = j
            elif text.startswith("$(", i) or c == "`":
                if data:
                    parts.append('"' + "".join(data) + '"')
                    data = []
                s, i = code(i + 2, ")", level + 1) if c == "$" else code(i + 1, "`", level + 1)
                parts.append("$(" + s + ")")
            else:
                data.append(c)
                i += 1
        raise _Unclosed

    if "$(" not in text and "`" not in text:
        return text
    try:
        return code(0, "", 0)[0]
    except (_Unclosed, RecursionError):
        return text


_FD_PREFIX = re.compile(r"\d+(?=[<>])")
_REDIR_PART = re.compile(r"[<>]\(|&>>?|>>|>&|>\||<<<|<<|<>|<&|[<>]|[^<>]+")


def _drop_fd_prefixes(text: str) -> str:
    """`text` without the file-descriptor number of each unquoted redirect (`2>x`, `2>>x`, `2>&1`, `1>|x`, `0<x`).
    Only a digit run that starts a word and directly touches `<`/`>` is an fd: a spaced `2 > x` keeps `2` as an
    operand, as in bash."""
    out, quote, i, n = [], "", 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if c in "'\"":
            quote = c
        elif c.isdigit() and (i == 0 or text[i - 1].isspace() or text[i - 1] in ";&|()"):
            m = _FD_PREFIX.match(text, i)
            if m:
                i = m.end()
                continue
        out.append(c)
        i += 1
    return "".join(out)


_QUOTE_MASK = {"<": "\ue03c", ">": "\ue03e"}
_QUOTE_UNMASK = str.maketrans({v: k for k, v in _QUOTE_MASK.items()})


class _Quoted(str):
    """A word whose `<`/`>` were quoted or escaped (`'>>'`, `"2>x"`, `\\>`): an argument, never a redirect."""


def _mask_quoted_redirects(text: str) -> str:
    """Mark each quoted or backslash-escaped `<`/`>` so it survives shlex as data: shlex drops the quotes, so
    `grep -c '>' f` would lex like `grep -c > f`. `text` comes back unchanged when its quotes do not balance."""
    out, quote, i, n = [], "", 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(c + _QUOTE_MASK.get(text[i + 1], text[i + 1]))
                i += 2
                continue
            if c == quote:
                quote = ""
                out.append(c)
            else:
                out.append(_QUOTE_MASK.get(c, c))
        elif c == "\\" and i + 1 < n:
            out.append(_QUOTE_MASK.get(text[i + 1]) or text[i:i + 2])
            i += 2
            continue
        else:
            if c in "'\"":
                quote = c
            out.append(c)
        i += 1
    return text if quote else "".join(out)


def _unmask_quoted(tokens: list) -> list:
    """Tokens lexed from `_mask_quoted_redirects` text, each word that held a quoted `<`/`>` as `_Quoted`."""
    return [_Quoted(t.translate(_QUOTE_UNMASK)) if any(m in t for m in _QUOTE_MASK.values()) else t for t in tokens]


def _drop_redirects(tokens: list) -> list:
    """Drop each redirect operator and the word after it (target, fd or heredoc tag) from tokens lexed with `<>` as
    punctuation. `<(`/`>(` (process substitution) stays a command boundary."""
    out, skip = [], False
    for t in tokens:
        if t and set(t) <= set(";&|()<>\n") and set(t) & set("<>") and not isinstance(t, _Quoted):
            for part in _REDIR_PART.findall(t):
                if part.endswith("("):
                    out.append("(")
                    skip = False
                elif set(part) & set("<>"):
                    skip = True
                else:
                    out.append(part)
                    skip = False
            continue
        if skip:
            skip = False
            if not (t and set(t) <= set(";&|()\n")):
                continue
        out.append(t)
    return out


# ---- heredoc bodies, comments, partial lexing ----------------------------------------------------------------
# Byte-identical in live-checkout-guard, mac-heavy-guard, busy-tree-guard, sqlite-live-guard, service-owner-guard,
# evidence-clobber-guard and coach/agent_rules.py (each plugin loads standalone). Change every copy together.

# Readers known to read a heredoc body only as data. A body is data only when every command that reads it is one
# of these; any other reader, or one this can't name (`su -`, `sudo -s`, `source /dev/stdin`, `"$SHELL"`, `eval`,
# `xargs sh`, `{ ...; }`), may run it as shell code, so the body stays code (fail closed).
_HD_DATA_READERS = frozenset({
    "cat", "tee", "grep", "egrep", "fgrep", "rg", "head", "tail", "wc", "sort", "uniq", "tr", "cut", "paste",
    "column", "fold", "fmt", "nl", "rev", "base64", "md5sum", "sha1sum", "sha256sum", "shasum", "xxd", "od",
    "diff", "cmp", "jq", "yq", "less", "more", "git", "gh", "patch", "pbcopy", "true", ":"})
_HD_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?\Z")
# Commands known to use a command substitution's output only as data (`git commit -m "$(cat <<'EOF' ...)"`,
# `x=$(cat <<EOF ...)`). Any other consumer (a shell, `eval`, `source`, `ssh`, `xargs`, `python -c`, ...) may run it.
_HD_DATA_CONSUMERS = frozenset({"git", "gh", "export", "local", "declare", "typeset", "readonly"})
_HD_WRAPPERS = frozenset({"sudo", "doas", "env", "nohup", "command", "exec", "time"})
_HD_ARG_OPTS = frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U", "--user", "--group"})
_HD_REDIRECT = re.compile(r"(?:\d+|&)?(?:<<<|<<-?|<>|>>|>\||>&|<&|&>>?|<|>)")
_HD_SEPARATOR = re.compile(r"\|\||&&|\|&?|;|\(|\)|(?<![<>])&(?!>)")
_HD_WORD_BREAK = " \t\r\n;&|()<>"


def _hd_word_start(text: str, i: int) -> bool:
    return i == 0 or text[i - 1] in _HD_WORD_BREAK


def _hd_arith_end(text: str, i: int) -> int:
    """Index just past the `((...))` whose first `(` is at i (the end of text when it never closes)."""
    depth, n = 0, len(text)
    while i < n:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _hd_subst_end(text: str, i: int) -> int:
    """Index just past the `)` closing a `$(` whose body starts at i, by command rules (quotes are quotes)."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return n
            i = j + 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 2
                elif text.startswith("$(", i):
                    i = _hd_subst_end(text, i + 2)
                else:
                    i += 1
            i += 1
        elif text.startswith("$(", i):
            i = _hd_subst_end(text, i + 2)
        elif c == "(":
            depth += 1
            i += 1
        elif c == ")":
            if depth == 0:
                return i + 1
            depth -= 1
            i += 1
        else:
            i += 1
    return n


def _hd_substitutions(body: str) -> list:
    """The command text of each `$(...)` and backtick substitution in an unquoted-delimiter heredoc body.

    Heredoc rules: `'` and `"` are literal characters (`it's $(rm x)` runs rm), `\\$`, `` \\` `` and `\\\\` are
    escapes, `$((...))` is arithmetic. Inside a substitution quotes start fresh, as in bash."""
    found, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            i += 2
        elif body.startswith("$((", i):
            i = _hd_arith_end(body, i + 1)
        elif body.startswith("$(", i):
            j = _hd_subst_end(body, i + 2)
            found.append(body[i + 2:j - 1] if j <= n and body[j - 1:j] == ")" else body[i + 2:])
            i = j
        elif c == "`":
            j = i + 1
            while j < n and body[j] != "`":
                j += 2 if body[j] == "\\" else 1
            found.append(re.sub(r"\\([\\`$])", r"\1", body[i + 1:j]))
            i = j + 1
        else:
            i += 1
    return found


def _hd_data_reader(words: list, consumer: bool = False) -> bool:
    """True when the simple command `words` reads its stdin only as data: a `_HD_DATA_READERS` name or Python,
    also behind `sudo`/`env`/`exec`-style wrappers, or no command word at all (`> f <<EOF`). `sudo -s`/`-i` is
    a shell. With `consumer`: whether it uses a command substitution in its words only as data instead (one of
    `_HD_DATA_CONSUMERS`, or a bare assignment)."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        m = _HD_REDIRECT.match(w)
        if m:
            i += 2 if m.end() == len(w) else 1
            continue
        if re.match(r"[A-Za-z_]\w*\+?=", w):
            i += 1
            continue
        base = os.path.basename(w)
        if base in _HD_WRAPPERS:
            i += 1
            while i < n and words[i].startswith("-") and words[i] != "-":
                opt = words[i]
                if base in ("sudo", "doas") and (opt in ("--shell", "--login") or (
                        not opt.startswith("--") and set(opt[1:]) & {"s", "i"})):
                    return False
                i += 2 if opt in _HD_ARG_OPTS else 1
            continue
        if consumer:
            return base in _HD_DATA_CONSUMERS
        return base in _HD_DATA_READERS or bool(_HD_PYTHON.match(base))
    return True


def _hd_feeds_shell(prefix: str, rest: str) -> bool:
    """False only when a heredoc body is known to be read as data; True (its body may run as code) otherwise.
    `prefix` is the line before the `<<` operator, `rest` the line from it on. The body goes to the command that
    holds the operator and, through `|`/`|&`, to each later stage of that pipeline: every one must be a data
    reader (`_hd_data_reader`). So `su - <<EOF`, `sudo -s <<EOF`, `source /dev/stdin <<EOF`, `"$SHELL" <<EOF`
    and `cat <<EOF | sudo -s` are all code, and so is any reader this does not know (fail closed)."""
    import shlex

    def words(seg: str) -> list:
        try:
            return shlex.split(seg)
        except ValueError:
            return seg.split()

    stages, pos = [], 0
    for m in _HD_SEPARATOR.finditer(rest):
        stages.append(rest[pos:m.start()])
        pos = m.end()
        if m.group() not in ("|", "|&"):
            pos = -1
            break
    if pos >= 0:
        stages.append(rest[pos:])
    stages[0] = _HD_SEPARATOR.split(prefix)[-1] + stages[0]
    if re.search(r"[<>]\(", prefix + rest):  # a process substitution on the line (`tee >(bash)`) may run it
        return True
    return not all(_hd_data_reader(words(seg)) for seg in stages)


def _hd_subst_data(text: str, openers: tuple) -> bool:
    """True when every command substitution enclosing a heredoc (`openers`: the index of each enclosing `$(`,
    backtick, `<(` or `>(`, outermost first) hands its output to a data consumer (`_hd_data_reader` with
    `consumer`): `git commit -m "$(cat <<'EOF' ...)"` and `x=$(cat <<EOF ...)` are data, while
    `bash -c "$(cat <<'EOF' ...)"`, `eval "$(cat ...)"` or a process substitution may run the body."""
    import shlex

    for k in range(len(openers) - 1, -1, -1):
        pos = openers[k]
        if text[pos] in "<>":
            return False
        lo = 0 if k == 0 else openers[k - 1] + (1 if text[openers[k - 1]] == "`" else 2)
        start = text.rfind("\n", 0, pos) + 1
        while start >= 2 and text[start - 2] == "\\":  # a `\`-continued command line
            start = text.rfind("\n", 0, start - 2) + 1
        seg = text[max(lo, start):pos].replace("\\\n", " ")
        prev = None
        while prev != seg:  # earlier, closed substitutions on the line are one word each
            prev, seg = seg, re.sub(r"\$\([^()]*\)|`[^`]*`", "_", seg)
        seg = _HD_SEPARATOR.split(seg)[-1]
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if not _hd_data_reader(words, consumer=True):
            return False
    return True


def _hd_written(prefix: str, rest: str) -> list:
    """The files the command holding a heredoc writes its body to: `> P`, `>> P`, `>| P` or `tee P`."""
    import shlex

    seg = _HD_SEPARATOR.split(prefix)[-1] + _HD_SEPARATOR.split(rest)[0]
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    paths, tee = [], False
    for k, w in enumerate(words):
        m = re.match(r"(?:\d|&)?(?:>>|>\||>)(.*)", w)
        if m and not w.startswith((">&", "&>&")):
            target = m.group(1) or (words[k + 1] if k + 1 < len(words) else "")
            if target and not target.startswith("&"):
                paths.append(target)
        elif os.path.basename(w) == "tee":
            tee = True
        elif tee and not w.startswith("-") and not _HD_REDIRECT.match(w) and not (
                k and _HD_REDIRECT.fullmatch(words[k - 1])):
            paths.append(w)
    return paths


def _hd_assigned(text: str, openers: tuple) -> str:
    """The variable a data heredoc is assigned to (`x=$(cat <<EOF ...)`, `export x="$(cat <<EOF ...)"`), else ''.
    Only the innermost enclosing substitution counts: its text must directly follow `NAME=`."""
    if not openers or text[openers[-1]] not in "$`":
        return ""
    k = len(openers) - 1
    lo = 0 if k == 0 else openers[k - 1] + (1 if text[openers[k - 1]] == "`" else 2)
    start = text.rfind("\n", 0, openers[-1]) + 1
    while start >= 2 and text[start - 2] == "\\":
        start = text.rfind("\n", 0, start - 2) + 1
    seg = _HD_SEPARATOR.split(text[max(lo, start):openers[-1]].replace("\\\n", " "))[-1]
    m = re.search(r"(?:^|\s)(?:(?:export|local|declare|typeset|readonly)\s+(?:-\S+\s+)*)?([A-Za-z_]\w*)\+?=\S*\Z", seg)
    return m.group(1) if m else ""


_HD_DQ_WORD = re.compile(r"(?:then|do|else|elif|while|until|if)\b")


def _hd_mask(code: str, keep: tuple = ()) -> str:
    """`code` (same length) with what bash does not expand made inert for pattern matching: the text of '...'
    (unless it is one plain path-like word or one of the `keep` paths), backslash pairs, and in "..." (outside a
    `$(...)`) the command separators `;` `&` `|` `(` `{` newline and keywords (`echo "do $x"` is not a loop), so a
    quoted argument stays one word. `$x` in "..." stays visible, `$x` in '...' or after `\\` does not."""
    out, i, n, dq, depth = [], 0, len(code), False, 0
    while i < n:
        c = code[i]
        if c == "\\":
            out.append("  " if code[i + 1:i + 2] == "\n" else "__")
            i += 2
        elif dq:
            if code.startswith("$(", i) and not code.startswith("$((", i):
                depth += 1
                out.append("$(")
                i += 2
                continue
            if depth:
                depth -= c == ")"
                out.append(c)
            elif c == '"':
                dq = False
                out.append(c)
            elif c in ";&|\n({" and not (c == "{" and code[i - 1:i] == "$") and not (c == "(" and code[i - 1:i] == "$"):
                out.append(" ")
            elif (m := _HD_DQ_WORD.match(code, i)) and not code[i - 1:i].isalnum():
                out.append("_" * len(m.group()))
                i += len(m.group())
                continue
            else:
                out.append(c)
            i += 1
        elif c == '"':
            dq = True
            out.append(c)
            i += 1
        elif c == "'" or code.startswith("$'", i):
            d = c == "$"
            j = i + 1 + d
            while j < n and code[j] != "'":
                j += 2 if d and code[j] == "\\" else 1
            seg, inner = code[i:j + 1], code[i + 1 + d:j]
            keepit = not d and (re.fullmatch(r"[\w./~+-]+", inner) or inner in keep)
            out.append(seg if keepit or len(seg) < 2 else "'" + "_" * (len(seg) - 2) + seg[-1:])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


_HD_SHELL = r"(?<![\w./$-])(?:[\w./-]*/)?(?:ba|z|da|k|mk)?sh(?![\w.-])"
_HD_RUN_WRAP = (r"(?:(?:sudo|doas|env|nohup|command|exec|time)[ \t]+(?:(?:-[ugCDhprtTU]|--user|--group)[ \t]+\S+[ \t]+"
                r"|-\S*[ \t]+|\w+=\S*[ \t]+)*)*")
_HD_RUN_START = (r"(?:^|(?<![=(])\(|[;&|`{]|\b(?:then|do|else|elif|while|until|if)\b)\s*(?:\w+=\S*[ \t]+)*"
                 + _HD_RUN_WRAP)


def _hd_exec_re(src: str, extra: tuple = ()):
    """Regex (on `_hd_mask`ed text) for a command that runs the text `src` matches as code: `src` at command
    position, an `eval`/`source`/`.` argument, the argument of `SHELL -c`, `echo src | SHELL`, `SHELL <<< src`.
    Each form is bounded to one command (`[^;&|\\n]`) and starts at a command position, so `echo bash -c $x` and
    `'$x'` do not match. `extra` are further whole patterns (already starting with `_HD_RUN_START`)."""
    s, sh = _HD_RUN_START, _HD_SHELL
    forms = [
        s + '"?' + src,
        s + r"(?:eval\b|source\b|\.[ \t])[^;&|\n]*?" + src,
        s + sh + r"[ \t][^;&|\n]*?" + src,  # any argument of a shell: `bash "$x"`, `bash -x $x`, `bash -c "$x"`
        s + r"(?:echo|printf|print)\b[^;&|\n]*?" + src + r"[^;&|\n]*\|\s*" + _HD_RUN_WRAP + sh,
        s + sh + r"[^;&|\n]*?<<<\s*\"?" + src,
    ]
    return re.compile("|".join("(?:%s)" % f for f in forms + list(extra)), re.M)


def _hd_file_run_re(path: str):
    """`_hd_exec_re` for a file a heredoc wrote: `bash < P`, `cat P | bash`, `source /dev/stdin < P`,
    `bash <(cat P)`, and `$(cat P)`, `$(< P)`, backticks or `<(cat P)` where code runs (`_hd_exec_re`)."""
    p = r"[\"']?(?:\./)?" + re.escape(path[2:] if path.startswith("./") else path) + r"[\"']?(?![\w/.-])"
    s, sh = _HD_RUN_START, _HD_SHELL
    subst = r"(?:\$\(|<\(|`)\s*(?:cat\s+(?:-\S+\s+)*|<\s*)" + p + r"\s*[)`]"
    return _hd_exec_re(subst, (
        s + sh + r"(?:[ \t]+-\S+)*[ \t]*<[ \t]*" + p,
        s + r"cat[ \t]+(?:-\S+[ \t]+)*" + p + r"[ \t]*\|[ \t]*" + _HD_RUN_WRAP + sh,
        s + r"(?:source|\.)[ \t]+/dev/stdin[ \t]*<[ \t]*" + p,
        s + sh + r"[ \t]+(?:-\S+[ \t]+)*<\(\s*cat[ \t]+(?:-\S+[ \t]+)*" + p,
    ))


def _hd_var_run_re(name: str):
    """`_hd_exec_re` for the variable `name` a heredoc body was assigned to: `$name` / `${name...}`."""
    return _hd_exec_re(r"\$(?:" + re.escape(name) + r"(?!\w)|\{" + re.escape(name) + r"[}:\-+=?/%^,\[@])")


def _split_heredocs(text: str, _level: int = 0) -> tuple:
    """(code, bodies) of shell `text`, by bash rules: the command text a shell parses, and the heredoc bodies.

    `code` is `text` without heredoc bodies, their terminator lines and `#` comments (a `#` that begins a word
    outside quotes, to the end of its line). Text after a terminator stays code. A body is data, so an apostrophe
    in it (`it's done`) can no longer unbalance the quotes of the commands after it. Every `<<WORD`/`<<-WORD` form
    is handled (bare, 'quoted', "quoted", \\escaped), several on one line in order, `<<-` tab stripping, an
    unterminated body to the end. Two kinds of body still reach `code`:
    - a body fed to a shell, or to any reader not known to read it as data (`_hd_feeds_shell`), is code that may
      run: it is split recursively and kept. So is a body read inside a command substitution whose output is not
      used only as data (`bash -c "$(cat <<'EOF' ...)"`, `_hd_subst_data`), and a body written to a file that
      `text` then runs (`cat > s.sh <<EOF ... EOF` then `bash s.sh`, `_hd_written`);
    - each `$(...)`/backtick substitution of an unquoted-delimiter body runs before the command does: its command
      text is appended as its own line (`_hd_substitutions`).
    `bodies` lists every other body as (text, quoted delimiter), for a guard that scans data conservatively.
    Quotes that never close leave the rest of `text` as code, unchanged (see `_lexable`)."""
    if "<<" not in text and "#" not in text:
        return text, []
    out, bodies, pending, stack, subs, written, assigned = [], [], [], [], [], [], []
    n, i, line_start, emitted = len(text), 0, 0, 0  # `emitted`: length of `code` before the current operator

    def take_bodies(i: int) -> int:
        for delim, strip_tabs, quoted, op_start, nl, openers, at in pending:
            lines = []
            while i < n:
                e = text.find("\n", i)
                e = n if e < 0 else e
                line = text[i:e]
                i = min(e + 1, n)
                if strip_tabs:
                    line = line.lstrip("\t")
                if line.rstrip("\r") == delim:
                    break
                lines.append(line)
            body = "\n".join(lines)
            if _hd_feeds_shell(text[line_start:op_start], text[op_start:nl]) or not _hd_subst_data(text, openers):
                runs = [body]
            else:
                bodies.append((body, quoted))
                runs = [] if quoted else _hd_substitutions(body)
                written.extend((path, body, at) for path in _hd_written(text[line_start:op_start], text[op_start:nl]))
                name = _hd_assigned(text, openers)
                if name:
                    assigned.append((name, body, at))
            for run in runs:
                sub_code, sub_bodies = _split_heredocs(run, _level + 1) if _level < 16 else (run, [])
                out.append(sub_code + "\n")
                bodies.extend(sub_bodies)
        pending.clear()
        return i

    while i < n:
        c = text[i]
        top = stack[-1] if stack else ""
        if top == '"':
            if c == "\\":
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == '"':
                stack.pop()
            elif text.startswith("$((", i):
                j = _hd_arith_end(text, i + 1)
                out.append(text[i:j])
                i = j
                continue
            elif text.startswith("$(", i):
                stack.append(0)
                subs.append(i)
                out.append("$(")
                i += 2
                continue
            elif c == "`":
                stack.append("`")
                subs.append(i)
            out.append(c)
            i += 1
            continue
        if c == "\\":
            out.append(text[i:i + 2])
            i += 2
        elif c == "'" or text.startswith("$'", i):
            j = i + 1 + (c == "$")
            while j < n and text[j] != "'":
                j += 2 if c == "$" and text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif c == '"':
            stack.append('"')
            out.append(c)
            i += 1
        elif c == "`" and top == "`":
            stack.pop()
            subs.pop()
            out.append(c)
            i += 1
        elif c == "`":
            stack.append("`")
            subs.append(i)
            out.append(c)
            i += 1
        elif isinstance(top, int) and c in "()":
            if c == ")" and top == 0:
                stack.pop()
                subs.pop()
            else:
                stack[-1] += 1 if c == "(" else -1
            out.append(c)
            i += 1
        elif text.startswith("$((", i) or (text.startswith("((", i) and _hd_word_start(text, i)):
            j = _hd_arith_end(text, i + (c == "$"))
            out.append(text[i:j])
            i = j
        elif text.startswith("$(", i) or (c in "<>" and text.startswith("(", i + 1)):  # `$(`, `<(`, `>(`
            stack.append(0)
            subs.append(i)
            out.append(text[i:i + 2])
            i += 2
        elif text.startswith("${", i):  # parameter expansion: a `<<` or `#` in it (`${x/<</y}`) is text
            j, depth = i + 2, 1
            while j < n and depth:
                if text[j] == "\\":
                    j += 1
                elif text[j] in "{}":
                    depth += 1 if text[j] == "{" else -1
                j += 1
            out.append(text[i:j])
            i = j
        elif c == "#" and _hd_word_start(text, i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("<<", i) and not text.startswith("<<<", i) and (i == 0 or text[i - 1] != "<"):
            k = i + 2
            strip_tabs = text.startswith("-", k)
            k += strip_tabs
            while k < n and text[k] in " \t":
                k += 1
            word, quoted, w = [], False, k
            while w < n and text[w] not in _HD_WORD_BREAK:
                if text[w] in "'\"":
                    e = text.find(text[w], w + 1)
                    e = n if e < 0 else e
                    word.append(text[w + 1:e])
                    quoted, w = True, e + 1
                elif text[w] == "\\":
                    word.append(text[w + 1:w + 2])
                    quoted, w = True, w + 2
                else:
                    word.append(text[w])
                    w += 1
            if w == k:  # `<<` with no word: not a heredoc (bash reports it)
                out.append("<<")
                i += 2
                continue
            nl = text.find("\n", w)
            pending.append(("".join(word), strip_tabs, quoted, i, n if nl < 0 else nl, tuple(subs),
                            sum(map(len, out))))
            out.append(text[i:w])
            i = w
        elif c == "\n":
            out.append(c)
            i += 1
            if pending:
                i = take_bodies(i)
            line_start = i
        else:
            out.append(c)
            i += 1
    code = "".join(out)
    masked = _hd_mask(code, tuple(w[0] for w in written)) if written or assigned else ""
    ran = []
    for name, body, at in assigned:  # a body assigned to a variable that is run after it (`x=$(cat <<EOF ...)`, then `$x`, `eval "$x"`)
        if _hd_var_run_re(name).search(masked, at):
            ran.append(body)
    for path, body, at in written:  # a script written from a body and run later in `text` (`bash s.sh`, `./s.sh`, `bash < s.sh`)
        p = r"[\"']?(?:\./)?" + re.escape(path[2:] if path.startswith("./") else path) + r"[\"']?(?![\w/.-])"
        if re.compile(r"(?<![\w/.-])(?:(?:ba|z|da|k|mk)?sh|source|\.)\s+(?:-\S+\s+)*" + p).search(code, at) \
                or re.compile(r"(?:^|[;&|(`])\s*" + p, re.M).search(code, at) or _hd_file_run_re(path).search(masked, at):
            ran.append(body)
    for body in ran:
        sub_code, sub_bodies = _split_heredocs(body, _level + 1) if _level < 16 else (body, [])
        code += "\n" + sub_code + "\n"
        bodies.extend(sub_bodies)
    return code, bodies


def _lexable(text: str, lex, dropped: Optional[list] = None) -> str:
    """`text` when `lex(text)` lexes it; otherwise the part of it that does, never nothing.

    A quote that never closes (`echo don't`) makes a lexer raise ValueError for the whole text, but bash has
    already run every line before it. So keep each run of whole lines that lexes on its own, joining a line with
    the following ones (up to 32) while a multi-line quote may close; a line no run closes is dropped (appended to
    `dropped` when given, for a guard that judges it some other way) and the scan resumes at the next line."""
    try:
        lex(text)
        return text
    except ValueError:
        pass
    lines, keep, i = text.split("\n"), [], 0
    while i < len(lines):
        for j in range(i, min(len(lines), i + 32)):
            chunk = "\n".join(lines[i:j + 1])
            try:
                lex(chunk)
            except ValueError:
                continue
            keep.append(chunk)
            i = j + 1
            break
        else:
            if dropped is not None:
                dropped.append(lines[i])
            i += 1
    return "\n".join(keep)

# ---- end of the shared heredoc block ---------------------------------------------------------------------------


def _segments(text: str) -> list:
    """argv per simple command, redirects dropped: `systemctl stop foo 2>/dev/null` stops `foo` only, never a unit
    named `2>/dev/null` (or `2>` from `2>&1`)."""
    def lex_(text: str) -> list:
        lex = shlex.shlex(_mask_quoted_redirects(_drop_fd_prefixes(_expose_substitutions(text))), posix=True,
                          punctuation_chars=";&|()<>\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)

    # Comments and heredoc bodies are split off first: an apostrophe in them hid the commands after them. A line
    # whose quotes never close is dropped, never the whole text (a whitespace split lost every separator). Each
    # body is still read as commands on its own (a script written to a file may stop a unit later), but its
    # quotes can no longer reach the real commands.
    code, bodies = _split_heredocs(text)
    tokens = _drop_redirects(_unmask_quoted(lex_(_lexable(code, lex_))))
    for body, _quoted in bodies:
        tokens += [";"] + _drop_redirects(_unmask_quoted(lex_(_lexable(body, lex_))))
    out, cur, skipping = [], [], False
    for tok in tokens:
        if tok and set(tok) <= set(";&|()\n"):
            if cur:
                out.append(cur)
            cur, skipping = [], False
        elif tok.startswith("#"):
            skipping = True  # a comment runs to the end of the segment
        elif not skipping:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


SHELL_KEYWORDS = {"then", "do", "else", "{", "!", "time", "if", "while", "until", "elif"}
# Wrappers that run the program after them, and their options that take a separate value
# (`sudo -u ubuntu systemctl ...`, `timeout -s KILL 10 systemctl ...`): the value is not the program.
WRAPPERS = {"sudo": {"-u", "-g", "-C", "-D", "-p", "-r", "-t", "-U", "-T", "-R", "--user", "--group",
                     "--chdir", "--prompt", "--role", "--type", "--other-user", "--command-timeout", "--chroot"},
            "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}, "nice": {"-n", "--adjustment"},
            "timeout": {"-s", "-k", "--signal", "--kill-after"}, "exec": {"-a"}, "setsid": set(),
            "command": set(), "nohup": set()}


def _strip(argv: list) -> list:
    i, wrapper, duration = 0, "", False
    while i < len(argv):
        tok = argv[i]
        if tok in SHELL_KEYWORDS:  # `if x; then systemctl stop u; fi`: the program follows the keyword
            i += 1
        elif wrapper and tok == "--":
            i += 1
        elif wrapper and tok.startswith("-") and len(tok) > 1:
            i += 2 if tok in WRAPPERS[wrapper] else 1
        elif duration:  # `timeout [opts] DURATION cmd`
            duration, i = False, i + 1
        elif re.match(r"^[A-Za-z_]\w*=", tok):
            i += 1
        elif os.path.basename(tok) in WRAPPERS:
            wrapper = os.path.basename(tok)
            duration, i = wrapper == "timeout", i + 1
        else:
            break
    return argv[i:]


def _ssh_parts(args: list) -> tuple:
    """(option args, destination, remote command) of an ssh invocation."""
    takes_value = set("BbcDEeFIiJLlmOopQRSWw")
    i, opts = 0, []
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        opts.append(arg)
        i += 1
        if len(arg) == 2 and arg[1] in takes_value and i < len(args):
            opts.append(args[i])
            i += 1
    dest = args[i] if i < len(args) else ""
    return opts, dest, " ".join(args[i + 1:])


# ───────────── actions ─────────────
def _domain_label(target: str) -> tuple:
    m = re.match(r"^((?:gui|user|login|session|pid)/[^/]+|system)/(.+)$", target)
    return (m[1], m[2]) if m else ("", target)


def _launchctl(args: list, host) -> list:
    pos = [a for a in args if not a.startswith("-")]
    if not pos:
        return []
    verb, rest = pos[0], pos[1:]
    act = {"tool": "launchctl", "verb": verb, "host": host, "plist": None, "label": "", "domain": ""}
    if verb in LOAD_VERBS:
        plist = rest[-1] if rest else ""
        return [dict(act, key=Path(plist).stem, load=True)]
    if verb not in LAUNCHCTL_STOP or not rest:
        return []
    if verb == "unload":
        plist = rest[0]
        return [dict(act, plist=plist, label=Path(plist).stem, key=Path(plist).stem)]
    domain, label = _domain_label(rest[0])
    if verb == "bootout" and len(rest) > 1 and rest[1].endswith(".plist"):  # bootout <domain> <plist>
        plist = rest[1]
        return [dict(act, domain=rest[0], plist=plist, label=Path(plist).stem, key=Path(plist).stem)]
    return [dict(act, domain=domain, label=label, key=label)]


SYSTEMCTL_VALUED = {"-s", "--signal", "-H", "--host", "-M", "--machine", "-p", "--property", "-P", "-t", "--type",
                    "--state", "-n", "--lines", "-o", "--output", "--kill-whom", "--kill-value", "--root",
                    "--image", "--job-mode", "--what", "--timestamp", "--preset-mode", "--message", "--when"}


def _systemctl(args: list, host) -> list:
    pos, i = [], 0
    while i < len(args):  # `systemctl -s KILL kill u`: an option's value is neither the verb nor a unit
        if args[i] in SYSTEMCTL_VALUED:
            i += 1
        elif not args[i].startswith("-"):
            pos.append(args[i])
        i += 1
    if len(pos) < 2 or pos[0] not in SYSTEMCTL_STOP:
        return []
    user = "--user" in args
    return [{"tool": "systemctl", "verb": pos[0], "host": host, "unit": u, "user": user, "label": u, "key": u}
            for u in pos[1:]]


def _hermes(args: list, host, home: str = "") -> list:
    """``hermes [-p NAME] gateway stop [--all]``: the stop of that profile's gateway unit(s).

    ``HERMES_HOME=<root>/profiles/NAME hermes gateway stop`` names the same profile."""
    m = re.search(r"/profiles/([^/]+)/*$", home)
    profile, words, i = (m[1] if m else ""), [], 0
    while i < len(args):
        arg = args[i]
        if arg in {"-p", "--profile"} and i + 1 < len(args):
            profile, i = args[i + 1], i + 1
        elif arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            words.append(arg)
        i += 1
    if words[:2] != ["gateway", "stop"]:
        return []
    profile = profile.lower()
    unit = "hermes-gateway-" + profile if profile and profile != "default" else "hermes-gateway"
    if "--all" in args:  # stops every profile's gateway
        unit = "hermes-gateway-*"
    return [{"tool": "systemctl", "verb": "stop", "host": host, "unit": unit, "user": True, "label": unit,
             "key": unit}]


def _script_text(args: list) -> str:
    """The text of the script file a shell/python invocation runs (first non-option argument), else ''."""
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in {"-o", "+o", "-O", "+O"}:
            skip = True  # `bash -o pipefail script.sh`: the option's value is not the script
            continue
        if arg.startswith(("-", "+")) or arg in {"&", ">"} or arg[:1] in {">", "<", "&"} or arg[:2] == "2>":
            continue
        path = Path(os.path.expandvars(os.path.expanduser(arg)))
        try:
            if path.is_file() and path.stat().st_size <= MAX_SCRIPT_BYTES:
                return path.read_text(errors="replace")
        except OSError:
            pass
        return ""
    return ""


def _kill(prog: str, args: list, host) -> list:
    if prog == "pkill":
        # pgrep accepts the matching flags, not pkill's signal selector.
        match_args, i = [], 0
        while i < len(args):
            a = args[i]
            if a == "--signal":
                i += 2
                continue
            if re.fullmatch(r"-(?:[0-9]+|(?:SIG)?[A-Z][A-Z0-9]+)", a) or a.startswith("--signal="):
                i += 1
                continue
            match_args.append(a)
            i += 1
        args = match_args
    valued = {"-s", "-n"} if prog == "kill" else {"-u", "-U", "-g", "-G", "-P", "-t", "-s"}
    pos, full, i = [], False, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a in valued:
            if prog == "kill" and a in {"-s", "-n"} and i < len(args) and args[i] in {"0", "EXIT"}:
                return []
            i += 1
        elif prog == "kill" and a in {"-l", "-L", "-0"}:
            return []  # list signals / existence probe
        elif a.startswith("-") and len(a) > 1:
            if prog != "kill" and not a.startswith("--") and "f" in a[1:]:
                full = True
        else:
            pos.append(a)
    if prog == "kill":
        pids = [int(p) for p in pos if p.isdigit() and int(p) > 1]
        if host and len(pids) > 1:
            label = ",".join(map(str, pids))
            return [{"tool": "kill", "verb": "kill", "host": host, "pids": pids, "label": label, "key": label}]
        return [{"tool": "kill", "verb": "kill", "host": host, "pid": p, "label": str(p), "key": str(p)}
                for p in pids]
    if not pos:
        return []
    return [{"tool": "kill", "verb": prog, "host": host, "pattern": pos[0], "full": full,
             "label": pos[0], "key": pos[0], "pgrep_args": (["-x", pos[0]] if prog == "killall" else args)}]


def _extract(argv: list, host, depth: int, out: list) -> None:
    full, argv = argv, _strip(argv)
    home = next((t.split("=", 1)[1] for t in full[:len(full) - len(argv)] if t.startswith("HERMES_HOME=")), "")
    if not argv:
        return
    prog, args = os.path.basename(argv[0]), argv[1:]
    if prog in SHELLS and depth < 3:
        for i, arg in enumerate(args[:-1]):
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg):
                _extract_text(args[i + 1], host, depth + 1, out)
                return
        if not host:  # `bash script.sh`: the script's own commands
            _extract_text(_script_text(args), host, depth + 1, out)
    elif re.fullmatch(r"python[0-9.]*", prog) and depth < 3 and not host:
        code = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "-c"), None)
        for cmd in _py_commands(code if code is not None else _script_text(args)):
            _extract_text(cmd, host, depth + 1, out)
    elif prog in {"source", "."} and depth < 3 and not host:
        _extract_text(_script_text(args), host, depth + 1, out)
    elif "/" in argv[0] and re.search(r"\.(sh|bash|py)$", prog) and depth < 3 and not host:  # direct exec
        text = _script_text([argv[0]])
        if prog.endswith(".py"):
            for cmd in _py_commands(text):
                _extract_text(cmd, host, depth + 1, out)
        else:
            _extract_text(text, host, depth + 1, out)
    elif prog == "hermes":
        out.extend(_hermes(args, host, home))
    elif prog == "ssh" and depth < 3:
        opts, dest, cmd = _ssh_parts(args)
        _extract_text(cmd, {"dest": dest, "opts": opts}, depth + 1, out)
    elif prog == "launchctl":
        out.extend(_launchctl(args, host))
    elif prog == "systemctl":
        out.extend(_systemctl(args, host))
    elif prog in {"kill", "pkill", "killall"}:
        out.extend(_kill(prog, args, host))


def _extract_text(text: str, host, depth: int, out: list) -> None:
    for argv in _segments(text):
        _extract(argv, host, depth, out)


def _py_commands(code: str) -> list:
    """Shell-like command strings found in Python source: argv lists and string literals."""
    cmds = []
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):  # ValueError: NUL bytes in a binary file
        return re.findall(r"""['"]((?:launchctl|systemctl|kill|pkill|killall|ssh)\b[^'"]*)['"]""", code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            parts = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else
                     str(e.value) if isinstance(e, ast.Constant) else "?" for e in node.elts]
            if os.path.basename(parts[0]) in {"launchctl", "systemctl", "kill", "pkill", "killall", "ssh",
                                                  "hermes", "bash", "sh", "zsh", "dash"}:
                cmds.append(shlex.join(parts))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and " " in node.value:
            cmds.append(node.value)
    return cmds


def extract_actions(text: str, python: bool = False) -> list:
    out: list = []
    if python:
        for cmd in _py_commands(text):
            _extract_text(cmd, None, 0, out)
    else:
        _extract_text(text, None, 0, out)
    return out


# ───────────── verdicts ─────────────
def _log(host: str, act: dict, owner: str, decision: str) -> None:
    path = Path(os.environ.get("SERVICE_OWNER_GUARD_LOG") or Path.home() / ".hermes/logs/service-owner-guard.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a") as f:
            f.write("\t".join([stamp, host, act["verb"], act["label"], owner or "-", decision]) + "\n")
    except OSError:
        logger.debug("service-owner-guard log write failed", exc_info=True)


def _host_name(host) -> str:
    if host:
        return host["dest"].split("@")[-1] or "remote"
    return platform.node().split(".")[0] or "local"


def _lookup(act: dict) -> dict:
    host = act["host"]
    if act["tool"] == "kill":
        if not host:
            return _lookup_pids(act)
        spec = dict(act, kind="pids")
        spec.pop("host", None)
    elif act["tool"] == "launchctl":
        spec = {"kind": "launchd", "label": act["label"], "domain": act["domain"], "plist": act["plist"]}
    else:
        spec = {"kind": "systemd", "unit": act["unit"], "user": act["user"]}
    if not host:
        return local_lookup(spec)
    opts = [o for o in host["opts"] if o not in {"-t", "-tt", "-T", "-n", "-f"}]
    prefix = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"] + opts + [host["dest"]]
    return remote_lookup(prefix, spec)


def _lookup_pids(act: dict) -> dict:
    collect = libs()[0]
    try:
        res = subprocess.run(["ps", "-Ao", "pid=,ppid=,pcpu=,rss=,etime=,args="], capture_output=True, text=True,
                             timeout=5)
        table = collect.parse_ps(res.stdout)
        if "pid" in act:
            pids = [act["pid"]] if act["pid"] in table else []
        else:
            rx = re.compile(act["pattern"])
            pids = [p for p, r in table.items() if p != os.getpid() and p > 1 and
                    rx.search(r["args"] if act.get("full") else os.path.basename(r["args"].split(" ")[0]))]
        chains = {p: collect.chain(table, p) for p in pids}
        every = sorted({q for c in chains.values() for q in c})
        for q in every:
            if "cwd" not in table[q]:
                cwd = libs()[1]._cwd_of(q, run_cmd)
                if cwd:
                    table[q]["cwd"] = cwd
        tags = collect.env_tags(every)
        for p in pids:
            agent = collect.owner(table, tags, p)
            if agent.get("HERMES_OWNER_CARD"):
                return {"status": "found", "owner": agent["HERMES_OWNER_CARD"],
                        "source": f"pid {p}: {agent.get('owner_source', 'env')}", "label": str(p)}
        return {"status": "none"}
    except Exception as exc:
        return {"status": "failed", "error": repr(exc)[:160]}


def classify(tool_name: str, args: Any) -> Optional[str]:
    """The refusal message for a tool call, or None."""
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        text = " ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return _evaluate(text, extract_actions(text))
    if tool_name in PY_TOOLS:
        text = str(args.get("code") or "")
        return _evaluate(text, extract_actions(text, python=True))
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"))
                if why:
                    return why
    return None


PROC = Path("/proc")
MAX_KILL_PIDS = 64


def _proc_unit(pid: int) -> tuple:
    """(service unit, is a user unit) whose cgroup holds ``pid``, else ('', False)."""
    try:
        text = (PROC / str(pid) / "cgroup").read_text()
    except (OSError, ValueError):
        return "", False
    for line in text.splitlines():
        path = line.split(":", 2)[-1].strip()
        for leaf in reversed(path.split("/")):
            if leaf.endswith(".service") and not leaf.startswith("user@"):
                return leaf, "/user@" in path
    return "", False


def _proc_ppid(pid: int) -> int:
    try:
        text = (PROC / str(pid) / "status").read_text()
    except (OSError, ValueError):
        return 0
    m = re.search(r"^PPid:\s*(\d+)", text, re.M)
    return int(m[1]) if m else 0


def _kill_pids(act: dict) -> list:
    """Local pids a kill/pkill/killall action targets (bounded); [] on any failure."""
    if "pid" in act:
        return [act["pid"]]
    try:
        res = subprocess.run(["ps", "-Ao", "pid=,ppid=,pcpu=,rss=,etime=,args="], capture_output=True, text=True,
                             timeout=5)
        table = libs()[0].parse_ps(res.stdout)
        rx = re.compile(act["pattern"])
        match = rx.fullmatch if act["verb"] == "killall" else rx.search
        return [p for p, r in table.items() if p != os.getpid() and p > 1 and
                match(r["args"] if act.get("full") else os.path.basename(r["args"].split(" ")[0]))][:MAX_KILL_PIDS]
    except Exception:  # health: allow BLE001 -- a lookup bug must not break a tool call
        return []


def _pinned(act: dict) -> tuple:
    """(pinned-owner drop-in, refusal label) for a local stop action, else ('', '')."""
    if act["host"]:
        return "", ""
    if act["tool"] == "systemctl":
        dropin = _pinned_dropin(act)
        return dropin, act["label"] if dropin else ""
    if act["tool"] != "kill":
        return "", ""
    seen = set()
    for pid in _kill_pids(act):
        unit, user = _proc_unit(pid)
        # Only the unit's top process stops it: a child in the same cgroup (a dev server or tool the
        # gateway's own agent started) is the agent's to kill.
        if not unit or (unit, user) in seen or _proc_unit(_proc_ppid(pid)) == (unit, user):
            continue
        seen.add((unit, user))
        dropin = _pinned_dropin({"tool": "systemctl", "host": None, "unit": unit, "user": user})
        if dropin:
            return dropin, f"{unit.removesuffix('.service')} (pid {pid})"
    return "", ""


def _pinned_dropin(act: dict) -> str:
    """The pinned-owner drop-in of a local systemd unit, or '' (lookup failure reads as not pinned)."""
    if act["tool"] != "systemctl" or act["host"]:
        return ""
    name = act["unit"]
    unit = name if "." in os.path.basename(name) else name + ".service"
    argv = ["systemctl"] + (["--user"] if act["user"] else []) + ["show", "-p", "DropInPaths", "--value", unit]
    try:
        rc, out = run_cmd(argv, 5)
    except Exception:  # health: allow BLE001 -- a lookup bug must not break a tool call
        rc, out = 1, ""
    if rc == 0:
        return next((p for p in out.replace(" ", "\n").splitlines() if PINNED_DROPIN.search(p)), "")
    # No bus (sandboxed profile, sudo): read the unit's drop-in directory itself.
    roots = [Path.home() / ".config/systemd/user", Path("/etc/systemd/user"), Path("/etc/systemd/system")]
    pattern = (name if "*" in name else glob.escape(unit)).removesuffix(".service") + ".service.d/*pinned-owner*.conf"
    for root in roots:
        try:
            hit = sorted(root.glob(pattern))
        except OSError:
            continue
        if hit:
            return str(hit[0])
    return ""


def _evaluate(text: str, actions: list) -> Optional[str]:
    stops = [a for a in actions if not a.get("load")]
    if not stops:
        return None
    for act in stops:
        dropin, label = _pinned(act)
        if dropin:
            _log(_host_name(act["host"]), act, "pinned-owner", "refused (pinned owner)")
            return (f"Refused: {label} is a pinned owner ({os.path.basename(dropin)}); an agent does not "
                    f"{act['verb']} it with a raw command, and no owner-approved token overrides this. Stopping "
                    f"one coupled Ask Eve owner makes the operator checks refuse, and hermes update no longer "
                    f"needs it stopped. If it really must stop, hand the owner terminal the exact commands "
                    f"(run_in_user_terminal) and do not run them yourself.")
    approved = set(APPROVED.findall(text))
    checked_none = bool(CHECKED_NONE.search(text))
    mine = os.environ.get("HERMES_KANBAN_TASK", "")
    for idx, act in enumerate(actions):
        if act.get("load"):
            continue
        if act["tool"] == "launchctl" and act["verb"] in {"bootout", "unload"} and any(
                b.get("load") and b["key"] == act["key"] and j > idx for j, b in enumerate(actions)):
            continue  # reload: the same label is loaded again in this command
        persistent = (act["tool"] == "launchctl" and act["verb"] == "disable") or (
            act["tool"] == "systemctl" and act["verb"] in {"disable", "mask"})
        res = _lookup(act)
        host = _host_name(act["host"])
        target = act["label"] if act["tool"] != "kill" or "pid" not in act else f"pid {act['pid']}"
        status = res.get("status")
        if status == "found":
            # One remote lookup may match several separately owned processes. An approval
            # for the first owner must not hide a later, unapproved owner.
            for found in res.get("owners", [res]):
                owner = found["owner"]
                card = owner.split("@")[0]
                if mine and mine == card:
                    continue
                if card in approved:
                    _log(host, act, owner, "override owner-approved")
                    continue
                _log(host, act, owner, "refused")
                what = f"{target} (pid {found['label']})" if act["tool"] == "kill" and ("pattern" in act or "pids" in act) else target
                return (f"Refused: {what} is owned by card {owner} ({found.get('source', '?')}). Ask on that card "
                        f"with the measured cost (CPU-time delta over >= 60 s). Stop it only after the owner agrees, "
                        f"then add owner-approved={card} to the command.")
            continue
        if status == "failed":
            _log(host, act, "-", f"lookup failed ({res.get('error', '?')}); " +
                 ("override owner-checked=none" if persistent and checked_none else
                  "refused" if persistent else "allowed"))
            if persistent and not checked_none:
                return (f"Refused: could not check the owner of {target} on {host} ({res.get('error', '?')}), and "
                        f"{act['verb']} persists across reboots. Check it another way (OWNER.md, plist "
                        f"EnvironmentVariables), then add owner-checked=none if there is no owner.")
            continue
        if persistent:  # no owner found
            if checked_none:
                _log(host, act, "-", "override owner-checked=none")
                continue
            _log(host, act, "-", "refused (no owner found, persistent)")
            return (f"Refused: {act['verb']} of {target} on {host} persists across reboots, and no owner marker "
                    f"was found. Confirm nobody owns it, then add owner-checked=none to the command.")
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        if not enabled():
            return None
        why = classify(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("service-owner-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("service-owner-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": why}


def check(command: str) -> Optional[str]:
    """Dry run: the refusal message for a shell command, or None. Never executes the command."""
    return classify("terminal", {"command": command})


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
