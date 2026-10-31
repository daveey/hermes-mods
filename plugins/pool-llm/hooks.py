"""pool-llm hooks: point a credit/quota failure at the account pool, and hold an ask that David buy API credit.

- transform_terminal_output / transform_tool_result: output carrying a paid-key credit error ("credit balance is too
  low", "no credits remaining", "insufficient_quota") gets one line naming ``hermes-pool-llm``.
- pre_tool_call: a kanban_block / kanban_create / inbox_post / action_request whose text asks the user to add or buy
  API credit (or links an Anthropic/OpenAI billing page) is refused ONCE per session when the session never ran the
  pool helper. The refusal says to try the pool first; a second attempt goes through (the pool may really be down).
  Only LLM provider credit counts: an ask whose nearest billing context is GitHub Actions/AWS/Vast/Cloudflare passes.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

MARK = "pool-llm:"
HELPER = "~/.hermes/plugins/pool-llm/bin/hermes-pool-llm"
CREDIT_ERROR_RE = re.compile(r"credit balance is too low|no credits remaining|insufficient_quota", re.I)
HINT = (f"{MARK} this is a paid API key out of credit; that needs no user action. Run the call on the Hermes "
        f"subscription account pool instead (Claude Max + ChatGPT accounts, rotates on limits). One call: `{HELPER} ask "
        f"--provider anthropic|openai --model <m> \"...\"`; an unmodified SDK harness: `{HELPER} serve --port 8765` then "
        "ANTHROPIC_BASE_URL=http://127.0.0.1:8765 OPENAI_BASE_URL=http://127.0.0.1:8765/v1 (any API key). "
        f"`{HELPER} models` lists the pool's models.")

_MONEY = r"(?:api\s+)?(?:credits?|credit balance|billing|funds|balance|budget|spend(?:ing)? limit|top[- ]?up)"
CREDIT_ASK_RE = re.compile(
    r"console\.anthropic\.com/settings/(?:billing|plans)|platform\.openai\.com/(?:settings/organization/|account/)?billing"
    r"|\b(?:add|buy|purchase|top[- ]?up|load|refill|fund|increase|raise)\b[^.\n]{0,60}?\$?\d*[^.\n]{0,20}?\b" + _MONEY +
    # noun first ("credit needs to be added", "a top-up is needed"): only a stated need counts, so descriptions like
    # "credits that predated the purchase" or "credit purchases, auto top-up" do not (backtest 2026-10-31)
    r"|\b(?:credits?|balance)\b[^.\n]{0,40}?\b(?:(?:needs?|must|should|has\s+to|have\s+to)\s+(?:to\s+)?(?:be\s+)?"
    r"(?:added|topped[- ]up|refilled|bought|purchased|increased|raised)|needs?\s+(?:a\s+|another\s+)?(?:top[- ]?up|refill)"
    r"|(?:top[- ]?up|refill|purchase)s?\s+(?:is|are|was|were)\s+(?:needed|required|necessary))\b"
    r"|\b(?:add|buy|purchase|top[- ]?up|load|refill|fund)\s+(?:about |around |roughly |another |~)?\$\d"
    r"|\bmoney ask\b",
    re.I)
# The ask must be about LLM API credit: the pool can't pay GitHub Actions, AWS, Vast or Cloudflare bills (live false
# positive: a kanban_block asking to "raise the spending limit" for GitHub Actions billing). Copied from
# plugins/coach/reply_check.py (_about_llm_credit); keep the two in step. Both signals are billing CONTEXT, never a
# bare name: almost every card says "GitHub" or "Claude", and that must not decide anything.
_LLM = r"(?:anthropic|openai|open\s?router|gemini|claude|gpt|llm)"
_BILL = (r"(?:credits?|credit balance|billing|quota|balance|funds|budget|spend(?:ing)?(?:\s+limit)?|top[- ]?up|bill"
         r"|org|account)")
_PREP = r"\s+(?:on|for|to|in|at|with|of)\s+(?:the\s+|our\s+|your\s+|my\s+)?"
LLM_CREDIT_RE = re.compile(
    r"\b" + _LLM + r"(?:\s+api)?\s+" + _BILL + r"\b"
    r"|\b" + _BILL + _PREP + _LLM + r"\b"
    r"|insufficient_quota|credit balance is too low|no credits remaining|\bapi\s+credits?\b"
    r"|console\.anthropic\.com|platform\.openai\.com", re.I)
_OTHER = (r"(?:github(?:\s+actions)?|actions|aws|ec2|vast(?:\.ai)?|cloudflare|runpod|lambda\s+labs|gcp|azure|heroku"
          r"|vercel|netlify)")
NON_LLM_BILLING_RE = re.compile(
    r"\bgithub\s+actions\b|\bactions\s+minutes\b"
    r"|\b" + _OTHER + r"(?:\s+account)?\s+" + _BILL + r"\b"
    r"|\b" + _BILL + _PREP + _OTHER + r"\b", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n")
# Money wording that is an ask on its own; budget/balance/funds/billing/spend limit are engineering words too ("retry
# budget", "memory budget", "time budget") and need LLM billing context nearby (self-heal agent-ddabb16633). A top-up
# counts as the noun, not as the verb that opens the match ("top-up went through; I'll check the balance").
_STRONG_MONEY_RE = re.compile(r"\bcredits?\b|\$\s?\d|console\.anthropic\.com|platform\.openai\.com|\bmoney ask\b", re.I)
_TOPUP_NOUN_RE = re.compile(r"\btop[- ]?ups?\b", re.I)


def _nearest(rx: "re.Pattern", scope: str, a: int, b: int) -> Optional[int]:
    """Distance from span [a, b) of scope to the nearest rx match (0 = overlapping), or None."""
    d = [max(0, x.start() - b, a - x.end()) for x in rx.finditer(scope)]
    return min(d) if d else None


def _about_llm_credit(text: str, m: "re.Match") -> bool:
    """The money wording at m may be about LLM provider credit. Its sentence decides first, then its paragraph: the
    billing target nearest the ask wins (LLM credit/quota wording = yes; GitHub Actions/AWS/Vast/Cloudflare billing =
    no). A scope naming neither defers outward, never to other paragraphs. With no billing context at all, only strong
    money wording counts (credit(s), a $ amount, a top-up noun, a billing URL): a bare "add $250 of credit" does, a bare
    budget/balance/funds/billing ("raise the retry budget", "load on the memory budget") does not."""
    p0 = text.rfind("\n\n", 0, m.start()) + 1
    p1 = text.find("\n\n", m.end())
    p1 = len(text) if p1 < 0 else p1
    cuts = [p0] + [x.end() + p0 for x in _SENTENCE_END.finditer(text[p0:p1])] + [p1]
    s0 = max(c for c in cuts if c <= m.start())
    s1 = min((c for c in cuts if c >= m.end()), default=p1)
    for lo, hi in ((s0, s1), (p0, p1)):
        scope, a, b = text[lo:hi], m.start() - lo, m.end() - lo
        llm, other = _nearest(LLM_CREDIT_RE, scope, a, b), _nearest(NON_LLM_BILLING_RE, scope, a, b)
        if llm is not None or other is not None:
            return other is None or (llm is not None and llm <= other)
    return bool(_STRONG_MONEY_RE.search(m.group(0)) or _TOPUP_NOUN_RE.search(m.group(0), 1))


# Only an ask counts, not a mention of one (live false positive 2026-10-29: a reply describing this rule, '"Raise the
# GitHub Actions spending limit" no longer sets it off, while a real ask to add Anthropic or OpenAI credit still
# does.'). Skipped: wording inside double quotes on its line, and a sentence that reports on the detector itself.
# Copied from plugins/coach/reply_check.py (_mentioned_not_asked); keep the two in step.
_QUOTED_RE = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')
_META_RE = re.compile(
    r"\bsets?\s+(?:it|this|that|them|the\s+\w+)\s+off\b|\bsetting\s+(?:it|this|that)\s+off\b"
    # "...credit still does." / "...no longer fires once...": the verb must END its clause (punctuation, end of text,
    # or a trailing condition), so "still does nothing" and "still fires 429s" are not reports on the detector.
    r"|\b(?:still|no\s+longer|never)\s+(?:does|do|did|fires?|fired|triggers?|trips?|flags?|matches?|catches?)\b"
    r"(?=\s*(?:[.,;:!?)\u2013\u2014]|$|(?:once|when|whenever|if|unless|after|because|since|now|anymore|either|too)\b))"
    r"|\bfalse\s+(?:alarm|positive|negative)s?\b|\bcredit_ask\b"
    r"|\b(?:the|this|that|its|my|our)\s+(?:detector|regex|rule|check)(?:[\u2019']s)?(?:\s+regex)?\s+(?:still\s+)?"
    r"(?:matched|matches|caught|catches|fires|fired|flags|flagged|triggers|triggered)\b"
    r"|\b(?:an?|the)\s+(?:real|genuine|actual|plain|bare)\s+(?:ask|request)\b"
    # another actor's ask reported back ("the eval chat asked you to add $250", "a card asking you to buy API credit
    # is refused"); "I asked you to..." is a restated ask and still counts
    r"|\b(?:card|post|request|reply|chat|message|block|session|eval|agent|worker)s?\s+(?:that\s+|which\s+)?"
    r"(?:asks?|asked|asking)\s+(?:you|david|the\s+user)\s+to\b", re.I)
# A clause that directly requests the ask ("...; please add $50", "so can you top up") is an ask whatever the rest of
# its sentence says. Only explicit request words count: a bare clause-initial verb also opens "false alarm: add ...".
_CLAUSE_CUT = re.compile(r"[;:,()\u2013\u2014]|\s-\s")
_DIRECT_RE = re.compile(r"\b(?:please|pls|can\s+you|could\s+you|would\s+you|need\s+you\s+to)\b(?:\s+\w+){0,3}\s*$",
                        re.I)


def _mentioned_not_asked(text: str, m: "re.Match") -> bool:
    """The money wording at m sits inside double quotes, or in a sentence reporting on the credit-ask detector."""
    ls = text.rfind("\n", 0, m.start()) + 1
    le = text.find("\n", m.end())
    line = text[ls:len(text) if le < 0 else le]
    if any(q.start() + ls <= m.start() and m.end() <= q.end() + ls for q in _QUOTED_RE.finditer(line)):
        return True
    cuts = [0] + [x.end() for x in _SENTENCE_END.finditer(text)] + [len(text)]
    s0 = max(c for c in cuts if c <= m.start())
    s1 = min((c for c in cuts if c >= m.end()), default=len(text))
    head = text[s0:m.start()]
    if _DIRECT_RE.search(head[max((c.end() for c in _CLAUSE_CUT.finditer(head)), default=0):]):
        return False
    return bool(_META_RE.search(text[s0:s1]))


def _is_llm_credit_ask(text: str, m: "re.Match") -> bool:
    """m is an ask (not a quoted or reported mention) about LLM provider credit."""
    return not _mentioned_not_asked(text, m) and _about_llm_credit(text, m)


# Copied from plugins/coach/reply_check.py (CREDIT_NEGATED_RE); keep the two in step.
# A sentence saying credit is NOT needed is not an ask (live false positive 2026-10-29: "So no credit top-up is
# needed."). Only explicit not-needed forms are blanked before the search, so "No credits left, so a top-up is
# needed", "No need to wait; add $250 of credit" and "... but add $250" still hold.
_NEG_MONEY = r"(?:api\s+)?(?:credits?|credit balance|billing|funds|budget|money|top[- ]?up)"
_NOT_NEEDED = r"(?:needed|required|necessary)"
_NEG_GAP = r"(?:(?!\b(?:but|except|only|just|unless|until)\b)[^.\n;:,\u2013\u2014]){0,40}?"
CREDIT_NEGATED_RE = re.compile(
    r"\bno\s+(?:more\s+|extra\s+|additional\s+|new\s+)?" + _NEG_MONEY +
    r"\b(?:\s+[\w-]+){0,2}?(?:\s+(?:is|are|was|were|gets|will\s+be))?\s+" + _NOT_NEEDED + r"\b"
    r"|\bno\s+need\s+(?:to|for)\b" + _NEG_GAP + r"\b" + _NEG_MONEY + r"\b"
    r"|\b(?:don[\u2019']t|doesn[\u2019']t|do\s+not|does\s+not|won[\u2019']t|will\s+not|never|no\s+longer)\s+"
    r"need(?:s|ed)?\b" + _NEG_GAP + r"\b" + _NEG_MONEY + r"\b"
    r"|\b" + _NEG_MONEY + r"\b(?:\s+[\w-]+){0,2}?\s+(?:is|are|was|were)(?:n[\u2019']t|\s+not|\s+no\s+longer)\s+" +
    _NOT_NEEDED + r"\b"
    # denials of an ask (self-heal agent-ddabb16633: "My reply had no credit or purchase ask"): "no credit (or X) ask",
    # "not a credit ask", "no purchase", "I'm not asking you to buy ... credit", "I didn't ask you to buy credit"
    r"|\bno\s+(?:credit|money|purchase|payment|spend(?:ing)?|top[- ]?up)s?\s+(?:or\s+[\w-]+\s+)?(?:ask|request)s?\b"
    r"|\bnot\s+an?\s+(?:credit|money|purchase|payment|spend(?:ing)?|billing)\s+(?:ask|request)\b|\bno\s+purchases?\b"
    r"|\b(?:not|never|didn[\u2019']t|did\s+not|don[\u2019']t|do\s+not|won[\u2019']t|will\s+not)\s+"
    r"(?:ask(?:s|ed|ing)?|request(?:s|ed|ing)?)\s+(?:you\s+|david\s+|the\s+user\s+)?(?:to|for)\b" + _NEG_GAP + r"\b" +
    _NEG_MONEY + r"\b"
    # attribution, not money: "a Co-authored-by line to get credit"
    r"|\b(?:get|gets|getting|got|take|takes|took|give|gives|gave|given|deserves?|claim)\s+"
    r"(?:the\s+|some\s+|full\s+|proper\s+)?credit\b",
    re.I)


def _strings(v: Any):
    """Every string in a tool call's arguments (real newlines kept, unlike json.dumps)."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)


ASK_TOOLS = {"kanban_block", "kanban_create", "inbox_post", "action_request"}
HELPER_USE_RE = re.compile(r"hermes-pool-llm|pool_llm")
REFUSAL = (f"{MARK} held once: this asks the user to add or buy API credit, but this session never tried the Hermes "
           f"account pool. Run the work on the pool first (`{HELPER} ask ...`, or `{HELPER} serve` for an SDK "
           "harness; see ~/AGENTS.md 'LLM Calls in Scripts and Evals'). Ask the user for money only after the pool failed too, "
           "and quote that failure in the ask. Retrying this exact call will go through.")

_lock = threading.Lock()
_used: set[str] = set()      # sessions that ran the pool helper
_held: set[str] = set()      # sessions already refused once


def _bare(name: str) -> str:
    return (name or "").rsplit("__", 1)[-1]


def hint_for(text: Any) -> Optional[str]:
    if isinstance(text, str) and MARK not in text and CREDIT_ERROR_RE.search(text):
        return text.rstrip("\n") + "\n\n" + HINT
    return None


def note_helper_use(session_id: str, text: Any) -> None:
    if session_id and isinstance(text, str) and HELPER_USE_RE.search(text):
        with _lock:
            _used.add(session_id)


def transform_terminal_output(command: str = "", output: Any = None, task_id: str = "", **kw) -> Optional[str]:
    note_helper_use(kw.get("session_id") or task_id, command)
    if HELPER_USE_RE.search(command or ""):
        return None  # the helper's own output (e.g. PoolExhausted quoting the error) needs no pointer to itself
    return hint_for(output)


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "",
                          **_) -> Optional[str]:
    name = _bare(tool_name)
    if name not in ("execute_code", "process_manage", "process", "terminal"):
        return None
    blob = json.dumps(args, default=str) if args else ""
    note_helper_use(session_id, blob)
    if HELPER_USE_RE.search(blob):
        return None
    return hint_for(result)


def _ask_calls(tool_name: str, args: Any):
    name = _bare(tool_name)
    if name in ASK_TOOLS and isinstance(args, dict):
        yield name, args
    elif name == "tool_call" and isinstance(args, dict) and isinstance(args.get("calls"), list):
        for call in args["calls"]:
            if isinstance(call, dict):
                yield from _ask_calls(str(call.get("name") or ""), call.get("arguments"))


def credit_ask(tool_name: str, args: Any) -> Optional[str]:
    """The matched phrase when this tool call asks the user for API credit, else None."""
    for name, a in _ask_calls(tool_name, args):
        if name == "kanban_create" and a.get("initial_status") != "blocked" and not a.get("human_request"):
            continue
        # each field is its own paragraph, so one field never lends billing context to an unrelated other field
        text = "\n\n".join(_strings(a))
        text = CREDIT_NEGATED_RE.sub(lambda n: " " * len(n.group(0)), text)
        m = next((m for m in CREDIT_ASK_RE.finditer(text) if _is_llm_credit_ask(text, m)), None)
        if m:
            return m.group(0)
    return None


def pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_) -> Optional[dict]:
    name = _bare(tool_name)
    if name in ("terminal", "execute_code"):
        note_helper_use(session_id, json.dumps(args, default=str) if args else "")
        return None
    if not credit_ask(tool_name, args):
        return None
    key = session_id or "?"
    with _lock:
        if key in _used or key in _held:
            return None
        _held.add(key)
    return {"action": "block", "message": REFUSAL}


def register(ctx) -> None:
    ctx.register_hook("transform_terminal_output", transform_terminal_output)
    ctx.register_hook("transform_tool_result", transform_tool_result)
    ctx.register_hook("pre_tool_call", pre_tool_call)
