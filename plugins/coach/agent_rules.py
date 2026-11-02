"""coach agent channel: watch the agent's own tool calls and hand it a short corrective hint.

Invisible to the user. `post_tool_call` feeds `observe()`; `transform_tool_result` calls `take()` and the hint is
attached to the tool result the model is about to read (a `coach_hint` key for JSON results, else an appended
`[coach]` line). `pre_llm_call` (once per user turn) calls `turn_start()`, which resets per-turn counters and
returns any hint that was still pending.

Each rule fires at most once per key per session, so a hint never repeats itself into noise.
The same observer also records the user-channel signals (subagent count, long jobs, Hermes bug signatures)
through the injected `signals` sink (core.SignalStore in production).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

REPEAT_FAILS = 3          # same command failing N times -> stop and debug
POLL_WINDOW = 10          # last N tool calls ...
POLL_HITS = 3             # ... with this many sleep/poll calls -> background + notify
CHECKPOINT_TOOLS = 40     # tool calls in one turn without a reply -> summarize
LONG_JOB_S = 600          # a job this long belongs on the board
ASK_CAP = 20000           # chars of the user's request kept for the reply check (a limit can end a long Kanban body)

# Preloaded-skill blocks core puts before the user's own request (agent/skill_commands.py: `hermes -s`, Kanban card
# skills, skills.auto_load, bundles). A Kanban worker with skills gets 20-220 KB of skill text before "# Kanban task",
# so a cap on the raw message keeps only skill text, and skill prose ("read-only probe", "**Plan first**") reads as the
# user's scope limit. Each block ends with its "Load any of these with skill_view(...)" line, or its
# "[Skill directory: ...]" line plus one note line.
_SKILL_PREAMBLE_RE = re.compile(
    r'\A\s*\[IMPORTANT: The (?:user launched this CLI session with the "|"[^"\n]+" skill is auto-loaded)')
_SKILL_BLOCK_END_RE = re.compile(
    r'^(?:Load any of these with skill_view\(name="[^"\n]*"[^\n]*|\[Skill directory: [^\n]*\]\n[^\n]*)$', re.M)


def request_text(message: Any) -> str:
    """The user's own request: `message` minus any preloaded-skill blocks in front of it (unchanged when there are
    none, or when their end can't be found)."""
    msg = message if isinstance(message, str) else ""
    if not _SKILL_PREAMBLE_RE.match(msg):
        return msg
    ends = list(_SKILL_BLOCK_END_RE.finditer(msg))
    rest = msg[ends[-1].end():].strip() if ends else ""
    return rest or msg

HINTS = {
    "repeat_failure": ("The same command has now failed {n} times: `{cmd}`. Stop retrying variations. Reproduce it "
                       "once in isolation, write down one hypothesis for the cause, test that hypothesis, and only "
                       "then change the code (skill: systematic-debugging)."),
    "poll_loop": ("You are polling with sleep/poll calls. Start the job with terminal(background=true, notify=true) "
                  "(or a Kanban card for anything over ~10 min) and end the turn; the completion notice wakes you."),
    "no_checkpoint": ("{n} tool calls this turn and the user has seen no update. Before more tool calls, write a "
                      "short state summary: what is done, what failed, what is next."),
    "live_checkout": ("You just changed the LIVE Hermes checkout ({path}) that running backends execute from. "
                      "Core work goes in a worktree: `~/.hermes/bin/hermes-core-wt new <slug>` -> edit/test/commit "
                      "there -> `hermes-core-wt ship <slug>`. Revert the change in the live tree if you can."),
    "kanban_db_write": ("You just wrote the Kanban board database (kanban.db) by hand with SQL. That skips every "
                        "lifecycle guard (acceptance receipts, run ownership, events). Use the kanban_* tools or "
                        "`hermes kanban` instead. A card whose PR contract can't pass because the repo has no "
                        "required checks completes with kanban_complete metadata.contract_waiver={{\"reason\": "
                        "\"...\"}}; a damaged board is repaired with `hermes kanban recover`."),
    "plan_first": ("Plan Before You Build: this is a new build request and the user has not seen a plan yet. Stop "
                   "editing, post the plan (approach, files, how it gets tested) as your reply and end the turn for "
                   "the user's go. Someone else's approval (a reviewer, a cut PR) does not replace it."),
    "widget_send": ("{path} is a chat widget that calls hermes.send but {issue}. Widget rules: hermes.send takes at "
                    "most 500 characters and one per second, and returns a Promise of {{ok:true}} or "
                    "{{ok:false,error,retryAfterMs}}: await it, retry after retryAfterMs when throttled, show the "
                    "user any other failure, and send large data in chunks. The frame is sandboxed, so "
                    "localStorage/sessionStorage are unavailable: keep state with yourself (write it into the "
                    "widget's file) and never promise local saving."),
}

# plan_first: a new build ask (a build verb plus a real spec), not a fix/bug report and not already a go.
BUILD_ASK_RE = re.compile(r"\b(build|implement|extend|create|add|make|write|design|develop|set\s+up|scaffold|"
                          r"port|migrate|integrate|new\s+feature)\b", re.I)
FIX_ASK_RE = re.compile(r"\b(fix\w*|bugs?|broken|regression|crash\w*|errors?|exceptions?|traceback|"
                        r"(not|isn'?t|doesn'?t|didn'?t)\s+work\w*|fail\w*|why\s+(is|does|did|was|are))\b", re.I)
GO_ASK_RE = re.compile(r"^\W*(go|go\s+ahead|do\s+it|yes|yep|approved|ship\s+it|lgtm|sounds\s+good|proceed)\b"
                       r"|\b(skip\s+the\s+plan|no\s+plan\s+needed|no\s+need\s+for\s+a\s+plan|just\s+(build|do)\s+it)\b",
                       re.I)
NOT_USER_ASK_RE = re.compile(r"^\s*(\[(System note|IMPORTANT|SYSTEM)|work kanban task\b)", re.I)
OOB_RE = re.compile(r"\[/?OUT-OF-BAND USER MESSAGE[^\]]*\]")
BUILD_ASK_MIN_WORDS = 25
GIT_EDIT_RE = re.compile(r"\bgit\b[^;&|\n]*?\s(commit|worktree\s+add|apply|am|cherry-pick)(?![-\w])")
INPLACE_RE = re.compile(r"\bsed\s+-i\b|\bperl\s+-pi\b")


def is_build_ask(text: Any) -> bool:
    """True for a user message that asks for a new build with a substantial spec (the Plan Before You Build case)."""
    if not isinstance(text, str):
        return False
    t = OOB_RE.sub(" ", text).strip()
    if not t or NOT_USER_ASK_RE.match(t) or GO_ASK_RE.search(t) or FIX_ASK_RE.search(t):
        return False
    return bool(BUILD_ASK_RE.search(t)) and len(t.split()) >= BUILD_ASK_MIN_WORDS


def _scratch_roots() -> list[str]:
    """Where edits are probes or plan drafts, not implementation. `COACH_SCRATCH_ROOTS` (os.pathsep list) replaces
    the defaults; $TMPDIR always counts."""
    env = os.environ.get("COACH_SCRATCH_ROOTS")
    roots = ([r for r in env.split(os.pathsep) if r] if env is not None else
             ["/tmp", "/private/tmp", "/var/folders", str(Path.home() / ".hermes" / "cache"),
              str(Path.home() / ".hermes" / "plans")])
    if os.environ.get("TMPDIR"):
        roots.append(os.environ["TMPDIR"].rstrip("/"))
    out = []
    for r in roots:
        try:
            out.append(str(Path(r).expanduser().resolve()))
        except Exception:
            out.append(r)
    return out


def _is_scratch(p: str) -> bool:
    return any(_inside(p, r) for r in _scratch_roots())


def _in_git_repo(p: str) -> bool:
    try:
        cur = Path(p).expanduser().resolve()
    except Exception:
        return False
    for d in [cur, *cur.parents]:
        if (d / ".git").exists():
            return True
    return False


_PATH_JOIN = {"os.path.join", "posixpath.join"}
_PATH_CTOR = {"Path", "PurePath", "PosixPath", "pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath",
              "pathlib.PurePosixPath"}
_PATH_SAME = {"str", "os.fspath", "os.path.expanduser", "os.path.abspath", "os.path.normpath", "os.path.realpath"}
_PATH_METHODS = {"expanduser", "resolve", "absolute", "as_posix"}
# Native (non-hermes_tools) writes inside execute_code.
_OPEN_FUNCS = {"open", "io.open", "builtins.open", "codecs.open"}
_PATH_WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch"}
_DEST_FUNCS = {"shutil.copy": (1,), "shutil.copy2": (1,), "shutil.copyfile": (1,), "shutil.copytree": (1,),
               "shutil.copymode": (1,), "shutil.copystat": (1,), "shutil.move": (0, 1),
               "os.rename": (0, 1), "os.replace": (0, 1)}


def _execute_file_change(code: str) -> bool:
    """Inspect helper calls without running Python.

    Each path/command argument is evaluated to its statically known value: fully known, or only a known leading
    prefix (the left side of `+`, an f-string head, the first arguments of os.path.join / Path(...) / `/`,
    expanduser of a literal, a loop over string literals). A write_file/patch whose known prefix is outside scratch
    is a project edit even when the tail is dynamic (`write_file(R + k, v)` in a loop); a prefix inside scratch is
    a probe. A path with no static prefix at all (a function's return value, an unknown variable) is not inferred:
    no evidence either way, so it stays quiet rather than guess. Comments, content strings and uncalled
    declarations never count."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    aliases = {name: name for name in ("write_file", "patch", "terminal")}
    modules = {"hermes_tools"}
    imported: dict[str, str] = {}  # local name -> dotted origin (os.path, pathlib.Path, ...)
    env: dict[str, tuple[str, bool]] = {}  # name -> (known text or prefix, complete?)

    def dotted(node) -> Optional[str]:
        if isinstance(node, ast.Name):
            return imported.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def cat(left, right, sep=""):
        """Concatenate two evaluated parts; stop at the first unknown/partial piece."""
        if left is None:
            return None
        if not left[1]:
            return left
        head = left[0].rstrip("/") + "/" if sep else left[0]
        if right is None:
            return (head, False) if head else None
        if sep and right[0].startswith("/"):
            return right
        return head + right[0], right[1]

    def joined(parts):
        acc = None
        for part in parts:
            v = val(part)
            acc = v if acc is None else cat(acc, v, "/")
            if acc is None or not acc[1]:
                return acc
        return acc

    def val(node) -> Optional[tuple[str, bool]]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, True
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            return cat(val(node.left), val(node.right), "/" if isinstance(node.op, ast.Div) else "")
        if isinstance(node, ast.JoinedStr):
            acc: Optional[tuple[str, bool]] = ("", True)
            for p in node.values:
                part = val(p.value) if isinstance(p, ast.FormattedValue) and not p.format_spec else (
                    val(p) if not isinstance(p, ast.FormattedValue) else None)
                acc = cat(acc, part)
                if acc is None or not acc[1]:
                    break
            return acc if acc and acc[0] else None
        if isinstance(node, ast.Call):
            fn = dotted(node.func)
            if fn in _PATH_JOIN or fn in _PATH_CTOR:
                return joined(node.args) if node.args else None
            if fn in _PATH_SAME and node.args:
                v = val(node.args[0])
                return (os.path.expanduser(v[0]), v[1]) if v else None
            if fn in ("Path.home", "pathlib.Path.home"):
                return str(Path.home()), True
            if fn in ("tempfile.gettempdir",):
                import tempfile
                return tempfile.gettempdir(), True
            if fn in ("os.getenv", "os.environ.get") and node.args:
                key = val(node.args[0])
                return (os.environ[key[0]], True) if key and key[1] and os.environ.get(key[0]) else None
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in _PATH_METHODS:
                    v = val(node.func.value)
                    return (os.path.expanduser(v[0]), v[1]) if v else None
                if node.func.attr == "joinpath":
                    return joined([node.func.value, *node.args])
        if (isinstance(node, ast.Subscript) and dotted(node.value) == "os.environ"):
            key = val(node.slice)
            return (os.environ[key[0]], True) if key and key[1] and os.environ.get(key[0]) else None
        return None

    def names(target) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [n for t in target.elts for n in names(t)]
        return []

    def bind(target, value):
        for n in names(target):
            env.pop(n, None)
        if isinstance(target, ast.Name) and value is not None:
            env[target.id] = value

    def loop_value(it):
        """A loop over string literals binds its variable to their common prefix."""
        if isinstance(it, (ast.List, ast.Tuple, ast.Set)):
            vals = [val(e) for e in it.elts]
            if vals and all(v is not None and v[1] for v in vals):
                texts = [v[0] for v in vals]
                if len(set(texts)) == 1:
                    return texts[0], True
                pre = os.path.commonprefix(texts)
                return (pre, False) if pre else None
        return None

    class Calls(ast.NodeVisitor):
        changed = False

        def visit_ImportFrom(self, node):
            for name in node.names:
                local = name.asname or name.name
                if node.module == "hermes_tools":
                    aliases[local] = name.name
                elif node.module:
                    imported[local] = f"{node.module}.{name.name}"

        def visit_Import(self, node):
            for name in node.names:
                if name.name == "hermes_tools":
                    modules.add(name.asname or name.name)
                elif name.asname:
                    imported[name.asname] = name.name

        def visit_Assign(self, node):
            self.visit(node.value)
            value = val(node.value)
            for target in node.targets:
                bind(target, value)

        def visit_AnnAssign(self, node):
            if node.value is not None:
                self.visit(node.value)
                bind(node.target, val(node.value))

        def visit_For(self, node):
            self.visit(node.iter)
            bind(node.target, loop_value(node.iter))
            for stmt in node.body + node.orelse:
                self.visit(stmt)

        def visit_Call(self, node):
            name = aliases.get(node.func.id) if isinstance(node.func, ast.Name) else None
            if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in modules):
                name = node.func.attr
            if name in ("write_file", "patch", "terminal"):
                key = "command" if name == "terminal" else "path"
                values = {kw.arg: val(kw.value) for kw in node.keywords if kw.arg}
                if key not in values and node.args:
                    values[key] = val(node.args[0])
                got = values.get(key)
                args = {k: v[0] for k, v in values.items() if v is not None and v[1]}
                if got is not None and got[0]:
                    # A partial path is classified by its known prefix; a partial command by its known head.
                    args[key] = got[0]
                    if is_file_change(name, args):
                        self.changed = True
            if self.native_write(node):
                self.changed = True
            self.generic_visit(node)

        def native_write(self, node) -> bool:
            """open(path, w/a/x/+), Path(...).write_text/write_bytes/mkdir/touch/open(mode), shutil.copy*/move and
            os.rename/replace. Same known-prefix evaluation and scratch policy as write_file; reads stay quiet."""
            def outside_scratch(expr) -> bool:
                v = val(expr)
                return bool(v and v[0]) and not _is_scratch(os.path.expanduser(v[0]))

            def writes(mode_node) -> bool:
                m = val(mode_node) if mode_node is not None else None
                return bool(m and m[1] and set(m[0]) & set("wax+"))

            def kwarg(name):
                return next((kw.value for kw in node.keywords if kw.arg == name), None)

            fn = dotted(node.func)
            if fn in _OPEN_FUNCS and node.args:
                mode = node.args[1] if len(node.args) > 1 else kwarg("mode")
                return writes(mode) and outside_scratch(node.args[0])
            if fn in _DEST_FUNCS:
                idx = _DEST_FUNCS[fn]
                args = [node.args[i] for i in idx if i < len(node.args)]
                return any(outside_scratch(a) for a in args)
            if isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in _PATH_WRITE_METHODS:
                    return outside_scratch(node.func.value)
                if attr == "open":
                    mode = node.args[0] if node.args else kwarg("mode")
                    return writes(mode) and outside_scratch(node.func.value)
            return False

        # A declaration alone doesn't run its body. Dynamic helper/function dispatch is not inferred.
        def visit_FunctionDef(self, node: ast.AST):
            pass

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

    visitor = Calls()
    visitor.visit(tree)
    return visitor.changed


def is_file_change(tool: str, args: Any) -> bool:
    """A call that changes project files: write_file/patch outside scratch, or a terminal git commit / worktree add /
    in-place edit / output redirect into a git repo, including helper calls inside execute_code.
    Reads, tests and scratch probes don't count."""
    a = args if isinstance(args, dict) else {}
    if tool == "execute_code":
        return _execute_file_change(str(a.get("code") or ""))
    if tool in ("write_file", "patch"):
        p = str(a.get("path") or "")
        return bool(p) and not _is_scratch(p)
    if tool != "terminal":
        return False
    cmd = shell_code(str(a.get("command") or ""))
    wd = str(a.get("workdir") or "")
    for m in list(GIT_EDIT_RE.finditer(cmd)) + list(INPLACE_RE.finditer(cmd)):
        g = GIT_C_RE.match(_segment(cmd, m.start()))
        d = _target_dir(cmd, m.start(), g.group(1) if g else "", wd)
        if d is None or not _is_scratch(d):
            return True
    for m in REDIRECT_RE.finditer(cmd):
        target = m.group(1).strip("\"'")
        if not target or target.startswith(("&", "$")) or target.startswith("/dev/"):
            continue
        t = os.path.expanduser(target)
        if not os.path.isabs(t):
            base = _target_dir(cmd, m.start(), "", wd)
            if not base:
                continue
            t = os.path.join(base, t)
        if not _is_scratch(t) and _in_git_repo(os.path.dirname(t) or t):
            return True
    return False

SLEEP_RE = re.compile(r"\bsleep\s+(\d+(?:\.\d+)?)")
LOOP_SLEEP_RE = re.compile(r"\b(while|until|for)\b[^\n]*?(;|\n|\bdo\b)[\s\S]*?\bsleep\s+\d", re.I)
WATCH_RE = re.compile(r"(^|[;&|]\s*)watch\s+-n")
# The mutating word must start after whitespace: glued to a revision, path or option value it is an argument
# (`cat-file -e "$sha^{commit}"`, `show HEAD:docs/checkout.md`, `--grep=merge`), not a subcommand.
GIT_MUTATE_RE = re.compile(r"\bgit\b[^;&|]*?\s(commit|pull|reset|checkout|switch|merge|rebase|stash|am|apply|cherry-pick)\b(?!-)")
FILE_MUTATE_RE = re.compile(r"(\bsed\s+-i\b|\bperl\s+-pi\b|\bnpm\s+run\s+(pack|build)\b|\bhermes\s+desktop\b.*--build)")
# Output redirects: `>`, `>>`, `>|`, `N>`, `&>`. The target group is either an fd dup (`&1`, `&2`, `&-`) or a path.
REDIRECT_RE = re.compile(r"""(?<![-=<>])(?:&|\d*)>>?\|?\s*(&[\d-]*|"[^"]*"|'[^']*'|[^\s;&|()<>]+)""")
CD_RE = re.compile(r"""(?:^|[;&|(\n'"]\s*)cd\s+("[^"]*"|'[^']*'|[^\s;&|()'"]+)""")
GIT_C_RE = re.compile(r"""^git\s+-C\s+("[^"]*"|'[^']*'|\S+)""")
HERMES_INFRA_TOOLS = {
    "cross_session", "delegate_task", "session_search", "memory", "skill_manage", "skill_view", "cronjob",
    "process_manage", "send_message", "todo", "clarify", "tool_search", "tool_describe", "tool_call",
} | {f"kanban_{v}" for v in ("create", "show", "complete", "block", "comment", "heartbeat", "link",
                              "request_review", "request_changes", "attach", "attach_url", "attachments")}
TRACE_HERMES_RE = re.compile(r'File "[^"]*(hermes-agent|hermes_cli|tui_gateway|/agent/|/tools/|/gateway/)[^"]*\.py"')
EXC_NAME_RE = re.compile(r"^[A-Z][A-Za-z]+(Error|Exception)$")


def default_live_checkout() -> Optional[Path]:
    env = os.environ.get("COACH_LIVE_CHECKOUT")
    if env:
        return Path(env).expanduser().resolve()
    try:
        import hermes_cli  # type: ignore
        root = Path(hermes_cli.__file__).resolve().parents[1]
        return root if (root / ".git").exists() else None
    except Exception:
        return None


def _norm_cmd(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _as_dict(result: Any) -> Optional[dict]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str) and result.lstrip().startswith("{"):
        try:
            d = json.loads(result)
            return d if isinstance(d, dict) else None
        except Exception:
            return None
    return None


# A Kanban call refused because the board couldn't be read (core's "corrupt" refusal, PR #125's unreliable view).
KANBAN_DB_DOWN = re.compile(r"Refusing to open corrupt kanban DB|attached to a deleted WAL/SHM generation"
                            r"|integrity probe disagrees with an independent process|KanbanDbUnreliableViewError")


def is_kanban_call(tool: str, args: Any) -> bool:
    name = (tool or "").rsplit("__", 1)[-1]
    if name == "tool_call":  # deferred kanban tools go through the tool_call bridge
        return "kanban_" in json.dumps(args, default=str)
    return name.startswith("kanban_")


def failed(tool: str, result: Any, status: Optional[str]) -> bool:
    if status in ("error", "timeout"):
        return True
    d = _as_dict(result)
    if not d:
        return False
    if tool == "terminal":
        code = d.get("exit_code")
        return code not in (None, 0)
    if tool == "execute_code":
        return bool(d.get("error")) or d.get("status") == "error"
    return d.get("success") is False or bool(d.get("error"))


def command_key(tool: str, args: dict) -> str:
    if tool == "terminal":
        return "terminal:" + _norm_cmd(str(args.get("command") or ""))[:400]
    if tool == "execute_code":
        return "execute_code:" + hashlib.sha1(str(args.get("code") or "").encode()).hexdigest()[:12]
    try:
        blob = json.dumps(args, sort_keys=True, default=str)[:400]
    except Exception:
        blob = str(args)[:400]
    return f"{tool}:{blob}"


def is_poll(tool: str, args: dict) -> bool:
    if tool == "terminal":
        cmd = str(args.get("command") or "")
        m = SLEEP_RE.search(cmd)
        return bool((m and float(m.group(1)) >= 2) or WATCH_RE.search(cmd))
    if tool == "process_manage":
        return str(args.get("action") or "") in ("poll", "log", "status")
    return False


def is_poll_loop(tool: str, args: dict) -> bool:
    return tool == "terminal" and bool(LOOP_SLEEP_RE.search(str(args.get("command") or "")))


def _inside(p: str, root: str) -> bool:
    try:
        rp = str(Path(p).expanduser().resolve())
    except Exception:
        rp = p
    return rp == root or rp.startswith(root + os.sep)


HEREDOC_RE = re.compile(r"""<<-?\s*(['"]?)([A-Za-z_][\w-]*)\1""")
SHELL_RUNNER_RE = re.compile(r"""(?:^|[\s;&|(])(?:(?:ba|z|da)?sh|eval)(?:\s+-\w*c\w*)?\s*$""")
# Runner word plus one -*c* flag. The pattern is end-anchored, so searching the
# whole prefix on every quote is quadratic in command length.
_SHELL_RUNNER_TAIL = 64
_SHELL_WS = re.compile(r"\s")
_SHELL_RUNNER_SEP = re.compile(r"[\s;&|(]")
HOME_VAR_RE = re.compile(r"\$\{HOME\}|\$HOME\b")


def _prefix_is_shell_runner(cmd: str, start: int) -> bool:
    """True when SHELL_RUNNER_RE matches cmd[:start], inspecting only the suffix.

    The pattern is end-anchored, so the whole prefix does not need to be scanned.
    Trailing whitespace is skipped first (it is the pattern's trailing space), then
    only a bounded tail is tested. A match that begins on a sliced boundary is
    kept when that boundary is a separator, or when the character just before
    the slice is a separator. Otherwise the slice cut a longer word.
    """
    i = start
    while i > 0 and _SHELL_WS.match(cmd[i - 1]):
        i -= 1
    lo = max(0, i - _SHELL_RUNNER_TAIL)
    match = SHELL_RUNNER_RE.search(cmd[lo:i])
    if match is None:
        return False
    if lo == 0 or match.start() > 0:
        return True
    # The match starts on the sliced boundary. A separator there is real. A
    # start-anchor match is real only when the character just before the slice
    # is a separator; otherwise the slice cut a longer word (notbash -> bash).
    if _SHELL_RUNNER_SEP.match(cmd[lo]):
        return True
    return _SHELL_RUNNER_SEP.match(cmd[lo - 1]) is not None


def _strip_heredocs(cmd: str) -> str:
    """Drop heredoc bodies (they are stdin data, not commands), except when fed to a shell (`bash <<EOF`)."""
    out, pos = [], 0
    while True:
        m = HEREDOC_RE.search(cmd, pos)
        if not m:
            out.append(cmd[pos:])
            return "".join(out)
        nl = cmd.find("\n", m.end())
        if nl < 0:
            out.append(cmd[pos:])
            return "".join(out)
        line_start = cmd.rfind("\n", 0, m.start()) + 1
        feeds_shell = re.search(r"(?:^|[\s;&|(])(?:ba|z|da)?sh(?=\s|$)", cmd[line_start:m.start()]) is not None
        end = re.compile(r"^\t*" + re.escape(m.group(2)) + r"[ \t]*$", re.M).search(cmd, nl + 1)
        body_end = end.end() if end else len(cmd)
        out.append(cmd[pos:nl + 1])
        if feeds_shell:
            out.append(cmd[nl + 1:body_end])
        pos = body_end


def _mask_quoted(cmd: str) -> str:
    """Mask shell data without shifting offsets, retaining nested command substitutions.

    Single-token paths and scripts passed to shell runners retain their existing treatment.
    Quotes inside $(...) / backticks start fresh contexts, even in double-quoted strings.
    Arithmetic operators are data; nested command substitutions still execute. Incomplete
    quotes/substitutions and excessively deep nesting are masked through the end.
    """
    out = list(cmd)
    limit = len(cmd)

    def mask(start: int, end: int) -> None:
        out[start:end] = "_" * (end - start)

    def substitution(start: int, level: int) -> int:
        # Arithmetic operators are data, but nested command substitutions still execute.
        arithmetic = cmd.startswith("$((", start)
        body = start + (3 if arithmetic else 2)
        if arithmetic:
            mask(start, body)
        end = code(body, ")", depth=1 if arithmetic else 0, data=arithmetic, level=level + 1)
        if end == limit and (not cmd or cmd[end - 1] != ")"):
            mask(start, end)  # Incomplete substitutions must not leak apparent code.
        return end

    def quoted(start: int, level: int, ansi: bool = False) -> int:
        quote = cmd[start]
        i = start + 1
        while i < limit:
            ch = cmd[i]
            if ch == quote:
                end = i + 1
                body = cmd[start + 1:i]
                # Preserve the existing single-token path and shell-script exceptions.
                if _prefix_is_shell_runner(cmd, start):
                    # Scan the script independently: its outer quotes are transport syntax.
                    out[start + 1:i] = _mask_quoted(body)
                elif not re.search(r"[\s><;&|()`\\]", body):
                    out[start + 1:i] = body
                return end
            if (quote == '"' or ansi) and ch == "\\":
                mask(i, min(i + 2, limit))
                i += 2
            elif quote == '"' and cmd.startswith("$(", i):
                i = substitution(i, level)
            elif quote == '"' and ch == "`":
                i = code(i + 1, "`", level=level + 1)
            else:
                mask(i, i + 1)
                i += 1
        mask(start, limit)
        return limit

    def code(start: int, stop: str = "", depth: int = 0, data: bool = False, level: int = 0) -> int:
        if level >= 64:
            mask(start, limit)
            return limit
        i = start
        while i < limit:
            ch = cmd[i]
            if ch == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|()"):
                end = cmd.find("\n", i)
                end = end if end >= 0 else limit
                mask(i, end)
                i = end
            elif ch == "\\":
                if data:
                    mask(i, min(i + 2, limit))
                i += 2
            elif cmd.startswith("$(", i):
                i = substitution(i, level)
            elif cmd.startswith("$'", i):
                i = quoted(i + 1, level + 1, ansi=True)
            elif ch in "\"'":
                i = quoted(i, level + 1)
            elif ch == stop and depth == 0:
                if data:
                    mask(i, i + 1)
                return i + 1
            elif ch == "`":
                i = code(i + 1, "`", level=level + 1)
            else:
                if data:
                    mask(i, i + 1)
                if stop == ")":
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                i += 1
        return limit

    try:
        code(0)
    except RecursionError:
        # Shell-runner strings recurse through _mask_quoted too; malformed input is data.
        mask(0, limit)
    return "".join(out)


def shell_code(cmd: str) -> str:
    """The parts of a terminal command the shell executes: $HOME expanded, heredoc bodies and quoted data blanked."""
    cmd = HOME_VAR_RE.sub(lambda _: str(Path.home()), cmd or "")
    cmd = re.sub(r"(?m)^[ \t]*#.*$", "", cmd)  # whole-line comments are notes, not commands
    return _mask_quoted(_strip_heredocs(cmd))


def redirect_writes_into(cmd: str, workdir: str, root: str) -> bool:
    """True when a shell output redirect in `cmd` writes a file inside `root`.

    fd dups (`2>&1`, `>&2`) never count; path targets count only when they resolve into the tree.
    Relative targets resolve against the last `cd` before the redirect, else `workdir`; with neither
    the cwd is unknown, so they don't count.
    """
    for m in REDIRECT_RE.finditer(cmd):
        target = m.group(1).strip("\"'")
        if not target or target.startswith("&") or target.startswith("$"):
            continue  # fd dup, or an unexpanded variable like $TMPDIR/x (never the live tree; $HOME is pre-expanded)
        t = os.path.expanduser(target)
        if not os.path.isabs(t):
            cds = [c.group(1).strip("\"'") for c in CD_RE.finditer(cmd[:m.start()])]
            base = os.path.expanduser(cds[-1]) if cds else (os.path.expanduser(workdir) if workdir else "")
            if base and not os.path.isabs(base) and workdir:
                base = os.path.join(os.path.expanduser(workdir), base)
            if not base or not os.path.isabs(base):
                continue
            t = os.path.join(base, t)
        if _inside(t, root):
            return True
    return False


KANBAN_DB_RE = re.compile(r"kanban[\w./-]*\.db\b|\bkanban\.db\b", re.I)
KANBAN_SQL_WRITE_RE = re.compile(r"\b(UPDATE\s+(tasks|task_runs|task_events)\b|INSERT\s+(OR\s+\w+\s+)?INTO\s+"
                                 r"(tasks|task_runs|task_events)\b|DELETE\s+FROM\s+(tasks|task_runs|task_events)\b)",
                                 re.I)


_WIDGET_FILE_RE = re.compile(r"\.(?:html?|xhtml)$", re.I)
_WIDGET_SEND_RE = re.compile(r"\bhermes\.send\s*\(")
_WIDGET_STORAGE_RE = re.compile(r"\b(?:localStorage|sessionStorage)\b")


def widget_send_issue(tool: str, args: dict) -> Optional[tuple[str, str]]:
    """(path, issue) when a write_file/patch to an .html file calls hermes.send( and either never reads a call's
    result (no `await`/`return` just before it and no `.then(` soon after) or uses localStorage/sessionStorage;
    else None (desktop-13785ebc41). data-hermes-send attributes alone never count: they are fire-and-forget by
    design, and the frame now shows their refusals itself."""
    if tool == "write_file":
        text = str(args.get("content") or "")
    elif tool == "patch":
        text = str(args.get("new_string") or "")
    else:
        return None
    path = str(args.get("path") or "")
    if not _WIDGET_FILE_RE.search(path) or not _WIDGET_SEND_RE.search(text):
        return None
    for m in _WIDGET_SEND_RE.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        before = re.sub(r"window\.$", "", before.rstrip())
        if re.search(r"\b(?:await|return)\s*$", before) or ".then(" in text[m.end():m.end() + 400]:
            continue
        return Path(path).name, "never reads its {ok} result, so a refused send fails silently"
    if _WIDGET_STORAGE_RE.search(text):
        return Path(path).name, "relies on localStorage/sessionStorage, which the sandboxed frame blocks"
    return None


def writes_kanban_db(tool: str, args: dict) -> bool:
    """True when a terminal command or execute_code script runs a write against kanban.db's task tables
    (UPDATE/INSERT/DELETE on tasks, task_runs, task_events). Reads (SELECT) never count."""
    if tool == "terminal":
        text = str(args.get("command") or "")
    elif tool == "execute_code":
        text = str(args.get("code") or "")
    else:
        return False
    return bool(KANBAN_DB_RE.search(text) and KANBAN_SQL_WRITE_RE.search(text))


def touches_live_checkout(tool: str, args: dict, checkout: Optional[Path]) -> Optional[str]:
    if not checkout:
        return None
    root = str(checkout)
    home_form = root.replace(str(Path.home()), "~", 1)
    if tool in ("write_file", "patch"):
        p = str(args.get("path") or "")
        if not p:
            return None
        try:
            rp = str(Path(p).expanduser().resolve())
        except Exception:
            rp = p
        return rp if rp == root or rp.startswith(root + os.sep) else None
    if tool == "terminal":
        cmd = shell_code(str(args.get("command") or ""))
        wd = str(args.get("workdir") or "")
        if mutation_in_tree(cmd, wd, root, home_form) or redirect_writes_into(cmd, wd, root):
            return root
    return None


def _segment(cmd: str, start: int) -> str:
    """The simple command starting at `start` (up to the next ; & | or newline)."""
    m = re.compile(r"[;&|\n]").search(cmd, start)
    return cmd[start:m.start() if m else len(cmd)]


def mutation_in_tree(cmd: str, workdir: str, root: str, home_form: str) -> bool:
    """True when a git mutation / in-place edit / build in `cmd` runs on the live tree.

    Each mutation is attributed to its own directory (`git -C X`, else the last `cd` before it, else
    `workdir`) or to a live-tree path inside its own simple command. A live path elsewhere in the command
    (e.g. a later `grep` of a file in the tree) doesn't count: that made `cd other-repo && git pull; grep x
    ~/.hermes/hermes-agent/f.py` look like a live-tree change."""
    def mentions(seg: str) -> bool:
        return root in seg or home_form in seg

    for m in GIT_MUTATE_RE.finditer(cmd):
        seg = _segment(cmd, m.start())
        c = GIT_C_RE.match(seg)
        d = _target_dir(cmd, m.start(), c.group(1) if c else "", workdir)
        if c and d is None and mentions(c.group(1)):
            return True
        if d and _inside(d, root):
            return True
        if d is None and not c and mentions(seg):
            return True
    for m in FILE_MUTATE_RE.finditer(cmd):
        seg = _segment(cmd, m.start())
        if mentions(seg):
            return True
        d = _target_dir(cmd, m.start(), "", workdir)
        if d and _inside(d, root):
            return True
    return False


GIT_HEAD_RE = re.compile(r"\bgit\b((?:\s+-C\s+(?:\"[^\"]*\"|'[^']*'|\S+))?)[^;&|\n]*?\s"
                         r"(commit|pull|reset|checkout|switch|merge|rebase|am|cherry-pick|revert)(?![-\w])")
PACK_RE = re.compile(r"(\bnpm\s+run\s+pack\b|\bhermes\s+desktop\b[^;&|\n]*--build)")
UPDATE_RE = re.compile(r"(?:^|[;&|(]\s*|\s)hermes\s+update\b")
ALLOW_LIVE_RE = re.compile(r"\bCOACH_ALLOW_LIVE=1\b")


def _target_dir(cmd: str, pos: int, explicit: str, workdir: str) -> Optional[str]:
    """Directory a command at `pos` runs in: `git -C X`, else the last `cd` before it, else workdir.
    None when it can't be known (a `cd $VAR` we can't expand)."""
    def norm(d: str, base: str) -> Optional[str]:
        d = d.strip("\"'")
        if "$" in d or "`" in d:
            return None
        d = os.path.expanduser(d)
        if not os.path.isabs(d):
            if not base:
                return None
            d = os.path.join(base, d)
        return os.path.normpath(d)
    base = os.path.expanduser(workdir) if workdir else ""
    for c in CD_RE.finditer(cmd[:pos]):
        nb = norm(c.group(1), base)
        base = nb if nb else ""
    if explicit:
        return norm(explicit, base)
    return base or None


def blocks_live_checkout(tool: str, args: dict, checkout: Optional[Path]) -> Optional[str]:
    """The live-checkout change that must be refused BEFORE it runs: anything that moves HEAD (every running
    backend then reports code skew and the Desktop restarts, killing in-flight turns in every chat), repacks
    the running Hermes.app, or writes a source file in the tree (backends lazily import from it, so even a
    transient edit can load live, and `hermes update` stamps the version `.dirty`). Writes to gitignored
    paths (logs, venv, build output) stay allowed, except `<live>[/<dir>]/node_modules` (cp/rsync/copytree into it,
    or npm/pnpm/yarn/bun install run there): it is the live dependency install. `COACH_ALLOW_LIVE=1` in the command (or the
    process env, for write_file/patch/execute_code) is the escape hatch for a change the user explicitly asked
    to make in the live tree."""
    if not checkout:
        return None
    root = str(checkout)
    if tool in ("write_file", "patch", "execute_code"):
        if os.environ.get("COACH_ALLOW_LIVE") == "1":
            return None
        if tool == "execute_code":
            code = str(args.get("code") or "")
            if ALLOW_LIVE_RE.search(code):
                return None
            return _execute_code_live_write(code, root)
        for path in _edit_tool_paths(tool, args):
            hit = _live_write_target(path, "", root)
            if hit:
                return f"{tool} of `{hit}`"
        return None
    if tool != "terminal":
        return None
    raw = str(args.get("command") or "")
    if ALLOW_LIVE_RE.search(raw):
        return None
    cmd = shell_code(raw)
    wd = str(args.get("workdir") or "")
    if any(not re.match(r"[^;&|\n]*--help", cmd[m.end():]) for m in UPDATE_RE.finditer(cmd)):
        return "`hermes update`"
    for m in GIT_HEAD_RE.finditer(cmd):
        if m.group(2) == "checkout" and re.match(r"[^;&|\n]*\s--(\s|$)", cmd[m.end():]):
            continue  # `git checkout -- <file>` discards live edits (what the post-hoc hint asks for); HEAD stays
        explicit = re.sub(r"^\s*-C\s+", "", m.group(1) or "")
        d = _target_dir(cmd, m.start(), explicit, wd)
        if d and _inside(d, root):
            return f"`git {m.group(2)}`"
    for m in PACK_RE.finditer(cmd):
        d = _target_dir(cmd, m.start(), "", wd)
        if d and _inside(d, root):
            return f"`{m.group(1).strip()}`"
    return _terminal_live_write(raw, cmd, wd, root)


# ── live-tree file writes (refused in pre_tool_call) ──
PY_RUNNER_RE = re.compile(r"(?:^|[\s;&|(/])(?:python[\d.]*|pypy[\d.]*)(?=\s|$)")
PY_DASH_C_RE = re.compile(r"""(?:^|[\s;&|(/])(?:python[\d.]*|pypy[\d.]*)\s+(?:-\w+\s+)*-c\s+("(?:[^"\\]|\\.)*"|'[^']*')""")
PY_WRITE_RE = re.compile(r"(?<!stdout)(?<!stderr)\.write\(|\.write_text\(|\.write_bytes\(|\.writelines\(|\bopen\([^)\n]*?['\"][rb]*[wax]\+?[bt]?['\"]"
                         r"|\bmode\s*=\s*['\"][rb]*[wax]|\bshutil\.(?:copy\w*|move)\(|\bos\.(?:replace|rename|remove|unlink)\("
                         r"|\.unlink\(|\.rename\(|\.touch\(")
PY_CHDIR_RE = re.compile(r"""\bos\.chdir\(\s*(?:os\.path\.expanduser\(\s*)?(['"])([^'"\n]+)\1""")
PY_LITERAL_RE = re.compile(r"""(['"])((?:(?!\1)[^\\\n]|\\.)*)\1""")
INPLACE_EDIT_RE = re.compile(r"(\bsed\s+(?:-\w+\s+)*-i\b|\bperl\s+-\w*i\w*\b)")
FILE_CMD_RE = re.compile(r"(?:^|(?<=[;&|(\n]))\s*(?:sudo\s+)?(cp|mv|install|rsync|ln|tee|rm|truncate|touch|patch)(?=\s)")
PKG_INSTALL_RE = re.compile(r"(?:^|(?<=[;&|(\n]))\s*(?:sudo\s+)?(?:[A-Z_][A-Z0-9_]*=\S*\s+)*(npm|pnpm|yarn|bun)\b"
                            r"[^;&|\n]*?\s(install|i|ci|add|update|up|upgrade|uninstall|remove|rm|un|link|ln|dedupe|"
                            r"prune|rebuild)(?![-\w])")
GIT_FILE_WRITE_RE = re.compile(r"\bgit\b((?:\s+-C\s+(?:\"[^\"]*\"|'[^']*'|\S+))?)[^;&|\n]*?\s(stash|apply|rm|mv)(?![-\w])")


def _rel(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _ignored(path: str, root: str) -> bool:
    """True when git ignores `path` in the live tree (logs, venv, node_modules, build output). Tracked files are
    never ignored. Unknown (not a repo, git missing, timeout) counts as NOT ignored, so the write is refused."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", root, "check-ignore", "-q", "--", _rel(path, root)],
                           capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _in_node_modules(rel: str) -> bool:
    """`<live>/node_modules/...` or `<live>/<dir>/node_modules/...`: gitignored, but it is the dependency install the
    running backends and the `hermes update` completion tail resolve from. A hand-copied package there that is not
    in package-lock.json shadows the lock's tree (2026-10-29: an extraneous hoisted @rolldown/plugin-babel broke the
    web typecheck and the update tail failed 6x), so writes into it are refused like source writes."""
    parts = Path(rel).parts
    return bool(parts) and (parts[0] == "node_modules" or (len(parts) > 1 and parts[1] == "node_modules"))


def _writable_ignored(path: str, root: str) -> bool:
    """Gitignored and safe to write (logs, venv, build output): node_modules is ignored but not writable."""
    return _ignored(path, root) and not _in_node_modules(_rel(path, root))


def _live_write_target(target: str, base: str, root: str) -> Optional[str]:
    """`target` (resolved against `base`) as a root-relative path, when it is a non-ignored path inside the tree
    (node_modules counts as not ignored)."""
    t = (target or "").strip().strip("\"'")
    if not t or t.startswith("&") or "$" in t or "`" in t or "\n" in t or set(t) <= {"_"}:
        return None
    t = os.path.expanduser(t)
    if not os.path.isabs(t):
        if not base or not os.path.isabs(base):
            return None
        t = os.path.join(base, t)
    t = os.path.normpath(t)
    if not _inside(t, root):
        return None
    try:
        rp = str(Path(t).resolve())
    except Exception:
        rp = t
    if rp == root or _writable_ignored(rp, root):
        return None
    return _rel(rp, root)


def _edit_tool_paths(tool: str, args: dict) -> list:
    out = [str(args.get("path") or "")]
    if tool == "patch":  # V4A multi-file patches name their files in the body
        body = str(args.get("patch") or "")
        out += re.findall(r"(?m)^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", body)
        out += re.findall(r"(?m)^\*\*\* Move to:\s*(.+?)\s*$", body)
    return [p for p in out if p]


def _py_literals(src: str) -> list:
    try:
        return [n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    except Exception:
        return [m.group(2) for m in PY_LITERAL_RE.finditer(src)]


def _python_live_write(src: str, cwd: str, root: str) -> Optional[str]:
    """A tree path Python `src` probably writes: it has a write call and names a non-ignored tree path
    (absolute, `~`-relative, or relative to its cwd / an `os.chdir` literal)."""
    if not PY_WRITE_RE.search(src):
        return None
    m = PY_CHDIR_RE.search(src)
    if m:
        cwd = os.path.expanduser(m.group(2))
    for lit in _py_literals(src):
        if not lit or len(lit) > 400 or "\n" in lit or " " in lit.strip():
            continue
        if not ("/" in lit or re.search(r"\.\w{1,5}$", lit)):
            continue
        hit = _live_write_target(lit, cwd, root)
        if hit:
            return hit
    return None


def _execute_code_live_write(code: str, root: str) -> Optional[str]:
    hit = _python_live_write(code, "", root)
    if hit:
        return f"an execute_code write to `{hit}`"
    for lit in _py_literals(code):  # shell commands handed to terminal(...) from inside the script
        if len(lit) < 6 or " " not in lit or ALLOW_LIVE_RE.search(lit):
            continue
        what = _terminal_live_write(lit, shell_code(lit), "", root)
        if what:
            return f"{what} (from execute_code)"
    return None


def _heredoc_python_bodies(raw: str):
    """(body, text before the opener) for every heredoc fed to a Python interpreter."""
    pos = 0
    while True:
        m = HEREDOC_RE.search(raw, pos)
        if not m:
            return
        nl = raw.find("\n", m.end())
        if nl < 0:
            return
        line_start = raw.rfind("\n", 0, m.start()) + 1
        end = re.compile(r"^\t*" + re.escape(m.group(2)) + r"[ \t]*$", re.M).search(raw, nl + 1)
        if PY_RUNNER_RE.search(raw[line_start:m.start()]):
            yield raw[nl + 1:end.start() if end else len(raw)], raw[:m.start()]
        pos = end.end() if end else len(raw)


def _seg_words(seg: str) -> list:
    import shlex
    try:
        return shlex.split(seg)
    except Exception:
        return seg.split()


def _terminal_live_write(raw: str, cmd: str, wd: str, root: str) -> Optional[str]:
    """A file write into the live tree by a terminal command: an output redirect, `sed -i`/`perl -pi`,
    cp/mv/tee/rm-style file commands, `git stash/apply/rm/mv`, or a Python heredoc / `-c` script that writes a
    tree path. `raw` is the command as sent; `cmd` is `shell_code(raw)` (quoted data and heredoc bodies masked)."""
    home_form = root.replace(str(Path.home()), "~", 1)
    for m in REDIRECT_RE.finditer(cmd):
        hit = _live_write_target(m.group(1), _target_dir(cmd, m.start(), "", wd) or "", root)
        if hit:
            return f"a write to `{hit}`"
    for m in INPLACE_EDIT_RE.finditer(cmd):
        seg = _segment(cmd, m.start())
        d = _target_dir(cmd, m.start(), "", wd) or ""
        for w in _seg_words(seg)[1:]:
            hit = None if w.startswith("-") else _live_write_target(w, d, root)
            if hit:
                return f"`{m.group(1).strip()}` of `{hit}`"
    for m in FILE_CMD_RE.finditer(cmd):
        name = m.group(1)
        seg = _segment(cmd, m.start(1))
        d = _target_dir(cmd, m.start(1), "", wd) or ""
        words = [w for w in _seg_words(seg)[1:] if not w.startswith("-")]
        if name == "patch":
            if d and _inside(d, root) and not _writable_ignored(d, root):
                return "`patch`"
            continue
        if name in ("cp", "install", "rsync", "ln"):
            words = words[-1:]
        for w in words:
            hit = _live_write_target(w, d, root)
            if hit:
                return f"`{name}` of `{hit}`"
    for m in PKG_INSTALL_RE.finditer(cmd):
        words = _seg_words(_segment(cmd, m.start(1)))
        if any(w in ("-g", "--global", "--location=global") for w in words):
            continue
        explicit = ""
        for i, w in enumerate(words):
            if w in ("--prefix", "-C", "--dir", "--cwd") and i + 1 < len(words):
                explicit = words[i + 1]
            elif w.startswith(("--prefix=", "--dir=", "--cwd=")):
                explicit = w.split("=", 1)[1]
        d = _target_dir(cmd, m.start(1), explicit, wd)
        if d and _inside(d, root):
            sub = _rel(os.path.normpath(d), root)
            nm = "node_modules" if sub in (".", "") else f"{sub}/node_modules"
            return f"`{m.group(1)} {m.group(2)}` into `{nm}`"
    for m in GIT_FILE_WRITE_RE.finditer(cmd):
        rest = cmd[m.end():]
        if m.group(2) == "stash" and re.match(r"\s+(list|show)\b", rest):
            continue
        if m.group(2) == "apply" and re.match(r"[^;&|\n]*\s--(check|stat|numstat|summary)\b", rest):
            continue
        explicit = re.sub(r"^\s*-C\s+", "", m.group(1) or "")
        d = _target_dir(cmd, m.start(), explicit, wd)
        if (d and _inside(d, root)) or (d is None and (root in explicit or home_form in explicit)):
            return f"`git {m.group(2)}`"
    for body, prefix in _heredoc_python_bodies(raw):
        pre = shell_code(prefix)
        hit = _python_live_write(body, _target_dir(pre, len(pre), "", wd) or "", root)
        if hit:
            return f"a Python heredoc write to `{hit}`"
    for m in PY_DASH_C_RE.finditer(raw):
        src = m.group(1)[1:-1]
        if m.group(1).startswith('"'):
            src = re.sub(r'\\(["\\$`])', r"\1", src)
        pre = shell_code(raw[:m.start()])
        hit = _python_live_write(src, _target_dir(pre, len(pre), "", wd) or "", root)
        if hit:
            return f"a `python -c` write to `{hit}`"
    return None


WRITE_BLOCK_PREFIXES = ("a ", "write_file", "patch", "`sed", "`perl", "`cp", "`mv", "`install", "`rsync", "`ln",
                        "`tee", "`rm", "`truncate", "`touch", "`patch", "`git stash", "`git apply", "`git rm", "`git mv")


def block_message(what: str, checkout: Path) -> str:
    path = str(checkout).replace(str(Path.home()), "~", 1)
    if "node_modules" in what:
        return (f"COACH LIVE-CHECKOUT GUARD: blocked {what} in {path}, the checkout every running Hermes backend "
                "executes. Its node_modules is the dependency install the backends and the `hermes update` completion "
                "tail resolve from: a package hand-installed or copied there that is not in package-lock.json shadows "
                "the lock's tree (2026-10-29: an extra hoisted @rolldown/plugin-babel broke the web typecheck and the "
                "update tail failed 6x). To get a test or build working, use a worktree with its own node_modules: "
                "`~/.hermes/bin/hermes-core-wt new <slug>` (no hermes-core-wt on this host: `git -C "
                f"{path} worktree add -b fix/<slug> ~/code/_wt/<slug> origin/main`), then `npm ci` and test there. "
                "Only if the user explicitly asked for this exact change in the live tree, rerun with "
                "`COACH_ALLOW_LIVE=1` at the start of the command.")
    if what.startswith(WRITE_BLOCK_PREFIXES):
        return (f"COACH LIVE-CHECKOUT GUARD: blocked {what} in {path}, the checkout every running Hermes backend "
                "executes. Backends lazily import from this tree, so even a transient edit can be loaded live, and "
                "`hermes update` then stamps the version `.dirty`. Reading is fine (read_file, grep, cat, git diff); "
                "write in a worktree: `~/.hermes/bin/hermes-core-wt new <slug>` -> edit/test/commit there -> "
                f"`hermes-core-wt ship <slug>` (no hermes-core-wt on this host: `git -C {path} worktree add -b "
                "fix/<slug> ~/code/_wt/<slug> origin/main`). Probe scripts go in $TMPDIR, not the tree. Only if the "
                "user explicitly asked for this exact change in the live tree, rerun with `COACH_ALLOW_LIVE=1` at "
                "the start of the command.")
    return (f"COACH LIVE-CHECKOUT GUARD: blocked {what} in {path}, the checkout every running Hermes backend executes. "
            "Moving its HEAD or repacking the app makes Desktop restart and kills in-flight turns in every chat "
            "(measured: ~10 Desktop quits on 2026-10-13, mostly from live-tree commits). Do core work in a worktree: "
            "`~/.hermes/bin/hermes-core-wt new <slug>` -> edit/test/commit there -> `hermes-core-wt ship <slug>`; "
            "hermes-sync deploys it when Desktop is idle. Only if the user explicitly asked for this exact change in "
            "the live tree, rerun with `COACH_ALLOW_LIVE=1` at the start of the command.")


def bug_signature(tool: str, result: Any, status: Optional[str], error_type: Optional[str],
                  error_message: Optional[str]) -> Optional[str]:
    """A normalized signature when this failure looks like a Hermes bug (not the agent's own mistake)."""
    text = str(error_message or "")
    d = _as_dict(result) or _leading_dict(result)
    # Decode the envelope first: JSON escaping hides both frame quotes and newlines.
    payloads = [v for k in ("error", "output", "stderr") if isinstance(v := d.get(k), str)]
    if not d and isinstance(result, str):
        payloads.append(result)
    payloads.append(text)
    if tool == "execute_code" and d.get("status") == "success" and not d.get("error"):
        return None  # A successful cell may be reading someone else's traceback.
    cell_failure = False
    for payload in payloads:
        cells = list(re.finditer(r'^\s*File "<cell>",', payload, re.M))
        if tool == "execute_code" and cells:
            cell_failure = True
            # The executor above the cell is infrastructure, not the failing code.
            payload = payload[cells[-1].start():]
        frames = [m for m in TRACE_HERMES_RE.finditer(payload)
                  if not re.search(r'/tools/python-[^/]+/(?:lib|Lib)/', m.group())]
        # Bundled Python lives under .hermes/tools, but stdlib is not Hermes code.
        if frames:
            last = re.findall(
                r"^((?:[\w]+\.)*[A-Z][\w]*(?:Error|Exception):[^\n]{0,120})", payload, re.M)
            head = last[-1] if last else "traceback"
            return _normalize_sig(f"{tool}: {head}")
    if cell_failure:
        return None  # An exception type alone cannot reclassify a caller failure.
    if status == "error" and error_type and (EXC_NAME_RE.match(error_type) or error_type == "thread_missing_result"):
        return _normalize_sig(f"{tool}: {error_type}: {text}")
    if status == "error" and tool in HERMES_INFRA_TOOLS:
        d = _as_dict(result) or _leading_dict(result)
        if _guided_refusal(tool, d):
            return None
        return _normalize_sig(f"{tool}: {text or d.get('error') or 'failed'}")
    return None


def _leading_dict(result: Any) -> dict:
    """The JSON object a result starts with, when Hermes appended guidance text after it (repeat-failure hints)."""
    if isinstance(result, str) and result.lstrip().startswith("{"):
        try:
            d, _ = json.JSONDecoder().raw_decode(result.lstrip())
            return d if isinstance(d, dict) else {}
        except ValueError:
            pass
    return {}


def _guided_refusal(tool: str, d: dict) -> bool:
    """A designed validation refusal that tells the agent how to retry (the agent's mistake, not a Hermes bug).

    The memory tool refuses over-budget adds/patches/batches, zero-match or ambiguous old_text, empty content and
    scan hits on purpose, with the fix in the message, and the agent's next call recovers. Filing those as
    "Hermes bug" cards spent a fix-worker card on working-as-designed guards. Hermes-side memory failures still count:
    an unreadable file (read_failed), external drift (drift_backup) and the terminal per-turn retry cap
    (done=True: the guidance failed to get the agent through).
    """
    if tool != "memory" or d.get("success") is not False or d.get("done") or "drift_backup" in d:
        return False
    return "could not be read right now" not in str(d.get("error") or "")


def _normalize_sig(s: str) -> str:
    s = re.sub(r"(/[\w.@-]+)+", "<path>", s)
    s = re.sub(r"\b[0-9a-f]{6,}\b|\b\d+\b", "<n>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def attach_hint(result: Any, hint: str) -> Any:
    """Put the hint where the model reads it without breaking JSON results for other consumers."""
    if isinstance(result, str):
        d = _as_dict(result)
        if d is not None:
            d["coach_hint"] = hint
            return json.dumps(d, ensure_ascii=False)
        return f"{result}\n\n[coach] {hint}"
    return result



def rich_tool_line(tool: str, args: dict, arg: str = "") -> str:
    """One tool-log line with the arguments that show what the agent started or handed off."""
    try:
        a = args if isinstance(args, dict) else {}
        if tool == "kanban_create":
            return (f"-> kanban_create(title={str(a.get('title'))[:200]!r}, assignee={a.get('assignee')}, "
                    f"body={str(a.get('body'))[:500]!r})")
        if tool == "delegate_task":
            ts = a.get("tasks") or [{"goal": a.get("goal")}]
            return "-> delegate_task(" + " | ".join(str((t or {}).get("goal"))[:300] for t in ts[:3]) + ")"
        if tool in ("patch", "write_file", "read_file"):
            return f"-> {tool}(path={a.get('path')}, {str(a.get('new_string') or a.get('content') or '')[:120]!r})"
        if tool in ("kanban_comment", "kanban_complete", "kanban_block", "cross_session"):
            return f"-> {tool}({json.dumps(a, default=str)[:400]})"
        if tool == "terminal":
            return f"-> terminal({str(a.get('command'))[:300]})"
    except Exception:
        pass
    return f"-> {tool}({arg[:140]})"

# agent-9516de5af8: observe logged only FAILED results (`<- ERROR`), so the report_only judges saw a failed owner lookup
# and nothing from the one that found the card running, and held a true "the fix card is running" reply. After a
# successful owner read (kanban_show, cross_task, `hermes kanban show`; direct or via tool_call) one `<- OK` line says
# what it found. `<- ERROR` readers in reply_check look only for that prefix, so they ignore it.
OWNER_READ_TOOLS = ("kanban_show", "cross_task")
_OWNER_READ_CLI = re.compile(r"\bhermes\s+kanban\b[^\n]*\bshow\b")
OK_LINE_MAX = 200


def owner_read_call(tool: str, args: Any) -> bool:
    name = (tool or "").rsplit("__", 1)[-1]
    a = args if isinstance(args, dict) else {}
    if name in OWNER_READ_TOOLS:
        return True
    if name == "tool_call":
        return any(str((c or {}).get("name") or "").rsplit("__", 1)[-1] in OWNER_READ_TOOLS
                   for c in (a.get("calls") or []) if isinstance(c, dict))
    if name == "terminal":
        return bool(_OWNER_READ_CLI.search(str(a.get("command") or "")))
    return False


def _tasks_in(obj: Any, depth: int = 0) -> list:
    """(task, runs, events) for each Kanban task found in a tool result (JSON strings inside fields are parsed)."""
    if depth > 5:
        return []
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "{[":
            try:
                return _tasks_in(json.loads(s), depth + 1)
            except Exception:
                return []
        return []
    if isinstance(obj, list):
        return [t for x in obj[:10] for t in _tasks_in(x, depth + 1)]
    if not isinstance(obj, dict):
        return []
    # Result formats differ (agent-773e2f779b): kanban_show puts runs/events beside `task`, cross_task nests them
    # inside it. Take the task's own lists when it has them, else the wrapper's.
    t = obj.get("task")
    if isinstance(t, dict) and t.get("status"):
        return [(t, _own_or(t, obj, "runs"), _own_or(t, obj, "events"))]
    if str(obj.get("id") or "").startswith("t_") and obj.get("status"):
        return [(obj, obj.get("runs") or [], obj.get("events") or [])]
    return [x for v in obj.values() if isinstance(v, (dict, list, str)) for x in _tasks_in(v, depth + 1)]


def _own_or(task: dict, outer: dict, key: str) -> list:
    own = task.get(key)
    return own if isinstance(own, list) and own else (outer.get(key) or [])


def _ago(secs: float) -> str:
    secs = max(0, int(secs))
    for unit, size, limit in (("s", 1, 90), ("m", 60, 90 * 60), ("h", 3600, 48 * 3600)):
        if secs < limit:
            return f"{secs // size}{unit}"
    return f"{secs // 86400}d"


def _ts(v: Any) -> Optional[float]:
    """Epoch seconds from epoch s/ms (number or numeric string) or an ISO-8601 string (`...Z` or with offset)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        s = str(v).strip()
        if not re.match(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d", s):
            return None
        try:
            d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)
        except ValueError:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    return f / 1000 if f > 1e12 else f


# Field names per format: kanban_show runs carry `status` + epoch `started_at`, cross_task runs carry `outcome` and
# no time; kanban_show events carry epoch `created_at`, cross_task events an ISO `at`.
_RUN_STATE = ("status", "outcome")
_RUN_TIME = ("started_at", "ended_at")
_EVENT_TIME = ("created_at", "at")


def _first(d: dict, keys: tuple) -> Any:
    return next((d[k] for k in keys if d.get(k) not in (None, "")), None)


def _first_ts(d: dict, keys: tuple) -> Optional[float]:
    return next((t for t in (_ts(d.get(k)) for k in keys) if t), None)


def _task_summary(task: dict, runs: list, events: list, now: float) -> str:
    parts = [f"task={task.get('id')}" if task.get("id") else "", f"status={task.get('status')}"]
    runs = [r for r in runs if isinstance(r, dict)]
    state = None
    if runs:
        # newest by time when runs carry one, else by id (cross_task lists them oldest first, untimed)
        last = max(enumerate(runs), key=lambda ir: (_first_ts(ir[1], _RUN_TIME) or 0, ir[1].get("id") or 0, ir[0]))[1]
        state = _first(last, _RUN_STATE)
        if state:
            parts.append(f"run={state}")
    if not state and task.get("latest_summary"):
        parts.append("summary=" + " ".join(str(task["latest_summary"]).split())[:60])
    evs = [(t, i, e) for i, e in enumerate(events) if isinstance(e, dict) for t in [_first_ts(e, _EVENT_TIME)] if t]
    if evs:
        at, _, e = max(evs, key=lambda tie: tie[:2])  # same-second events: the later-listed one is newer
        parts.append(f"last={e.get('kind')} @ {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(at))} ({_ago(now - at)} ago)")
    return " ".join(p for p in parts if p)


def owner_result_line(tool: str, args: Any, result: Any, now: Optional[float] = None) -> Optional[str]:
    """The `   <- OK ...` line for a successful owner read, else None. Callers skip failed calls."""
    if not owner_read_call(tool, args):
        return None
    now = time.time() if now is None else now
    try:
        found = _tasks_in(result if isinstance(result, (dict, list)) else str(result or ""))
        if found:
            body = " | ".join(_task_summary(t, r, e, now) for t, r, e in found[:2])
        else:
            d = _as_dict(result)
            text = str((d or {}).get("output") or (d or {}).get("result") or result or "")
            m = re.search(r"^\s*status:\s+(\S+)", text, re.M)
            body = (f"status={m.group(1)} " if m else "") + " ".join(text.split())[:160]
    except Exception:
        return None
    return f"   <- OK {body}"[:OK_LINE_MAX]


# agent-9f966b0682: report_only nudged a Kanban worker right after it blocked its card for the user's decision; the
# block had ended the run, so no revised reply could pass. reply_check.lifecycle_park exempts a turn that parked its
# run (a human block or a schedule). The `-> kanban_block(...)` line is cut at 140 chars, which drops `kind` and
# `human_request` for any real reason text, so after a successful park call one `<- OK park={...}` line keeps the facts
# reply_check.human_block needs to classify it (no reason text, no card id).
PARK_TOOLS = ("kanban_block", "kanban_schedule")


def park_result_line(tool: str, args: Any) -> Optional[str]:
    """The `   <- OK park={...}` line for a park call (direct or via tool_call), else None. Callers skip failed calls."""
    name = (tool or "").rsplit("__", 1)[-1]
    a = args if isinstance(args, dict) else {}
    if name == "tool_call":
        for c in a.get("calls") or []:
            if isinstance(c, dict):
                got = park_result_line(str(c.get("name") or ""), c.get("arguments"))
                if got:
                    return got
        return None
    if name not in PARK_TOOLS:
        return None
    facts: dict = {"tool": name}
    if name == "kanban_block":
        facts.update(kind=str(a.get("kind") or ""), human_request=bool(isinstance(a.get("human_request"), dict)
                                                                       and a.get("human_request")),
                     needs=bool(a.get("needs")))
    return "   <- OK park=" + json.dumps(facts, sort_keys=True)


class AgentCoach:
    def __init__(self, checkout: Optional[Path] = None, signals=None, clock: Callable[[], float] = time.monotonic):
        self.checkout = checkout
        self.signals = signals  # SignalStore-like: subagents(sid, n), long_job(sid, reason), bug(sid, sig, sample)
        self.clock = clock
        self.sessions: dict[str, dict] = {}

    def _s(self, sid: str) -> dict:
        s = self.sessions.get(sid)
        if s is None:
            s = self.sessions[sid] = {"turn": 0, "turn_tools": 0, "fails": {}, "recent": deque(maxlen=POLL_WINDOW),
                                      "fired": set(), "pending": [], "bg": {}, "ask": "",
                                      "turn_log": deque(maxlen=60), "turn_rich": deque(maxlen=60), "loop": {"fails": []}}
        return s

    def _fire(self, s: dict, sid: str, rule: str, key: str, **fmt) -> None:
        k = f"{rule}:{key}"
        if k in s["fired"]:
            return
        s["fired"].add(k)
        s["pending"].append(HINTS[rule].format(**fmt))
        if self.signals:
            self.signals.log({"event": "agent_hint", "session_id": sid, "rule": rule})

    def turn_start(self, sid: str, user_message: Any = None, platform: str = "", parent_session_id: str = "",
                   replied_before: bool = False) -> Optional[str]:
        s = self._s(sid)
        s["turn"] += 1
        # plan_first arms on a new build ask while the user has seen no reply in this session; a reply (turn_end)
        # or a go message disarms it. Cron runs and subagents (spec already decided by the parent) never arm it.
        if replied_before:
            s["replied"] = True
        if platform == "cron" or parent_session_id or s.get("replied"):
            s["plan_due"] = False
        elif is_build_ask(user_message):
            s["plan_due"] = True
        elif isinstance(user_message, str) and GO_ASK_RE.search(user_message):
            s["plan_due"] = False
        s["turn_tools"] = 0
        s["turn_log"].clear()
        s["turn_rich"].clear()
        s.pop("turn_commits", None)
        s["turn_t0"] = time.time()  # wall clock: the diff review asks git for commits made since the turn began
        if isinstance(user_message, str) and user_message.strip():
            # the user's own request (preloaded-skill blocks dropped), capped: "Diagnosis only." can end a Kanban body
            # past char 2000 (self-heal agent-668442d68f). Prompt builders clip it (reply_check.request_excerpt).
            s["ask"] = request_text(user_message)[:ASK_CAP]
        return self.take(sid)

    def queue_hint(self, sid: str, text: str) -> None:
        """A hint from a background judge: delivered with the next tool result or the next turn's context."""
        self._s(sid)["pending"].append(text[len("[coach] "):] if text.startswith("[coach] ") else text)

    def turn_end(self, sid: str, reply: str) -> None:
        """Remember the turn's final answer and tool log: the fix-loop judge reads them on the next user turn."""
        s = self._s(sid)
        if isinstance(reply, str) and reply.strip():
            s["replied"] = True
            s["plan_due"] = False
        s["loop"]["last_reply"] = reply[-4000:]
        s["loop"]["last_log"] = list(s["turn_log"])[-25:]
        # the reply check of a no-tool follow-up turn reads these (prev_turn; self-heal agent-1f34642d7f)
        s["loop"]["last_head"] = reply[:800] if isinstance(reply, str) else ""
        s["loop"]["last_rich"] = list(s.get("turn_rich") or [])[-25:]
        s["loop"]["last_end"] = time.time()

    def prev_turn(self, sid: str) -> Optional[dict]:
        """The previous turn of this session for the reply check: {"reply" (its first 800 chars), "rich" (its tool log
        with real arguments), "ended_at", "gap_s" (from its end to this turn's start)}, or None before any turn ended.
        A follow-up turn that only sums up checks made a turn earlier is judged with them (agent-1f34642d7f)."""
        lp = self._s(sid)["loop"]
        end = lp.get("last_end")
        if not end:
            return None
        start = self._s(sid).get("turn_t0") or time.time()
        return {"reply": lp.get("last_head") or "", "rich": list(lp.get("last_rich") or []), "ended_at": end,
                "gap_s": max(0.0, start - end)}

    def loop_state(self, sid: str) -> dict:
        return self._s(sid)["loop"]

    def turn_number(self, sid: str) -> int:
        return self._s(sid)["turn"]

    def turn_context(self, sid: str) -> tuple[str, list[str]]:
        """(latest user ask, this turn's tool log) for the reply check."""
        s = self._s(sid)
        return s["ask"], list(s["turn_log"])

    def kanban_db_down(self, sid: str) -> bool:
        """The session's last Kanban call was refused because the board couldn't be read (stale view or damage)."""
        return bool(self._s(sid).get("kanban_db_down"))

    def turn_commit_lines(self, sid: str) -> list[str]:
        """This turn's terminal commands that mention `git ... commit`, uncut (`-> terminal(<command>)`): the 140-char
        turn_log and 300-char rich log can cut `cd <long path> && git commit` before the commit, so the diff review
        reads the commit dirs from these."""
        return list(self._s(sid).get("turn_commits") or [])

    def turn_rich(self, sid: str) -> list[str]:
        """This turn's tool log with real arguments (kanban_create title and body, delegate goals, patch paths), for the
        report_only second opinion."""
        return list(self._s(sid).get("turn_rich") or [])

    def turn_started(self, sid: str) -> float | None:
        """Wall-clock start of the session's current turn (None before the first turn)."""
        return self._s(sid).get("turn_t0")

    def take(self, sid: str) -> Optional[str]:
        s = self.sessions.get(sid)
        if not s or not s["pending"]:
            return None
        out = " ".join(s["pending"])
        s["pending"] = []
        return out

    def check_live(self, sid: str, tool: str, args: Any) -> None:
        """live_checkout rule for one call. Also run from transform_tool_result: core's sequential path fires
        transform BEFORE post_tool_call, so a hint queued only from observe() lands on the NEXT result."""
        if not sid:
            return
        hit = touches_live_checkout(tool, args if isinstance(args, dict) else {}, self.checkout)
        if hit:
            self._fire(self._s(sid), sid, "live_checkout", "session", path=hit.replace(str(Path.home()), "~", 1))

    def check_kanban_db_write(self, sid: str, tool: str, args: Any) -> None:
        """kanban_db_write rule (once per session): a hand-written SQL write to the board's task tables.
        Also run from transform_tool_result so the hint lands on this very call."""
        if sid and writes_kanban_db(tool, args if isinstance(args, dict) else {}):
            self._fire(self._s(sid), sid, "kanban_db_write", "session")

    def check_widget_send(self, sid: str, tool: str, args: Any) -> None:
        """widget_send rule (once per widget file): a chat widget that ignores hermes.send's ack or relies on browser
        storage. Also run from transform_tool_result so the hint lands on the write itself."""
        if not sid:
            return
        hit = widget_send_issue(tool, args if isinstance(args, dict) else {})
        if hit:
            self._fire(self._s(sid), sid, "widget_send", hit[0], path=hit[0], issue=hit[1])

    def check_plan_first(self, sid: str, tool: str, args: Any) -> None:
        """plan_first rule for one call (also run from transform_tool_result, so the hint lands on this call)."""
        if not sid:
            return
        s = self._s(sid)
        if s.get("plan_due") and is_file_change(tool, args):
            self._fire(s, sid, "plan_first", "session")

    def observe(self, sid: str, tool: str, args: Any, result: Any = None, status: Optional[str] = None,
                duration_ms: int = 0, error_type: Optional[str] = None, error_message: Optional[str] = None) -> None:
        if not sid:
            return
        args = args if isinstance(args, dict) else {}
        s = self._s(sid)
        s["turn_tools"] += 1
        now = self.clock()
        try:
            arg = next((str(args[k]) for k in ("command", "code", "path", "url", "query", "goal", "name")
                        if args.get(k)), json.dumps(args, default=str))
        except Exception:
            arg = ""
        s["turn_log"].append(f"-> {tool}({arg[:140]})")
        s.setdefault("turn_rich", deque(maxlen=60)).append(rich_tool_line(tool, args, arg))
        if tool == "terminal" and "commit" in arg and "git" in arg:
            s.setdefault("turn_commits", deque(maxlen=20)).append(f"-> terminal({arg[:8000]})")
        if failed(tool, result, status):
            s["turn_log"].append(f"   <- ERROR {str(result)[:120]}")
        else:
            ok = owner_result_line(tool, args, result)
            if ok:  # what a successful owner check found, for the report_only judges (agent-9516de5af8)
                s["turn_log"].append(ok)
                s.setdefault("turn_rich", deque(maxlen=60)).append(ok)
            park = park_result_line(tool, args)
            if park:  # this turn parked its run: reply_check.lifecycle_park (agent-9f966b0682)
                s["turn_log"].append(park)
                s.setdefault("turn_rich", deque(maxlen=60)).append(park)
        if is_kanban_call(tool, args):  # sticky across turns until the next Kanban call answers
            s["kanban_db_down"] = bool(failed(tool, result, status) and KANBAN_DB_DOWN.search(str(result)))

        # repeat_failure
        key = command_key(tool, args)
        if failed(tool, result, status):
            n = s["fails"].get(key, 0) + 1
            s["fails"][key] = n
            if n >= REPEAT_FAILS:
                shown = key.split(":", 1)[1][:120] if tool == "terminal" else tool
                self._fire(s, sid, "repeat_failure", key, n=n, cmd=shown)
        else:
            s["fails"].pop(key, None)

        # poll_loop
        poll = is_poll(tool, args)
        s["recent"].append(poll)
        if is_poll_loop(tool, args) or (poll and sum(s["recent"]) >= POLL_HITS):
            self._fire(s, sid, "poll_loop", f"t{s['turn']}" if not is_poll_loop(tool, args) else "loop")

        # no_checkpoint (once per turn)
        if s["turn_tools"] >= CHECKPOINT_TOOLS:
            self._fire(s, sid, "no_checkpoint", f"t{s['turn']}", n=s["turn_tools"])

        # live_checkout (once per session)
        self.check_live(sid, tool, args)
        self.check_kanban_db_write(sid, tool, args)
        self.check_widget_send(sid, tool, args)
        # plan_first (once per session). A clarify the user answered showed them the plan: that counts as a reply.
        if tool == "clarify":
            d = _as_dict(result) or {}
            if d.get("outcome") == "submitted" or any(
                    isinstance(r, dict) and r.get("status") == "answered" for r in d.get("responses") or []):
                s["replied"] = True
                s["plan_due"] = False
        self.check_plan_first(sid, tool, args)

        if not self.signals:
            return
        # user-channel signals
        if tool == "delegate_task":
            tasks = args.get("tasks")
            n = len(tasks) if isinstance(tasks, list) and tasks else 1
            self.signals.add_subagents(sid, n)
        # Long job = it really ran (or was waited on) for 10+ min. A generous `timeout=` alone is not evidence:
        # agents set 600 s caps on 5-second commands, which made every such call look like board work.
        if tool == "terminal":
            if args.get("background"):
                d = _as_dict(result) or {}
                pid = str(d.get("session_id") or d.get("process_id") or d.get("pid") or "")
                if pid:
                    s["bg"][pid] = now
        if tool == "process_manage":
            pid = str(args.get("session_id") or args.get("process_id") or "")
            started = s["bg"].get(pid)
            if started is not None and now - started >= LONG_JOB_S:
                self.signals.long_job(sid, f"waiting on a background job for {int((now - started) // 60)} min")
        if duration_ms and duration_ms >= LONG_JOB_S * 1000:
            self.signals.long_job(sid, f"{tool} ran {int(duration_ms // 60000)} min")
        sig = bug_signature(tool, result, status, error_type, error_message)
        if sig:
            self.signals.bug(sid, sig, str(error_message or "")[:300])
