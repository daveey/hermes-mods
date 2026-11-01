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
Unsupported state is unknown, and unknown never infers a safe repo.

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
WRAPPERS = {"sudo", "env", "command", "exec", "time", "nice", "nohup", "stdbuf"}
SHELLS = {"bash", "sh", "zsh", "dash"}
_STASH_BRANCH = re.compile(r"^(?:WIP on|On|index on) (.+?): ")
_TIMEOUT_FLAG = re.compile(r"^-[a-zA-Z]")


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
            if value is None or (quote is None and any(c.isspace() for c in value)):
                return None
            out.append(value)
            i += len(match[0])
            continue
        elif (char == "`" and quote != "'") or (quote is None and char in "*?[]()"):
            return None
        else:
            out.append(char)
        i += 1
    return None if quote else "".join(out)


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
                return self.parse_for()
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

    def parse_for(self):
        self.i += 1
        head = self.next()
        var, words = None, []
        if head.kind == "w":
            var = head.text
            self.skip_nl()
            if self.is_word("in"):
                self.i += 1
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
        return ("loop", None, body, (var, words, head.text if head.kind == "arith" else None),
                self.parse_redirs())

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
    __slots__ = ("cwd", "vars", "exported", "funcs")

    def __init__(self, cwd, variables, exported, funcs):
        self.cwd, self.vars, self.exported, self.funcs = cwd, variables, exported, funcs

    def copy(self) -> "_State":
        return _State(self.cwd, dict(self.vars), set(self.exported), dict(self.funcs))

    def key(self):
        return (self.cwd, tuple(sorted(self.vars.items(), key=lambda kv: kv[0])),
                tuple(sorted(self.exported)), tuple(sorted((k, id(v)) for k, v in self.funcs.items())))


def _unknown(states) -> _State:
    """Nothing is known about cwd or variables; known functions remain callable."""
    funcs = {}
    for st in states:
        funcs.update(st.funcs)
    return _State(None, {}, set(), funcs)


def _union(*groups) -> list:
    seen = {}
    for group in groups:
        for st in group:
            seen.setdefault(st.key(), st)
    out = list(seen.values())
    return [_unknown(out)] if len(out) > MAX_STATES else out


def _skip_wrappers(words: list[str]) -> list[str]:
    while words:
        if words[0] in WRAPPERS:
            wrapper, words = words[0], words[1:]
            if wrapper == "env":
                while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
                    words = words[1:]
            continue
        if os.path.basename(words[0]) == "timeout":
            words = words[1:]
            while words and (_TIMEOUT_FLAG.match(words[0]) or re.match(r"^[0-9]", words[0])):
                # `timeout 60`, `timeout -k 5 60`, `timeout --signal=TERM 60`
                if words[0] in ("-k", "--kill-after", "-s", "--signal") and len(words) > 1:
                    words = words[2:]
                else:
                    words = words[1:]
            continue
        break
    return words


class _Analyzer:
    def __init__(self):
        self.calls: list = []
        self.steps = 0
        # Set when any fragment could not be analyzed (parse error, depth limit, unknown eval).
        # A fragment's boundaries come from the same matcher that may have failed (a quoted
        # `$(case a in a) ...)` ends at the pattern's `)`), so the rest of the original command
        # may never have been analyzed: the caller then fails closed on the whole text.
        self.uncertain = False

    def record(self, repo, sub, args):
        self.calls.append((repo, sub, list(args)))

    def fail_closed(self, text: str):
        """Analysis gave up on ``text``: any restore it may contain runs in an unknown repo."""
        self.uncertain = True
        for op in sorted(REFUSE_OPS):
            if re.search(rf"\bstash\b[\s\S]*\b{op}\b", text or ""):
                self.record(None, "stash", [op])

    def run_text(self, text: str, states: list, depth: int) -> list:
        if depth > MAX_DEPTH:
            self.fail_closed(text)
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
                for cmd in cmds:  # every element runs in its own subshell
                    self.exec(cmd, states, depth)
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
            var, words, arith = forspec
            for st in states:
                for word in words:
                    for inner in _substitutions(word):
                        self.isolated(inner, st, depth)
                if arith:
                    self.isolated(arith, st, depth)
            if var:
                pre = []
                for st in states:
                    new = st.copy()
                    new.vars[var] = None
                    pre.append(new)

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
        return _State(st.cwd, variables, set(st.exported) | set(prefix), {})

    def simple(self, node, st: _State, depth: int):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise _Budget()
        _kind, assigns, words, redirs = node
        for raw in assigns + words:
            for inner in _substitutions(raw):
                self.isolated(inner, st, depth)
        self.redirs(redirs, [st], depth)
        if not words:
            new = st.copy()
            for raw in assigns:
                self.assign(new, raw)
            return [new], [new]
        prefix = {}
        scratch = st.copy()
        for raw in assigns:  # temporary environment of this command only
            name = self.assign(scratch, raw)
            prefix[name] = scratch.vars[name]
        lit = [_literal(w) for w in words]
        kept = _skip_wrappers(lit)
        raw = words[len(lit) - len(kept):]
        lit = kept
        if not lit:
            return [st], [st]
        name = _expand_word(raw[0], st.vars)
        if name is None:
            if "stash" in lit:
                self.record(None, "stash", lit[lit.index("stash") + 1:])  # unknown command
            return [st], [st]
        cmd = os.path.basename(name)
        if raw[0] in st.funcs:
            if depth >= MAX_DEPTH:
                self.fail_closed(repr(st.funcs[raw[0]]))
                unknown = _unknown([st])
                return [unknown], [unknown]
            return self.exec(st.funcs[raw[0]], [st], depth + 1)
        if cmd in SHELLS:
            child = self.child(st, prefix)
            if "-c" in lit:
                idx = lit.index("-c")
                if idx + 1 < len(lit):
                    script = _expand_word(raw[idx + 1], st.vars)
                    self.run_text(lit[idx + 1] if script is None else script, [child], depth + 1)
            for op, target, body, _quoted in redirs:  # bash <<EOF / bash <<< "script"
                if op in ("<<", "<<-") and body:
                    self.run_text(body, [child], depth + 1)
                elif op == "<<<":
                    text = _expand_word(target, st.vars)
                    self.run_text(_literal(target) if text is None else text, [child], depth + 1)
            return [st], [st]
        if cmd in ("cd", "pushd", "popd"):
            return self.chdir(cmd, raw, lit, st)
        if cmd == "unset":
            new = st.copy()
            for word in lit[1:]:
                if _NAME.match(word):
                    new.vars[word] = None
                    new.funcs.pop(word, None)
            return [new], [new]
        if cmd in DECLARE:
            return self.declare(cmd, raw, lit, st)
        if cmd in MUTATORS:
            new = st.copy()
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
            unknown = _unknown([st])  # the sourced file is not read
            return [unknown], [unknown]
        if cmd == "exit":
            return [], []
        if cmd == "trap":
            args = [w for w in lit[1:] if w not in ("--", "-p", "-l")]
            if args and args[0] != "-":
                self.run_text(args[0], [_unknown([st])], depth + 1)  # runs later, cwd unknown
            return [st], [st]
        if cmd == "git":
            self.git(raw, lit, st, prefix)
        return [st], [st]

    def assign(self, st: _State, raw: str) -> str:
        match = _ASSIGN.match(raw)
        assert match is not None
        name = match[1]
        if match[2] or match[3]:
            st.vars[name] = None  # arrays and += are not modeled
        else:
            st.vars[name] = _expand_word(raw[match.end():], st.vars)
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
        if not targets:
            target = st.vars.get("HOME")
        elif len(targets) > 1:
            target = None
        elif targets[0][1] == "-":
            target = st.vars.get("OLDPWD")
        else:
            target = _expand_word(targets[0][0], st.vars)
        new.cwd = _resolve(target, st.cwd)
        new.vars["OLDPWD"] = st.cwd
        new.vars["PWD"] = new.cwd
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
            if match:
                unmodeled = match[2] or match[3] or (flags & _UNMODELED_FLAGS and cmd != "export")
                new.vars[name] = None if unmodeled else _expand_word(r[match.end():], st.vars)
            elif cmd == "local" or (flags & _UNMODELED_FLAGS and cmd != "export"):
                new.vars[name] = None
            if unexport:
                new.exported.discard(name)
            elif cmd == "export" or "x" in flags:
                new.exported.add(name)
        return [new], [new]

    def git(self, raw, lit, st, prefix):
        repo, i = st.cwd, 1
        if "GIT_DIR" in prefix or "GIT_WORK_TREE" in prefix:
            repo = None
        while i < len(lit) and lit[i].startswith("-"):
            opt = lit[i]
            if opt == "-C" and i + 1 < len(lit):
                repo = _resolve(_expand_word(raw[i + 1], st.vars), repo)
                i += 2
            elif opt.startswith("--git-dir=") or opt.startswith("--work-tree="):
                repo = None  # alternate repository selection is not inferred
                i += 1
            elif opt in ("-c", "--git-dir", "--work-tree") and i + 1 < len(lit):
                if opt != "-c":
                    repo = None
                i += 2
            else:
                i += 1
        if i < len(lit):
            self.record(repo, lit[i], lit[i + 1:])


def _git_calls(cmd: str, cwd: Optional[str], depth: int = 0):
    """Yield (repo, subcommand, args) for every git call the command may make.

    Bounded static analysis, not a shell interpreter: nothing is executed. Only HOME and
    TMPDIR are borrowed from the process environment; other variables of a persistent
    terminal may differ and stay unknown. ``repo`` is None when it cannot be resolved.
    """
    analyzer = _Analyzer()
    variables = {key: os.environ.get(key) for key in ("HOME", "TMPDIR")}
    variables["PWD"] = cwd
    state = _State(cwd, variables, {"HOME", "TMPDIR", "PWD", "OLDPWD"}, {})
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
    if "stash" not in (cmd or "") or "git" not in (cmd or ""):
        return
    base = _resolve(os.path.expanduser(cwd or os.getcwd()), os.getcwd())
    for repo, sub, args in _git_calls(cmd, base):
        if sub != "stash":
            continue
        op = stash_op(args)
        if op:
            yield repo, op, args


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
    for repo, _op, _args in _stash_invocations(cmd, cwd):
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
    for repo, op, args in _stash_invocations(cmd, cwd):
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
