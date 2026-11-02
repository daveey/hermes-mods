"""backend-restart-guard: agents restart the shared Desktop backend only through the pause wrapper.

A restart of ``hermes-always-on-dashboard``, ``hermes-gateway`` or a ``hermes serve``/dashboard process cuts
every Desktop turn in flight; those turns come back with the CRASH resume note. ``hermes-sync pause-turns
--timeout N -- CMD`` holds admission, asks resumable turns to pause at a step boundary, waits, then runs CMD.
This ``pre_tool_call`` hook refuses a bare ``systemctl [--user] restart|stop|kill|... <unit>`` and
``pkill|killall|kill`` of a ``hermes serve``/dashboard process from terminal, execute_code and process calls
(also inside ``bash -c`` payloads and nested ``tool_call`` batches) and names the wrapper.
Wrapped service restarts are allowed. Literal shell signals, pattern kills and inline Python os.kill/os.killpg calls to live gateway,
launcher and Desktop backend PIDs are refused even through the wrapper; signal 0 is allowed.
Use sample/py-spy for stacks, not signals. ``HERMES_BACKEND_RESTART_GUARD=off`` disables it.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
import subprocess
import os
import re
import shlex
from typing import Any, Optional

logger = logging.getLogger(__name__)

SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}
UNIT = re.compile(r"^(?:hermes-always-on-dashboard|hermes-gateway(?:-[\w.@-]+)?|hermes-dashboard)(?:\.service)?$")
DISRUPTIVE = {"restart", "stop", "kill", "try-restart", "reload-or-restart", "try-reload-or-restart",
              "force-reload", "condrestart", "isolate", "reboot", "poweroff"}
SHELLS = {"sh", "bash", "zsh", "dash"}
PROC_PATTERN = re.compile(r"hermes[\w\s./-]*\b(?:serve|dashboard)\b|hermes[_-]?dashboard|hermes-always-on", re.I)
WRAPPER = "~/.hermes/sync-runner/hermes-sync pause-turns"
REMOTE_CONNECT_TIMEOUT = 10  # ssh ConnectTimeout for remote PID discovery; run limit is this + 5 s

MESSAGE = (
    "Refused: restarting the shared Hermes dashboard/gateway/serve backend cuts every live Desktop turn, "
    "which then resumes with the CRASH note. Run the restart through the pause wrapper so turns pause at a "
    "step boundary first:\n"
    f"  python3 {WRAPPER} --timeout 180 -- <the same command>\n"
    "e.g. python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- "
    "systemctl --user restart hermes-always-on-dashboard.service\n"
    "The wrapper holds admission, waits for resumable turns to pause, runs the command under the hold, then "
    "releases it. It still runs the command when a turn does not pause within the timeout.\n"
    "Never signal a live gateway, its launcher, or Desktop backend (only signal 0 is safe). "
    "For restart use python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- "
    "hermes gateway restart. For stacks use sample <pid> 5 on macOS or "
    "py-spy dump --nonblocking --pid <pid> on Linux, never a signal."
)


def enabled() -> bool:
    return os.environ.get("HERMES_BACKEND_RESTART_GUARD", "on").lower() not in {"off", "0", "false", "no"}


SUBST = "__brg_subst__"  # stands in for an extracted $(...)/`...` body in the outer command


def _subst_end(text: str, i: int) -> Optional[int]:
    """Index of the ')' closing a $(...)/<(...) body starting at ``i``; quoting restarts inside it."""
    depth, single, double = 1, False, False
    while i < len(text):
        c = text[i]
        if single:
            single = c != "'"
        elif c == "\\":
            i += 1
        elif c == "'" and not double:
            single = True
        elif c == '"':
            double = not double
        elif c == "$" and text.startswith("$(", i):
            end = _subst_end(text, i + 2)
            if end is None:
                return None
            i = end
        elif c == "`":
            end = text.find("`", i + 1)
            if end < 0:
                return None
            i = end
        elif not double and c == "(":
            depth += 1
        elif not double and c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _substitutions(text: str) -> tuple[str, list[str]]:
    """Pull command-substitution bodies out of ``text``.

    shlex does not understand $(...), so double quotes nested inside one ("$(grep -E "a|kill" f)")
    close early and words of a quoted pattern became commands. Bodies the current shell would run
    ($(...), `...`, <(...), >(...) outside single quotes, also inside double quotes) are returned to be
    split as command lists of their own; the outer text keeps a placeholder. Single-quoted text is
    literal here and is examined when its consumer (bash -c, ssh) is parsed. Unbalanced input is
    left unchanged (the old splitting applies).
    """
    out, bodies, i, single, double = [], [], 0, False, False
    while i < len(text):
        c = text[i]
        if single:
            single = c != "'"
        elif c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        elif c == "'" and not double:
            single = True
        elif c == '"':
            double = not double
        elif (c == "$" or (c in "<>" and not double)) and text.startswith("(", i + 1):
            end = _subst_end(text, i + 2)
            if end is not None:
                bodies.append(text[i + 2:end])
                out.append(SUBST)
                i = end + 1
                continue
        elif c == "`":
            end = i + 1
            while end < len(text) and text[end] != "`":
                end += 2 if text[end] == "\\" else 1
            if end < len(text):
                bodies.append(text[i + 1:end])
                out.append(SUBST)
                i = end + 1
                continue
        out.append(c)
        i += 1
    return "".join(out), bodies


def _segments(text: str) -> list[list[str]]:
    """argv lists split on shell control operators; unbalanced quoting falls back to whitespace splitting.
    Command-substitution bodies are split as separate command lists (see ``_substitutions``)."""
    text, bodies = _substitutions(text)
    nested = [argv for body in bodies for argv in _segments(body)]
    return _split(text) + nested


def _split(text: str) -> list[list[str]]:
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace = " \t\r"  # unquoted newlines are command boundaries, not argument whitespace
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


def _is_wrapped(argv: list[str]) -> bool:
    """`<...>/hermes-sync pause-turns ... -- CMD` (optionally behind python/sudo/env)."""
    argv = _strip(argv)
    if argv and re.fullmatch(r"python(?:3(?:\.\d+)?)?", os.path.basename(argv[0])):
        argv = argv[1:]
    return len(argv) >= 2 and os.path.basename(argv[0]) == "hermes-sync" and argv[1] == "pause-turns"


def _strip(argv: list[str]) -> list[str]:
    i = 0
    while i < len(argv):
        tok = argv[i]
        if re.match(r"^[A-Za-z_]\w*=", tok) or os.path.basename(tok) in {"sudo", "env", "nice", "timeout", "command",
                                                                       "nohup", "exec", "setsid"}:
            i += 1
            if os.path.basename(tok) == "timeout" and i < len(argv):
                i += 1  # its duration
            continue
        if tok.startswith("-") and i > 0 and os.path.basename(argv[i - 1]) in {"sudo", "env", "nice"}:
            i += 1
            continue
        break
    return argv[i:]


def _ssh_parts(args: list) -> tuple:
    """Service-owner-guard's SSH option/destination unwrapping."""
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
    return opts, args[i] if i < len(args) else "", " ".join(args[i + 1:])


def _inspection_ssh_options(opts: list) -> list:
    """Never execute ProxyCommand/LocalCommand or create forwards from hook input."""
    safe, i = [], 0
    allowed = {'port', 'user', 'hostname', 'identityfile', 'identitiesonly'}
    while i < len(opts):
        arg = opts[i]
        i += 1
        if arg in {'-i', '-p', '-l', '-o'} and i < len(opts):
            value = opts[i]
            i += 1
            if arg != '-o' or re.split(r'[=\s]', value.lower(), 1)[0] in allowed:
                safe.extend([arg, value])
        elif arg.startswith(('-i', '-p', '-l')) and len(arg) > 2:
            safe.append(arg)
        elif arg.startswith('-o') and re.split(r'[=\s]', arg[2:].lower(), 1)[0] in allowed:
            safe.append(arg)
    return safe


def _backend_argv(text: str) -> bool:
    try:
        argv = shlex.split(text)
    except ValueError:
        argv = text.split()
    # Role must be a whole argv element, not prose inside a Python/SSH payload.
    roles = {"serve", "gateway", "dashboard"}
    for i, token in enumerate(argv):
        if token not in roles or i == 0:
            continue
        prefix = argv[:i]
        if any(os.path.basename(a) in {"hermes", "hermes_cli.main"} or
               a.endswith("/hermes_cli/main.py") for a in prefix):
            return True
        if "-c" in prefix and any("hermes_bootstrap" in a or "hermes_cli" in a for a in prefix):
            return True
    return False


def protected_processes(host=None) -> dict:
    """Fresh read-only PID/argv snapshot. Never send a signal, locally or remotely."""
    try:
        if host:
            opts, dest = host
            # Execute this same discovery code on the target, not local PID guesses.
            source = Path(__file__).read_text()
            source += '\nprint(json.dumps(protected_processes()))\n'
            safe_opts = _inspection_ssh_options(opts)
            # A ProxyCommand route probe counts against ConnectTimeout (the MacBook's hub proxy spends
            # up to 4 s before connecting), so allow for proxied hosts. Still fail open when unreachable.
            try:
                res = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes",
                                      "-o", f"ConnectTimeout={REMOTE_CONNECT_TIMEOUT}",
                                      "-o", "ClearAllForwardings=yes", *safe_opts, dest, "python3 -"], input=source,
                                     capture_output=True, text=True, timeout=REMOTE_CONNECT_TIMEOUT + 5)
            except subprocess.TimeoutExpired:
                logger.warning("backend-restart-guard PID discovery failed open: remote %s timed out after %ss",
                               dest, REMOTE_CONNECT_TIMEOUT + 5)
                return {}
            if res.returncode:
                tail = " | ".join((res.stderr or "").strip().splitlines()[-3:])[-300:]
                logger.warning("backend-restart-guard PID discovery failed open: remote %s rc=%s: %s",
                               dest, res.returncode, tail or "(no stderr)")
                return {}
            return {int(p): row for p, row in json.loads(res.stdout).items()}
        res = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,args="],
                             capture_output=True, text=True, timeout=2, check=True)
        table = {}
        for line in res.stdout.splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) == 4 and all(p.isdigit() for p in parts[:3]):
                table[int(parts[0])] = {"ppid": int(parts[1]), "pgid": int(parts[2]), "args": parts[3]}
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        # A profile runtime must also protect the default gateway and sibling profiles.
        root = home.parent.parent if home.parent.name == "profiles" else home
        pids = {p for p, row in table.items() if _backend_argv(row['args'])}
        files = [root / 'gateway.pid', *root.glob('profiles/*/gateway.pid')]
        if home != root:
            files.append(home / 'gateway.pid')
        for file in files:
            try:
                value = json.loads(file.read_text())
                pid = int(value['pid'] if isinstance(value, dict) else value)
                if pid in table:
                    pids.add(pid)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, KeyError):
                logger.warning("backend-restart-guard cannot read %s; fail open for this file", file, exc_info=True)
        # Immediate launcher/wrapper only. Never promote PID 1 or an interactive shell
        # to a protected service merely because it launched a gateway.
        for pid in list(pids):
            parent = table[pid]['ppid']
            if parent > 1 and parent in table:
                cmd = table[parent]['args']
                if re.search(r'hermes|gateway|dashboard', cmd, re.I):
                    pids.add(parent)
        return {p: table[p] for p in pids}
    except Exception:
        logger.warning("backend-restart-guard PID discovery failed open", exc_info=True)
        return {}


def _signal_args(args: list, prog='kill') -> tuple:
    """(signal, operands); kill -s/-n differ from pkill's session/newest flags."""
    sig, out, i, operands, selected = 'TERM', [], 0, False, False
    while i < len(args):
        arg = args[i]
        i += 1
        if operands:
            out.append(arg)
        elif arg == '--':
            operands = True
        elif (arg == '--signal' or (prog == 'kill' and arg in {'-s', '-n'})) and i < len(args):
            sig, selected = args[i], True
            i += 1
        elif arg.startswith('--signal='):
            sig, selected = arg.split('=', 1)[1], True
        elif re.fullmatch(r'-(?:\d+|(?:SIG)?[A-Z][A-Z0-9]+)', arg) and not out and not selected:
            sig, selected = arg[1:], True
        else:
            out.append(arg)
    return sig, out


def _targets_pid(pid: int, rows: dict, group=False) -> bool:
    if group:
        return any(r['pgid'] == (os.getpgrp() if pid == 0 else pid) for r in rows.values())
    if pid == -1:
        return bool(rows)
    if pid <= 0:
        return any(r['pgid'] == (os.getpgrp() if pid == 0 else -pid) for r in rows.values())
    return pid in rows


def _python_signals(code: str, host=None) -> Optional[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    rows = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == 'os' and func.attr in {'kill', 'killpg'}):
            continue
        try:
            pid = ast.literal_eval(node.args[0])
            if not isinstance(pid, int):
                continue
        except (ValueError, TypeError):
            continue
        if isinstance(node.args[1], ast.Constant) and node.args[1].value == 0:
            continue
        if rows is None:
            rows = protected_processes(host)
        if _targets_pid(pid, rows, func.attr == 'killpg'):
            return f'Python os.{func.attr} of protected backend {pid}'
    return None


def _signal_argv(argv: list, depth=0, host=None) -> Optional[str]:
    argv = _strip(argv)
    if not argv or depth > 5:
        return None
    prog, args = os.path.basename(argv[0]), argv[1:]
    if _is_wrapped(argv) and '--' in args:
        return _signal_argv(args[args.index('--') + 1:], depth + 1, host)
    if prog in SHELLS:
        for i, arg in enumerate(args[:-1]):
            if re.fullmatch(r'-[a-zA-Z]*c[a-zA-Z]*', arg):
                return _signal_shell(args[i + 1], depth + 1, host)
    if prog == 'ssh':
        opts, dest, command = _ssh_parts(args)
        return _signal_shell(command, depth + 1, (opts, dest))
    if re.fullmatch(r'python(?:3(?:\.\d+)?)?', prog) and '-c' in args:
        i = args.index('-c')
        return _python_signals(args[i + 1], host) if i + 1 < len(args) else None
    if prog not in {'kill', 'pkill', 'killall'}:
        return None
    sig, rest = _signal_args(args, prog)
    if sig in {'0', 'EXIT', 'SIG0'} or (prog == 'kill' and args and args[0] in {'-l', '-L'}):
        return None
    rows = protected_processes(host)
    if prog == 'kill':
        for operand in rest:
            if re.fullmatch(r'-?\d+', operand) and _targets_pid(int(operand), rows):
                return f'signal {sig} to protected backend {operand}'
    else:
        # Match live command lines as well as canonical names (a pid-file fixture
        # can have a deliberately unrelated argv). Ignore option values.
        patterns, i = [], 0
        valued = {'-u', '-U', '-g', '-G', '-P', '-t', '-s', '-F', '--uid', '--euid'}
        while i < len(rest):
            arg = rest[i]
            i += 1
            if arg in valued:
                i += 1
            elif not arg.startswith('-'):
                patterns.append(arg)
        for pattern in patterns:
            if PROC_PATTERN.search(pattern) or re.search(r'hermes[\w\s./-]*\bgateway\b', pattern, re.I):
                return f'{prog} pattern targeting shared backend'
            try:
                rx = re.compile(pattern)
                for row in rows.values():
                    cmd = row['args']
                    if prog == 'killall':
                        match = os.path.basename(cmd.split()[0]) == pattern
                    else:
                        match = bool(rx.search(cmd))
                    if match:
                        return f'{prog} matching protected backend'
            except re.error:
                continue
    return None


def _signal_shell(text: str, depth=0, host=None) -> Optional[str]:
    for argv in _segments(text):
        why = _signal_argv(argv, depth, host)
        if why:
            return why
    return None


def _check_argv(argv: list[str], depth: int = 0) -> Optional[str]:
    if _is_wrapped(argv):
        return None
    argv = _strip(argv)
    if not argv:
        return None
    prog = os.path.basename(argv[0])
    args = argv[1:]
    if prog in SHELLS and depth < 3:
        for i, arg in enumerate(args[:-1]):
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg):
                return check_shell(args[i + 1], depth + 1)
    if prog == "ssh" and depth < 3:
        # OpenSSH options with separate arguments must not become the remote program.
        i = 0
        takes_value = set("BbcDEeFIiJLlmOopQRSWw")
        while i < len(args):
            arg = args[i]
            if arg == "--":
                i += 1
                break
            if not arg.startswith("-") or arg == "-":
                break
            i += 1
            if len(arg) == 2 and arg[1] in takes_value:
                i += 1
        # Skip the destination; ssh joins the remaining args into a remote shell string.
        return check_shell(" ".join(args[i + 1:]), depth + 1)
    if prog == "systemctl":
        verbs = [a for a in args if not a.startswith("-")]
        if verbs and verbs[0] in DISRUPTIVE and any(UNIT.match(u) for u in verbs[1:]):
            return "systemctl " + verbs[0] + " of the shared backend"
        return None
    if prog in {"pkill", "killall"}:
        if _signal_args(args, prog)[0] in {'0', 'EXIT', 'SIG0'}:
            return None
        if any(PROC_PATTERN.search(a) for a in args if not a.startswith("-")):
            return prog + " of the shared backend"
        return None
    if prog == "hermes" and len(args) >= 2 and args[0] in {"gateway", "dashboard"} and args[1] in {"restart", "stop"}:
        return "hermes " + args[0] + " " + args[1]
    return None


def check_shell(text: str, depth: int = 0) -> Optional[str]:
    for argv in _segments(text):
        why = _check_argv(argv, depth)
        if why:
            return why
    return None


PY_SYSTEMCTL = re.compile(r"systemctl", re.I)


def check_python(code: str) -> Optional[str]:
    if "pause-turns" in code:
        return None
    if PY_SYSTEMCTL.search(code) and re.search(r"[\"'](?:%s)[\"']" % "|".join(sorted(DISRUPTIVE)), code) and \
            re.search(r"hermes-always-on-dashboard|hermes-gateway|hermes-dashboard", code):
        return "systemctl restart/stop of the shared backend from Python"
    if re.search(r"\b(?:system|run|call|Popen|check_call|check_output)\b", code) and \
            re.search(r"(?:pkill|killall)[^\n]*hermes[^\n]*(?:serve|dashboard)", code):
        return "pkill of the shared backend from Python"
    return None


def classify(tool_name: str, args: Any) -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        text = " ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return _signal_shell(text) or check_shell(text)
    if tool_name in PY_TOOLS:
        code = str(args.get("code") or "")
        return _python_signals(code) or check_python(code)
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"))
                if why:
                    return why
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        if not enabled():
            return None
        why = classify(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("backend-restart-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("backend-restart-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": f"{MESSAGE}\n({why})"}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
