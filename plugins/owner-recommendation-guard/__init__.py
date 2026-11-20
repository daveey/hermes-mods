"""owner-recommendation-guard: ask-gate's owner_recommendation_hold on every host, with no model call.

ask-gate holds a kanban_block / blocked kanban_create whose human_request hands David an in-limits pick (red-sweep
slug owner-recommendation-not-applied). ask-gate is enabled only through ``home/plugins-enabled.txt``, which pool
workers never read, and three of the four red-sweep incidents were on pool workers. Loading all of ask-gate there
would also add its aux-model judge to every worker ask and handoff, so this plugin runs only the deterministic rule.

The rule itself is ``plugins/ask-gate/owner_recommendation.py`` (loaded by path from the sibling plugin dir; the
path is spelled out here so scripts/ci-plugin-suites runs this suite on every ask-gate change), so the two
can never disagree. When ask-gate is loaded and enabled in this process it already applies the hold, and this guard
steps aside so the hold is counted once. Holds are bounded per (session, tool, blocked card); any error fails open.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

TAG = "owner-recommendation-guard"
TOOLS = {"kanban_block", "kanban_create"}
_ASK_GATE = Path(__file__).resolve().parent.parent / "ask-gate"
_RULE_PATH = _ASK_GATE / "owner_recommendation.py"
_RULE_KEY = "owner_recommendation_guard_rule"
# The unblock-card brief rule (try-to-unblock / generate-unblock-card), same file ask-gate uses:
# plugins/ask-gate/unblock_card_quality.py
_BRIEF_PATH = _ASK_GATE / "unblock_card_quality.py"
_BRIEF_KEY = "owner_recommendation_guard_brief_rule"
_HELD: dict[tuple[str, str, str], int] = {}
_BRIEF_HELD: dict[tuple[str, str, str], int] = {}


def _load(key: str, path: Path):
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        mod = sys.modules.setdefault(key, mod)
    return mod


def _rule():
    return _load(_RULE_KEY, _RULE_PATH)


def _brief_rule():
    return _load(_BRIEF_KEY, _BRIEF_PATH)


def _log(kind: str, **fields: Any) -> None:
    try:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        path = home / "logs" / f"{TAG}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), "kind": kind, **fields}) + "\n")
    except Exception:
        pass


def _ask_gate_active() -> bool:
    """True when ask-gate is loaded, enabled and error-free in this process's plugin manager: it holds itself."""
    if os.environ.get("OWNER_RECOMMENDATION_GUARD_IGNORE_ASK_GATE"):
        return False
    try:
        from hermes_cli.plugins import get_plugin_manager  # type: ignore
        for p in get_plugin_manager().list_plugins():
            if p.get("name") == "ask-gate":
                return bool(p.get("enabled")) and not p.get("error") and bool(p.get("hooks"))
    except Exception:
        pass
    return False


def _card(tool_name: str, args: dict) -> str:
    if tool_name != "kanban_block":
        return ""
    tid = str(args.get("task_id") or "").strip()
    return tid or (os.environ.get("HERMES_KANBAN_TASK") or "").strip()


def pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> dict | None:
    name = str(tool_name or "")
    name = name[5:] if name.startswith("mcp__") else name
    if name not in TOOLS or not isinstance(args, dict):
        return None
    request = args.get("human_request")
    if not isinstance(request, dict):
        return None
    if name == "kanban_create" and args.get("initial_status") != "blocked":
        return None
    if _ask_gate_active():
        return None
    held = _owner_hold(name, args, request, session_id)
    return held if held is not None else _brief_hold(name, args, request, session_id)


def _owner_hold(name: str, args: dict, request: dict, session_id: str) -> dict | None:
    try:
        rule = _rule()
        hit = rule.hit(request)
        if hit is None:
            return None
        card = _card(name, args)
        key = (session_id or "", name, card)
        n = _HELD.get(key, 0)
        if n >= rule.MAX_HOLDS:
            _log("owner_recommendation_released", tool=name, session=session_id, card=card, holds=n,
                 quote=hit["quote"])
            return None
        _HELD[key] = n + 1
        _log("owner_recommendation_hold", tool=name, session=session_id, card=card, attempt=n, quote=hit["quote"])
        return {"action": "block", "message": rule.HOLD_MESSAGE}
    except Exception as e:
        _log("owner_recommendation_error", tool=name, session=session_id, error=repr(e)[:300])
        return None


def _brief_hold(name: str, args: dict, request: dict, session_id: str) -> dict | None:
    """unblock_card_hold: the request lacks the decision brief generate-unblock-card writes. Bounded, fails open."""
    try:
        rule = _brief_rule()
        hit = rule.hit(request)
        if hit is None:
            return None
        card = _card(name, args) or (
            "create:" + str(args.get("idempotency_key") or args.get("title") or "").strip()[:200]
            if name == "kanban_create" else "")
        key = (session_id or "", name, card)
        n = _BRIEF_HELD.get(key, 0)
        total_key = (session_id or "", name, "<creates>")
        creates = _BRIEF_HELD.get(total_key, 0) if name == "kanban_create" else 0
        if n >= rule.MAX_HOLDS or creates >= rule.MAX_CREATE_HOLDS:
            _log("unblock_card_released", tool=name, session=session_id, card=card, holds=n, missing=hit["missing"])
            return None
        _BRIEF_HELD[key] = n + 1
        if name == "kanban_create":
            _BRIEF_HELD[total_key] = creates + 1
        _log("unblock_card_hold", tool=name, session=session_id, card=card, attempt=n, missing=hit["missing"])
        return {"action": "block", "message": hit["message"]}
    except Exception as e:
        _log("unblock_card_error", tool=name, session=session_id, error=repr(e)[:300])
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
