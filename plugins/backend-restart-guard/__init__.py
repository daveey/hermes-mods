"""backend-restart-guard: agents restart the shared Desktop backend only through the pause wrapper.

A restart of ``hermes-always-on-dashboard``, ``hermes-gateway`` or a ``hermes serve``/dashboard process cuts
every Desktop turn in flight; those turns come back with the CRASH resume note. ``hermes-sync pause-turns
--timeout N -- CMD`` holds admission, asks resumable turns to pause at a step boundary, waits, then runs CMD.
This ``pre_tool_call`` hook refuses a bare ``systemctl [--user] restart|stop|kill|... <unit>`` and
``pkill|killall|kill`` of a ``hermes serve``/dashboard process from terminal, execute_code and process calls
(also inside ``bash -c`` payloads and nested ``tool_call`` batches) and names the wrapper. The wrapped form is
allowed. ``HERMES_BACKEND_RESTART_GUARD=off`` disables it.
"""

from __future__ import annotations

import logging
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

MESSAGE = (
    "Refused: restarting the shared Hermes dashboard/gateway/serve backend cuts every live Desktop turn, "
    "which then resumes with the CRASH note. Run the restart through the pause wrapper so turns pause at a "
    "step boundary first:\n"
    f"  python3 {WRAPPER} --timeout 180 -- <the same command>\n"
    "e.g. python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- "
    "systemctl --user restart hermes-always-on-dashboard.service\n"
    "The wrapper holds admission, waits for resumable turns to pause, runs the command under the hold, then "
    "releases it. It still runs the command when a turn does not pause within the timeout."
)


def enabled() -> bool:
    return os.environ.get("HERMES_BACKEND_RESTART_GUARD", "on").lower() not in {"off", "0", "false", "no"}


def _segments(text: str) -> list[list[str]]:
    """argv lists split on shell control operators; unbalanced quoting falls back to whitespace splitting."""
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
        return check_shell(" ".join(str(args.get(k) or "") for k in ("command", "input", "data")))
    if tool_name in PY_TOOLS:
        return check_python(str(args.get("code") or ""))
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
