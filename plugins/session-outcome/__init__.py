"""session-outcome: the agent says how a chat ended, so the sidebar paints from a stored outcome.

Contract: side-session-notes session-outcome-contract.md, "Mods". Core owns storage and derivation
(``hermes_state_outcome``); this plugin only feeds it:

- tool ``session_outcome`` (desktop/tui/cli chats only): ``done`` (summary), ``decide`` (questions with >=2 choices,
  written to the user-steps ledger first, so the For you tiles show them) or ``wait`` (named cards, processes,
  cron, times, capacity or subagents). Each call is ``declare_outcome(sid, outcome)``.
- an outcome provider: open user-steps steps -> ``decision_items``.
- an outcome judge: is the last assistant message a closing summary? Pool model, ``fast`` tier, 3 s budget,
  None (core falls back to its heuristic) on any failure or timeout.
- ``pre_final_gate``: a reply with no ``session_outcome`` call that would end ``unresolved{no_summary}`` is held
  once with a request for the call. Never loops: the second miss passes.

On a core without ``hermes_state_outcome`` everything is a no-op: the tool is not offered, nothing registers and
the gate passes. Off switch: HERMES_SESSION_OUTCOME_OFF=1.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("session-outcome")

TOOL = "session_outcome"
TOOLSET = "session_outcome"
INTERACTIVE = frozenset({"desktop", "tui", "cli"})
WAIT_KINDS = ("card", "process", "cron", "time", "capacity", "subagent")
JUDGE_BUDGET_S = 3.0
TAG = "[session-outcome]"
SUMMARY_MAX = 1000
PLUGINS = Path(__file__).resolve().parent.parent

NUDGE = (f"{TAG} Before this reply goes out, record how this chat ends with ONE session_outcome call: "
         '{"kind": "done", "summary": "<what was done and the result>"} if the work is finished; '
         '{"kind": "decide", "items": [{"question": "...", "choices": [{"label": "...", "value": "..."}, ...]}]} '
         'if the user must choose (at least 2 choices each); '
         '{"kind": "wait", "on": [{"kind": "card|process|cron|time|capacity|subagent", "id": "...", "label": "..."}]} '
         "if it waits on named work. Then send your final reply. If none fits, just send the reply.")


# ---- optional dependencies -----------------------------------------------------------------------------

def _outcome_api():
    """Core's hermes_state_outcome, or None on a core that predates it (then this plugin is a no-op)."""
    try:
        import hermes_state_outcome as api  # type: ignore
    except ImportError:
        return None
    return api


def _load_sibling(key: str, rel: str):
    """Load another plugin's module from this installed tree (plugins have no cross-import API)."""
    path = (PLUGINS / rel).resolve(strict=True)
    existing = sys.modules.get(key)
    if existing is not None and Path(getattr(existing, "__file__", "")).resolve() == path:
        return existing
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ImportError(rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[key] = mod
    return mod


def _user_steps():
    """user-steps core with the open_steps/add_step helpers, or None."""
    try:
        mod = _load_sibling("session_outcome_user_steps_core", "user-steps/core.py")
    except Exception:
        log.debug("session-outcome: user-steps unavailable", exc_info=True)
        return None
    return mod if callable(getattr(mod, "open_steps", None)) and callable(getattr(mod, "add_step", None)) else None


# ---- interactive gating --------------------------------------------------------------------------------

def _session_env(name: str) -> str:
    try:
        from gateway.session_context import get_session_env  # type: ignore

        return str(get_session_env(name, "") or "")
    except Exception:
        return os.environ.get(name, "")


def _surface(platform: str = "") -> str:
    platform = platform or os.environ.get("HERMES_PLATFORM", "") or _session_env("HERMES_SESSION_PLATFORM")
    try:
        from agent.session_source import session_source_for  # type: ignore

        return str(session_source_for(platform or None) or "").strip().lower()
    except Exception:
        source = _session_env("HERMES_SESSION_SOURCE").strip()
        return (source or platform or "cli").strip().lower()


def _row_source(session_id: str) -> str:
    us = _user_steps()
    if us is None or not session_id:
        return ""
    try:
        return str(us.session_source(session_id) or "").strip().lower()
    except Exception:
        return ""


def _interactive(platform: str = "", session_id: str = "") -> bool:
    if os.environ.get("HERMES_SESSION_OUTCOME_OFF") or os.environ.get("HERMES_KANBAN_TASK"):
        return False
    if (platform or "").lower() == "subagent":
        return False
    row = _row_source(session_id)
    return (row or _surface(platform)) in INTERACTIVE


def check_fn() -> bool:
    """Offer the tool only to desktop/tui/cli chats, and only when core can store the outcome."""
    return _outcome_api() is not None and _interactive()


try:  # evaluated per tool-list build, never cached across sessions
    from tools.registry import no_cache_check_fn  # type: ignore

    check_fn = no_cache_check_fn(check_fn)
except Exception:
    pass


# ---- the tool ------------------------------------------------------------------------------------------

_CHOICE = {"type": "object", "additionalProperties": False, "required": ["label", "value"],
           "properties": {"label": {"type": "string"}, "value": {"type": "string"}}}
SCHEMA = {
    "name": TOOL,
    "description": (
        "Record how this chat ends, once, right before your final reply. "
        '{"kind": "done", "summary": "..."} when the work is finished (one or two sentences: what was done and '
        'the result). {"kind": "decide", "items": [{"question": "...", "choices": [{"label", "value"}, ...]}]} '
        "when the user must choose: each question becomes a decision tile with buttons (at least 2 choices). "
        '{"kind": "wait", "on": [{"kind": "card|process|cron|time|capacity|subagent", "id": "...", '
        '"label": "...", "eta": <unix seconds>}]} when the chat waits on named work. '
        "The sidebar shows this outcome; without it the chat may show as unresolved."),
    "parameters": {
        "type": "object", "additionalProperties": False, "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": ["done", "decide", "wait"]},
            "summary": {"type": "string", "description": "done: what was done and the result."},
            "items": {"type": "array", "minItems": 1, "description": "decide: the questions for the user.",
                      "items": {"type": "object", "additionalProperties": False, "required": ["question", "choices"],
                                "properties": {"question": {"type": "string"},
                                               "choices": {"type": "array", "minItems": 2, "items": _CHOICE},
                                               "card_id": {"type": "string"}}}},
            "on": {"type": "array", "minItems": 1, "description": "wait: what the chat waits on.",
                   "items": {"type": "object", "required": ["kind", "id"],
                             "properties": {"kind": {"type": "string", "enum": list(WAIT_KINDS)},
                                            "id": {"type": "string"}, "label": {"type": "string"},
                                            "state": {"type": "string"}, "eta": {"type": "number"}}}},
        },
    },
}

# (session, tool_call_id) -> time of a session_outcome call. The gate counts it only when that call id is in the
# turn it is checking, so a call never carries into the next turn's gate.
_CALLED: dict[tuple[str, str], float] = {}
_CALLED_TTL_S = 3600.0


def _current_tool_call_id(explicit: str = "") -> str:
    if explicit:
        return str(explicit)
    try:
        from tools.approval_context import _approval_tool_call_id  # type: ignore

        return str(_approval_tool_call_id.get() or "")
    except Exception:
        return ""


def _note_call(session_id: str, tool_call_id: str) -> None:
    now = time.time()
    for key, at in list(_CALLED.items()):
        if now - at > _CALLED_TTL_S:
            _CALLED.pop(key, None)
    if tool_call_id:
        _CALLED[(session_id, tool_call_id)] = now


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _validate(args: dict) -> dict:
    """Normalized tool args; ValueError with an actionable message when malformed. Writes nothing."""
    kind = args.get("kind")
    if kind == "done":
        summary = _text(args.get("summary"))
        if not summary:
            raise ValueError('done needs a non-empty "summary"')
        return {"kind": "done", "summary": summary[:SUMMARY_MAX]}
    if kind == "decide":
        items = args.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError('decide needs "items": [{"question", "choices": [{"label", "value"}, ...]}]')
        out = []
        for i, it in enumerate(items):
            q = _text(it.get("question")) if isinstance(it, dict) else ""
            raw = it.get("choices") if isinstance(it, dict) else None
            choices = [{"label": _text(c.get("label")), "value": str(c.get("value", "")).strip() or _text(c.get("label"))}
                       for c in raw or [] if isinstance(c, dict) and _text(c.get("label"))]
            if not q:
                raise ValueError(f"items[{i}] needs a question")
            if len(choices) < 2:
                raise ValueError(f"items[{i}] needs at least 2 choices, each {{label, value}}")
            item = {"question": q, "choices": choices}
            if isinstance(it.get("card_id"), str) and it["card_id"].strip():
                item["card_id"] = it["card_id"].strip()
            out.append(item)
        return {"kind": "decide", "items": out}
    if kind == "wait":
        on = args.get("on")
        if not isinstance(on, list) or not on:
            raise ValueError('wait needs "on": [{"kind", "id", "label"?, "eta"?}]')
        out = []
        for i, w in enumerate(on):
            if not isinstance(w, dict) or w.get("kind") not in WAIT_KINDS or not _text(w.get("id")):
                raise ValueError(f"on[{i}] needs kind (one of {', '.join(WAIT_KINDS)}) and id")
            entry = {"kind": w["kind"], "id": _text(w["id"])}
            for k in ("label", "state"):
                if _text(w.get(k)):
                    entry[k] = _text(w[k])
            if isinstance(w.get("eta"), (int, float)) and not isinstance(w.get("eta"), bool):
                entry["eta"] = float(w["eta"])
            out.append(entry)
        return {"kind": "wait", "on": out}
    raise ValueError('kind must be "done", "decide" or "wait"')


def _rollback_steps(us, session_id: str, ids: list[str]) -> None:
    """Remove steps this call added, so a failed decide leaves no tiles behind."""
    if not ids:
        return
    try:
        lg = us.Ledger()
        with lg.edit(lg.root(session_id)) as d:
            d["steps"] = [s for s in d["steps"] if s.get("id") not in ids]
    except Exception:
        log.exception("session-outcome: rolling back decide steps failed")


def handle(args: dict, session_id: str = "", tool_call_id: str = "", **_: Any) -> str:
    try:
        if not session_id:
            return _err("no session")
        if not _interactive(session_id=session_id):
            return _err("session_outcome is only for desktop, tui and cli chats")
        try:
            spec = _validate(args if isinstance(args, dict) else {})
        except ValueError as exc:
            return _err(str(exc))
        api = _outcome_api()
        result: dict[str, Any] = {"ok": True}
        added: list[str] = []
        if spec["kind"] == "done":
            outcome = {"kind": "done", "summary": spec["summary"]}
        elif spec["kind"] == "wait":
            outcome = {"kind": "waiting", "on": spec["on"]}
        else:
            # Every item was validated above, before any write. Writes are all or nothing: a failed write or a
            # rejected declaration removes the steps this call added.
            us = _user_steps()
            items = []
            before = {s["id"] for s in us.open_steps(session_id)} if us is not None else set()
            for it in spec["items"]:
                if us is not None:
                    try:
                        step = us.add_step(session_id, it["question"], it["choices"])
                    except Exception as exc:
                        _rollback_steps(us, session_id, added)
                        return _err(f"decide not recorded (nothing written): {exc}")
                    sid = step["id"]
                    if sid not in before:
                        added.append(sid)
                else:
                    sid = "us_" + uuid.uuid4().hex[:10]
                item = {"id": sid, "question": it["question"], "choices": it["choices"], "state": "open"}
                if it.get("card_id"):
                    item["card_id"] = it["card_id"]
                items.append(item)
            outcome = {"kind": "decision", "items": items}
            result["steps"] = [i["id"] for i in items]
            if us is None:
                result["note"] = "user-steps is not installed: no decision tiles were created"
        if api is None:
            result.update(declared=False, outcome=outcome,
                          note=(result.get("note", "") + " " if result.get("note") else "")
                          + "this Hermes core cannot store outcomes yet; nothing was declared")
            return json.dumps(result)
        try:
            api.declare_outcome(session_id, outcome)
        except Exception as exc:
            us = _user_steps() if added else None
            if us is not None:
                _rollback_steps(us, session_id, added)
            return _err(f"outcome rejected (nothing kept): {exc}")
        _note_call(session_id, _current_tool_call_id(tool_call_id))
        result.update(declared=True, outcome=outcome)
        return json.dumps(result)
    except Exception as exc:  # never crash the agent loop
        log.exception("session_outcome failed")
        return _err(f"{type(exc).__name__}: {exc}")


# ---- provider: open user-steps steps -> decision items ---------------------------------------------------

def provider(session_id: str) -> Optional[dict]:
    us = _user_steps()
    if us is None or not session_id:
        return None
    try:
        steps = us.open_steps(session_id)
    except Exception:
        log.debug("session-outcome provider failed", exc_info=True)
        return None
    if not steps:
        return None
    items = []
    for s in steps:
        choices = s.get("choices") or [{"label": o, "value": o} for o in s.get("options") or []]
        items.append({"id": s["id"], "question": s.get("text", ""), "choices": choices, "state": "open"})
    return {"decision_items": items, "waiting_on": []}


# ---- judge: is the last assistant message a closing summary? ----------------------------------------------

JUDGE_SYSTEM = (
    "You read the last message an AI assistant sent in a chat. Answer YES if it closes the work for now: it says "
    "what was done or found and the result, so the user has nothing to wait for. Answer NO if it is mid-task "
    "status, a promise to report later, a bare acknowledgement, an error with no result, or a question. "
    "Reply with exactly YES or NO.")


def _pool_llm(system: str, user: str) -> str:
    """One call on the subscription pool, fast tier (pool-llm's in-process library)."""
    pool = _load_sibling("session_outcome_pool_llm", "pool-llm/pool_llm.py")
    r = pool.complete([{"role": "system", "content": system}, {"role": "user", "content": user}],
                      provider="anthropic", tier="fast", max_tokens=5)
    return str(r.get("text") or "")


_llm: Callable[[str, str], str] = _pool_llm
_JUDGED: dict[tuple, Optional[bool]] = {}


def _with_budget(fn: Callable[[], Any], budget: float) -> Any:
    """fn() on a daemon thread; TimeoutError past budget (the call is abandoned, never awaited)."""
    box: dict = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the caller below
            box["e"] = exc

    t = threading.Thread(target=run, name="session-outcome-judge", daemon=True)
    t.start()
    t.join(budget)
    if t.is_alive():
        raise TimeoutError(f"judge over {budget}s")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def judge(session_id: str, last_assistant_text: str, *, source: Optional[str] = None) -> Optional[bool]:
    """True = a closing summary, False = not, None = can't tell (core uses its heuristic).

    Only desktop/tui/cli chats are judged: any other ``source`` (or, when core passes none, a session row whose
    source is known and not interactive) gets None without a model call."""
    text = last_assistant_text if isinstance(last_assistant_text, str) else ""
    if not text.strip():
        return None
    src = str(source or "").strip().lower() or _row_source(session_id)
    if src and src not in INTERACTIVE:
        return None
    key = (session_id, hashlib.sha1(text.encode("utf-8", "replace")).hexdigest())
    if key in _JUDGED:
        return _JUDGED[key]
    try:
        raw = _with_budget(lambda: _llm(JUDGE_SYSTEM, text[-4000:]), JUDGE_BUDGET_S)
        word = str(raw or "").strip().strip(".!\"'`*").upper()
        verdict = True if word.startswith("YES") else False if word.startswith("NO") else None
    except Exception:
        log.debug("session-outcome judge gave up", exc_info=True)
        return None  # failures are not cached: the next call may get through
    if len(_JUDGED) > 256:
        _JUDGED.clear()
    _JUDGED[key] = verdict
    return verdict


# ---- pre_final_gate: ask once for the call ---------------------------------------------------------------

_ASKED: dict[str, float] = {}


def _tool_names(msg: dict) -> list[str]:
    names = []
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        if name:
            names.append(name)
    if msg.get("role") == "tool":
        names += [n for n in (msg.get("name"), msg.get("tool_name")) if n]
    return names


def _call_ids(msg: dict) -> list[str]:
    ids = []
    for call in msg.get("tool_calls") or []:
        cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
        if cid:
            ids.append(str(cid))
    if msg.get("role") == "tool" and msg.get("tool_call_id"):
        ids.append(str(msg["tool_call_id"]))
    return ids


def _called_this_turn(session_id: str, msgs: list) -> bool:
    msgs = [m for m in msgs if isinstance(m, dict)]
    if any(TOOL in _tool_names(m) for m in msgs):
        return True
    return any((session_id, cid) in _CALLED for m in msgs for cid in _call_ids(m))


def _asked_this_turn(session_id: str, msgs: list, attempt: int) -> bool:
    if any(isinstance(m, dict) and m.get("role") == "user" and TAG in str(m.get("content") or "") for m in msgs):
        return True
    return attempt > 0 and time.time() - _ASKED.get(session_id, 0) < 900


def _would_be_no_summary(api, session_id: str, reply: str) -> bool:
    """Would this reply leave the chat unresolved{no_summary}? Core's own derivation answers, skipping the live
    turn this gate runs inside and treating the unsaved candidate as the last assistant message (it calls the
    judge, cached, and falls back to its heuristic). A core that can't do that, or any error, never holds."""
    try:
        out = api.derive_outcome(session_id, ignore_live_turn=True, pending_reply=reply) or {}
    except Exception:
        log.debug("session-outcome derive failed", exc_info=True)
        return False
    return out.get("kind") == "unresolved" and out.get("reason") == "no_summary"


def pre_final_gate(session_id: str = "", platform: str = "", final_response: str = "",
                   turn_messages: list | None = None, attempt: int = 0, hold_budget: int | None = None,
                   **_: Any) -> Optional[dict]:
    if not isinstance(final_response, str) or not final_response.strip() or not session_id:
        return None
    try:
        api = _outcome_api()
        if api is None or not _interactive(platform, session_id):
            return None
        msgs = list(turn_messages or [])
        if _called_this_turn(session_id, msgs):
            return None
        if _asked_this_turn(session_id, msgs, attempt):
            _ASKED.pop(session_id, None)
            return None  # asked once already: the second miss passes
        if hold_budget is not None and attempt >= hold_budget:
            return None
        if not _would_be_no_summary(api, session_id, final_response):
            return None
        _ASKED[session_id] = time.time()
        return {"hold": True, "nudge": NUDGE}
    except Exception:
        log.debug("session-outcome pre_final_gate failed", exc_info=True)
        return None  # fail open


pre_final_gate.needs_buffer = True
pre_final_gate.accepts_hold_budget = True


# ---- registration ----------------------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_tool(name=TOOL, toolset=TOOLSET, schema=SCHEMA, handler=handle, check_fn=check_fn,
                      description="Record how this chat ends: done, decide or wait", emoji="🏁")
    ctx.register_hook("pre_final_gate", pre_final_gate)
    api = _outcome_api()
    if api is None:
        log.info("session-outcome: core has no hermes_state_outcome; tool hidden, gate and judge idle")
        return
    try:
        api.register_outcome_provider("user-steps", provider)
        api.register_outcome_judge(judge)
    except Exception:
        log.exception("session-outcome: registering provider/judge failed")
