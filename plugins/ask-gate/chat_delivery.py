"""Refuse a human ask whose action is to deliver text into a Hermes chat (self-heal agent-c25865e208).

Workers were refused a ``cross_session`` send into a Desktop chat and then blocked asking David to type that same
line into the chat. Delivering text into a chat is agent work: the refusal subscribes the chat to the worker's own
card, and a ``FINAL SEND for @session:<ref>: <text>`` line in ``kanban_complete`` delivers the text; a non-worker
session sends it directly with ``cross_session``. Pure stdlib, no I/O; the caller fails open.

Narrow on purpose: a match needs a delivery verb aimed at a specific Hermes chat (an ``@session:`` ref, a session
id, or a named "<x> chat"), or a structured ask naming a chat this session was just refused a send to. "Reply in
this chat", physical and external asks are never matched.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

_REF = re.compile(r"@session:(?:[A-Za-z0-9_.-]+(?:@[A-Za-z0-9_.-]+)?/)?[A-Za-z0-9_.-]+")
_SID = re.compile(r"\bsession\s+`?(?P<sid>[0-9a-f]{16,}|\d{8}_\d{6}_[0-9a-f]{4,})`?", re.I)
# "the <name> chat": a specific Hermes chat ("the canary repair chat", "the hermes-hub chat"). Bare "the chat", "a/any
# hub chat", "in chat" and other messaging apps are not one.
_OTHER_APPS = r"slack|whatsapp|telegram|signal|imessage|teams|discord|group|team|family|web|video|voice|support|sales"
_STOP = r"in|on|into|to|of|a|an|via|for|from|with|this|that|and|or"
_NAMED = re.compile(r"\bthe\s+(?:(?!(?:chat|" + _OTHER_APPS + "|" + _STOP + r")\b)[\w'·-]+\s+){1,4}chat\b", re.I)
_VERB = (r"send|type|post|paste|relay|forward|message|tell|ask|say|write|prompt|nudge|ping|drop|put|enter|"
         r"submit|give")
# "Resume/open the X chat and ...": counts only with the chat right after it.
_RESUME = re.compile(r"\b(?:resume|reopen)\s+(?:the\s+)?(?:[\w'·-]+\s+){0,4}chat\b|\bresume\s+@session:", re.I)
_ANY_VERB = re.compile(r"\b(?:" + _VERB + r")\b", re.I)
# An imperative position: start of a line/sentence/list item, or after and/then/please/a modal addressed to David.
_LEAD = (r"(?:^|\n|[.!?;:]\s+|\b(?:and|then|please|or)\s+|,\s*|\b(?:you|david)\s+(?:can|could|should|must|need\s+to|"
         r"needs\s+to|have\s+to|will\s+need\s+to)\s+|\bneeds?\s+(?:you\s+)?to\s+)\s*(?:\d+[.)]\s*|[-*•]\s*)?(?:\*\*)?")
_IMPERATIVE = re.compile(_LEAD + r"(?:" + _VERB + r")\b", re.I)
_SKIP_INTERACTIONS = {"physical", "external"}


def _target(text: str) -> tuple[str, int] | None:
    """(ref-or-label, offset) of the first specific Hermes chat named in ``text``."""
    best = None
    for rx, kind in ((_REF, "ref"), (_SID, "sid"), (_NAMED, "named")):
        m = rx.search(text)
        if m and (best is None or m.start() < best[1]):
            label = m.group(0) if kind == "ref" else (
                f"@session:default/{m.group('sid')}" if kind == "sid" else m.group(0))
            best = (label, m.start())
    return best


def _best_ref(text: str, refused: Iterable[str]) -> str:
    m = _REF.search(text)
    if m:
        return m.group(0)
    for r in refused:
        sid = r.rsplit("/", 1)[-1]
        if sid and sid in text:
            return r
    m = _SID.search(text)
    if m:
        return f"@session:default/{m.group('sid')}"
    return "@session:<profile>/<session_id>"


def refused_targets(tools: list[dict]) -> list[str]:
    """Session refs this turn tried to ``cross_session`` send into and was refused."""
    out: list[str] = []
    for t in tools or []:
        if t.get("tool") != "cross_session":
            continue
        if "refused" not in str(t.get("result") or "").lower():
            continue
        args = t.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                m = _REF.search(args)
                args = {"target": m.group(0)} if m else {}
        target = str((args or {}).get("target") or "").strip() if isinstance(args, dict) else ""
        if not target:
            continue
        ref = target if target.startswith("@session:") else (
            f"@session:default/{target.split(':', 1)[-1]}" if ":" in target else f"@session:default/{target}")
        if ref not in out:
            out.append(ref)
    return out


_AGENT_ROUTE = re.compile(r"cross_session|FINAL[ _]SEND", re.I)


def _sentence_span(text: str, i: int) -> tuple[int, int]:
    start = max(text.rfind(c, 0, i) for c in ".!?\n") + 1
    ends = [j for j in (text.find(c, i) for c in ".!?\n") if j >= 0]
    return start, (min(ends) if ends else len(text))


def _sentence_at(text: str, i: int) -> str:
    s, e = _sentence_span(text, i)
    return text[s:e]


def _delivery_in(text: str, *, structured: bool) -> bool:
    """A delivery verb aimed at a specific chat named in the same sentence or the one before it ("1) open the X
    chat. 2) Send: ..."). ``structured``: the text IS the ask, so any verb counts. A sentence that already names the
    agent route (cross_session / FINAL SEND) is an instruction to an agent; "tell me" is a reply to this agent."""
    if not _target(text):
        return False
    if (r := _RESUME.search(text)) and not _AGENT_ROUTE.search(_sentence_at(text, r.start())):
        return True
    verbs = _ANY_VERB if structured else _IMPERATIVE
    for m in verbs.finditer(text):
        if re.match(r"\s+(?:me|us)\b", text[m.end():]):
            continue
        s, e = _sentence_span(text, m.end() - 1)
        if _AGENT_ROUTE.search(text[s:e]):
            continue
        ps, _ = _sentence_span(text, max(s - 2, 0)) if s > 1 else (s, e)
        if _target(text[ps:e]):
            return True
    return False


def find_delivery(text: str, *, structured: bool = False, refused: Iterable[str] = ()) -> str | None:
    """The session ref to deliver to when ``text`` asks a human to put a message into a Hermes chat, else None."""
    if not isinstance(text, str) or not text.strip():
        return None
    refused = list(refused or [])
    flat = text.replace("“", "\"").replace("”", "\"")
    if _delivery_in(flat, structured=structured):
        return _best_ref(flat, refused)
    if structured:
        for r in refused:
            sid = r.rsplit("/", 1)[-1]
            if len(sid) >= 8 and sid in flat:
                return r
    return None


def check_request(request: Any, refused: Iterable[str] = (), context: str = "") -> str | None:
    """A structured ``human_request``: judge its action only, never why_human/evidence narration. The other fields
    and ``context`` (the block reason) only supply the session ref for the refusal text."""
    if not isinstance(request, dict):
        return None
    if str(request.get("interaction") or "").strip().lower() in _SKIP_INTERACTIONS:
        return None
    action = str(request.get("action") or "")
    ref = find_delivery(action, structured=True, refused=refused)
    if ref and "<" in ref:
        more = " ".join(str(request.get(k) or "") for k in ("scope", "why_human", "evidence")) + " " + str(context)
        ref = _best_ref(more, list(refused))
    return ref


def refusal(ref: str, *, worker: bool) -> str:
    route = (f"As a Kanban worker: call cross_session action=send target={ref} once (it is refused, and the refusal "
             f"subscribes that chat to your card), then kanban_complete with a line `FINAL SEND for {ref}: <text>`; "
             f"the chat receives exactly that text when the card completes. "
             if worker else
             f"Send it yourself with cross_session action=send target={ref} message=<text>. ")
    return ("ask-gate: refused. This ask has a human deliver a message into a Hermes chat, and that is agent work, "
            "not a human step. " + route
            + ("(Outside a worker, a session sends it directly with cross_session action=send.) " if worker else
               f"(A Kanban worker uses a `FINAL SEND for {ref}: <text>` line in kanban_complete instead.) ")
            + "\"Workers are refused sending into Desktop chats\" is not a reason to ask "
              "David: the refusal is what arms FINAL SEND. If the real need is David's decision, ask for that "
              "decision itself (interaction=approval, the exact thing to approve), not for him to relay text.")
