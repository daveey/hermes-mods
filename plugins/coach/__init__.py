"""coach: one nudge bus. Agent channel = hooks here (hints the model reads, invisible to the user).
User channel = dashboard/plugin_api.py + desktop-plugins/coach (one-button panels above the composer)."""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

log = logging.getLogger("coach")
_HERE = Path(__file__).resolve().parent


def _load(name: str):
    """Load a sibling module once; re-exec it when the file changed, so a plugin reload
    (activate / discover_and_load(force=True)) picks up edits instead of the cached copy."""
    key = f"_coach_{name}"
    path = _HERE / f"{name}.py"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    mod = sys.modules.get(key)
    if mod is None or getattr(mod, "_coach_mtime", None) != mtime:
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
        setattr(mod, "_coach_mtime", mtime)
    return mod


def build(store=None, checkout=None):
    """The three hook callbacks around one AgentCoach (exposed for tests)."""
    core, rules = _load("core"), _load("agent_rules")
    store = store or core.Store()
    coach = rules.AgentCoach(checkout if checkout is not None else rules.default_live_checkout(), signals=store)

    def pre_llm_call(session_id="", user_message=None, **_):
        try:
            if not session_id:
                return None
            store.user_turn(session_id, user_message)
            hint = coach.turn_start(session_id, user_message)
            return {"context": f"[coach] {hint}"} if hint else None
        except Exception as exc:
            log.debug("coach pre_llm_call: %s", exc)
            return None

    def post_tool_call(tool_name="", args=None, result=None, session_id="", status=None, duration_ms=0,
                       error_type=None, error_message=None, **_):
        try:
            coach.observe(session_id, tool_name, args, result, status, duration_ms or 0, error_type, error_message)
        except Exception as exc:
            log.debug("coach post_tool_call: %s", exc)

    def transform_tool_result(tool_name="", result=None, session_id="", **_):
        try:
            hint = coach.take(session_id)
            return rules.attach_hint(result, hint) if hint and isinstance(result, str) else None
        except Exception as exc:
            log.debug("coach transform_tool_result: %s", exc)
            return None

    def pre_tool_call(tool_name="", args=None, **_):
        try:
            what = rules.blocks_live_checkout(tool_name, args if isinstance(args, dict) else {}, coach.checkout)
        except Exception as exc:
            log.debug("coach pre_tool_call: %s", exc)
            return None
        if not what:
            return None
        log.info("coach blocked live-checkout change: %s", what)
        try:
            store.log({"event": "agent_block", "rule": "live_checkout", "what": what})
        except Exception:
            pass
        return {"action": "block", "message": rules.block_message(what, coach.checkout)}

    def pre_verify(session_id="", platform="", attempt=0, final_response="", changed_paths=None, **_):
        """Check the agent's final answer before the turn ends; a hit keeps the turn going with a nudge.
        At most one nudge per turn (attempt 0 only); fail-open on every error."""
        try:
            if attempt or not session_id or not final_response or platform in ("cron",):
                return None
            rc = _load("reply_check")
            cfg = rc.settings()
            if not cfg["rules"]:
                return None
            ask, log_lines = coach.turn_context(session_id)
            hit = rc.check(final_response, ask, log_lines, rc.aux_judge, threshold=cfg["threshold"],
                           only=cfg["rules"], changed_paths=changed_paths)
            if not hit:
                return None
            store.log({"event": "reply_nudge", "session_id": session_id, "rule": hit["rule"],
                       "p": hit["p"], "evidence": hit["evidence"]})
            return {"action": "continue", "message": hit["nudge"]}
        except Exception as exc:
            log.debug("coach pre_verify: %s", exc)
            return None

    return coach, {"pre_tool_call": pre_tool_call, "pre_verify": pre_verify, "pre_llm_call": pre_llm_call, "post_tool_call": post_tool_call,
                   "transform_tool_result": transform_tool_result}


def register(ctx) -> None:
    _, hooks = build()
    for name, fn in hooks.items():
        ctx.register_hook(name, fn)
