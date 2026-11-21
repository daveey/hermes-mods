"""Bounded deterministic lint for Desktop reopen asks; no probes or model calls."""
from __future__ import annotations

import json
import re
import time

MAX_HOLDS = 2
MAX_AGE = 15 * 60
HOLD_MESSAGE = (
    "Desktop reopen ask without readiness proof: run `hermes-core-wt eta <sha>` "
    "(or `tailnet_exec … hermes-sync core-eta <sha>`). If NOT ready, don't ask; "
    "park a watch card that reports when the line says 'a Desktop quit + reopen runs it'.")
_TARGET = (r"(?:(?:the|your)\s+)?(?:Hermes(?:\s+Desktop)?(?:\s+app)?|Desktop(?:\s+app)?)\b"
           r"(?!\s+(?:gateway|serve|server|backend|dashboard)\b)")
_ASK = re.compile(
    r"\b(?:quit|reopen|restart|relaunch)\s+(?:and\s+(?:reopen|restart)\s+)?" + _TARGET
    + r"|\b(?:restart|relaunch)\s+(?:(?:the|your)\s+)?app\s+to\s+apply\b"
    + r"|\bUpdate\s+ready\b|\brestart\s+to\s+apply\b|⌘\s*Q\s*(?:and|\+)\s*reopen\b", re.I)
_NEG = re.compile(r"\b(?:don['’]t|do\s+not|not|never|avoid|without)\b[^.;\n]{0,100}$", re.I)
_HOST = re.compile(r"^\s*([\w.-]+):\s*(.*)$")
_COMMAND = re.compile(r"\bhermes-core-wt\s+eta\b|\bhermes-sync\s+core-eta\b")


def asks(text):
    # Clause-local negation: a negative instruction must not hide a later positive one.
    for clause in re.split(r"[.;\n]|\bbut\b|\bthen\b", str(text), flags=re.I):
        clause = clause.replace('**', '').replace('`', '')
        for match in _ASK.finditer(clause):
            if not _NEG.search(clause[:match.start()]):
                return True
    return False


def _strings(value):
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return [value]
        return _strings(decoded) if decoded != value else [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def ready(tools, now=None):
    """Newest ETA per host wins. Untimed history and command arguments are not proof."""
    now = time.time() if now is None else now
    hosts = {}
    for row in sorted(tools, key=lambda r: float(r.get('t') or 0)):
        age = now - float(row.get('t') or 0)
        if not 0 <= age <= MAX_AGE or not _COMMAND.search(str(row.get('args', ''))):
            continue
        for text in _strings(row.get('result', '')):
            for line in text.splitlines():
                m = _HOST.match(line)
                if not m:
                    continue
                host, status = m.groups()
                if re.search(r'\bNOT ready\b', status, re.I):
                    hosts[host] = False
                elif 'a Desktop quit + reopen runs it' in status:
                    hosts[host] = True
    return any(hosts.values())


def message_tools(messages):
    """Timestamped OpenAI tool rows, joined by call ID without clipping output.

    Untimed history is not refreshed merely because it is replayed in a turn.
    The normal post_tool_call log supplies timestamps for current-turn results.
    """
    def get(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    calls, rows = {}, []
    for message in messages:
        if get(message, 'role') == 'assistant':
            for call in get(message, 'tool_calls', []) or []:
                fn = get(call, 'function', {})
                calls[get(call, 'id')] = {'tool': get(fn, 'name', ''),
                                           'args': get(fn, 'arguments', '')}
        elif get(message, 'role') == 'tool':
            call = calls.get(get(message, 'tool_call_id'))
            stamp = get(message, 't', get(message, 'timestamp'))
            if call is not None and isinstance(stamp, (int, float)):
                rows.append(dict(call, result=get(message, 'content', ''), t=stamp))
    return rows
