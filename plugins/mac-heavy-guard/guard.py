"""Decide whether a shell command is heavy work that should not run on a laptop.

Shared by the Hermes pre_tool_call hook (__init__.py) and the Claude Code PreToolUse hook
(claude_hook.py). Heavy = release builds (`cargo build --release`), container builds
(`docker build`), whole test suites (`pytest` with no test path or with `-n`, bare `npm test`,
`vitest run` with no filter, `make test`, Hermes `scripts/run_tests.sh` with no test path) and whole-repo dependency installs (`npm install`,
`pnpm install`, `uv sync`, `pip install -r`). Targeted work stays allowed (`pytest tests/test_x.py`,
`cargo build`, `npm install left-pad`), as does anything sent over `ssh`, including the body of a
heredoc fed to it (`ssh hub bash -s <<'EOF'`).

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
import stat
import subprocess
import sys
from urllib.parse import quote
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


def _strip_comments(text: str) -> str:
    """Drop every `# comment` up to (not including) its newline. shlex's own comment handling eats the newline
    too, which glued the next line's command onto this one (`echo hi # note\\nuv sync` -> `echo hi uv sync`).
    As in bash, `#` starts a comment only at the start of a word and outside quotes (`a#b`, `'#'` are words)."""
    if "#" not in text:
        return text
    out: list[str] = []
    quote = ""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            if c == "'":
                quote = ""
        elif c == "\\":  # escapes the next char, outside quotes and inside "..."
            out.append(text[i:i + 2])
            i += 2
            continue
        elif quote == '"':
            if c == '"':
                quote = ""
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1] in " \t\r\n;&|()"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(c)
        i += 1
    return "".join(out)


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


def _tokens(text: str) -> Optional[list[str]]:
    lex = shlex.shlex(_expose_substitutions(_strip_comments(text.replace("\\\n", " "))), posix=True,
                      punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: don't guess
        return None


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


def _drop_redirects(tokens: list[str]) -> list[str]:
    """Drop each redirect operator and the word after it (target, fd or heredoc tag) from tokens lexed with `<>` as
    punctuation. `<(`/`>(` (process substitution) stays a command boundary."""
    out: list[str] = []
    skip = False
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


def _command_lex(text: str) -> list[str]:
    lex = shlex.shlex(_mask_quoted_redirects(_drop_fd_prefixes(_expose_substitutions(_strip_comments(text)))),
                      posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    return _drop_redirects(_unmask_quoted(list(lex)))


def _command_tokens(text: str) -> list[str]:
    """`_tokens` without redirects: `pytest 2>&1 | tail` and `pytest 2>/dev/null` are a bare `pytest`, never a
    `pytest` with a test path `2>` or `2>/dev/null` (that hid whole-suite runs). Comments and heredoc bodies are
    split off first (`_split_heredocs`: an apostrophe in them no longer hides the commands after them, and an
    unquoted body's `$(...)` is a command); a line whose quotes never close is dropped, never the whole text."""
    code = _split_heredocs(text)[0].replace("\\\n", " ")
    return _command_lex(_lexable(code, _command_lex))


def commands(text: str) -> Iterator[tuple[list[str], set[str]]]:
    """Yield (argv, env-assignment names) for each simple command, recursing into `bash -c`."""
    toks = _command_tokens(text)
    if toks is None:
        return
    cur: list[str] = []
    in_array = False
    for t in toks + [";"]:
        # `name=(a b c)` / `local name=(...)` is a bash array literal: its words are data, not a command
        # (hermes-core-wt's `here=(./scripts/run_tests.sh)` read as a whole-suite run and blocked the tool
        # this guard itself recommends).
        if in_array:
            if ")" in t:
                in_array = False
            continue
        if t == "(" and cur and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\+?=", cur[-1]):
            in_array = True
            continue
        if t in SEPARATORS or (t and set(t) <= set(";&|()\n")):
            if cur:
                yield from _unwrap(cur)
            cur = []
        else:
            cur.append(t)


def _strip_prefix(argv: list[str]) -> tuple[list[str], set[str]]:
    """Drop leading `NAME=value` assignments and `env`/`nice`/`timeout`/... wrappers: (the command, env names)."""
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
    return argv[i:], env


def _unwrap(argv: list[str]) -> Iterator[tuple[list[str], set[str]]]:
    rest, env = _strip_prefix(argv)
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


# `<<WORD` / `<<-WORD`, never the here-string `<<<`. WORD may be bare, 'quoted', "quoted" or \escaped.
HEREDOC = re.compile(r"""(?<!<)<<(?!<)(-?)[ \t]*((?:'[^'\n]*'|"[^"\n]*"|\\.|[^\s;&|()<>'"\\])+)""")
CONTINUED = re.compile(r"(?<!\\)(?:\\\\)*\\\n\Z")  # a line ending in an unescaped backslash
_PROBE = "mac-heavy-guard-probe\x01"


def _owner(before: str) -> Optional[str]:
    """First word of the simple command still open at the end of `before` (shell text), past assignments and
    wrappers. None when `before` ends inside a quote or a comment (so what follows is no operator)."""
    toks = _tokens(before + " " + _PROBE)
    if not toks or toks[-1] != _PROBE:
        return None
    toks = toks[:-1]
    start = 0
    for k, t in enumerate(toks):
        if t == "&" and k and toks[k - 1][-1:] in (">", "<"):
            continue  # `2>&1`, `>&2`: a redirect, not a separator
        if t in SEPARATORS or (t and set(t) <= set(";&|()\n")):
            start = k + 1
    rest, _ = _strip_prefix(toks[start:])
    return os.path.basename(rest[0]) if rest else ""


def _drop_ssh_heredocs(text: str) -> str:
    """Drop the body and delimiter lines of every heredoc fed to `ssh`: that text runs on the remote host.
    Every other heredoc body stays in the text; `_command_tokens` then keeps a body a local shell reads
    (`bash -s`) as commands and reads any other body (`python3 -`, `cat > f`) as data (`_split_heredocs`)."""
    if "<<" not in text:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    shell = ""  # shell text read so far (heredoc bodies excluded): quote state and the owner's prefix
    i = 0
    while i < len(lines):
        logical = lines[i]
        i += 1
        # A command line ends at a newline that is neither escaped nor inside a quote.
        while i < len(lines) and (CONTINUED.search(logical) or _tokens(shell + logical) is None):
            logical += lines[i]
            i += 1
        out.append(logical)
        bodies = []
        for m in HEREDOC.finditer(logical):
            owner = _owner(shell + logical[:m.start()])
            if owner is None:
                continue
            try:
                word = shlex.split(m.group(2))[0]
            except (ValueError, IndexError):
                continue
            bodies.append((owner == "ssh", m.group(1) == "-", word))
        shell += logical
        for remote, strip_tabs, word in bodies:  # several on one line: their bodies follow in order
            while i < len(lines):  # no delimiter line: the body runs to the end of the text
                line = lines[i]
                i += 1
                if not remote:
                    out.append(line)
                bare = line.rstrip("\r\n")
                if (bare.lstrip("\t") if strip_tabs else bare) == word:
                    break
    return "".join(out)


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


def _large_copy(name: str, args: list[str], cwd: Optional[str]) -> Optional[str]:
    """Inspect explicit local source files only; never expand unknown shell syntax."""
    # shlex leaves redirections in argv; they are not copy operands.
    clean = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        redirect = re.match(r"^\d*[<>]+", arg)
        if redirect:
            skip = redirect.end() == len(arg)
            continue
        clean.append(arg)
    args = clean
    if name == "dd":
        sources = [a[3:] for a in args if a.startswith("if=")]
    else:
        operands = []
        target_dir = False
        options = True
        i = 0
        value_flags = {
            "cp": {"-t", "--target-directory", "-S", "--suffix"},
            "rsync": {"-e", "--rsh", "--exclude", "--include", "--filter", "-f",
                      "--exclude-from", "--include-from", "--files-from", "--log-file",
                      "--backup-dir", "--suffix", "--rsync-path", "--bwlimit"},
            "ditto": {"--arch", "--bom", "--rsrcFork"},
        }[name]
        while i < len(args):
            a = args[i]
            i += 1
            if options and a == "--":
                options = False
            elif options and a.startswith("-"):
                flag = a.split("=", 1)[0]
                if name == "cp" and (flag in ("-t", "--target-directory") or a.startswith("-t")):
                    target_dir = True
                if a in value_flags:
                    i += 1
            else:
                operands.append(a)
        sources = operands if target_dir else operands[:-1]
    for source in sources:
        path = re.sub(r"\$\{HOME\}|\$HOME(?![A-Za-z_0-9])", lambda _: os.environ.get("HOME", "$HOME"), source)
        if any(c in path for c in "$`*?[]{}") or (path.startswith("~") and not path.startswith("~/")):
            continue
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(cwd or os.getcwd(), path)
        try:
            info = os.stat(path)
        except (OSError, ValueError):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size < 2 * 1024**3:
            continue
        advice = "Use read-only in-place access instead of copying."
        if path.lower().endswith((".db", ".sqlite", ".sqlite3")):
            base = "file:" + quote(os.path.abspath(path), safe="/")
            uri = base + "?mode=ro"
            # mode=ro can't open a WAL db whose -wal/-shm sidecars are gone (tests/test_sqlite_ro_guard.py),
            # so name the read-only rw fallback as well.
            fallback = f"sqlite3 -cmd 'PRAGMA query_only=ON' {shlex.quote(base + '?mode=rw')}"
            advice += (f" For SQLite: sqlite3 {shlex.quote(uri)} with indexed queries (session_id or an id range);"
                       f" if that says 'unable to open database file', {fallback}.")
        return f"{name} source {path!r} ({info.st_size / 1024**3:.2f} GiB; {info.st_size} bytes). {advice}"
    return None


def check_argv(argv: list[str], cwd: Optional[str] = None) -> Optional[str]:
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
    if name in ("cp", "rsync", "ditto", "dd"):
        return _large_copy(name, args, cwd)
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
    for argv, env in commands(_drop_ssh_heredocs(text)):
        if argv[0] == "cd" and len(argv) > 1 and not argv[1].startswith("-"):
            cwd = _cd(cwd, argv[1])
            continue
        if OPT_OUT in env:
            continue
        why = check_argv(argv, cwd)
        if not why and in_script:  # `step pytest python3 -m pytest -q`: a heavy command passed as arguments
            why = next((w for w in (check_argv(argv[k:], cwd) for k in range(1, len(argv))) if w), None)
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
    for argv, env in commands(_drop_ssh_heredocs(text)):
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
    core = (f"- This is a Hermes core worktree: {core_cmd} runs it on hermes-hub (under nice), or on a pool worker "
            "while the hub is over its CPU admission gate, and streams the output back.\n") if core_cmd else ""
    return (
        f"mac-heavy-guard: blocked {why} on this Mac. Heavy builds, whole test suites and whole-repo installs "
        "pile up across agents here (load 150-270) and make Hermes Desktop slow, so they run on a pool worker by "
        "default.\n"
        f"{core}"
        "- Hermes: a pool-worker card: cards that name tests/builds go to worker-1 automatically "
        "(kanban.placement.heavy_route), or set runs_on=[pool:worker] / [machine:worker-1]. Suites, reviews and CI "
        "waits of a published branch belong there, not in raw ssh on hermes-hub: that skips the hub's CPU admission "
        "gate. Use the hub only for hub-runtime work (its live board, services or state).\n"
        "- Narrow it: a single test file/dir or a debug build is allowed here.\n"
        f"- Needs this Mac (Xcode/macOS-only, a Mac-only checkout)? Re-run with {OPT_OUT}=1 <cmd>; "
        "CARGO_BUILD_JOBS / PYTEST_XDIST_AUTO_NUM_WORKERS / VITEST_MAX_WORKERS cap it at 4 workers."
    )
