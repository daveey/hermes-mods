"""skill-scope-gate: automatic skill edits may not add unscoped "don't act" rules to shared skills.

The background self-improvement review (and the curator walk) patch skills on their own after a turn. Those skills
are shared: every session on every host loads them. A caution that fit one task ("this card is diagnosis-only, do not
restart the runner") turns into a blanket rule when the review writes it unconditionally, e.g. hermes-mods c729a23
made five-whys tell every failure analysis "do not start services ... without authorization".

A ``pre_tool_call`` hook on ``skill_manage``: when the write comes from the review fork / curator (skill-ledger actor
``curator``), each sentence the edit ADDS is classified (gate.py). An unscoped prohibition or authorization
requirement blocks the edit with a message telling the reviewer to scope it in the same sentence or drop it.
Foreground edits (a user-directed session) are never blocked, only logged as shadow hits.

Every gated-path edit appends one JSON line to ``<HERMES_HOME>/logs/skill-scope-gate.jsonl``
(verdict pass / block / shadow). ``SKILL_SCOPE_GATE=off|shadow|on`` (default on).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

try:
    from . import gate
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    import sys

    _spec = importlib.util.spec_from_file_location("skill_scope_gate_gate", Path(__file__).with_name("gate.py"))
    gate = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(gate)
    gate = sys.modules.setdefault("skill_scope_gate_gate", gate)

logger = logging.getLogger(__name__)

WRITE_ACTIONS = {"patch", "create", "edit", "write_file"}


def mode() -> str:
    m = (os.environ.get("SKILL_SCOPE_GATE") or "on").strip().lower()
    return m if m in {"off", "shadow", "on"} else "on"


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def log_path() -> Path:
    return Path(os.environ.get("SKILL_SCOPE_GATE_LOG") or _hermes_home() / "logs" / "skill-scope-gate.jsonl")


def actor() -> str:
    """'curator' for the background review fork and the curator walk, else 'agent'/'user' (skill-ledger rule)."""
    try:
        from tools.skill_ledger import derive_actor
        return derive_actor()
    except Exception:
        try:
            from tools.skill_provenance import is_background_review
            return "curator" if is_background_review() else "agent"
        except Exception:
            return "agent"


def _current_text(name: str, file_path: Optional[str]) -> str:
    """Current content of the skill file an edit replaces ('' when new or unresolvable: every sentence counts)."""
    try:
        from tools.skill_manager_tool import _find_skill
        found = _find_skill(name)
        if not found:
            return ""
        p = Path(found["path"]) / (file_path or "SKILL.md")
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except Exception:
        logger.debug("skill-scope-gate: could not read current %s/%s", name, file_path, exc_info=True)
        return ""


def _ops(args: dict) -> list[dict]:
    ops = args.get("operations")
    if isinstance(ops, list):
        return [o for o in ops if isinstance(o, dict)]
    return [args] if args.get("action") else []


def edit_pairs(args: dict, read_current=_current_text) -> list[tuple[str, str, str, str]]:
    """[(skill, action, old_text, new_text)] for each write op in a skill_manage call."""
    out = []
    for op in _ops(args):
        action = str(op.get("action") or "")
        if action not in WRITE_ACTIONS:
            continue
        name = str(op.get("name") or "")
        fp = op.get("file_path") or None
        if action == "patch" and op.get("old_string") is not None:
            old, new = str(op.get("old_string") or ""), str(op.get("new_string") or op.get("content") or "")
        elif action == "create":
            old, new = "", str(op.get("content") or "")
        elif action == "write_file":
            old, new = read_current(name, fp), str(op.get("file_content") or op.get("content") or "")
        else:  # edit, or a patch given as a full rewrite
            old, new = read_current(name, fp), str(op.get("content") or op.get("new_string") or "")
        out.append((name, action, old, new))
    return out


def evaluate(args: dict, who: str, gate_mode: str, read_current=_current_text) -> tuple[str, list[dict], list[str]]:
    """(verdict, hits, skills). verdict: pass | block | shadow."""
    pairs = edit_pairs(args, read_current)
    skills = sorted({p[0] for p in pairs})
    hits = []
    for name, action, old, new in pairs:
        for h in gate.find_hits(old, new):
            hits.append({"skill": name, "action": action, **h})
    if not hits:
        return "pass", hits, skills
    if who == "curator" and gate_mode == "on":
        return "block", hits, skills
    return "shadow", hits, skills


def block_message(hits: list[dict]) -> str:
    shown = "\n".join(f"  - [{h['skill']}] \"{h['sentence'][:300]}\" ({', '.join(h['labels'])})" for h in hits[:5])
    return (
        "skill-scope-gate refused this skill edit. It adds a prohibition or an authorization requirement to a shared "
        "skill with no scoping condition in the same sentence:\n" + shown + "\n"
        "Every session on every host loads this skill, so a caution that fit one task becomes a blanket "
        "\"don't act\" rule for all of them. Either drop the sentence, or name the condition in the same sentence, "
        "e.g. \"When the card says diagnosis-only, do not restart the runner.\" Then retry the edit."
    )


def _log(entry: dict) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("skill-scope-gate: log write failed", exc_info=True)


def _pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> Optional[dict]:
    if tool_name != "skill_manage" or not isinstance(args, dict):
        return None
    gate_mode = mode()
    if gate_mode == "off":
        return None
    try:
        who = actor()
        verdict, hits, skills = evaluate(args, who, gate_mode)
    except Exception:  # never let this plugin break a tool call
        logger.debug("skill-scope-gate evaluate failed", exc_info=True)
        return None
    if not skills:
        return None
    _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "actor": who,
          "mode": gate_mode, "verdict": verdict, "skills": skills, "session_id": session_id or None,
          "hits": hits})
    logger.info("skill-scope-gate %s actor=%s skills=%s hits=%d", verdict, who, ",".join(skills), len(hits))
    if verdict == "block":
        return {"action": "block", "message": block_message(hits)}
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
