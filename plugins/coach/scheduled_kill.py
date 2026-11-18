"""scheduled_kill: hold, once, a kill of a background job that is still sleeping toward its scheduled run.

Self-heal agent-d483b8fda1: at 21:11 PST the agent scheduled `secs=$(( $(TZ=America/Los_Angeles date -d 'tomorrow
00:01' +%s) - $(date +%s) )); echo "sleeping $secs"; sleep $secs; <post reply>`, polled it (`sleeping 10185`),
decided by mental arithmetic that the wait "should be ~50 minutes", and killed the valid job in the same parallel
batch as the `date` check that proved it right. The job showed only a raw seconds count, so checking it took the
seconds->h:m conversion the model got wrong.

Scope (pre_tool_call): `process_manage` action=kill of a job the registry reports running, whose command sleeps
(a literal >= MIN_S, or a `sleep $var` / `sleep $(...)` computed wait) outside a poll loop, and whose sleep has not
ended yet. The refusal states the computed wake time (start + the `sleeping N` the job printed, else its literal
sleep), so the agent compares facts, not arithmetic. Held once per (session, job): the same kill again goes through
(logged), so the rule can never loop. Every check fails open.
"""
from __future__ import annotations

import re
import time

RULE = "scheduled_kill"
MIN_S = 60.0

_SLEEP_LIT = re.compile(r"\bsleep\s+(\d+(?:\.\d+)?)([smhd]?)\b")
_SLEEP_VAR = re.compile(r"\bsleep\s+[\"']?\$")
_PRINTED = re.compile(r"\bsleep(?:ing)?\s*:?\s*(\d+(?:\.\d+)?)\s*(?:s\b|sec|seconds?\b)?", re.I)
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
_LOOP_SLEEP = re.compile(r"\b(while|until|for)\b[^\n]*?(;|\n|\bdo\b)[\s\S]*?\bsleep\b", re.I)


def _dur(secs: float) -> str:
    secs = int(max(0, secs))
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{h} h {m} m" if h else f"{m} m"


def _clock(epoch: float) -> str:
    local = time.strftime("%a %b %d %H:%M %Z", time.localtime(epoch))
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        pt = datetime.fromtimestamp(epoch, ZoneInfo("America/Los_Angeles")).strftime("%a %b %d %H:%M %Z")
        if pt.rsplit(" ", 1)[-1] != local.rsplit(" ", 1)[-1]:
            local += f" ({pt})"
    except Exception:
        pass
    return local


def wake_at(command: str, output: str, started_at: float) -> tuple[bool, float | None]:
    """(is a scheduled wait, wake epoch or None when not computable)."""
    if not command or _LOOP_SLEEP.search(command):
        return False, None
    lit = sum(float(n) * _UNIT[u] for n, u in _SLEEP_LIT.findall(command))
    var = bool(_SLEEP_VAR.search(command))
    if not var and lit < MIN_S:
        return False, None
    if not started_at:
        return True, None
    if var:
        m = _PRINTED.search(output or "")
        return True, (started_at + float(m.group(1))) if m else None
    return True, started_at + lit


def _registry():
    from tools.process_registry import process_registry  # type: ignore
    return process_registry


def check(held: set, session_id: str, tool: str, args, log, registry=None, now: float | None = None) -> str | None:
    """The block message for this call, or None (allow). `held` remembers (session, job) already held."""
    try:
        name = str(tool or "").rsplit("__", 1)[-1]
        if name not in ("process_manage", "process") or not isinstance(args, dict):
            return None
        if str(args.get("action") or "") != "kill":
            return None
        job = str(args.get("session_id") or args.get("process_id") or "")
        if not job:
            return None
        reg = registry if registry is not None else _registry()
        proc = reg.get(job)
        if proc is None or getattr(proc, "exited", False):
            return None
        command = str(getattr(proc, "command", "") or "")
        sched, wake = wake_at(command, str(getattr(proc, "output_buffer", "") or ""),
                              float(getattr(proc, "started_at", 0) or 0))
        if not sched:
            return None
        t = time.time() if now is None else now
        if wake is not None and t >= wake:
            return None  # the sleep is over: it is doing its work now
        key = (session_id or "", job)
        if key in held:
            log({"event": "scheduled_kill_repeat", "rule": RULE, "session_id": session_id, "job": job})
            return None
        held.add(key)
        log({"event": "agent_block", "rule": RULE, "session_id": session_id, "tool": name, "job": job,
             "wake": wake, "command": command[:200]})
        if wake is not None:
            when = (f"It wakes at {_clock(wake)}, in {_dur(wake - t)} (epoch @{int(wake)}; check with "
                    f"`date -d @{int(wake)}`), then runs the rest of its command.")
        else:
            when = ("Its wake time is unknown to the coach (wake time unknown: the job did not print its computed "
                    "seconds); print the target with "
                    "`date -d @<epoch>` before deciding.")
        return (f"[coach scheduled_kill] {job} is a scheduled wait that is still sleeping. {when} If you are "
                "killing it because the wait looks wrong, verify the target time with a tool first, not mental "
                "arithmetic, and do that check before the kill, not in the same batch. This kill was not run; "
                "kill it again if it still needs to go.")
    except Exception:
        return None
