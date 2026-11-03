"""skill-outcomes: log one JSONL line per skill use with its Done-means result and whether the user corrected it.

Hooks (all fail-open):
- post_tool_call: a successful full `skill_view` (no file_path) = one skill use in this turn.
- transform_tool_result: a loaded skill with a `## Done means` section gets one line asking for the tick-off.
- post_llm_call: turn end -> pending records with the reply's `Done means (<skill>): p/t first pass, k fix rounds`.
- pre_final_gate: holds the reply once (first stop only) when a Done-means skill loaded this turn lacks its
  tick-off line, so the agent re-sends it with the line.
- pre_llm_call: the session's next real user message closes the correction window.
- on_session_end: nothing is forced; due records are flushed by the sweep (hooks, at most once a minute) and by
  `outcomes.py flush` (coach_feed runs it before collecting).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent


def _load_core():
    name = "skill_outcomes_core"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / "outcomes.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


core = _load_core()
_lock = threading.Lock()
_loads: dict[str, dict[str, dict]] = {}  # session -> skill -> {skill, ts, has_done_means}
_last_sweep = [0.0]
SWEEP_EVERY_S = 60


def _args(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    try:
        d = json.loads(args or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _view_payload(result: Any) -> dict | None:
    """The skill_view result dict when it was a successful full load (not a dedup stub, not an error)."""
    if isinstance(result, dict):
        d = result
    else:
        try:
            d = json.loads(result) if isinstance(result, str) else None
        except ValueError:
            # a result another plugin already appended text to: parse the leading JSON object
            try:
                d, _ = json.JSONDecoder().raw_decode(result)
            except Exception:
                return None
    if not isinstance(d, dict) or not d.get("success") or not isinstance(d.get("content"), str):
        return None
    return d


def _sweep(now: float | None = None) -> None:
    now = time.time() if now is None else now
    if now - _last_sweep[0] < SWEEP_EVERY_S:
        return
    _last_sweep[0] = now
    try:
        core.flush(None, now)
    except Exception as exc:
        log.debug("skill-outcomes sweep: %s", exc)


def post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_):
    try:
        if tool_name != "skill_view" or not session_id:
            return None
        a = _args(args)
        if a.get("file_path"):
            return None
        d = _view_payload(result)
        if d is None:
            return None
        skill = str(d.get("name") or a.get("name") or "")
        if not skill:
            return None
        with _lock:
            _loads.setdefault(session_id, {}).setdefault(
                skill, {"skill": skill, "ts": time.time(), "has_done_means": core.has_done_means(d["content"]),
                        "checks_expected": core.count_done_means(d["content"])})
    except Exception as exc:
        log.debug("skill-outcomes post_tool_call: %s", exc)
    return None


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_):
    try:
        if tool_name != "skill_view" or not isinstance(result, str) or core.MARK in result:
            return None
        a = _args(args)
        if a.get("file_path"):
            return None
        d = _view_payload(result)
        if d is None or not core.has_done_means(d["content"]):
            return None
        name = str(d.get("name") or a.get("name") or "")
        return result + (
            f"\n\n{core.MARK} This skill has a `## Done means` list. Before your final reply, check each line, fix "
            f"failures at most 2 rounds, and end the reply with one line: `Done means ({name}): <passed on first "
            f"check>/<total> first pass, <k> fix rounds` (add `; still failing: <what>` if any still fail).")
    except Exception as exc:
        log.debug("skill-outcomes transform_tool_result: %s", exc)
        return None


def post_llm_call(session_id: str = "", assistant_response: Any = None, turn_id: Any = None, **_):
    try:
        if not session_id:
            return None
        with _lock:
            loads = list((_loads.pop(session_id, None) or {}).values())
        if loads:
            core.record_turn(None, session_id, loads, assistant_response if isinstance(assistant_response, str)
                             else "", turn_id=turn_id)
        _sweep()
    except Exception as exc:
        log.debug("skill-outcomes post_llm_call: %s", exc)
    return None


def pre_llm_call(session_id: str = "", user_message: Any = None, **_):
    try:
        if session_id and isinstance(user_message, str):
            core.note_user_message(None, session_id, user_message)
        _sweep()
    except Exception as exc:
        log.debug("skill-outcomes pre_llm_call: %s", exc)
    return None


def pre_final_gate(session_id: str = "", final_response: Any = None, attempt: int = 0, **_):
    """Hold a final reply once when a Done-means skill loaded this turn has no tick-off line in it. The ask in the
    skill_view result is often far behind (or compacted away) by the time the reply is written."""
    try:
        if attempt or not session_id or not isinstance(final_response, str) or not final_response.strip():
            return None
        with _lock:
            loads = list((_loads.get(session_id) or {}).values())
        ticks = core.parse_tickoffs(final_response)
        missing = [ld["skill"] for ld in loads if ld.get("has_done_means") and ld["skill"].lower() not in ticks]
        if not missing:
            return None
        want = "\n".join(f"`Done means ({s}): <passed on first check>/<total> first pass, <k> fix rounds`"
                         for s in missing)
        return {"hold": True, "nudge": (
            f"{core.MARK} You loaded {', '.join(missing)} this turn; each has a `## Done means` list. Check each "
            f"line against your work, then send the reply again ending with one line per skill:\n{want}\n"
            "(add `; still failing: <what>` when any line fails).")}
    except Exception as exc:
        log.debug("skill-outcomes pre_final_gate: %s", exc)
        return None


pre_final_gate.needs_buffer = True  # type: ignore[attr-defined]  # a held draft must not have streamed


def register(ctx):
    ctx.register_hook("pre_final_gate", pre_final_gate)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("transform_tool_result", transform_tool_result)
    ctx.register_hook("post_llm_call", post_llm_call)
    ctx.register_hook("pre_llm_call", pre_llm_call)
