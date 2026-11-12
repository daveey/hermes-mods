"""user-chat-probe-guard: agents never send test turns into a person's chat.

A fix worker proved its fix with ``hermes chat --resume <David's Desktop chat> --oneshot -q "... Reply exactly
ROUTE_INSTALLED_23ef49"``: the test prompt and the bare token became the newest turns of his real chat and buried
the one question waiting for him (self-heal agent-93a55211b1). This ``pre_tool_call`` hook refuses a terminal,
process or execute_code call (also inside ``bash -c`` payloads and nested ``tool_call`` batches) that resumes a
session with a query (``-q/--query``, ``--query-file``, ``-z/--oneshot``) when that session, looked up in this
profile's state.db, is user-facing (desktop, cli or a messaging platform) and is not the caller's own session.
The block names the replacement: ``hermes sessions branch <id>`` makes a hidden copy, probe the copy, then
``hermes sessions delete --yes <copy>``.

Allowed: interactive resume (no query), worker/internal sessions (kanban, cron, oneshot, tool, ...), probe copies,
a Desktop/cli session an agent opened for its own test (its first user message is itself a "Reply exactly <TOKEN>"
probe) or with no user message yet, the caller's own session, ids this host's store does not know. Opt-out: ``HERMES_PROBE_REAL_CHAT_OK=1`` (process
env or command prefix). ``HERMES_USER_CHAT_PROBE_GUARD=off`` disables the hook.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}
SHELLS = {"sh", "bash", "zsh", "dash"}
OPT_OUT = "HERMES_PROBE_REAL_CHAT_OK"
# Sources a person types into. Everything else (kanban, cron, oneshot, tool, subagent, probe, selfheal-*, ...) is
# an agent's own session.
USER_SOURCES = {"desktop", "cli", "tui", "telegram", "discord", "slack", "whatsapp", "signal", "imessage", "sms",
                "email", "matrix", "mattermost", "webui", "web", "api_server", "acp", "homeassistant", "bluebubbles"}
WRAPPERS = {"sudo", "env", "nice", "timeout", "command", "nohup", "exec", "setsid", "time", "uv", "run"}
QUERY_FLAGS = {"-q", "--query", "--query-file", "-z", "--oneshot"}


def enabled() -> bool:
    return os.environ.get("HERMES_USER_CHAT_PROBE_GUARD", "on").lower() not in {"off", "0", "false", "no"}


def _opted_out_env() -> bool:
    return os.environ.get(OPT_OUT, "").lower() in {"1", "true", "yes", "on"}


def _segments(text: str) -> list[list[str]]:
    """argv lists split on shell control operators; unbalanced quoting falls back to whitespace splitting."""
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError:
        tokens = text.split()
    out, cur = [], []
    for tok in tokens:
        if tok and set(tok) <= set(";&|()\n"):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _strip(argv: list[str]) -> tuple[list[str], bool]:
    """Drop env assignments and wrapper programs; report whether the opt-out was set inline."""
    i, opt_out = 0, False
    while i < len(argv):
        tok = argv[i]
        if re.match(r"^[A-Za-z_]\w*=", tok):
            if tok.split("=", 1)[0] == OPT_OUT and tok.split("=", 1)[1].lower() in {"1", "true", "yes", "on"}:
                opt_out = True
            i += 1
            continue
        base = os.path.basename(tok)
        if base in WRAPPERS:
            i += 1
            if base == "timeout" and i < len(argv) and not argv[i].startswith("-"):
                i += 1
            continue
        if tok.startswith("-") and i > 0 and os.path.basename(argv[i - 1]) in WRAPPERS:
            i += 1
            continue
        break
    return argv[i:], opt_out


def probe_target(argv: list[str]) -> Optional[tuple[str, str]]:
    """``(kind, ref)`` when argv is a hermes invocation that resumes a session AND sends a query; kind is
    ``id`` (--resume/-r) or ``title`` (--continue/-c NAME)."""
    if not argv or os.path.basename(argv[0]) != "hermes":
        return None
    args = argv[1:]
    if args and args[0] not in {"chat"} and not args[0].startswith("-"):
        return None  # another subcommand (sessions, kanban, ...)
    ref: Optional[tuple[str, str]] = None
    query = False
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        flag, _, inline = a.partition("=")
        if flag in {"--resume", "-r"}:
            val = inline if inline else nxt
            if val and not val.startswith("-"):
                ref = ("id", val)
            i += 1 if inline else 2
            continue
        if flag in {"--continue", "-c"}:
            val = inline if inline else (nxt if nxt and not nxt.startswith("-") else None)
            if val:
                ref = ("title", val)
                i += 1 if inline else 2
            else:
                ref = ("latest", "")
                i += 1
            continue
        if flag in QUERY_FLAGS:
            query = True
        i += 1
    return ref if ref and query else None


def find_probes(text: str, depth: int = 0) -> list[tuple[tuple[str, str], bool]]:
    """Every ``((kind, ref), inline_opt_out)`` probe in a shell command string."""
    found = []
    for argv in _segments(text):
        argv, opt_out = _strip(argv)
        if not argv:
            continue
        prog = os.path.basename(argv[0])
        if prog in SHELLS and depth < 3:
            for i, arg in enumerate(argv[1:-1], start=1):
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg):
                    found += [(t, o or opt_out) for t, o in find_probes(argv[i + 1], depth + 1)]
                    break
            continue
        target = probe_target(argv)
        if target:
            found.append((target, opt_out))
    return found


_PY_STR = re.compile(r"""(?s)(\"\"\"(.*?)\"\"\"|'''(.*?)'''|"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')""")


def find_probes_python(code: str) -> list[tuple[tuple[str, str], bool]]:
    """Probes in execute_code: shell strings passed to terminal()/os.system, and argv lists for subprocess."""
    found = []
    for m in _PY_STR.finditer(code):
        s = next(g for g in m.groups()[1:] if g is not None) if any(g is not None for g in m.groups()[1:]) else ""
        if "hermes" in s and ("--resume" in s or "-r " in s or "--continue" in s or "-c " in s):
            found += find_probes(s)
    # list form: ['hermes', 'chat', '--resume', 'X', '-q', 'hi']
    for m in re.finditer(r"\[([^\[\]]*['\"]hermes['\"][^\[\]]*)\]", code):
        items = [next(g for g in x.groups() if g is not None) for x in re.finditer(r"'([^']*)'|\"([^\"]*)\"", m.group(1))]
        target = probe_target(items)
        if target:
            found.append((target, False))
    if "subprocess" in code or "terminal" in code or "os.system" in code:
        return [(t, o or bool(re.search(OPT_OUT + r"['\"]?\s*[:=]\s*['\"]?1", code))) for t, o in found]
    return found


def _db_path() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "state.db"


def _connect(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=3)
        c.execute("PRAGMA query_only=ON")
        return c


def lookup(kind: str, ref: str) -> Optional[dict]:
    """The session row a probe targets, or None when this host's store cannot tell (missing store/row,
    ambiguous prefix)."""
    con = _connect(_db_path())
    if con is None:
        return None
    try:
        cols = "id, source, title, model_config"
        if kind == "id":
            if ref == "latest":
                rows = con.execute(f"select {cols} from sessions order by started_at desc limit 1").fetchall()
            else:
                rows = con.execute(f"select {cols} from sessions where id = ?", (ref,)).fetchall()
                if not rows:
                    rows = con.execute(f"select {cols} from sessions where id like ? or id like ? limit 2",
                                       (ref + "%", "%" + ref)).fetchall()
        elif kind == "title":
            rows = con.execute(f"select {cols} from sessions where title = ? order by started_at desc limit 1",
                               (ref,)).fetchall()
        else:  # bare --continue: the most recent session
            rows = con.execute(f"select {cols} from sessions order by started_at desc limit 1").fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if len(rows) != 1:
        return None
    r = rows[0]
    return {"id": r[0], "source": r[1] or "", "title": r[2] or "", "model_config": r[3]}


# "Reply exactly ROUTE_ACCEPTED_23ef49" / "reply with exactly: READY": an agent's probe, not a person's message.
PROBE_ASK = re.compile(r"\breply\s+(?:with\s+)?exactly[:\s]+[`\"']?[A-Z][A-Z0-9]+", re.I)


def _person_typed(sid: str) -> bool:
    """True when the session's first user message is not itself an agent probe. A Desktop session an agent
    opened for its own live test (first turn "... Reply exactly READY") is not a person's chat; unknown = True."""
    con = _connect(_db_path())
    if con is None:
        return True
    try:
        row = con.execute("select content from messages where session_id = ? and role = 'user' "
                          "and content is not null and trim(content) != '' order by id limit 1", (sid,)).fetchone()
    except sqlite3.Error:
        return True
    finally:
        con.close()
    if row is None:
        return False  # no user message yet: nobody's chat
    m = PROBE_ASK.search(str(row[0]))
    return not (m and m.group(0).split()[-1].lstrip("`\"':").split("_")[0].isupper())


def user_facing(row: dict) -> bool:
    try:
        cfg = json.loads(row.get("model_config") or "{}")
    except (TypeError, ValueError):
        cfg = {}
    if isinstance(cfg, dict) and cfg.get("_probe_copy_of"):
        return False
    if str(row.get("source") or "").lower() not in USER_SOURCES:
        return False
    return _person_typed(row["id"])


def message(row: dict) -> str:
    sid = row["id"]
    title = f' "{row["title"]}"' if row.get("title") else ""
    return (
        f"Refused: session {sid}{title} is a person's {row['source']} chat. A one-shot `hermes chat --resume` "
        "sends a real turn into it: your test prompt and its reply become the newest messages the user sees "
        "(self-heal agent-93a55211b1). Probe a hidden copy instead:\n"
        f"  copy=$(hermes sessions branch {sid})\n"
        "  hermes chat --resume \"$copy\" --oneshot -q '<your probe>'\n"
        "  hermes sessions delete --yes \"$copy\"\n"
        "Acceptance that asks for \"a turn on chat X\" means a turn on a branch copy of X. Only if the user asked "
        f"for a turn in that very chat, prefix the command with {OPT_OUT}=1."
    )


def classify(tool_name: str, args: Any, session_id: str = "") -> Optional[dict]:
    """The user-facing session row a call would probe, or None."""
    args = args if isinstance(args, dict) else {}
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                row = classify(str(call.get("name") or ""), call.get("arguments"), session_id)
                if row:
                    return row
        return None
    if tool_name in SHELL_TOOLS:
        probes = find_probes(" ".join(str(args.get(k) or "") for k in ("command", "input", "data")))
    elif tool_name in PY_TOOLS:
        probes = find_probes_python(str(args.get("code") or ""))
    else:
        return None
    for (kind, ref), opt_out in probes:
        if opt_out:
            continue
        row = lookup(kind, ref)
        if not row or not user_facing(row):
            continue
        if session_id and row["id"] == session_id:
            continue
        return row
    return None


# ── restart-proof backends on the production home (self-heal gateway-9aaf151a15) ──
#
# A restart proof started `HERMES_DESKTOP=1 hermes serve --isolated` on the hub with the real ~/.hermes home.
# `--isolated` isolates the server endpoint, not the stored chats: the backend's startup-resume sweep
# (tui_gateway/startup_resume.py) adopted David's real interrupted chat, and the proof's planned `kill -TERM` of that
# PID interrupted his turn. Core's launch-time check is the enforcement boundary; this recognises the common shapes
# (shell, `bash -c`, `ssh host '...'`/`hub-admin '...'`, execute_code, a local python script) before they run.

SERVE_OPT_OUT = "HERMES_SHARED_HOME_SERVE_OK"
SERVE_SUBCOMMANDS = {"serve", "dashboard"}
REMOTE_WRAPPERS = {"hub-admin"}  # every argument is the remote shell command
SSH_OPTS_WITH_ARG = set("BbcDEeFIiJLlmOoPpQRSWw")
HERMES_GLOBAL_OPTS_WITH_ARG = {"--profile", "-p", "--home"}
_CMD_SUBST = re.compile(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)|`([^`]*)`")
_PROD_HOME = re.compile(r"(?:^|/)\.hermes(?:/profiles/[^/]+)?/?$")
_TRUTHY = {"1", "true", "yes", "on"}


def _serve_opted_out_env() -> bool:
    return os.environ.get(SERVE_OPT_OUT, "").lower() in _TRUTHY


def _caller_home() -> str:
    return os.path.normpath(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"))


def _serve_argv(argv: list[str]) -> Optional[list[str]]:
    """The arguments after ``serve``/``dashboard`` when argv starts a Hermes web backend, else None."""
    if not argv:
        return None
    prog = os.path.basename(argv[0])
    if prog == "hermes":
        args = argv[1:]
    elif re.fullmatch(r"python[\d.]*", prog):
        rest = argv[1:]
        while rest and rest[0].startswith("-") and rest[0] != "-m":
            rest = rest[1:]
        if len(rest) >= 2 and rest[0] == "-m" and rest[1] in {"hermes_cli.main", "hermes_cli", "hermes"}:
            args = rest[2:]
        elif rest and (rest[0].endswith("hermes_cli/main.py") or os.path.basename(rest[0]) == "hermes"):
            args = rest[1:]
        else:
            return None
    else:
        return None
    i = 0
    while i < len(args):
        a = args[i]
        if a in HERMES_GLOBAL_OPTS_WITH_ARG:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return args[i + 1:] if a in SERVE_SUBCOMMANDS else None
    return None


def _assignments(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """Strip leading env assignments and wrapper programs; return (argv, assignments)."""
    env: dict[str, str] = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if re.match(r"^[A-Za-z_]\w*=", tok):
            k, v = tok.split("=", 1)
            env[k] = v
            i += 1
            continue
        base = os.path.basename(tok)
        if base in WRAPPERS:
            i += 1
            if base == "timeout" and i < len(argv) and not argv[i].startswith("-"):
                i += 1
            continue
        if tok.startswith("-") and i > 0 and os.path.basename(argv[i - 1]) in WRAPPERS:
            i += 1
            continue
        break
    return argv[i:], env


def _expand(value: str, shell_vars: dict[str, str]) -> str:
    for _ in range(3):
        new = re.sub(r"\$\{?([A-Za-z_]\w*)\}?",
                     lambda m: shell_vars.get(m.group(1), m.group(0)), value)
        if new == value:
            break
        value = new
    return value


def _home_is_production(value: Optional[str], shell_vars: dict[str, str], substs: dict[str, str],
                        remote: bool) -> bool:
    """True when a HERMES_HOME value (None = inherited) names a production home."""
    if value is None:
        return True  # inherits the live home of the caller (local) or of the remote login (remote)
    value = _expand(value, shell_vars)
    for key, body in substs.items():
        if key in value:
            if "mktemp" in body or "mkdtemp" in body:
                return False
            value = value.replace(key, body.strip())
    if re.search(r"\$\{?HERMES_HOME\}?", value):
        return True
    if not value.strip():
        return True  # HERMES_HOME= (empty) falls back to the default home
    if _PROD_HOME.search(value.rstrip()):
        return True
    if not remote:
        local = os.path.normpath(os.path.expanduser(value.replace("$HOME", "~").replace("${HOME}", "~")))
        if local == _caller_home():
            return True
    return False


def _shell_serve_launches(text: str, remote: bool = False, depth: int = 0) -> list[tuple[str, bool]]:
    """``(description, inline_opt_out)`` for each shared-home restart-proof launch in a shell command string."""
    if depth > 3 or not text:
        return []
    substs: dict[str, str] = {}

    def _sub(m: re.Match) -> str:
        key = f"__HCMDSUBST{len(substs)}__"
        substs[key] = m.group(1) if m.group(1) is not None else (m.group(2) or "")
        return key

    flat = _CMD_SUBST.sub(_sub, text)
    found: list[tuple[str, bool]] = []
    shell_vars: dict[str, str] = {}
    for seg in _segments(flat):
        if seg and seg[0] == "export":
            seg = seg[1:]
        argv, env = _assignments(seg)
        if not argv:
            shell_vars.update(env)  # `home=$(mktemp -d)` / `export HERMES_HOME=...`
            continue
        opt_out = env.get(SERVE_OPT_OUT, "").lower() in _TRUTHY
        prog = os.path.basename(argv[0])
        if prog in SHELLS:
            for i, arg in enumerate(argv[1:-1], start=1):
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg):
                    inner = _restore(argv[i + 1], substs)
                    found += [(d, o or opt_out) for d, o in _shell_serve_launches(inner, remote, depth + 1)]
                    break
            continue
        if prog in REMOTE_WRAPPERS:
            inner = _restore(" ".join(argv[1:]), substs)
            found += [(d, o or opt_out) for d, o in _shell_serve_launches(inner, True, depth + 1)]
            continue
        if prog == "ssh":
            i = 1
            while i < len(argv) and argv[i].startswith("-"):
                flag = argv[i]
                i += 2 if (len(flag) == 2 and flag[1] in SSH_OPTS_WITH_ARG) else 1
            if i + 1 < len(argv):
                inner = _restore(" ".join(argv[i + 1:]), substs)
                found += [(d, o or opt_out) for d, o in _shell_serve_launches(inner, True, depth + 1)]
            continue
        if re.fullmatch(r"python[\d.]*", prog) and not remote:
            script = next((a for a in argv[1:] if not a.startswith("-")), None)
            if script and script.endswith(".py") and "-m" not in argv[1:argv.index(script)]:
                found += [(d, o or opt_out) for d, o in _script_serve_launches(script)]
        rest = _serve_argv(argv)
        if rest is None:
            continue
        isolated = any(a == "--isolated" for a in rest)
        desktop = (env.get("HERMES_DESKTOP") or shell_vars.get("HERMES_DESKTOP") or "").lower() in _TRUTHY
        if not (isolated or desktop):
            continue  # a plain `hermes serve`/`dashboard` is the production backend, not a fixture
        home = env.get("HERMES_HOME", shell_vars.get("HERMES_HOME"))
        if _home_is_production(home, shell_vars, substs, remote):
            where = "on the remote host's live home" if remote else "on this host's live home"
            found.append((f"{' '.join(_restore(a, substs) for a in argv[:6])} ... ({where})", opt_out))
    return found


def _restore(text: str, substs: dict[str, str]) -> str:
    for key, body in substs.items():
        text = text.replace(key, f"$({body})")
    return text


def _script_serve_launches(path: str) -> list[tuple[str, bool]]:
    try:
        p = Path(os.path.expanduser(path))
        if not p.is_file() or p.stat().st_size > 1_000_000:
            return []
        code = p.read_text(errors="replace")
    except OSError:
        return []
    return [(f"{path}: {d}", o) for d, o in _python_serve_launches(code)]


_PY_EXPR = r"""([A-Za-z_][\w.]*\([^()\n]*\)|[^,)}\n]+)"""
_PY_HOME_ASSIGN = re.compile(r"""["']HERMES_HOME["']\s*(?:\]\s*=|:)\s*""" + _PY_EXPR
                             + r"""|\bHERMES_HOME\s*=\s*(?!=)""" + _PY_EXPR)


def _py_home_expr_production(code: str) -> bool:
    """Whether the HERMES_HOME a Python launcher passes is (or inherits) a production home."""
    exprs = [next(g for g in m.groups() if g is not None).strip() for m in _PY_HOME_ASSIGN.finditer(code)]
    exprs = [e for e in exprs if e]
    if not exprs:
        return True  # the child inherits the caller's live home
    for expr in exprs:
        resolved = expr
        name = re.fullmatch(r"(?:str|os\.fspath)\(\s*([A-Za-z_]\w*)\s*\)|([A-Za-z_]\w*)", expr)
        if name:
            ident = name.group(1) or name.group(2)
            m = re.search(rf"^\s*{ident}\s*(?::[^=\n]+)?=\s*(.+)$", code, re.M)
            resolved = m.group(1) if m else expr
        if re.search(r"mkdtemp|TemporaryDirectory|mktemp", resolved):
            continue
        if re.search(r"""\.hermes["']\s*\)*\s*$|\.hermes/profiles/[^"'/]+["']\s*\)*\s*$""", resolved.strip()) or \
                re.search(r"get_hermes_home\(|environ(?:\.get\(|\[)\s*['\"]HERMES_HOME", resolved):
            return True
    return False


def _python_serve_launches(code: str) -> list[tuple[str, bool]]:
    found: list[tuple[str, bool]] = []
    runs = bool(re.search(r"subprocess|terminal\(|os\.system|Popen|os\.exec", code))
    if not runs:
        return found
    opt_out = bool(re.search(SERVE_OPT_OUT + r"['\"]?\s*[:=]\s*['\"]?1", code))
    for m in _PY_STR.finditer(code):
        s = next((g for g in m.groups()[1:] if g is not None), "")
        if "serve" in s or "dashboard" in s:
            found += [(d, o or opt_out) for d, o in _shell_serve_launches(s)]
    desktop = bool(re.search(r"""HERMES_DESKTOP["']?\s*(?:\]\s*=|:|=)\s*["']1["']""", code))
    for m in re.finditer(r"\[([^\[\]]*)\]", code):
        items = [next(g for g in x.groups() if g is not None)
                 for x in re.finditer(r"'([^']*)'|\"([^\"]*)\"", m.group(1))]
        sub = next((x for x in items if x in SERVE_SUBCOMMANDS), None)
        if not sub:
            continue
        if not ("--isolated" in items or desktop):
            continue
        if _py_home_expr_production(code):
            found.append((f"python launch [{m.group(1).strip()[:80]}] (inherits or passes the live home)", opt_out))
    return found


def classify_serve(tool_name: str, args: Any) -> Optional[str]:
    """A description of the first shared-home restart-proof backend launch in a tool call, or None."""
    args = args if isinstance(args, dict) else {}
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                hit = classify_serve(str(call.get("name") or ""), call.get("arguments"))
                if hit:
                    return hit
        return None
    if tool_name in SHELL_TOOLS:
        launches = _shell_serve_launches(" ".join(str(args.get(k) or "") for k in ("command", "input", "data")))
    elif tool_name in PY_TOOLS:
        launches = _python_serve_launches(str(args.get("code") or ""))
    else:
        return None
    return next((d for d, opt_out in launches if not opt_out), None)


def serve_message(desc: str) -> str:
    return (
        f"Refused: this starts a restart/fixture backend on a production Hermes home: {desc}. "
        "`--isolated` isolates the server endpoint, not the stored chats: at boot the backend's startup-resume "
        "sweep adopts every interrupted chat in that home, so killing or restarting it then interrupts a person's "
        "real turn (self-heal gateway-9aaf151a15). Run the proof on a disposable home and remove it afterwards:\n"
        "  HERMES_HOME=$(mktemp -d) HERMES_DESKTOP=1 hermes serve --isolated --host 127.0.0.1 --port 0\n"
        "Never copy auth.json, .env or other credentials into the proof home; fetch a secret the proof needs with "
        "`token-broker exec --scope <scope> --reason <why> -- <cmd>`. Only for a real (non-test) backend on the "
        f"production home, prefix the command with {SERVE_OPT_OUT}=1."
    )


def _pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> Optional[dict]:
    if not enabled():
        return None
    try:
        serve = None if _serve_opted_out_env() else classify_serve(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("user-chat-probe-guard classify_serve failed", exc_info=True)
        serve = None
    if serve:
        logger.info("user-chat-probe-guard blocked %s shared-home backend launch: %s", tool_name, serve)
        return {"action": "block", "message": serve_message(serve)}
    try:
        if _opted_out_env():
            return None
        row = classify(tool_name, args, session_id or "")
    except Exception:  # never let this plugin break a tool call
        logger.debug("user-chat-probe-guard classify failed", exc_info=True)
        return None
    if not row:
        return None
    logger.info("user-chat-probe-guard blocked %s into %s (%s)", tool_name, row["id"], row["source"])
    return {"action": "block", "message": message(row)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
