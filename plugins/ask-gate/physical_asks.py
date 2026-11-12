"""Read-only admission for duplicate hands-on asks and unprobed prerequisites.

Scope labels deliberately identify an act, not a topic. No fuzzy matching, probe
execution, approval, card writes, or inferred evidence. Remote-only boards still
require the caller's cross-gateway search; this guard covers readable local boards.
"""
from __future__ import annotations

import json
import re

_SIGNIN = re.compile(r"\b(?:sign[ -]?in|log[ -]?in|authenticate|authentication|oauth|2fa)\b", re.I)
_PREREQUISITE = re.compile(
    r"(?im)^\s*(?:PREREQUISITE|EXPIRES):\s*\S|\b(?:installed\s+build|install\s+(?:build\s+)?\d|"
    r"build\s+\d|waiter\s+(?:job|expired)|file\s+(?:must\s+exist|/|~[/]))", re.I)


# A passive observation window: time or normal use that has to pass, which no human act can hurry.
_WINDOW = re.compile(
    r"\b(?:normal|regular|everyday|ordinary|daily|real[- ]world)\s+(?:use|usage|wear)\b|"
    r"\b(?:over|for|during|across)\s+(?:the\s+next\s+)?(?:a\s+(?:full\s+|whole\s+)?|one\s+(?:full\s+)?|\d+\s*)"
    r"(?:days?|hours?|hrs?|nights?|weeks?)\b|"
    r"\bovernight\b|\ball[- ]day\b|\bat\s+least\s+\d+\s*(?:h|hours?|hrs?|days?)\b|"
    r"\b\d+\s*(?:h|hours?|hrs?|days?)\s+(?:of\s+)?(?:soak|wear|use|usage|observation|recording)\b|"
    r"\bsoak\b|\bobservation\s+window\b", re.I)
# A resolved_check that waits on the clock (elapsed-time arithmetic, file ages, sleeps).
_CLOCK_PROBE = re.compile(r"\bdate\s+\+%s|\bstat\s+(?:-c|-f|--format)\s*['\"]?%[YmM]|-m(?:min|time)\b|"
                          r"-newermt\b|\bsleep\s+\d|\btime\.time\(\)|\bdatetime\.now\(", re.I)
# A HUMAN_STEP label saying the act is already done. Unknown/hedged values are deliberately not matched.
_STEP_DONE = re.compile(r"\A(?:done|complete[d]?|finished|performed|none|n/?a)\b", re.I)


def _label(scope, key):
    values = re.findall(r"(?im)^\s*" + key + r":\s*([^\n]+)", scope)
    return values[0].strip() if len(values) == 1 else None


def phase_hold(request):
    """Hold a hands-on ask that shares one blocking condition with a passive observation window.

    The human act and the time/normal-use window are different phases: the act is a human ask whose probe reads
    its own completion evidence; the window is a scheduled wait on the same (still open) test. Never infers that
    the act happened and never relaxes the test's acceptance."""
    text = "\n".join(str(request.get(k, "")) for k in ("action", "scope", "why_human"))
    probe = request.get("resolved_check")
    probe = probe if isinstance(probe, str) else ""
    clock = bool(_CLOCK_PROBE.search(probe))
    if not (_WINDOW.search(text) or clock):
        return None
    scope = str(request.get("scope", ""))
    step, window = _label(scope, "HUMAN_STEP"), _label(scope, "OBSERVATION")
    keep = ("Keep the test's acceptance and its owner card unchanged: the person's report or a finished step "
            "is not evidence the test passed, so never complete or waive the test on it.")
    if not step or not window:
        reason = ("names a passive observation window (time or normal use that must elapse) in the same human "
                  "ask as a hands-on step")
        fix = ("Split the phases in human_request.scope with one `HUMAN_STEP: <the outstanding act>` line and one "
               "`OBSERVATION: <the window, and the card that waits for it>` line, and make resolved_check read only "
               "the act's completion evidence. If the act is already done, or its status is unknown, read it first; "
               "a finished act leaves no human ask: park the window with kanban_schedule (wake_check = the test's "
               "probe) instead of blocking on David.")
    elif _STEP_DONE.search(step):
        reason = f"says HUMAN_STEP is done ({step[:80]!r}), so no human step remains, only the observation window"
        fix = ("Do not keep a human ask open on elapsed time: park the still-open test with kanban_schedule "
               "(SCHEDULED_UNTIL = end of the window, wake_check = the test's probe) and finish any human-step "
               "helper card with the step's own evidence.")
    elif clock:
        reason = "has a resolved_check that waits on elapsed time, which doing the human step early can never clear"
        fix = ("Make resolved_check read only the HUMAN_STEP's completion evidence (e.g. the gesture capture "
               "exists); move the elapsed-time condition to the scheduled OBSERVATION card's wake_check.")
    else:
        return None
    return {"action": "block", "message": (
        f"ask-gate: physical_ask_phases — this hands-on ask {reason}. {fix} {keep} "
        "A genuine outstanding unlock, consent or device act stays a human ask when labelled this way.")}


def eligible(request):
    interaction = str(request.get("interaction", "")).strip()
    return interaction == "physical" or (
        interaction in {"external", "user_terminal"}
        and _SIGNIN.search(str(request.get("action", ""))))


def identity(request):
    scope = str(request.get("scope", ""))
    labels = {}
    for key in ("DEVICE", "EVIDENCE"):
        values = re.findall(r"(?im)^\s*" + key + r":\s*([^\n]+)", scope)
        if len(values) == 1:
            labels[key] = values[0].strip()
    if len(labels) == 2:
        # Device ids and evidence ids can be case-sensitive: preserve them exactly.
        return ("labels", labels["DEVICE"], labels["EVIDENCE"])
    return ("legacy", str(request.get("action", "")).strip(), scope.strip())


def boards(core):
    paths = list(core.boards())
    home = core.home()
    root = home.parent.parent if home.parent.name == "profiles" else home
    paths.extend([root / "kanban.db"])
    paths.extend((root / "kanban" / "boards").glob("*/kanban.db"))
    paths.extend((root / "profiles").glob("*/kanban.db"))
    paths.extend((root / "profiles").glob("*/kanban/boards/*/kanban.db"))
    return sorted({p.resolve() for p in paths if p.is_file()})


def preflight(core, args, own=()):
    request = args.get("human_request") or {}
    if not eligible(request):
        return None
    key = identity(request)
    for path in boards(core):
        conn = core._ro_connect(path, timeout=2)
        try:
            columns = {r[1] for r in conn.execute("pragma table_info(tasks)")}
            if "human_request" not in columns:  # Older boards have no structured asks.
                continue
            rows = conn.execute("select id, human_request from tasks where status not in "
                                "('done','archived','cancelled') order by rowid")
            for task_id, raw in rows:
                if task_id in own:
                    continue
                try:
                    old = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    continue
                if not isinstance(old, dict) or not eligible(old) or identity(old) != key:
                    continue
                return {"action": "block", "message": (
                    f"ask-gate: physical_ask_duplicate — reuse {task_id} on board {path}. "
                    "Comment the evidence requirements on that existing ask and link your work to its owner "
                    "(cross-gateway dependency when remote); do not file a second human ask. Read its latest "
                    "state first, refresh expired prerequisites with its owner, and preserve all acceptance. "
                    "This is deduplication, not evidence that the person performed or approved anything.")}
        finally:
            conn.close()
    phases = phase_hold(request)
    if phases is not None:
        return phases
    text = "\n".join(str(request.get(k, "")) for k in ("action", "scope", "why_human"))
    probe = request.get("resolved_check")
    if _PREREQUISITE.search(text) and (not isinstance(probe, str) or not probe.strip()):
        return {"action": "block", "message": (
            "ask-gate: physical_ask_probe_required — this hands-on/sign-in ask names a readable prerequisite "
            "or expiry. Supply human_request.resolved_check: a read-only probe in the target gateway's context. "
            "Core checks it before filing and while blocked. Re-read build/file/waiter state now; an already "
            "met prerequisite is not a new ask. An expired waiter needs a refreshed supported flow and ask, "
            "not a claim that sign-in succeeded. A prerequisite never proves the remaining physical acceptance.")}
    return None
