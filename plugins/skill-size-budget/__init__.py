"""skill-size-budget: automatic skill edits keep SKILL.md bodies lean and push detail to references/.

The background self-improvement review and the curator walk patch skills after turns (kanban-orchestrator took 168
automatic patches and reached 52,824 chars). Every load pays for the whole body, so growth goes to
``references/<topic>.md``, linked by one line from the step that needs it, one hop deep.

Hooks on ``skill_manage``:

* ``pre_tool_call``: simulate the call's operations in order. When the skill-ledger actor is ``curator`` and a
  SKILL.md write would leave the body over budget (default 300 lines or 30,000 chars) while growing it beyond a
  one-line pointer / one Done-means checklist line, refuse it with the redirect recipe. A write to
  ``references/*.md`` that links another reference file is refused the same way (one hop).
  Foreground (agent/user) edits are never refused.
* ``transform_tool_result``: a foreground edit that tripped either check gets ``size_budget_warning`` added to its
  (successful) result, so the session sees the same guidance without being blocked.

Telemetry: one JSON line per checked edit in ``<HERMES_HOME>/logs/skill-size-budget.jsonl``.
``SKILL_SIZE_BUDGET=off|shadow|on`` (default on; shadow = log + warn, never block).
``SKILL_SIZE_BUDGET_LINES`` / ``SKILL_SIZE_BUDGET_CHARS`` override the budget.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from . import budget
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    import sys

    _spec = importlib.util.spec_from_file_location("skill_size_budget_budget", Path(__file__).with_name("budget.py"))
    budget = sys.modules.get("skill_size_budget_budget")
    if budget is None:
        budget = importlib.util.module_from_spec(_spec)
        sys.modules["skill_size_budget_budget"] = budget  # dataclasses look the module up while it executes
        _spec.loader.exec_module(budget)

logger = logging.getLogger(__name__)

WRITE_ACTIONS = {"patch", "create", "edit", "write_file"}
_PENDING: "OrderedDict[str, str]" = OrderedDict()  # call key -> warning text for transform_tool_result
_PENDING_MAX = 64


def mode() -> str:
    m = (os.environ.get("SKILL_SIZE_BUDGET") or "on").strip().lower()
    return m if m in {"off", "shadow", "on"} else "on"


def limits() -> tuple[int, int]:
    def _int(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name) or default))
        except ValueError:
            return default
    return _int("SKILL_SIZE_BUDGET_LINES", budget.MAX_LINES), _int("SKILL_SIZE_BUDGET_CHARS", budget.MAX_CHARS)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def log_path() -> Path:
    return Path(os.environ.get("SKILL_SIZE_BUDGET_LOG") or _hermes_home() / "logs" / "skill-size-budget.jsonl")


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


def _current_text(name: str, file_path: Optional[str]) -> Optional[str]:
    """Current content of a skill file; '' when the skill exists but the file doesn't, None when unresolvable."""
    try:
        from tools.skill_manager_tool import _find_skill
        found = _find_skill(name)
        if not found:
            return None
        p = Path(found["path"]) / (file_path or "SKILL.md")
        return p.read_text(encoding="utf-8-sig") if p.is_file() else ""
    except Exception:
        logger.debug("skill-size-budget: could not read %s/%s", name, file_path, exc_info=True)
        return None


def _replace(content: str, old: str, new: str, replace_all: bool) -> Optional[str]:
    """Apply a patch the way skill_manage does (core fuzzy matcher when importable); None when it won't match."""
    try:
        from tools.fuzzy_match import fuzzy_find_and_replace
        out, _count, _strategy, err = fuzzy_find_and_replace(content, old, new, replace_all)
        return None if err else out
    except Exception:
        if old not in content:
            return None
        return content.replace(old, new) if replace_all else content.replace(old, new, 1)


def _ops(args: dict) -> list[dict]:
    ops = args.get("operations")
    if isinstance(ops, list):
        return [o for o in ops if isinstance(o, dict)]
    return [args] if args.get("action") else []


def simulate(args: dict, read_current: Callable[[str, Optional[str]], Optional[str]] = _current_text
             ) -> dict[tuple[str, str], tuple[str, str]]:
    """{(skill, file): (before, after)} for every file a skill_manage call writes, ops applied in order."""
    state: dict[tuple[str, str], tuple[str, str]] = {}
    for op in _ops(args):
        action = str(op.get("action") or "")
        if action not in WRITE_ACTIONS:
            continue
        name = str(op.get("name") or "")
        fp = str(op.get("file_path") or "") or "SKILL.md"
        key = (name, fp)
        if key in state:
            before, cur = state[key]
        else:
            if action == "create" and fp == "SKILL.md":
                before = cur = ""
            else:
                got = read_current(name, None if fp == "SKILL.md" else fp)
                if got is None:
                    continue
                before = cur = got
        if action == "patch" and op.get("old_string") is not None:
            new = op.get("new_string") if op.get("new_string") is not None else op.get("content")
            out = _replace(cur, str(op.get("old_string") or ""), str(new or ""), bool(op.get("replace_all")))
            if out is None:
                continue  # skill_manage will refuse the stale anchor itself
        elif action == "create":
            out = str(op.get("content") or "")
        elif action == "write_file":
            out = str(op.get("file_content") or op.get("content") or "")
        else:  # edit, or patch given as a full rewrite
            out = str(op.get("content") or op.get("new_string") or "")
        state[key] = (before, out)
    return state


def evaluate(args: dict, who: str, gate_mode: str, read_current=_current_text) -> dict:
    """{verdict: pass|block|warn, findings: [...], skills: [...]}."""
    max_lines, max_chars = limits()
    findings = []
    skills = set()
    for (name, fp), (before, after) in simulate(args, read_current).items():
        skills.add(name)
        if fp == "SKILL.md":
            v = budget.check_skill_md(before, after, max_lines, max_chars)
            if v.over:
                findings.append({"skill": name, "file": fp, "kind": "over-budget", "reason": v.reason,
                                 "before": list(v.before), "after": list(v.after), "added": v.added,
                                 "has_done_means": budget.has_done_means(after)})
        elif budget.is_reference_path(fp):
            links = budget.cross_reference_links(before, after, fp)
            if links:
                findings.append({"skill": name, "file": fp, "kind": "second-hop", "links": links})
    verdict = "pass"
    if findings:
        verdict = "block" if (who == "curator" and gate_mode == "on") else "warn"
    return {"verdict": verdict, "findings": findings, "skills": sorted(skills)}


def guidance(findings: list[dict], blocked: bool) -> str:
    max_lines, max_chars = limits()
    lines = []
    for f in findings[:5]:
        if f["kind"] == "over-budget":
            lines.append(f"  - [{f['skill']}] SKILL.md body would be {f['reason']}; it was "
                         f"{f['before'][0]} lines / {f['before'][1]:,} chars.")
        else:
            lines.append(f"  - [{f['skill']}] {f['file']} links other reference file(s): {', '.join(f['links'])}.")
    head = ("skill-size-budget refused this automatic skill edit:" if blocked else
            "skill-size-budget warning (the write went through, but this is what an automatic edit is refused for):")
    recipe = []
    if any(f["kind"] == "over-budget" for f in findings):
        done = any(f.get("has_done_means") for f in findings if f["kind"] == "over-budget")
        recipe.append(
            f"A SKILL.md body over {max_lines} lines or {max_chars:,} chars may not grow: every load pays for it. ")
        if done:
            recipe.append("This skill has a `## Done means` list: if the new material is a correction, add it as ONE "
                          "`- [ ]` yes/no line there (keep the list short; merge into an existing line if it is "
                          "already 5+). ")
        recipe.append("Otherwise, in ONE skill_manage batch: write_file `references/<topic>.md` with the new material "
                      "(or append to the existing reference on that topic), then patch ONE line into the step that "
                      "needs it, e.g. \"Before X, read `references/<topic>.md`.\" To make room for more, move a large "
                      "section to references/ first (shrinking ops before the growing one). ")
    if any(f["kind"] == "second-hop" for f in findings):
        recipe.append("References are one hop deep: SKILL.md links each reference directly; a reference never "
                      "points to another reference for rules. Inline the rule in this file, or link the other "
                      "reference from SKILL.md instead. ")
    return head + "\n" + "\n".join(lines) + "\n" + "".join(recipe).strip() + (" Then retry the edit." if blocked else "")


def _key(args: dict, tool_call_id: str = "") -> str:
    if tool_call_id:
        return "id:" + tool_call_id
    try:
        return "args:" + hashlib.sha1(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()
    except Exception:
        return "args:?"


def _log(entry: dict) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("skill-size-budget: log write failed", exc_info=True)


def _pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", tool_call_id: str = "",
                   **_: Any) -> Optional[dict]:
    if tool_name != "skill_manage" or not isinstance(args, dict):
        return None
    gate_mode = mode()
    if gate_mode == "off":
        return None
    try:
        who = actor()
        res = evaluate(args, who, gate_mode)
    except Exception:  # never let this plugin break a tool call
        logger.debug("skill-size-budget evaluate failed", exc_info=True)
        return None
    if not res["skills"]:
        return None
    _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "actor": who,
          "mode": gate_mode, "verdict": res["verdict"], "skills": res["skills"], "session_id": session_id or None,
          "findings": res["findings"]})
    if res["verdict"] == "pass":
        return None
    logger.info("skill-size-budget %s actor=%s skills=%s", res["verdict"], who, ",".join(res["skills"]))
    if res["verdict"] == "block":
        return {"action": "block", "message": guidance(res["findings"], blocked=True)}
    text = guidance(res["findings"], blocked=False)
    for k in {_key(args, tool_call_id), _key(args)}:  # transform may or may not carry the call id
        _PENDING[k] = text
    while len(_PENDING) > _PENDING_MAX:
        _PENDING.popitem(last=False)
    return None


def _transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, tool_call_id: str = "",
                           **_: Any) -> Optional[str]:
    if tool_name != "skill_manage" or not isinstance(args, dict):
        return None
    warning = None
    for k in (_key(args, tool_call_id), _key(args)):
        warning = _PENDING.pop(k, None) or warning
    if warning is None or not isinstance(result, str):
        return None
    try:
        data = json.loads(result)
    except Exception:
        return result + "\n\n" + warning
    if not isinstance(data, dict) or data.get("success") is False:
        return None  # the write failed; nothing grew
    data["size_budget_warning"] = warning
    return json.dumps(data, ensure_ascii=False)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
