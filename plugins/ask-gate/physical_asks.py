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
