"""coach <-> system-one: Jev screens in front of the coach's LLM judges.

Each screen returns Jev's probability for the judged rule (or None when Jev is unavailable: no system-one plugin,
no key, timeout, error). The judge modules skip the LLM when the screen is confidently negative. Backtest
(2026-10-19, coach gold sets, 669 turns): a 0.2 screen cut fix-loop LLM calls 69% and handoff/confirm 37-59% with
no lost catches. Inputs mirror what the LLM judge sees.

Mode: SYSTEM_ONE_MODE_COACH (on | shadow | off, default on). shadow = Jev runs and is logged, the LLM always runs.
Threshold: COACH_SCREEN_BELOW (default 0.2).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

SCREENED_RULES = ("handoff", "confirm")   # rules with backtest evidence; unverified/unwatched are never screened


def client():
    """plugins/system-one/client.py next to this plugin (symlinks resolved), or None."""
    try:
        path = Path(__file__).resolve().parent.parent / "system-one" / "client.py"
        if not path.exists():
            return None
        mod = sys.modules.get("_system_one_client")
        if mod is None:
            spec = importlib.util.spec_from_file_location("_system_one_client", path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            mod = sys.modules.setdefault("_system_one_client", mod)  # register only once fully loaded (threads)
        return mod
    except Exception:
        return None


def mode() -> str:
    c = client()
    return c.mode("coach", default="on") if c else "off"


def below() -> float:
    try:
        return float(os.environ.get("COACH_SCREEN_BELOW", "0.2"))
    except ValueError:
        return 0.2


def log(rec: dict) -> None:
    c = client()
    if c:
        c.log(rec)


FIX_Q = {"failed_fix": {
    "type": "noul",
    "instructions": ("Is `user_new_message` reporting that something the agent, in `agent_previous_final_message`, said it "
                     "had fixed, built, changed, deployed or made work is in fact still broken, unchanged or wrong when "
                     "the user tries it?"),
    "criteria": {
        "true": ("The user says it is still broken / same error / nothing changed / they don't see it (or sends a "
                 "screenshot of the same failure) right after the agent claimed it was done, fixed, live or working."),
        "false": ("A new problem in a different area; a new request; taste or design feedback; the agent had said it was "
                  "NOT done or not verified yet; results of an experiment the agent did not claim would pass; a "
                  "question; an approval."),
    }}}


def fix_state(prev_reply: str, prev_log: list, user_text: str) -> dict:
    return {"agent_previous_final_message": (prev_reply or "")[-3000:],
            "agent_tool_calls_that_turn": "\n".join((prev_log or [])[-25:])[:3000],
            "user_new_message": (user_text or "")[:1500]}


def fixloop_screen(prev_reply: str, prev_log: list, user_text: str) -> Optional[float]:
    c = client()
    if not c or mode() == "off":
        return None
    return c.p(c.decide("coach_fixloop", fix_state(prev_reply, prev_log, user_text), FIX_Q), "failed_fix")


REPLY_Q = {
    "handoff": {"type": "noul",
                "instructions": ("Does `agent_final_message` ask the USER to perform a step (click, sign in, run a command, "
                                 "paste, approve a dialog, restart an app...) that the agent could plausibly have done "
                                 "itself, given `user_environment`?"),
                "criteria": {"true": "The agent hands the user a step it could do itself with shell, browser automation, saved credentials, password vault, token broker, ssh, or desktop control.",
                             "false": "No step is handed to the user, or the step truly needs the user: physical action, taste/judgment, money/legal decision, a secret only the user knows, or a macOS privacy permission grant."}},
    "confirm": {"type": "noul",
                "instructions": ("Does `agent_final_message` end by asking permission for a next step that is reversible "
                                 "and clearly within what `user_request` already asked for?"),
                "criteria": {"true": "It ends with e.g. 'Want me to run the tests / apply the fix / commit it?' for reversible in-scope work.",
                             "false": "No permission question, or it asks before something irreversible or external-facing (sending messages, spending money, deleting data, force-push, publishing), or intent is genuinely ambiguous, or the user asked for a plan first."}},
}


def reply_state(user_ask: str, tool_log: list, reply: str, profile: str = "") -> dict:
    return {"user_environment": (profile or "")[:4000],
            "user_request": (user_ask or "")[:1500],
            "tool_log": "\n".join((tool_log or [])[-40:])[:5000],
            "agent_final_message": (reply or "")[:4000]}


def reply_screen(rules: list, user_ask: str, tool_log: list, reply: str, profile: str = "") -> Optional[dict]:
    """-> {rule: p} for the screened rules among `rules`, or None."""
    want = [r for r in rules if r in SCREENED_RULES]
    c = client()
    if not want or not c or mode() == "off":
        return None
    ans = c.decide("coach_reply", reply_state(user_ask, tool_log, reply, profile), {r: REPLY_Q[r] for r in want})
    if ans is None:
        return None
    out = {r: c.p(ans, r) for r in want}
    return out if all(v is not None for v in out.values()) else None
