"""Refuse a cross-worktree stash swap.

``refs/stash`` is one ref per repository, shared by every linked worktree. Two workers that
``git stash`` / ``stash pop`` at the same time trade diffs: each pop applies the other's
patch. This module is the decision logic for the Hermes hooks in ``__init__.py``.

- Any ``git stash`` (push, pop, apply, list, no-arg, ...) inside a repo with more than one
  worktree gets a warning. ``list`` is included so a read-only command can prove the guard.
- ``git stash pop`` / ``apply`` is refused when that repo has more than one worktree and the
  stash's branch (parsed from ``WIP on <branch>:`` / ``On <branch>:``) is not the current
  branch. A missing or unparseable subject fails closed. Same-branch pop is allowed (the
  warning still fires). A single-worktree repo is left alone: stash is the normal undo there.

Commands are parsed by a bounded static analyzer (see ``_Analyzer``); nothing is executed.
Unsupported state is unknown, and unknown never infers a safe repo. Uncertain fragments
(unsupported wrapper argv, unknown command names, ANSI-C text) are re-analyzed with an
unknown repo, so adding uncertainty can only add refusals.

``STASH_WORKTREE_GUARD=off`` in the command, or ``STASH_WORKTREE_GUARD_DISABLE=1`` in the
environment, skips both paths.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Optional

MARK = "[stash-worktree-guard]"

STASH_SUBS = {
    "push", "save", "pop", "apply", "list", "show", "drop", "clear",
    "branch", "create", "store", "export", "import",
}
REFUSE_OPS = {"pop", "apply"}
WRAPPERS = {"sudo", "env", "command", "exec", "time", "nice", "nohup", "stdbuf", "builtin"}
SHELLS = {"bash", "sh", "zsh", "dash"}
# Programs that run another argv (or a shell string) with option syntax the guard does not
# model. Any argv suffix may be the command they run; xargs/parallel/find append words.
RUNNERS = {
    "xargs", "parallel", "find", "flock", "setsid", "ionice", "chrt", "taskset", "nsenter",
    "unshare", "chroot", "doas", "su", "runuser", "watch", "chronic", "unbuffer", "faketime",
    "strace", "ltrace", "systemd-run", "script", "caffeinate", "direnv", "uv", "poetry",
}
_APPENDING_RUNNERS = {"xargs", "parallel", "find"}
_UNKNOWN_WORD = "$?"  # expands to an unknown, splittable word
_STASH_BRANCH = re.compile(r"^(?:WIP on|On|index on) (.+?): ")
# GNU timeout options; anything else (or a non-duration) is an unsupported wrapper form.
_TIMEOUT_NOARG = {"--foreground", "--preserve-status", "-v", "--verbose"}
_TIMEOUT_ARG = {"-k", "--kill-after", "-s", "--signal"}
_TIMEOUT_DURATION = re.compile(r"^(?:[0-9]+\.?[0-9]*|\.[0-9]+)[smhd]?$")
# Git global options (git.c handle_options). Unknown options have unknown arity, so the
# subcommand position after them is unknown too.
_GIT_FLAG_OPTS = {
    "-p", "--paginate", "-P", "--no-pager", "--no-replace-objects", "--literal-pathspecs",
    "--no-literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
    "--no-optional-locks", "--no-advice", "--no-lazy-fetch", "--html-path", "--man-path",
    "--info-path", "--exec-path", "-v", "--version", "-h", "--help",
}
_GIT_ARG_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                 "--config-env", "--attr-source", "--shallow-file"}
_GIT_EQ_OPTS = ("--exec-path=", "--namespace=", "--super-prefix=", "--list-cmds=",
                "--attr-source=", "--config-env=")
# Common builtin commands; config-injected aliases (`-c alias.x=...`) cannot shadow these.
_GIT_BUILTINS = {
    "add", "am", "apply", "blame", "branch", "checkout", "cherry-pick", "clean", "clone",
    "commit", "config", "describe", "diff", "fetch", "format-patch", "grep", "init", "log",
    "ls-files", "merge", "mv", "pull", "push", "rebase", "remote", "reset", "restore",
    "rev-parse", "revert", "rm", "show", "stash", "status", "switch", "symbolic-ref", "tag",
    "worktree",
}
# Environment that selects another repository/stash ref, or injects config (aliases).
_GIT_ALT_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"}
_GIT_CONFIG_ENV = {"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL",
                   "GIT_CONFIG_SYSTEM", "GIT_CONFIG", "XDG_CONFIG_HOME"}
# `-c` keys that define aliases directly or load another config file that may.
_CONFIG_ALIAS_KEYS = ("alias.", "include.", "includeif.")
# Programs whose argv is data, never a command they run. Any other unmodeled program whose
# argv names git is treated like a runner: the git suffix is analyzed with an unknown repo.
_INERT = {"echo", ":", "true", "false", "test", "[", "type", "which", "man", "help",
          "grep", "egrep", "fgrep", "rg",
          # Programs whose arguments are data or files, never a command line they run.
          # (`sed` runs code only through its `e` command, handled separately.)
          "sed", "gh", "jq", "yq", "tee", "cat", "sort", "head", "tail", "cut", "tr", "wc",
          "diff", "cp", "mv", "rm", "mkdir", "touch", "ls", "curl", "wget", "chmod", "ln"}
_ALLEXPORT = "*allexport*"  # marker in _State.exported: `set -a` exports later assignments
# Builtins run before the hash table is consulted, so BASH_CMDS cannot remap them.
BUILTINS_NOT_HASHED = {"echo", ":", "true", "false", "test", "[", "type", "help", "printf"}
_KEYWORD = "*keyword*"      # `set -k`: assignment words anywhere in argv go to the environment
_NAMEREF = "*nameref*"      # a nameref exists: an assignment may write any variable
_CMDS = "*bash_cmds*"       # BASH_CMDS was assigned: the hash table maps unknown names
_MARKERS = {_ALLEXPORT, _KEYWORD, _NAMEREF, _CMDS}
# Interpreters whose argv may hold a program that runs git (`perl -e 'system("git …")'`).
_INTERPRETERS = {"perl", "python", "python2", "python3", "ruby", "node", "php", "tclsh",
                 "lua", "awk", "gawk", "mawk"}
_NAMEREF_TAINT = ("IFS", "HOME", "CDPATH", "OLDPWD")


def _taint_nameref(st) -> None:
    """After `declare -n`, an assignment may set IFS/HOME/CDPATH/GIT_DIR through the ref."""
    for name in _NAMEREF_TAINT:
        st.vars[name] = None
    st.exported.add(_NAMEREF)


def _shell_flags(lit: list, st, shopt: bool = False, invocation: bool = False) -> None:
    """Apply allexport/keyword from `set`, `shopt -o` or a shell's own options to ``st``."""
    names = {"allexport": _ALLEXPORT, "keyword": _KEYWORD}

    def toggle(marker, on):
        (st.exported.add if on else st.exported.discard)(marker)

    if shopt:
        letters = "".join(w[1:] for w in lit[1:] if w.startswith("-"))
        if "o" in letters:
            for w in lit[1:]:
                if w in names:
                    toggle(names[w], "u" not in letters)
        return
    i = 1
    while i < len(lit):
        w = lit[i]
        if invocation and w.startswith("--") and w != "--":
            i += 1
            continue
        if not re.fullmatch(r"[-+][A-Za-z]+", w):
            break  # `--`, `-` or the first operand ends the options
        on, opt_args = w[0] == "-", 0
        for ch in w[1:]:
            if ch == "a":
                toggle(_ALLEXPORT, on)
            elif ch == "k":
                toggle(_KEYWORD, on)
            elif ch in "oO":
                opt_args += 1
        i += 1
        for _ in range(opt_args):
            if i < len(lit):
                if lit[i] in names:
                    toggle(names[lit[i]], on)
                i += 1


def enabled() -> bool:
    if os.environ.get("STASH_WORKTREE_GUARD_DISABLE") == "1":
        return False
    return os.environ.get("STASH_WORKTREE_GUARD") != "off"


def _bypassed(cmd: str) -> bool:
    return not enabled() or "STASH_WORKTREE_GUARD=off" in (cmd or "")


def _expand_word(raw: str, variables: dict) -> Optional[str]:
    """Expand only scalar variables and home tilde; never execute shell code."""
    out, quote, i = [], None, 0
    if raw == "~" or raw.startswith("~/"):
        home = variables.get("HOME")
        if home is None:
            return None
        out.append(home)
        i = 1
    while i < len(raw):
        char = raw[i]
        if char in "\"'" and (quote is None or quote == char):
            quote = char if quote is None else None
            i += 1
            continue
        if char == "\\" and quote != "'":
            i += 1
            if i == len(raw):
                return None
            out.append(raw[i])
        elif char == "$" and quote != "'":
            match = re.match(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))", raw[i:])
            if not match:
                return None
            value = variables.get(match[1] or match[2])
            if value is None or (quote is None and (
                    not value or any(c.isspace() or c in "*?[]" for c in value)
                    or _ifs_splits(value, variables))):
                return None
            out.append(value)
            i += len(match[0])
            continue
        elif (char == "`" and quote != "'") or (quote is None and char in "*?()"):
            return None
        elif char == "[" and quote is None and _glob_bracket(raw, i):
            return None
        elif char == "{" and quote is None and _brace_expansion(raw, i):
            return None  # {a,b} / {1..3} brace expansion is not modeled
        else:
            out.append(char)
        i += 1
    return None if quote else "".join(out)


def _config_runs(key: str) -> bool:
    """Git config keys whose value is a program Git runs (hooks, diff/merge drivers, ...)."""
    return (key in _GIT_EXEC_KEYS or key.startswith(("filter.", "credential", "gpg."))
            or key.endswith((".textconv", ".driver", ".command", ".cmd", ".tool", ".helper")))


_GIT_EXEC_KEYS = {"core.hookspath", "core.fsmonitor", "diff.external", "core.editor",
                  "core.pager", "sequence.editor", "core.sshcommand", "core.askpass",
                  "uploadpack.packobjectshook"}
_GIT_EXEC_ENV = {"GIT_EXTERNAL_DIFF", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_PAGER",
                 "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "EDITOR", "VISUAL", "PAGER"}


def _ifs_splits(value: str, variables: dict) -> bool:
    """A changed IFS may split an unquoted expansion on characters other than whitespace."""
    if "IFS" not in variables:
        return False
    ifs = variables["IFS"]
    return ifs is None or any(c in ifs for c in value)


def _shell_script(lit: list) -> tuple:
    """Where a shell gets its script: ("c", index), ("stdin", None) or ("file", index).

    Handles option clusters (``-ec``, ``-lc``), ``-o``/``-O`` arguments and ``--``.
    """
    i, has_c = 1, False
    while i < len(lit):
        w = lit[i]
        if w in ("--", "-"):
            i += 1
            break
        if w in ("--rcfile", "--init-file"):
            i += 2
            continue
        if re.fullmatch(r"[-+][A-Za-z]+", w):
            has_c |= w.startswith("-") and "c" in w
            i += 1 + sum(ch in "oO" for ch in w[1:])
            continue
        if w.startswith("--"):
            i += 1
            continue
        break
    if has_c:
        return ("c", i) if i < len(lit) else ("unknown", None)
    return ("stdin", None) if i >= len(lit) else ("file", i)


def _brace_expansion(raw: str, i: int) -> bool:
    """Conservative: an unquoted ``{`` with a later ``}`` and a ``,`` or ``..`` between."""
    j = raw.find("}", i + 1)
    return j > 0 and ("," in raw[i + 1:j] or ".." in raw[i + 1:j])


def _glob_bracket(raw: str, i: int) -> bool:
    """Conservative: an unquoted ``[`` opens a bracket glob only with a later ``]``.

    Bash leaves a ``[`` with no closing ``]`` (the ``[`` test builtin) and a lone ``]``
    literal, so neither makes the word unknown.
    """
    return "]" in raw[i + 1:]


def _splittable(raw: str) -> bool:
    """True if an unquoted expansion in ``raw`` may split into several words."""
    quote, i = None, 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and quote != "'":
            i += 2
            continue
        if c in "\"'" and (quote is None or quote == c):
            quote = c if quote is None else None
        elif quote is None and c in "$`*?[{":
            return True
        i += 1
    return False


def _resolve(p: Optional[str], base: Optional[str] = None) -> Optional[str]:
    if not p:
        return None
    if not os.path.isabs(p):
        if base is None:
            return None
        p = os.path.join(base, p)
    return os.path.realpath(p)


def _git_env() -> dict:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return env


def _run_git(repo: str, args: list[str]) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=5, env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def worktree_count(repo: str) -> int:
    """How many worktrees this repo has. 0 if ``repo`` is not a checkout we can read."""
    proc = _run_git(repo, ["worktree", "list", "--porcelain"])
    if proc is None or proc.returncode != 0:
        return 0
    return sum(1 for line in proc.stdout.splitlines() if line.startswith("worktree "))


def current_branch(repo: str) -> str:
    proc = _run_git(repo, ["symbolic-ref", "-q", "--short", "HEAD"])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "HEAD"


def stash_subject(repo: str, ref: str = "stash@{0}") -> Optional[str]:
    proc = _run_git(repo, ["log", "-1", "--format=%s", ref])
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip()


def stash_branch(subject: str) -> Optional[str]:
    match = _STASH_BRANCH.match(subject or "")
    return match.group(1) if match else None


# --- Bounded static shell analysis -----------------------------------------------------------
#
# A small lexer and recursive-descent parser for the shell constructs that change where a
# later command runs: lists, and-or lists, pipelines, background jobs, ( ) subshells,
# { } groups, if/while/until/for/case, function definitions, and command/process
# substitutions. Nothing is executed. The analyzer tracks a bounded *set* of possible
# states (cwd, scalar variables, exported names, functions); a restore must be safe in
# every state it may run in. Constructs it cannot model degrade to an unknown cwd/variable,
# which refuses a restore; parse failures and exhausted bounds fail closed for any textual
# `stash ... pop|apply` in the WHOLE original command (not only the failed fragment, whose
# boundaries may themselves be wrong) instead of dropping the call.

MAX_DEPTH = 8      # nested bash -c / eval / substitution / function-call levels
MAX_STATES = 16    # possible states kept before collapsing to one unknown state
MAX_STEPS = 4000   # simple-command evaluations per analyzed tool call

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?(\+)?=")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPS = (";;&", ";;", ";&", "&&", "&>>", "&>", "||", "|&", "<<<", "<<-", "<<", "<>", "<&",
        ">>", ">&", ">|", ";", "&", "|", "<", ">", "(", ")")
REDIR_OPS = {"&>>", "&>", "<<<", "<<-", "<<", "<>", "<&", ">>", ">&", ">|", "<", ">"}
_CMD_START_OPS = {";", "&", "&&", "||", "|", "|&", ";;", ";&", ";;&", "(", "\n"}
_CMD_START_WORDS = {"do", "then", "else", "elif", "{", "!", "time", "if", "while", "until", "for"}
_META = set(" \t\r\n;&|<>()")
DECLARE = {"export", "readonly", "declare", "local", "typeset"}
MUTATORS = {"read", "mapfile", "readarray", "getopts", "printf", "let", "wait"}
_UNMODELED_FLAGS = set("aAinluc")


class _ParseError(Exception):
    pass


class _Budget(Exception):
    pass


class _Tok:
    __slots__ = ("kind", "text", "body", "quoted", "delim")

    def __init__(self, kind: str, text: str):
        self.kind, self.text = kind, text
        self.body: Optional[str] = None
        self.quoted = False
        self.delim = ""


def _skip_single(s: str, i: int) -> int:
    j = s.find("'", i)
    if j < 0:
        raise _ParseError("unterminated '")
    return j + 1


def _skip_double(s: str, i: int) -> int:
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        elif c == "`":
            i = _skip_backtick(s, i + 1)
        elif s.startswith("$(", i):
            i = _match_paren(s, i + 2)
        elif s.startswith("${", i):
            i = _match_brace(s, i + 2)
        else:
            i += 1
    raise _ParseError('unterminated "')


def _skip_backtick(s: str, i: int) -> int:
    while i < len(s):
        if s[i] == "\\":
            i += 2
        elif s[i] == "`":
            return i + 1
        else:
            i += 1
    raise _ParseError("unterminated `")


def _match_paren(s: str, i: int) -> int:
    """Index just past the ``)`` closing the ``(`` before ``i`` (quote-aware)."""
    depth = 1
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            i = _skip_single(s, i + 1)
            continue
        if c == '"':
            i = _skip_double(s, i + 1)
            continue
        if c == "`":
            i = _skip_backtick(s, i + 1)
            continue
        if c == "#" and (i == 0 or s[i - 1] in " \t\n;&|("):
            j = s.find("\n", i)
            i = len(s) if j < 0 else j
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise _ParseError("unterminated (")


def _match_brace(s: str, i: int) -> int:
    depth = 1
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            i = _skip_single(s, i + 1)
            continue
        if c == '"':
            i = _skip_double(s, i + 1)
            continue
        if c == "`":
            i = _skip_backtick(s, i + 1)
            continue
        if s.startswith("$(", i):
            i = _match_paren(s, i + 2)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise _ParseError("unterminated ${")


def _scan_word(s: str, i: int) -> int:
    start = i
    while i < len(s):
        c = s[i]
        if c in _META:
            if c == "(" and i > start and (_ASSIGN.match(s[start:i]) and s[i - 1] == "="
                                           or s[i - 1] in "@!+*?"):
                i = _match_paren(s, i + 1)  # array assignment / extglob stays one word
                continue
            break
        if c == "\\":
            i += 2
        elif c == "'":
            i = _skip_single(s, i + 1)
        elif c == '"':
            i = _skip_double(s, i + 1)
        elif c == "`":
            i = _skip_backtick(s, i + 1)
        elif s.startswith("$(", i):
            i = _match_paren(s, i + 2)
        elif s.startswith("${", i):
            i = _match_brace(s, i + 2)
        else:
            i += 1
    return min(i, len(s))


def _cmd_position(toks: list) -> bool:
    if not toks:
        return True
    last = toks[-1]
    return (last.kind == "op" and last.text in _CMD_START_OPS) or (
        last.kind == "w" and last.text in _CMD_START_WORDS)


def _read_heredoc(s: str, i: int, tok: _Tok) -> int:
    body = []
    while i < len(s):
        j = s.find("\n", i)
        line = s[i:] if j < 0 else s[i:j]
        check = line.lstrip("\t") if tok.text == "<<-" else line
        if check == tok.delim:
            tok.body = "".join(body)
            return len(s) if j < 0 else j + 1
        body.append(line + "\n")
        if j < 0:
            break
        i = j + 1
    tok.body = "".join(body)
    return len(s)


def _lex(s: str) -> list:
    """Tokens: words keep their quotes; operators are separated from redirections."""
    toks, pending, i, n = [], [], 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\r":
            i += 1
            continue
        if c == "\\" and s.startswith("\\\n", i):
            i += 2
            continue
        if c == "#":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "\n":
            toks.append(_Tok("op", "\n"))
            i += 1
            for tok in pending:
                i = _read_heredoc(s, i, tok)
            pending = []
            continue
        if s.startswith("((", i) and _cmd_position(toks):
            end = _match_paren(s, i + 1)
            if s[end - 2] == ")":
                toks.append(_Tok("arith", s[i + 2:end - 2]))
                i = end
                continue
        if s.startswith("<(", i) or s.startswith(">(", i):
            end = _match_paren(s, i + 2)
            end = _scan_word(s, end)
            toks.append(_Tok("w", s[i:end]))
            i = end
            continue
        op = next((o for o in _OPS if s.startswith(o, i)), None)
        if op:
            tok = _Tok("op", op)
            toks.append(tok)
            i += len(op)
            if op in ("<<", "<<-"):
                while i < n and s[i] in " \t":
                    i += 1
                j = _scan_word(s, i)
                if j == i:
                    raise _ParseError("heredoc without delimiter")
                raw = s[i:j]
                tok.quoted = any(q in raw for q in "'\"\\")
                tok.delim = _literal(raw)
                toks.append(_Tok("w", raw))
                pending.append(tok)
                i = j
            continue
        j = _scan_word(s, i)
        if j == i:
            raise _ParseError(f"unexpected {c!r}")
        word = s[i:j]
        i = j
        if i < n and s[i] in "<>" and (word.isdigit() or re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", word)):
            continue  # file-descriptor number of a redirection, e.g. 2>&1
        toks.append(_Tok("w", word))
    for tok in pending:
        tok.body = ""
    return toks


class _Parser:
    def __init__(self, toks: list):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def is_op(self, *ops) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "op" and tok.text in ops

    def is_word(self, *words) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "w" and tok.text in words

    def next(self):
        tok = self.peek()
        if tok is None:
            raise _ParseError("unexpected end")
        self.i += 1
        return tok

    def skip_nl(self):
        while self.is_op("\n"):
            self.i += 1

    def expect_word(self, word: str):
        if not self.is_word(word):
            raise _ParseError(f"expected {word}")
        self.i += 1

    def expect_op(self, op: str):
        if not self.is_op(op):
            raise _ParseError(f"expected {op}")
        self.i += 1

    def parse_top(self):
        node = self.parse_list()
        if self.peek() is not None:
            raise _ParseError(f"unexpected {self.peek().text!r}")
        return node

    def parse_list(self, stop_words=(), stop_ops=()):
        items = []
        while True:
            while self.is_op("\n", ";", "&"):
                self.i += 1  # empty commands (e.g. the hook's " ; " joins) are lenient
            tok = self.peek()
            if tok is None or (tok.kind == "op" and tok.text in stop_ops) or (
                    tok.kind == "w" and tok.text in stop_words):
                return ("list", items)
            node = self.parse_andor()
            sep = self.next().text if self.is_op(";", "&", "\n") else None
            items.append((node, sep))
            if sep is None:
                tok = self.peek()
                if tok is None or (tok.kind == "op" and tok.text in stop_ops) or (
                        tok.kind == "w" and tok.text in stop_words):
                    return ("list", items)
                raise _ParseError(f"unexpected {tok.text!r}")

    def parse_andor(self):
        parts, ops = [self.parse_pipeline()], []
        while self.is_op("&&", "||"):
            ops.append(self.next().text)
            self.skip_nl()
            parts.append(self.parse_pipeline())
        return ("andor", parts, ops) if ops else parts[0]

    def parse_pipeline(self):
        negated = False
        while self.is_word("!", "time"):
            negated ^= self.next().text == "!"
            if self.is_word("-p"):
                self.i += 1
        cmds = [self.parse_command()]
        while self.is_op("|", "|&"):
            self.i += 1
            self.skip_nl()
            cmds.append(self.parse_command())
        if len(cmds) == 1 and not negated:
            return cmds[0]
        return ("pipeline", negated, cmds)

    def parse_redirs(self):
        redirs = []
        while self.peek() is not None and self.peek().kind == "op" and self.peek().text in REDIR_OPS:
            op = self.next()
            target = self.next()
            if target.kind != "w":
                raise _ParseError("redirection without target")
            redirs.append((op.text, target.text, op.body, op.quoted))
        return redirs

    def parse_command(self):
        tok = self.peek()
        if tok is None:
            raise _ParseError("missing command")
        if tok.kind == "op" and tok.text == "(":
            self.i += 1
            body = self.parse_list(stop_ops=(")",))
            self.expect_op(")")
            return ("subshell", body, self.parse_redirs())
        if tok.kind == "arith":
            self.i += 1
            return ("arith", tok.text, self.parse_redirs())
        if tok.kind == "w":
            word = tok.text
            if word == "{":
                self.i += 1
                body = self.parse_list(stop_words=("}",))
                self.expect_word("}")
                return ("group", body, self.parse_redirs())
            if word == "if":
                return self.parse_if()
            if word in ("while", "until"):
                self.i += 1
                cond = self.parse_list(stop_words=("do",))
                self.expect_word("do")
                body = self.parse_list(stop_words=("done",))
                self.expect_word("done")
                return ("loop", cond, body, None, self.parse_redirs())
            if word in ("for", "select"):
                return self.parse_for(word)
            if word == "case":
                return self.parse_case()
            if word == "function":
                self.i += 1
                name = self.next()
                if name.kind != "w":
                    raise _ParseError("function name")
                if self.is_op("("):
                    self.i += 1
                    self.expect_op(")")
                self.skip_nl()
                return ("funcdef", name.text, self.parse_command())
            if word == "[[":
                words = []
                while not self.is_word("]]"):
                    words.append(self.next().text)
                self.i += 1
                return ("simple", [], ["[[", *words, "]]"], self.parse_redirs())
        return self.parse_simple()

    def parse_simple(self):
        assigns, words, redirs = [], [], []
        while True:
            tok = self.peek()
            if tok is None:
                break
            if tok.kind == "op" and tok.text in REDIR_OPS:
                redirs.extend(self.parse_redirs())
                continue
            if tok.kind != "w":
                if (tok.kind == "op" and tok.text == "(" and len(words) == 1 and not assigns
                        and self.i + 1 < len(self.t) and self.t[self.i + 1].text == ")"):
                    self.i += 2
                    self.skip_nl()
                    return ("funcdef", words[0], self.parse_command())
                break
            if not words and _ASSIGN.match(tok.text):
                assigns.append(tok.text)
            else:
                words.append(tok.text)
            self.i += 1
        if not (assigns or words or redirs):
            raise _ParseError(f"unexpected {self.peek().text if self.peek() else 'end'!r}")
        return ("simple", assigns, words, redirs)

    def parse_if(self):
        self.expect_word("if")
        branches = []
        cond = self.parse_list(stop_words=("then",))
        self.expect_word("then")
        branches.append((cond, self.parse_list(stop_words=("elif", "else", "fi"))))
        while self.is_word("elif"):
            self.i += 1
            cond = self.parse_list(stop_words=("then",))
            self.expect_word("then")
            branches.append((cond, self.parse_list(stop_words=("elif", "else", "fi"))))
        other = None
        if self.is_word("else"):
            self.i += 1
            other = self.parse_list(stop_words=("fi",))
        self.expect_word("fi")
        return ("if", branches, other, self.parse_redirs())

    def parse_for(self, keyword: str = "for"):
        self.i += 1
        head = self.next()
        # words is None without `in`: the loop runs over "$@", unknown here.
        var, words = None, None
        if head.kind == "w":
            var = head.text
            self.skip_nl()
            if self.is_word("in"):
                self.i += 1
                words = []
                while self.peek() is not None and self.peek().kind == "w":
                    words.append(self.next().text)
        elif head.kind != "arith":
            raise _ParseError("for header")
        if self.is_op(";", "\n"):
            self.i += 1
        self.skip_nl()
        self.expect_word("do")
        body = self.parse_list(stop_words=("done",))
        self.expect_word("done")
        return ("loop", None, body, (var, words, head.text if head.kind == "arith" else None,
                                     keyword), self.parse_redirs())

    def parse_case(self):
        self.i += 1
        word = self.next()
        if word.kind != "w":
            raise _ParseError("case word")
        self.skip_nl()
        self.expect_word("in")
        self.skip_nl()
        arms = []
        while not self.is_word("esac"):
            if self.is_op("("):
                self.i += 1
            pats = [self.next().text]
            while self.is_op("|"):
                self.i += 1
                pats.append(self.next().text)
            self.expect_op(")")
            body = self.parse_list(stop_ops=(";;", ";&", ";;&"), stop_words=("esac",))
            term = self.next().text if self.is_op(";;", ";&", ";;&") else None
            self.skip_nl()
            arms.append((pats, body, term))
        self.i += 1
        return ("case", word.text, arms, self.parse_redirs())


def _literal(raw: str) -> str:
    """Quote removal without expansion (the shell word as written)."""
    try:
        parts = shlex.split(raw)
    except ValueError:
        return raw
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else raw


def _substitutions(raw: str) -> list:
    """Inner text of $(...), `...`, <(...), >(...) in a word; single quotes suppress them."""
    out, i, quote = [], 0, None
    while i < len(raw):
        c = raw[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "'" and quote is None:
            quote = "'"
        elif c == '"':
            quote = None if quote == '"' else '"'
        elif c == "`":
            try:
                j = _skip_backtick(raw, i + 1)
            except _ParseError:
                return out + [raw[i:]]  # unterminated: analyzed (and failed closed) as text
            out.append(re.sub(r"\\([\\`$])", r"\1", raw[i + 1:j - 1]))
            i = j
            continue
        elif raw.startswith("$(", i) or (quote is None and raw[i:i + 2] in ("<(", ">(")):
            try:
                j = _match_paren(raw, i + 2)
            except _ParseError:
                return out + [raw[i:]]
            out.append(raw[i + 2:j - 1])  # $((x)) yields "(x)": analyzed as a subshell
            i = j
            continue
        i += 1
    return out


class _State:
    # cwd is physical (realpath). lcwd is Bash's logical working directory (cd -L), which
    # differs after a cd through a symlink and decides where a later `cd ..` lands.
    __slots__ = ("cwd", "vars", "exported", "funcs", "lcwd")

    def __init__(self, cwd, variables, exported, funcs, lcwd=None):
        self.cwd, self.vars, self.exported, self.funcs = cwd, variables, exported, funcs
        self.lcwd = cwd if lcwd is None else lcwd

    def copy(self) -> "_State":
        return _State(self.cwd, dict(self.vars), set(self.exported), dict(self.funcs), self.lcwd)

    def key(self):
        return (self.cwd, self.lcwd, tuple(sorted(self.vars.items(), key=lambda kv: kv[0])),
                tuple(sorted(self.exported)), tuple(sorted((k, id(v)) for k, v in self.funcs.items())))


def _command_unknown(word: str, st) -> bool:
    """Whether ``word`` is unknown because of this command (not inherited terminal state)."""
    if re.search(r"\$\(|`|<\(|\$[@*#0-9]|\$\{[@*#0-9]", word):
        return True
    return any((m[1] or m[2]) in st.vars for m in _PARAM.finditer(word))


def _strings(node) -> list:
    """Every string in a parsed node (words, redirection targets, here-doc bodies)."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, (list, tuple)):
        return [s for part in node for s in _strings(part)]
    return []


def _unknown(states) -> _State:
    """Nothing is known about cwd or variables; known functions remain callable."""
    funcs = {}
    markers = set()
    for st in states:
        funcs.update(st.funcs)
        markers |= st.exported & _MARKERS  # shell options survive a loss of state
    return _State(None, {"IFS": None} if _NAMEREF in markers else {}, markers, funcs)


def _union(*groups) -> list:
    seen = {}
    for group in groups:
        for st in group:
            seen.setdefault(st.key(), st)
    out = list(seen.values())
    return [_unknown(out)] if len(out) > MAX_STATES else out


def _skip_wrappers(words: list[str]) -> list[str]:
    while words:
        if os.path.basename(words[0]) in WRAPPERS | {"coproc"}:
            wrapper, words = os.path.basename(words[0]), words[1:]
            # Do not guess option arity, environment changes, or coprocess scope.
            # The caller marks these unsupported wrappers uncertain for the whole text.
            if wrapper == "coproc" or (words and (
                    words[0].startswith("-") or _ASSIGN.match(words[0]))):
                raise _ParseError("unsupported wrapper options/environment")
            continue
        if os.path.basename(words[0]) == "timeout":
            # `timeout [--foreground|--preserve-status|-v] [-k D|--kill-after=D]
            # [-s SIG|--signal=SIG] DURATION CMD...`; any other shape is unsupported.
            words = words[1:]
            while words and words[0].startswith("-") and words[0] != "--":
                opt = words[0]
                if opt in _TIMEOUT_NOARG or opt.startswith(("--kill-after=", "--signal=")):
                    words = words[1:]
                elif opt in _TIMEOUT_ARG and len(words) > 1:
                    words = words[2:]
                elif re.fullmatch(r"-[ks].+", opt):
                    words = words[1:]  # -k5 / -sKILL
                else:
                    raise _ParseError("unsupported timeout option")
            if words and words[0] == "--":
                words = words[1:]
            if not words or not _TIMEOUT_DURATION.match(words[0]):
                raise _ParseError("unsupported timeout duration")
            words = words[1:]
            continue
        break
    return words


# Evidence-only helpers for the uncertainty fallback. They never make a command safer:
# each variant can only add textual restore evidence to the raw text.
_ANSI_C = re.compile(r"\$'((?:[^'\\]|\\[\s\S])*)'")
_ANSI_ESC = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|[0-7]{1,3}|c[\s\S]|[\s\S])")
_ANSI_SIMPLE = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
                "t": "\t", "v": "\v"}
_ASSIGN_TEXT = re.compile(
    r"(?:^|(?<=[\s;&|(`{]))([A-Za-z_][A-Za-z0-9_]*)=('[^']*'|\"(?:[^\"\\]|\\.)*\"|[^\s;&|()<>`]*)")
_PARAM = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_STASH_ARG_OPTS = {"-m", "--message", "--pathspec-from-file"}
_DYNAMIC_OP = re.compile(
    r"\bstash(?:\s+(?:-m|--message|--pathspec-from-file)\s+[^\s;&|]+"
    r"|\s+-(?!(?:m|-message|-pathspec-from-file)(?:\s|$))[^\s;&|]*)*\s+[^\s;&|]*[$`{*?\[]")
_DYNAMIC_SUB = re.compile(r"\bgit(?:\s+-C\s+[^\s;&|]+)*\s+[^\s;&|-][^\s;&|]*?[$`{*?\[]")


def _ansi_c_decode(body: str) -> str:
    def one(m):
        esc = m[1]
        if esc[0] in "xuU" and len(esc) > 1:
            code = int(esc[1:], 16)
            return chr(code) if code <= 0x10FFFF else ""
        if esc[0] in "01234567":
            return chr(int(esc, 8) & 0xFF)
        if esc[0] == "c" and len(esc) == 2:
            return chr(ord(esc[1]) & 0x1F)
        return _ANSI_SIMPLE.get(esc, esc)
    return _ANSI_ESC.sub(one, body)


def _restore_evidence(text: str) -> list[str]:
    """Restore ops (``pop``/``apply``) for which ``text`` holds any ``stash`` + op evidence.

    Order-independent: scalar assignments may name the op before the subcommand. Variants
    add ANSI-C decoding, literal ``NAME=value`` substitution (first and last binding), and
    quote/backslash removal, so ``"$A$B"``, ``st'a'sh`` and ``$'\\x73tash'`` count too.
    """
    texts = [text]
    if "\\\n" in text:  # backslash-newline joins words before tokenizing
        text = text.replace("\\\n", "")
        texts.append(text)
    if "$'" in text:
        texts.append(_ANSI_C.sub(lambda m: _ansi_c_decode(m[1]), text))
    if re.search(r"\\(x[0-9A-Fa-f]|[0-7]|u[0-9A-Fa-f])", text):  # printf / echo -e escapes
        texts.append(re.sub(
            r"\\(x[0-9A-Fa-f]{1,2}|0?[0-7]{1,3}|u[0-9A-Fa-f]{1,4})",
            lambda m: chr(int(m[1][1:], 16) if m[1][0] in "xu" else int(m[1], 8)), text))
    values: dict = {}
    for t in texts:
        for m in _ASSIGN_TEXT.finditer(t):
            values.setdefault(m[1], []).append(m[2])
    if values:
        for t in list(texts):
            for pick in (0, -1):
                texts.append(_PARAM.sub(
                    lambda m: values.get(m[1] or m[2], [m[0]])[pick], t))
    found = set()
    for t in texts:
        for flat in {re.sub(r"[\"'\\]", "", t), re.sub(r"[\"']", "", re.sub(r"\\[a-z]", " ", t))}:
            # (a printf/echo -e escape like `pop\n` ends the word: `\n` is not part of it)
            if re.search(r"\bstash\b", flat):
                found |= {op for op in REFUSE_OPS if re.search(rf"\b{op}\b", flat)}
            # Values the text does not hold (printf -v, read, $(...), {a,b}) are unknown, as
            # in ordinary analysis: an unknown stash op or git subcommand may be a restore.
            if _DYNAMIC_OP.search(flat) or _DYNAMIC_SUB.search(flat):
                found.add("pop")
    return sorted(found)


class _Analyzer:
    def __init__(self):
        self.calls: list = []
        self.steps = 0
        # Set when any fragment could not be analyzed (parse error, depth limit, unknown eval).
        # A fragment's boundaries come from the same matcher that may have failed (a quoted
        # `$(case a in a) ...)` ends at the pattern's `)`), so the rest of the original command
        # may never have been analyzed: the caller then fails closed on the whole text.
        self.uncertain = False
        # >0 while re-analyzing a fragment whose boundaries are uncertain (ANSI-C text,
        # unsupported wrapper argv): every git call found there gets an unknown repo, so the
        # re-analysis can only add refusals, never remove one.
        self.shadow = 0
        self.splitting = False
        self.joining = False
        self.home0 = os.environ.get("HOME")
        self.pipe_text: Optional[str] = None  # text of earlier commands of the current pipeline
        self.config_alias = False  # an earlier `git config alias.*` in this command

    def record(self, repo, sub, args, note: Optional[str] = None):
        """``note``: the unresolved shell word that alone made this a possible restore."""
        self.calls.append((None if self.shadow else repo, sub, list(args), note))

    def shadow_text(self, text: str, st: _State, depth: int):
        """Analyze ANSI-C text after decoding ``$'...'`` to single quotes, repo unknown."""
        decoded = _ANSI_C.sub(
            lambda m: "'" + _ansi_c_decode(m[1]).replace("'", "'\\''") + "'", text)
        if "$'" in decoded:
            return  # nested/unbalanced ANSI-C: the text fallback alone applies
        self.shadow += 1
        try:
            node = _Parser(_lex(decoded)).parse_top()
            self.exec(node, [st], depth + 1)
        except (_ParseError, RecursionError):
            pass  # already failed closed on the text
        finally:
            self.shadow -= 1

    def shadow_argv(self, words: list, st: _State, depth: int, as_git: bool = False,
                    tail: bool = False):
        """Unsupported wrapper or unknown command name: any suffix may be the real command.

        Each suffix of the argv is analyzed as a command with unknown repo (and, for an
        unknown command name that may itself expand to ``git``, the argv as git's). With
        ``tail`` an unknown word is appended (xargs input). Bounded: suffixes are not split
        again.
        """
        if self.splitting:
            return
        self.splitting = True
        self.shadow += 1
        extra = [_UNKNOWN_WORD] if tail else []
        try:
            for k in range(1, len(words)):
                self.simple(("simple", [], words[k:] + extra, []), st, depth, nested=True)
            if as_git:
                self.simple(("simple", [], ["git", *words[1:]], []), st, depth, nested=True)
        finally:
            self.shadow -= 1
            self.splitting = False

    def fail_closed(self, text: str):
        """Analysis gave up on ``text``: any restore it may contain runs in an unknown repo."""
        self.uncertain = True
        for op in _restore_evidence(text or ""):
            self.record(None, "stash", [op])

    def run_text(self, text: str, states: list, depth: int) -> list:
        if "\\\n" in text and not self.joining:
            # Bash removes backslash-newline before tokenizing, except inside single quotes
            # and comments. Analyze both the joined and the original text: the union can
            # only add refusals, whichever reading is right.
            self.joining = True
            try:
                joined = self._run_text(text.replace("\\\n", ""), states, depth)
            finally:
                self.joining = False
            return _union(joined, self._run_text(text, states, depth))
        return self._run_text(text, states, depth)

    def shadow_string(self, text: Optional[str], st: _State, depth: int):
        """A string a program or shell setting may run as shell code: analyze it, unknown repo.

        Only strings that may hold a git call (``git`` or a substitution) are parsed. A
        restore in such a string is refused even when it is only data (a PR body).
        """
        if (not text or self.splitting or depth >= MAX_DEPTH
                or not re.search(r"\bgit\b|\$\(|`", text)):
            return
        self.shadow += 1
        try:
            # Editors and pagers run `!cmd` / `:!cmd` filters: read those as the command.
            for variant in dict.fromkeys([text, re.sub(r"^[\s:!]+", "", text)]):
                try:
                    node = _Parser(_lex(variant)).parse_top()
                    self.exec(node, [self.child(st, {})], depth + 1)
                except (_ParseError, RecursionError):
                    self.evidence(variant)  # not shell syntax: its restore text still counts
        finally:
            self.shadow -= 1

    def shadow_value(self, raw: str, st: _State, depth: int):
        """An assignment value bash may run later (PS4, PROMPT_COMMAND, `${x@P}`, `(( x ))`).

        Substitutions in it already ran during analysis; only quoted literal text is new.
        """
        value = raw.split("=", 1)[-1]
        if not _substitutions(value):
            self.shadow_string(_literal(value), st, depth)

    def evidence(self, text: Optional[str], loose: bool = False):
        """Textual restores in input a shell reads without the guard seeing it as a script.

        ``loose``: text generated for a shell (`printf '… %s%s pop' sta sh | bash`) may build
        the ``stash`` word from pieces, so a restore op word alone counts.
        """
        ops = set(_restore_evidence(text or ""))
        if loose:
            flat = re.sub(r"[\"']", "", re.sub(r"\\[a-z]", " ", text or ""))
            ops |= {op for op in REFUSE_OPS if re.search(rf"\b{op}\b", flat)}
        for op in sorted(ops):
            self.record(None, "stash", [op])

    def _run_text(self, text: str, states: list, depth: int) -> list:
        if depth > MAX_DEPTH:
            self.fail_closed(text)
            return [_unknown(states)]
        if "$'" in text:
            # ANSI-C escapes change quote boundaries. Reject conservatively before
            # the ordinary quote matcher can hide an executable substitution; then also
            # re-analyze the decoded text with unknown repos, so ANSI-C text never
            # refuses less than the same command without it.
            self.fail_closed(text)
            for st in states:
                self.shadow_text(text, st, depth)
            return [_unknown(states)]
        try:
            node = _Parser(_lex(text)).parse_top()
        except (_ParseError, RecursionError):
            self.fail_closed(text)
            return [_unknown(states)]
        succ, fail = self.exec(node, states, depth)
        return _union(succ, fail)

    def isolated(self, text: str, st: _State, depth: int):
        self.run_text(text, [st], depth + 1)  # copy-on-write states: nothing leaks back

    def exec(self, node, states, depth):
        """Return (success states, failure states) after ``node``."""
        if not states:
            return [], []
        kind = node[0]
        if kind == "list":
            cur, succ, fail = states, states, states
            for item, sep in node[1]:
                if sep == "&":
                    self.exec(item, cur, depth)  # background job: a subshell
                    succ = fail = cur
                else:
                    succ, fail = self.exec(item, cur, depth)
                    cur = _union(succ, fail)
            return succ, fail
        if kind == "andor":
            succ, fail = self.exec(node[1][0], states, depth)
            for op, part in zip(node[2], node[1][1:]):
                if op == "&&":
                    s2, f2 = self.exec(part, succ, depth)
                    succ, fail = s2, _union(fail, f2)
                else:
                    s2, f2 = self.exec(part, fail, depth)
                    succ, fail = _union(succ, s2), f2
            return succ, fail
        if kind == "pipeline":
            negated, cmds = node[1], node[2]
            if len(cmds) == 1:
                succ, fail = self.exec(cmds[0], states, depth)
            else:
                saved = self.pipe_text
                try:
                    for k, cmd in enumerate(cmds):  # every element runs in its own subshell
                        if k:  # a shell reading this pipe sees the earlier commands' output
                            self.pipe_text = " ".join(filter(None, [saved, *_strings(cmds[:k])]))
                        self.exec(cmd, states, depth)
                finally:
                    self.pipe_text = saved
                succ = fail = states
            return (fail, succ) if negated else (succ, fail)
        if kind == "subshell":
            self.redirs(node[2], states, depth)
            self.exec(node[1], states, depth)
            return states, states
        if kind == "group":
            self.redirs(node[2], states, depth)
            return self.exec(node[1], states, depth)
        if kind == "arith":
            self.redirs(node[2], states, depth)
            for st in states:
                self.isolated(node[1], st, depth)
            return states, states
        if kind == "if":
            outs, cur = [], states
            for cond, body in node[1]:
                cs, cf = self.exec(cond, cur, depth)
                outs.append(_union(*self.exec(body, cs, depth)))
                cur = cf
            outs.append(_union(*self.exec(node[2], cur, depth)) if node[2] is not None else cur)
            self.redirs(node[3], states, depth)
            out = _union(*outs)
            return out, out
        if kind == "loop":
            return self.loop(node, states, depth)
        if kind == "case":
            for st in states:
                for inner in _substitutions(node[1]):
                    self.isolated(inner, st, depth)
            outs, prev = [states], None
            for _pats, body, term in node[2]:
                entry = states if prev is None else _union(states, prev)
                after = _union(*self.exec(body, entry, depth))
                outs.append(after)
                prev = after if term in (";&", ";;&") else None
            self.redirs(node[3], states, depth)
            out = _union(*outs)
            return out, out
        if kind == "funcdef":
            if node[1] == "command_not_found_handle" and not self.splitting:
                # Bash runs it implicitly for any unknown command: analyze it, unknown repo.
                self.shadow += 1
                try:
                    self.exec(node[2], [_unknown(states)], depth + 1)
                finally:
                    self.shadow -= 1
            out = []
            for st in states:
                new = st.copy()
                new.funcs[node[1]] = node[2]
                out.append(new)
            return out, out
        if kind == "simple":
            succ, fail = [], []
            for st in states:
                s, f = self.simple(node, st, depth)
                succ += s
                fail += f
            return _union(succ), _union(fail)
        raise _ParseError(f"unknown node {kind}")

    def loop(self, node, states, depth):
        cond, body, forspec, redirs = node[1], node[2], node[3], node[4]
        self.redirs(redirs, states, depth)
        pre = states
        if forspec:
            var, words, arith, keyword = forspec
            for st in states:
                for word in words or ():
                    for inner in _substitutions(word):
                        self.isolated(inner, st, depth)
                if arith:
                    self.isolated(arith, st, depth)
            if var:
                pre = []
                for st in states:
                    for value in self.loop_values(keyword, var, words, st):
                        new = st.copy()
                        new.vars[var] = value
                        pre.append(new)
                pre = _union(pre)

        def once(sts):
            cs, cf = self.exec(cond, sts, depth) if cond is not None else (sts, sts)
            return cf, _union(*self.exec(body, cs, depth))

        exit1, after1 = once(pre)
        first = _union(pre, exit1, after1)
        exit2, after2 = once(first)
        out = _union(first, exit2, after2)
        known = {st.key() for st in first}
        if any(st.key() not in known for st in out):
            out = _union(out, [_unknown(out)])  # did not converge in two iterations
        return out, out

    @staticmethod
    def loop_values(keyword, var, words, st) -> list:
        """Values ``var`` may hold in the body of ``for var in words``.

        Each word that expands (scalars, quotes, tilde) to exactly one known word gives one
        value. Anything else (no ``in`` list, a glob, brace expansion, a substitution, an
        unquoted expansion that may split, `select`'s user input) leaves it unknown.
        """
        if keyword != "for" or not _NAME.match(var or "") or not words:
            return [None]
        values = [_expand_word(word, st.vars) for word in words]
        return [None] if None in values else list(dict.fromkeys(values))

    def redirs(self, redirs, states, depth):
        for _op, target, body, quoted in redirs:
            for st in states:
                for inner in _substitutions(target):
                    self.isolated(inner, st, depth)
                if body and not quoted:
                    for inner in _substitutions(body):
                        self.isolated(inner, st, depth)

    def child(self, st: _State, prefix: dict) -> _State:
        """A child shell sees exported variables only; others are unknown, not inherited."""
        variables = {k: st.vars.get(k) for k in st.exported if k in st.vars}
        variables.update(prefix)
        variables["PWD"] = st.cwd
        return _State(st.cwd, variables, (set(st.exported) - _MARKERS) | set(prefix), {}, st.lcwd)

    def simple(self, node, st: _State, depth: int, nested: bool = False):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise _Budget()
        _kind, assigns, words, redirs = node
        if not nested:  # a shadow re-analysis of the same words: already inspected
            for raw in assigns + words:
                for inner in _substitutions(raw):
                    self.isolated(inner, st, depth)
            self.redirs(redirs, [st], depth)
        if not words:
            new = st.copy()
            for raw in assigns:
                self.assign(new, raw)
                # PS4, PROMPT_COMMAND, a `${x@P}` or arithmetic operand may run the value.
                self.shadow_value(raw, st, depth)
            return [new], [new]
        prefix = {}
        scratch = st.copy()
        if _KEYWORD in st.exported and len(words) > 1:
            # `set -k`: assignment words anywhere in argv go to the command's environment.
            assigns = assigns + [w for w in words[1:] if _ASSIGN.match(w)]
            words = [words[0]] + [w for w in words[1:] if not _ASSIGN.match(w)]
        for raw in assigns:  # temporary environment of this command only
            name = self.assign(scratch, raw)
            prefix[name] = scratch.vars[name]
            self.shadow_value(raw, st, depth)
        lit = [_literal(w) for w in words]
        # Bash looks a function up by the expanded command name before builtins and PATH
        # (non-POSIX mode: even `command`/`exec`/`env` can be shadowed). Wrapper stripping
        # comes only after that; a wrapped name (`command git`, `env git`) bypasses functions.
        first = _expand_word(words[0], st.vars)
        if first is not None and "/" not in first and first in st.funcs:
            if st.funcs[first][0] == "hashed":
                # `hash -p PATH NAME`: NAME runs PATH, unless the hash table was cleared
                # (`hash -r`, PATH=..., set +h). Analyze both readings; keep every refusal.
                path = st.funcs[first][1]
                head = _UNKNOWN_WORD if path is None else shlex.quote(path)
                s1, f1 = self.simple((_kind, assigns, [head, *words[1:]], redirs), st, depth,
                                     nested=True)
                plain = st.copy()
                plain.funcs.pop(first, None)
                s2, f2 = self.simple((_kind, assigns, words, redirs), plain, depth, nested=True)
                return _union(s1, s2), _union(f1, f2)
            if depth >= MAX_DEPTH:
                self.fail_closed(repr(st.funcs[first]))
                unknown = _unknown([st])
                return [unknown], [unknown]
            return self.exec(st.funcs[first], [st], depth + 1)
        wrapper_words = [_expand_word(w, st.vars) or _literal(w) for w in words]
        try:
            kept = _skip_wrappers(wrapper_words)
        except _ParseError:
            # Expanded words carry scalar evidence that the raw text may only hold split.
            self.fail_closed(" ".join(words + wrapper_words))
            self.shadow_argv(words, st, depth)
            unknown = _unknown([st])
            return [unknown], [unknown]
        raw = words[len(lit) - len(kept):]
        lit = lit[len(lit) - len(kept):]
        if not lit:
            return [st], [st]
        name = _expand_word(raw[0], st.vars)
        if name is None:
            # Unknown command name: it may be git itself, or a wrapper of a later word.
            self.fail_closed(" ".join(words))
            self.shadow_argv(raw, st, depth, as_git=True)
            return [st], [st]
        cmd = os.path.basename(name)
        if cmd in SHELLS:
            child = self.child(st, prefix)
            mode, idx = _shell_script(lit)
            _shell_flags(lit[:idx] if idx else lit, child, invocation=True)
            env = set(prefix) | st.exported
            if ({"BASH_ENV", "ENV"} & env or {"--rcfile", "--init-file"} & set(lit)
                    or (("HOME" in prefix or st.vars.get("HOME") != self.home0)
                        and any(re.fullmatch(r"-[A-Za-z]*[il][A-Za-z]*|--login", w)
                                for w in lit[1:idx or len(lit)]))):
                self.fail_closed("")  # a startup file (perhaps written earlier) runs first
            if mode == "c":
                script = _expand_word(raw[idx], st.vars)
                self.run_text(lit[idx] if script is None else script, [child], depth + 1)
            elif mode == "file" or not any(r[0] in ("<<", "<<-", "<<<") for r in redirs):
                # A script file (perhaps written earlier in this command), a pipe or a process
                # substitution the guard does not read: whole-command restore evidence decides.
                self.fail_closed("")
            # Text piped in from earlier pipeline commands counts even next to a here-doc,
            # which may feed another fd (`… | bash 3<<<x`); with -c it is data, not script.
            if mode != "c":
                self.evidence(self.pipe_text, loose=True)
            for op, target, body, _quoted in redirs:  # bash <<EOF / bash <<< "script"
                if op in ("<<", "<<-") and body:
                    self.run_text(body, [child], depth + 1)
                elif op == "<<<":
                    text = _expand_word(target, st.vars)
                    self.run_text(_literal(target) if text is None else text, [child], depth + 1)
            return [st], [st]
        if cmd in ("cd", "pushd", "popd"):
            # Prefix CDPATH/HOME/OLDPWD affect this builtin, but must not leak as bindings.
            if prefix:
                scratch = st.copy()
                scratch.vars.update(prefix)
                succ, fail = self.chdir(cmd, raw, lit, scratch)
                for result in succ + fail:
                    for name in prefix:
                        if name in ("OLDPWD", "PWD"):
                            result.vars[name] = None  # rebound by cd under a temp binding
                        elif name in st.vars:
                            result.vars[name] = st.vars[name]
                        else:
                            result.vars.pop(name, None)
                return succ, fail
            return self.chdir(cmd, raw, lit, st)
        if cmd == "unset":
            new = st.copy()
            for word in lit[1:]:
                if _NAME.match(word):
                    new.vars[word] = None
                    if new.funcs.get(word, ("hashed",))[0] != "hashed":
                        new.funcs.pop(word, None)  # unset does not clear the hash table
            return [new], [new]
        if cmd in DECLARE:
            return self.declare(cmd, raw, lit, st)
        if cmd in MUTATORS:
            new = st.copy()
            if cmd in ("mapfile", "readarray"):
                for k, w in enumerate(lit[1:-1], 1):  # -C callback: shell code it runs
                    if w == "-C" or (w.startswith("-") and w.endswith("C")):
                        self.shadow_string(lit[k + 1], st, depth)
            for word in lit[1:]:
                match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:$|[=\[+])", word)
                if match:
                    new.vars[match[1]] = None
            return [new], [new]
        if cmd == "eval":
            parts = [_expand_word(w, st.vars) for w in raw[1:]]
            if any(p is None for p in parts):
                self.fail_closed(" ".join(lit[1:]))
                unknown = _unknown([st])
                return [unknown], [unknown]
            out = self.run_text(" ".join(parts), [st], depth + 1)
            return out, out
        if cmd in ("source", "."):
            # The sourced file is not read: whole-command restore evidence decides.
            self.fail_closed("")
            unknown = _unknown([st])
            return [unknown], [unknown]
        if cmd == "exit":
            return [], []
        if cmd == "trap":
            args = [w for w in lit[1:] if w not in ("--", "-p", "-l")]
            if args and args[0] != "-":
                self.run_text(args[0], [_unknown([st])], depth + 1)  # runs later, cwd unknown
            return [st], [st]
        if cmd in RUNNERS:
            self.shadow_argv(raw, st, depth, tail=cmd in _APPENDING_RUNNERS)
            self.shadow += 1
            try:  # `su -c 'cmd'`, `watch 'cmd'`, `script -c 'cmd'` run a shell string
                for word in raw[1:]:
                    text = _expand_word(word, st.vars)
                    if text and any(c.isspace() or c in ";&|" for c in text):
                        self.run_text(text, [self.child(st, prefix)], depth + 1)
            finally:
                self.shadow -= 1
            return [st], [st]
        if cmd in ("set", "shopt"):
            new = st.copy()
            _shell_flags(lit, new, shopt=cmd == "shopt")
            return [new], [new]
        if cmd == "hash":
            # `hash -p PATH NAME` runs PATH for NAME (a function of that name still wins).
            new, path, names, k = st.copy(), False, [], 1
            while k < len(raw):
                if lit[k] == "-p" and k + 1 < len(raw):
                    path, k = _expand_word(raw[k + 1], st.vars), k + 2
                    continue
                if lit[k].startswith("-p") and len(lit[k]) > 2:
                    path = _expand_word(raw[k][2:], st.vars)  # attached: -p/usr/bin/git
                elif path is not False and not lit[k].startswith("-"):
                    names.append(lit[k])
                k += 1
            for fname in names if path is not False else []:
                if new.funcs.get(fname, ("hashed",))[0] == "hashed":
                    new.funcs[fname] = ("hashed", path)
            return [new], [new]
        if cmd == "git" or (cmd.startswith("git-") and len(cmd) > 4):
            expanded = [_expand_word(w, st.vars) for w in raw]
            if any(w is None for w in expanded):
                self.fail_closed(" ".join(raw))
            if cmd != "git":  # dashed form: `git-stash pop` is `git stash pop`
                raw, expanded = ["git", cmd[4:], *raw[1:]], ["git", cmd[4:], *expanded[1:]]
            self.git(raw, expanded, st, prefix, depth)
            return [st], [st]
        if cmd not in _INERT:
            # Unmodeled program: if its argv may name git or a shell, any suffix may be the
            # command it runs (`prlimit git …`, `nix develop -c git …`, `busybox sh -c …`).
            # An unknown word counts when this command made it unknown (a substitution, a
            # variable it assigned, a positional); inherited terminal state is out of scope.
            for w in raw[1:]:
                word = _expand_word(w, st.vars)
                base = os.path.basename(word) if word is not None else None
                if (base in SHELLS or (base is not None and re.fullmatch(r"git(?:-[\w-]+)?", base))
                        or (base is None and _command_unknown(w, st))):
                    self.shadow_argv(raw, st, depth)
                    break
            # A string argument may be shell code it runs (`tmux new 'git …'`,
            # `make --eval`, `vim -c '!…'`, `npx -c`).
            for w in raw[1:]:
                self.shadow_string(_expand_word(w, st.vars) or _literal(w), st, depth)
        if _CMDS in st.exported and cmd not in BUILTINS_NOT_HASHED:
            self.shadow_argv(raw, st, depth, as_git=True)  # BASH_CMDS may map it to git
        if re.sub(r"[\d.]+$", "", cmd) in _INTERPRETERS or (cmd == "sed" and any(
                re.search(r"(?:^|[;\n{}])\s*e(?:\s|;|$)|^s(.).*\1.*\1[gpwIiMm0-9]*e", w)
                for w in lit[1:])):
            # Program text in argv, here-docs or a pipe may run git (`sed 's/^//e'`).
            self.evidence(" ".join([*raw[1:], *(r[2] or r[1] for r in redirs)]))
            self.evidence(self.pipe_text, loose=True)
        return [st], [st]

    def assign(self, st: _State, raw: str) -> str:
        match = _ASSIGN.match(raw)
        assert match is not None
        name = match[1]
        if match[2] or match[3]:
            st.vars[name] = None  # arrays and += are not modeled
        else:
            st.vars[name] = _expand_word(raw[match.end():], st.vars)
        if _ALLEXPORT in st.exported:
            st.exported.add(name)  # `set -a`: assignments after it are exported
        if name == "BASH_CMDS":
            st.exported.add(_CMDS)  # BASH_CMDS[name]=path: any command name may run git
        return name

    def chdir(self, cmd, raw, lit, st):
        new = st.copy()
        args = list(zip(raw[1:], lit[1:]))
        flags = [l for _r, l in args if l.startswith("-") and l != "-"]
        targets = [(r, l) for r, l in args if not (l.startswith("-") and l != "-")]
        if cmd == "popd" or (cmd == "pushd" and (not targets or re.match(r"^[+-][0-9]+$", targets[0][1]))):
            if "-n" in flags:
                return [st], [st]  # stack-only change
            new.cwd = None  # the directory stack is not modeled
            new.vars["PWD"] = None
            return [new], [new]
        if cmd == "pushd" and "-n" in flags:
            return [st], [st]
        if any(not re.fullmatch(r"-[LPe@]+", f) for f in flags if f != "--"):
            target = None  # unknown option: the target is not inferred
        elif not targets:
            target = st.vars.get("HOME")
        elif len(targets) > 1:
            target = None
        elif targets[0][1] == "-":
            target = st.vars.get("OLDPWD")
        else:
            target = _expand_word(targets[0][0], st.vars)
        # CDPATH may select a different directory before the cwd-relative candidate.
        if (target and not os.path.isabs(target) and target.split("/")[0] not in (".", "..")
                and "CDPATH" in st.vars and st.vars["CDPATH"] != ""):
            target = None
        physical = "".join(f.lstrip("-") for f in flags if f != "--").replace("e", "").replace("@", "")
        physical = physical.endswith("P")
        new.cwd = _resolve(target, st.cwd)
        new.lcwd = new.cwd
        if new.cwd is not None and not physical:
            # cd -L (default) resolves `..` textually against the logical directory, the
            # kernel (and realpath) after following symlinks. Where they differ, the
            # resulting directory is not inferred; else track the logical path.
            base = st.lcwd if not os.path.isabs(target) else "/"
            logical = os.path.normpath(os.path.join(base, target)) if base else None
            if logical is None or os.path.realpath(logical) != new.cwd:
                new.cwd = new.lcwd = None
            else:
                new.lcwd = logical
        new.vars["OLDPWD"] = st.lcwd
        new.vars["PWD"] = new.lcwd
        if new.cwd is not None and os.path.isdir(new.cwd):
            return [new], []  # an existing directory: cd succeeds
        return [new], [st]

    def declare(self, cmd, raw, lit, st):
        new, flags, unexport = st.copy(), set(), False
        for r, l in zip(raw[1:], lit[1:]):
            if l[:1] in "-+" and not _ASSIGN.match(l):
                flags |= set(l[1:])
                unexport |= cmd == "export" and l.startswith("-") and "n" in l
                continue
            match = _ASSIGN.match(r)
            name = match[1] if match else (l if _NAME.match(l) else None)
            if name is None:
                continue
            if "n" in flags and cmd != "export" and (
                    not match or not re.fullmatch(r"[A-Za-z_]\w*", _literal(r[match.end():]))
                    or _literal(r[match.end():]) in _NAMEREF_TAINT + ("PATH", "BASH_CMDS")
                    or _literal(r[match.end():]).startswith(("GIT_", "BASH"))):
                _taint_nameref(new)  # an assignment through the ref may set that variable
            if match:
                unmodeled = match[2] or match[3] or (flags & _UNMODELED_FLAGS and cmd != "export")
                new.vars[name] = None if unmodeled else _expand_word(r[match.end():], st.vars)
                if _ALLEXPORT in new.exported:
                    new.exported.add(name)
            elif cmd == "local" or (flags & _UNMODELED_FLAGS and cmd != "export"):
                new.vars[name] = None
            if unexport:
                new.exported.discard(name)
            elif cmd == "export" or "x" in flags:
                new.exported.add(name)
        return [new], [new]

    def git(self, raw, ex, st, prefix, depth=MAX_DEPTH):
        """Record the git call; ``ex`` is the expanded argv, None where a word is unknown.

        Unknown words, unknown global options (unknown arity) and config-injected aliases
        make the subcommand unknown: then any unknown word or restore evidence after that
        point is a possible restore in an unknown repo. Never trusts an unknown boundary.
        """
        repo, i = st.cwd, 1
        env = set(prefix) | st.exported
        if _ALLEXPORT in env:
            env |= set(st.vars)  # conservative: may include names set before `set -a`
        if _GIT_EXEC_ENV & env:
            for name in _GIT_EXEC_ENV & env:  # an editor/pager/diff program Git may run
                value = prefix.get(name, st.vars.get(name))
                if value is None:
                    self.fail_closed("")
                self.shadow_string(value, st, depth)
        alternate = bool(_GIT_ALT_ENV & env) or _NAMEREF in env
        # Config read from another file (include, GIT_CONFIG_GLOBAL, a changed HOME) may
        # define aliases, like `-c alias.x=...`; so may an earlier `git config alias.*`.
        aliased = (bool(_GIT_CONFIG_ENV & env) or any(n.startswith("GIT_CONFIG_KEY_") for n in env)
                   or "HOME" in prefix or st.vars.get("HOME") != self.home0
                   or self.config_alias or _NAMEREF in env)
        if "config" in ex:
            values = ex[ex.index("config") + 1:]
            keys = [w.lower() for w in values if w]
            if any(k.startswith(_CONFIG_ALIAS_KEYS) for k in keys):
                text = " ".join(w or "$?" for w in values)
                if None in values or "!" in text or "include" in text or _restore_evidence(
                        text) or re.search(r"\bstash\b", text):
                    self.config_alias = True  # later git calls in this command may use it
            if None in values or "core.hookspath" in keys:
                self.config_alias = True
                self.fail_closed("")  # hook files (perhaps written earlier) may run later
            for k, w in enumerate(values):
                if w and _config_runs(w.lower()) and k + 1 < len(values):
                    self.shadow_string(values[k + 1], st, depth)  # a stored program
        while i < len(ex):
            opt = ex[i]
            if opt is None or not opt.startswith("-"):
                break
            if opt in _GIT_ARG_OPTS:
                if i + 1 >= len(ex):
                    return
                value = ex[i + 1]
                if value is None and _splittable(raw[i + 1]):
                    break  # may split into more words: later positions are unknown
                if opt == "-C":
                    repo = _resolve(value, repo)
                elif opt in ("--git-dir", "--work-tree"):
                    alternate = True
                elif opt in ("-c", "--config-env"):
                    key = (value or "").split("=", 1)[0].lower()
                    aliased |= value is None or key.startswith(_CONFIG_ALIAS_KEYS)
                    alternate |= key in ("core.worktree", "core.bare")
                    if value is None or key == "core.hookspath":
                        self.fail_closed("")  # hook files (perhaps written earlier) may run
                    self.shadow_string(value.split("=", 1)[-1] if value else None, st, depth)
                i += 2
            elif opt.startswith(("--git-dir=", "--work-tree=")) or opt == "--bare":
                alternate = True  # alternate repository selection is not inferred
                i += 1
            elif opt.startswith("--config-env="):
                aliased |= opt[len("--config-env="):].lower().startswith(_CONFIG_ALIAS_KEYS)
                i += 1
            elif opt in _GIT_FLAG_OPTS or opt.startswith(_GIT_EQ_OPTS):
                i += 1
            else:
                break  # unknown global option: its arity, hence the subcommand, is unknown
        if alternate:
            # Sticky: a later (even absolute) -C changes cwd, not an explicit git-dir/work-tree.
            repo = None
        if i >= len(ex):
            return
        # An alias cannot shadow a builtin command, but any other name may be an alias.
        known = ex[i] is not None and not ex[i].startswith("-") and (
            not aliased or ex[i] in _GIT_BUILTINS)
        if not known:
            rest = ex[i:]
            text = " ".join(w for w in rest if w is not None)
            if any(w is None for w in rest) or aliased or _restore_evidence(text) or (
                    REFUSE_OPS & set(rest)):
                note = None
                if not (aliased or REFUSE_OPS & set(rest) or re.search(r"\bstash\b", " ".join(raw))
                        or _restore_evidence(" ".join(raw[i:]))):
                    # Only an unknown word (no stash text): name it for the refusal message.
                    note = next((raw[j] for j in range(i, len(ex)) if ex[j] is None), None)
                self.record(None, "stash", ["pop"], note)
            return
        sub, args = ex[i], ex[i + 1:]
        if sub != "stash" or None not in args:
            self.record(repo, sub, args)
            return
        # Unknown stash argv: an unknown first positional may be the op; an unknown ref
        # may name another entry. Either way the repo/ref is not inferred.
        first, k = "", 0
        while k < len(args):
            if args[k] in _STASH_ARG_OPTS:
                k += 2  # `git stash -m "$MSG"` is a push; its message is not the op
                continue
            if args[k] is None or not args[k].startswith("-"):
                first = args[k]
                break
            k += 1
        if first is None or first in REFUSE_OPS:
            self.record(None, "stash", ["pop" if first is None else first])
        else:
            self.record(repo, sub, [a for a in args if a is not None])


def _git_calls(cmd: str, cwd: Optional[str], depth: int = 0):
    """Yield (repo, subcommand, args) for every git call the command may make."""
    for repo, sub, args, _note in _git_calls_noted(cmd, cwd, depth):
        yield repo, sub, args


def _git_calls_noted(cmd: str, cwd: Optional[str], depth: int = 0):
    """Yield (repo, subcommand, args, note) for every git call the command may make.

    ``note`` is the unresolved shell word behind a synthesized possible restore, else None.

    Bounded static analysis, not a shell interpreter: nothing is executed. Only HOME and
    TMPDIR are borrowed from the process environment; other variables of a persistent
    terminal may differ and stay unknown. ``repo`` is None when it cannot be resolved.
    """
    analyzer = _Analyzer()
    variables = {key: os.environ.get(key) for key in ("HOME", "TMPDIR")}
    variables["PWD"] = cwd
    state = _State(cwd, variables, {"HOME", "TMPDIR", "PWD", "OLDPWD"}, {})
    if re.search(r"BASH_CMDS|\balias\b", cmd or ""):
        # BASH_CMDS (set by declare/read/printf -v too) or an alias may map any name to git.
        state.exported.add(_CMDS)
    try:
        analyzer.run_text(cmd or "", [state], depth)
    except Exception:  # budget, recursion, or an analyzer defect: never fail open
        analyzer.fail_closed(cmd or "")
    if analyzer.uncertain:  # a fragment failed: its text may not be all that went unanalyzed
        analyzer.fail_closed(cmd or "")
    yield from analyzer.calls


def stash_op(args: list[str]) -> Optional[str]:
    """``push`` for no-arg / ``git stash -q``, the subcommand name, or None for ``--help``."""
    if "-h" in args or "--help" in args:
        return None
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return "push"
    if positional[0] in STASH_SUBS:
        return positional[0]
    return None


def stash_ref(args: list[str]) -> str:
    for arg in args:
        if arg.startswith("stash@") or arg.startswith("refs/stash"):
            return arg
    return "stash@{0}"


def _stash_invocations(cmd: str, cwd: Optional[str]):
    """Yield (repo, op, args, note) for every stash call (see ``_git_calls_noted``)."""
    base = _resolve(os.path.expanduser(cwd or os.getcwd()), os.getcwd())
    for repo, sub, args, note in _git_calls_noted(cmd, base):
        if sub != "stash":
            continue
        op = stash_op(args)
        if op:
            yield repo, op, args, note


def _safe_recipe() -> str:
    return (
        'Save a patch instead: `git diff -- <files> > "$TMPDIR/fix.patch" && git checkout -- <files>`; '
        "rerun the tests; `git apply \"$TMPDIR/fix.patch\"`. Or `git worktree add --detach` a baseline. "
        "Do not work around this guard."
    )


def warning_text(repo: str, count: int) -> str:
    shown = repo.replace(str(os.path.expanduser("~")), "~", 1)
    return (
        f"{MARK} `refs/stash` is one ref shared by all {count} worktrees of {shown}. "
        "A stash/pop here can apply another worker's diff and hand them yours. " + _safe_recipe()
    )


def warning_for(cmd: str, cwd: Optional[str] = None) -> Optional[str]:
    """Warning suffix for a stash command in a multi-worktree repo, else None."""
    if _bypassed(cmd):
        return None
    seen = set()
    for repo, _op, _args, _note in _stash_invocations(cmd, cwd):
        if repo is None or repo in seen:
            continue
        seen.add(repo)
        count = worktree_count(repo)
        if count > 1:
            return warning_text(repo, count)
    return None


def refuse_reason(cmd: str, cwd: Optional[str] = None) -> Optional[str]:
    """Why ``pop``/``apply`` must be blocked, or None to let it run."""
    if _bypassed(cmd):
        return None
    seen = set()
    calls = list(_stash_invocations(cmd, cwd))
    # Every unresolved-repo restore comes only from an unknown word, with no stash text.
    notes = [note for repo, op, _a, note in calls if repo is None and op in REFUSE_OPS]
    if notes and all(notes):
        word = notes[0]
        return (
            f"STASH WORKTREE GUARD: blocked: the unquoted word `{word}` has an unknown value, so it "
            "may split into several words and shift git's subcommand (it could become "
            f"`stash pop`). Quote it (`\"{word}\"`) or assign it a literal earlier in the same "
            "command."
        )
    for repo, op, args, _note in calls:
        if op not in REFUSE_OPS:
            continue
        key = (repo, op, stash_ref(args))
        if key in seen:
            continue
        seen.add(key)
        if repo is None:
            return (
                f"STASH WORKTREE GUARD: blocked `git stash {op}`: cannot resolve the repository "
                "without executing shell expressions. " + _safe_recipe()
            )
        count = worktree_count(repo)
        if count == 0:
            return (
                f"STASH WORKTREE GUARD: blocked `git stash {op}`: cannot resolve/read the repository "
                f"at {repo!r}. " + _safe_recipe()
            )
        if count == 1:
            continue
        ref = stash_ref(args)
        subject = stash_subject(repo, ref)
        if subject is None:
            continue  # no such stash: pop fails on its own, nothing to swap
        branch = stash_branch(subject)
        here = current_branch(repo)
        if branch == here:
            continue
        shown = repo.replace(str(os.path.expanduser("~")), "~", 1)
        whose = f"branch `{branch}`" if branch else "a subject that does not name a branch"
        return (
            f"STASH WORKTREE GUARD: blocked `git stash {op}` in {shown}. "
            f"The stash `{ref}` says {whose} ({subject!r}), but this worktree is on `{here}`. "
            f"`refs/stash` is shared by all {count} worktrees, so this would apply another worker's diff. "
            + _safe_recipe()
        )
    return None


def check_python(code: str, cwd: Optional[str] = None) -> Optional[str]:
    """Refuse path for shell strings and git argv lists inside Python (execute_code)."""
    if _bypassed(code):
        return None
    for lit in re.findall(r"""(?:"([^"\n]*\bgit\b[^"\n]*stash[^"\n]*)"|'([^'\n]*\bgit\b[^'\n]*stash[^'\n]*)')""", code or ""):
        why = refuse_reason(lit[0] or lit[1], cwd)
        if why:
            return why
    for match in re.finditer(r"\[\s*[\"']git[\"'](?:\s*,\s*[\"'][^\"']*[\"'])+\s*\]", code or ""):
        words = re.findall(r"[\"']([^\"']*)[\"']", match.group(0))
        why = refuse_reason(shlex.join(words), cwd)
        if why:
            return why
    return None
