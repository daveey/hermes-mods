"""answer_first: a live user's explicit stop/answer demand, or a plain question, gets an answer before more tool work.

Self-heal agent-e9ebbbb4cd (hermes-hub Desktop session 20261111_200009_edbe39, 2026-11-16 05:30Z): asked "can you
explain what this session is doing? ... a week later its not done", the agent made 13 tool calls over 6 minutes with
no text, was stopped twice, then got "STOP DOING THINGS< ANSWER MY FUCKING QUESTIOM" and still made two more calls
(one filed a new approval request) before answering. A memory line it then saved did not hold the next turn.
long_wait / live_chat_sleep only refuse foreground sleeps, not many short reads, and reply-time checks come too late.

Two kinds of user turn (pre_llm_call classifies the user's own text; pre_tool_call enforces):
- "stop": the message demands the agent stop acting or just answer ("stop", "stop doing things", "answer my
  question", "just answer"), with no new action asked for ("stop the gateway", "stop and revert that" are actions).
  Every tool call this turn is refused: the user withdrew permission to act until he has his answer.
- "question": an explain/why/what/status question with no action verb. Reads to answer it are fine; after BUDGET_CALLS
  calls or BUDGET_S seconds with no text to the user this turn, the next call is refused once (the same call again
  goes through, logged), so the rule can never loop.

Live chats only: cron, Kanban workers and subagents (parent_session_id) are exempt. COACH_ANSWER_FIRST=0 disables.
Every check fails open.
"""
from __future__ import annotations

import os
import re
import time

RULE = "answer_first"
BUDGET_CALLS = 6
BUDGET_S = 120.0

STOP_MESSAGE = ("[coach answer_first] The user told you to stop and answer. Do not run any tool this turn (this call "
                "was not run, and no other will be). Reply now, in plain text, to the question they asked earlier, "
                "from what you already know; say what you don't know and offer to look further.")
QUESTION_MESSAGE = ("[coach answer_first] The user asked a question and has had no answer for {calls} tool calls / "
                    "{secs} s. Send the answer now, in text, from what you already know (say what is still unknown "
                    "and what you would check next); further tool work comes after that answer, not before. (This "
                    "call was not run.)")

# Core and plugin notices that ride as user turns are not the user's words.
_NOTICE = re.compile(r"^\s*(?:\[(?!Turn stopped)[^\]]*\]|[✔✖⏳⚠✅❌]|\[?CONTEXT COMPACTION)", re.I)
_DESKTOP_STOP = re.compile(r"\[Turn stopped by Desktop before it finished\.\]\s*", re.I)

# A stop/answer demand. Anchored to the start of a line (after "please"/"ok") or a whole-line "stop".
_STOP = re.compile(
    r"(?:^|[.!?\n<>]\s*)(?:(?:please|ok(?:ay)?|just|now)[,\s]+)*"
    r"(?:stop(?:\s+(?:doing\s+(?:things|stuff|anything|everything|all\s+(?:this|that))|working|everything|"
    r"acting|taking\s+actions?|making\s+changes|(?:running|calling)\s+(?:tools|commands|things)|"
    r"what\s+you'?re\s+doing|it|now|all(?:\s+actions)?))?(?:\s+and\s+(?:just\s+)?answer\b[^.!?\n]*)?\s*[.!<>]*\s*$"
    r"|stop(?:\s+doing\s+(?:things|stuff|anything|everything)|\s+what\s+you'?re\s+doing|\s+taking\s+actions?|"
    r"\s+working|\s+acting)\b"
    r"|(?:just\s+)?answer\s+(?:my|the)\s+(?:\w+\s+)?questi\w*|just\s+answer\b)",
    re.I | re.M)
# New work asked for in the same message: then it is a redirect, not a stop.
_ACTION = re.compile(
    r"\b(?:fix|deploy|ship|merge|restart|kill|create|file|implement|install|delete|remove|revert|roll\s*back|undo|"
    r"retry|resume|continue|run|set\s+up|setup|build|launch|start|make|update|change|edit|push|land|go\s+ahead|"
    r"do\s+it|instead|until)\b", re.I)
# The stop phrase's own words never count as an action ("stop doing things", "answer my question").
_STOP_WORDS = re.compile(r"\b(?:stop|doing|things|stuff|working|acting|taking\s+actions?|making\s+changes|"
                         r"running\s+(?:tools|commands|things)|calling\s+(?:tools|commands|things))\b", re.I)

_QUESTION_START = re.compile(
    r"^\s*(?:(?:hey|so|ok(?:ay)?|and|but|please)[,\s]+)*(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:explain|tell\s+me|describe|summari[sz]e)|explain|tell\s+me\s+(?:why|what|how|whether|if)|"
    r"why\b|what(?:'s|\s+is|\s+are|\s+was|\s+were|\s+did|\s+does|\s+happened|\s+went)?\b|how\s+come\b|"
    r"how\s+(?:is|are|did|does|far|long)\b|where\s+(?:are|is|did)\b|is\s+(?:it|the|this)\b|are\s+(?:we|you)\b|"
    r"did\s+(?:it|you|the)\b|status\b)", re.I)
# A question that is really a request for action ("can you set up X?", "why is it broken? fix it"): an action verb in
# imperative position (clause start, or after please/can you/and/then/now/just). Narrated past work ("i asked to set
# up an ASG") is not a request.
_Q_ACTION = re.compile(
    r"(?:^|[.!?;:\n]\s*|\b(?:please|you|and|then|now|just|go)\s+)(?:fix|deploy|ship|merge|restart|kill|create|file|implement|install|delete|remove|revert|roll\s*back|undo|"
    r"retry|resume|continue|set\s+up|setup|build|launch|start|make|update|change|edit|push|land|go\s+ahead|do\s+it|"
    r"run|try|test|check|look\s+into|investigate|debug|find\s+out|dig|move|prove|use|let'?s|kill)\b", re.I)


def now() -> float:
    return time.time()


def enabled() -> bool:
    return os.environ.get("COACH_ANSWER_FIRST", "1").strip().lower() not in ("0", "off", "false", "no")


def _user_text(message) -> str:
    text = message if isinstance(message, str) else ""
    text = _DESKTOP_STOP.sub("", text).strip()
    return text


def classify(message) -> str | None:
    """"stop", "question" or None for the user's own message text."""
    text = _user_text(message)
    if not text or _NOTICE.match(text) or len(text) > 600:
        return None
    if _STOP.search(text):
        rest = _STOP_WORDS.sub(" ", _STOP.sub(" ", text))
        if not _ACTION.search(rest):
            return "stop"
        return None
    first = text.splitlines()[0]
    if _QUESTION_START.match(first) and not _Q_ACTION.search(text):
        # "what" alone starts imperatives rarely; a question still needs a "?" or a why/how/explain lead.
        if "?" in text or re.match(r"^\s*(?:\w+[,\s]+)*?(?:why|how|explain|can|could|would|tell)\b", first, re.I):
            return "question"
    return None


def arm(turns: dict, session_id: str, user_message, live: bool) -> None:
    """pre_llm_call: record this turn's kind on the shared per-turn record (long_wait's wait_turns)."""
    t = turns.get(session_id)
    if t is None:
        return
    kind = classify(user_message) if (live and enabled()) else None
    t["af_kind"] = kind
    t["af_start"] = now()
    t["af_calls"] = 0
    t["af_held"] = False


def check(turns: dict, session_id: str, tool: str, args, log, persisted_reply=None) -> str | None:
    """The block message for this call, or None. Fails open."""
    t = turns.get(session_id or "")
    if not t or not enabled():
        return None
    kind = t.get("af_kind")
    if not kind or not _live():
        return None
    name = str(tool or "").rsplit("__", 1)[-1]
    if kind == "stop":
        log({"event": "agent_block", "rule": RULE, "kind": "stop", "session_id": session_id, "tool": name})
        return STOP_MESSAGE
    t["af_calls"] = int(t.get("af_calls") or 0) + 1
    if t.get("replied"):
        return None
    calls = t["af_calls"] - 1  # calls already run before this one
    secs = now() - float(t.get("af_start") or now())
    if calls < BUDGET_CALLS and secs < BUDGET_S:
        return None
    if t.get("af_held"):
        log({"event": "answer_first_repeat", "rule": RULE, "session_id": session_id, "tool": name})
        return None
    if persisted_reply is not None:
        try:
            if persisted_reply(session_id):  # the interim hook is queued and can lag this call
                t["replied"] = True
                return None
        except Exception:
            pass
    t["af_held"] = True
    log({"event": "agent_block", "rule": RULE, "kind": "question", "session_id": session_id, "tool": name,
         "calls": calls, "secs": int(secs)})
    return QUESTION_MESSAGE.format(calls=calls, secs=int(secs))


def _live() -> bool:
    return not any(os.environ.get(k) for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"))
