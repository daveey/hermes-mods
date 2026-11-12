"""kanban-worker-nice: run Kanban workers (and everything they spawn) at a lower CPU priority.

When the plugin loads inside a dispatcher-spawned Kanban worker (``HERMES_KANBAN_TASK`` set), it
raises the worker process's own niceness. Niceness is inherited across fork/exec, so every child
the worker starts afterwards (terminal shells, xcodebuild + SWBBuildService + swift-frontend,
test runners, cargo...) runs niced too. On configured Linux orchestration hosts,
chat/subagent terminal commands also get a niceness floor via request middleware.
The gateway process and the user's own interactive terminals are untouched.

Config: env ``HERMES_KANBAN_WORKER_NICE`` (0-20, default 10). 0 disables.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shlex
import socket
import sys


def protected_hosts():
    """Same host-list convention as cpu-load-guard, with independent policy."""
    raw = os.environ.get("HERMES_TERMINAL_NICE_HOSTS")
    if raw is not None:
        return [host.strip() for host in raw.split(',') if host.strip()]
    path = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes') / 'kanban-worker-nice.yaml'
    try:
        text = path.read_text()
    except OSError:
        return ['hermes-hub']
    # Support the normal flat YAML list without requiring PyYAML in production.
    lines = [line.split('#', 1)[0].strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if lines and lines[0] == 'protected_hosts:' and all(line.startswith('- ') for line in lines[1:]):
        return [line[2:].strip().strip('\"\'') for line in lines[1:]]
    try:
        import yaml
        hosts = (yaml.safe_load(text) or {}).get('protected_hosts')
        if isinstance(hosts, list):
            return [str(host) for host in hosts]
    except Exception:
        pass
    return ['hermes-hub']


def command_opt_out(command):
    """Only a leading literal assignment, not quoted mentions later in the script."""
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        return False
    if tokens and tokens[0] == 'env':
        tokens = tokens[1:]
    for token in tokens:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=.*', token, re.S):
            break
        if token == 'HUB_NICE_OK=1':
            return True
    return False


_MARKER = '# kanban-worker-nice terminal floor\n'


def terminal_request(tool_name='', args=None, **_):
    """Rewrite the shell before approvals; never renice the shared agent process.

    A prefix (not a nested bash -c) preserves terminal cwd/export tracking. Each
    terminal invocation gets a fresh shell, so an opt-out can still start at zero.
    The inner terminal RPC from execute_code uses this same request middleware.
    """
    if tool_name != 'terminal' or not isinstance(args, dict) or sys.platform != 'linux':
        return None
    # Core's fixed five-second HTTP readback is direct exec, not a shell.
    if args.get('http_readback'):
        return None
    if os.environ.get('HERMES_KANBAN_TASK') or os.environ.get('HERMES_KANBAN_WORKER_TASK'):
        return None
    if os.environ.get('HUB_NICE_OK') == '1':
        return None
    command = args.get('command')
    if not isinstance(command, str) or command.startswith(_MARKER) or command_opt_out(command):
        return None
    host = socket.gethostname().lower()
    if not any(host.startswith(h.lower()) for h in protected_hosts() if h):
        return None
    want = min(19, target_nice())
    if want <= 0:
        return None
    prefix = (_MARKER + f'if [ "$(nice)" -lt {want} ]; then '
              f'renice -n {want} -p $$ >/dev/null || exit $?; fi\n')
    return {'args': dict(args, command=prefix + command),
            'source': 'kanban-worker-nice', 'reason': 'host terminal niceness floor'}

logger = logging.getLogger(__name__)

DEFAULT_NICE = 10
ENV_VAR = "HERMES_KANBAN_WORKER_NICE"


def target_nice(env=None) -> int:
    env = os.environ if env is None else env
    raw = env.get(ENV_VAR)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_NICE
    try:
        val = int(str(raw).strip())
    except ValueError:
        return DEFAULT_NICE
    return max(0, min(20, val))


def apply(env=None, getprio=None, setprio=None) -> int | None:
    """Raise this process's niceness to the target if it's a Kanban worker.

    Returns the niceness set, or None when nothing was changed. Never lowers niceness (that
    needs root and would undo an operator's own ``renice``).
    """
    env = os.environ if env is None else env
    if not env.get("HERMES_KANBAN_TASK"):
        return None
    want = target_nice(env)
    if want <= 0 or not hasattr(os, "setpriority"):
        return None
    getprio = getprio or (lambda: os.getpriority(os.PRIO_PROCESS, 0))
    setprio = setprio or (lambda n: os.setpriority(os.PRIO_PROCESS, 0, n))
    try:
        cur = getprio()
        if cur >= want:
            return None
        setprio(want)
        return want
    except OSError as exc:  # pragma: no cover - permission/platform oddities
        logger.debug("kanban-worker-nice: setpriority failed: %s", exc)
        return None


def register(ctx) -> None:
    ctx.register_middleware('tool_request', terminal_request)
    applied = apply()
    if applied is not None:
        logger.info("kanban-worker-nice: worker %s niced to %d", os.environ.get("HERMES_KANBAN_TASK"), applied)
