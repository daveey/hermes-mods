"""relay_repeat: the user asks again while the agent keeps relaying an unfinished owner's progress.

Self-heal agent-1406d53913 (2026-11-02): a chat bumped a repair card for a blocked builder session, then for over 4 h
only relayed that card's progress ("repair underway, not deployed", "sent that evidence to the active repair worker").
David asked again five times ("session still red", "why cant you fix it, what's going on"). Every rule passed it: the
owner was committing, so the 24 h owner budget (AGENTS.md "Fix, Don't Just Report") held; report_only scored the
relays 0.12-0.30; fix_loop scores low when the agent "had said it was NOT done yet". The chat could do the work itself
and later did.

Rule: in a live chat, a repeat ask about the same unresolved state ends the owner's budget unless that owner delivered
since the last reply. Fires (once per turn, pre_llm_call) when
  (a) the user's message is a repeat ask (fix_loop's prefilter, or "why can't you fix / what's going on / look again"),
      typed by a person (never a notice or worker prompt);
  (b) the previous final reply relayed an unfinished owner: OWNER_HANDOFF_RE / OWNER_CARD_RE, or an unfinished marker
      ("underway", "not deployed", "still being tested", "waits for capacity") with an owner noun (a card id, a
      worker/agent/session/lane, "the repair"); and
  (c) that owner has not completed since: no card the reply names is done/archived on this host's boards, and no
      Kanban completion notice for it (or, when the reply named no card, any completion notice) arrived after it.
The hint tells the agent to take the next step itself or delegate with fix authority, and to link an unlinked card to
the chat. The hook marks the turn (loop state `relay_repeat_turn`) so the reply check always runs the report_only
second opinion on that turn's reply (reply_check.check(relay_repeat=True); report_only_scored gets relay_repeat: true).

Off switch: env COACH_RELAY_REPEAT=off, or config plugins.entries.coach.settings.relay_repeat: false.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

_HERE = Path(__file__).resolve().parent
_cache_spec = importlib.util.spec_from_file_location("_coach_module_cache", _HERE / "module_cache.py")
assert _cache_spec and _cache_spec.loader
_cache = importlib.util.module_from_spec(_cache_spec)
_cache_spec.loader.exec_module(_cache)


def _rc():
    return _cache.load_sibling(_HERE, "reply_check")


def _fl():
    return _cache.load_sibling(_HERE, "fix_loop")


REPEAT_RE = re.compile(
    r"\bwhy (?:can'?t|cant|couldn'?t|don'?t|dont|didn'?t|didnt|won'?t|wont|isn'?t|isnt|hasn'?t|hasnt|is(?:n'?t)? it|"
    r"aren'?t|arent|haven'?t|havent) (?:you |we |it |this |that )?(?:just )?(?:fix|fixed|done|work|working|solve|"
    r"resolved?|unblock|move|moving|happen)"
    r"|\bwhat'?s (?:going on|happening|the (?:hold ?up|status|delay))\b|\bwhat is going on\b"
    r"|\blook again\b|\bcheck again\b|\bany (?:update|progress|news)\b|\bis it (?:fixed|done|working|unblocked) (?:yet|now)\b"
    r"|\bstill (?:red|blocked|broken|stuck|failing|waiting|down|not)\b|\bwhere are we\b"
    # agent-48e909978c: "how goes?", "what's the deal, can we unblock ...?" (same phrasings as status_only's ask)
    + _rc().PROGRESS_ASK_EXTRA,
    re.I)

# the reply says the work is not finished yet ...
UNFINISHED_RE = re.compile(
    r"\b(?:not|isn'?t|hasn'?t been|haven'?t been|wasn'?t|aren'?t|not yet) (?:yet )?(?:been )?(?:deployed|done|fixed|"
    r"finished|landed|merged|shipped|live|retried|recovered|resolved|unblocked)\b"
    r"|\bunderway\b|\bin progress\b|\bunfinished\b|\bstill (?:being )?(?:tested|testing|running|working|debugging|"
    r"blocked|waiting|queued|pending|in review)\b|\bwaits? (?:for|on) (?:capacity|a worker|the worker|a slot)\b"
    r"|\bqueued (?:behind|for|on)\b|\bholding up (?:deploy(?:ment)?|the fix|recovery)\b"
    r"|\b(?:before|pending) (?:deploy(?:ment)?|the deploy|recovery)\b",
    re.I)
# ... and names someone else doing it
OWNER_NOUN_RE = re.compile(
    r"\bt_[0-9a-f]{6,}\b"
    r"|\b(?:its|the|that|active|repair|fix|recovery|deploy(?:ment)?|other|another|background|existing|owning)\s+"
    r"(?:repair |fix |recovery )?(?:worker|card|agent|subagent|session|chat|lane|owner)s?\b"
    r"|\bthe (?:existing |active |current )?(?:repair|fix|recovery)\b",
    re.I)
CARD_RE = re.compile(r"\bt_[0-9a-f]{6,}\b")
# a Kanban completion notice: "✔ [default] @default Kanban t_… done — …"
DONE_NOTICE_RE = re.compile(r"\bKanban (t_[0-9a-f]{6,}) (?:done|completed)\b")
CLOSED = ("done", "archived")
# Hermes-written user rows reply_check.notice_turn does not class as notices (blocked/failed Kanban notices, the todo
# list and system notes re-injected after compaction, coach nudges): never a person asking again. A backtest of the
# rule on 10 days of hub chats fired on all of these before this exclusion.
_SYNTHETIC_RE = re.compile(r"^\s*(?:[\u2714\u2713\u2716\u2717\u23f8\u26a0\u2757]|\[(?!image|screenshot)[^\]\n]{1,80}[\]:])", re.I)

HINT = ("[coach] The user has asked again ({n} time{s}) while your replies relayed {owner}'s unfinished progress "
        "(\"{phrase}\"). A repeat ask in a live chat ends that owner's budget: take the next step yourself this turn "
        "(reproduce, fix, deploy, verify on the user's path), or delegate_task with fix authority, instead of another "
        "status relay. If that card is not linked to this chat, link it this turn (kanban_origin_adopt, or "
        "session_outcome wait) so the user can see the work.")


def enabled() -> bool:
    """Env COACH_RELAY_REPEAT, then config plugins.entries.coach.settings.relay_repeat; default on."""
    raw: Any = os.environ.get("COACH_RELAY_REPEAT")
    if raw is None:
        try:
            raw = _cache.load_sibling(_HERE, "second_opinion")._config_settings().get("relay_repeat")
        except Exception:
            raw = None
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "off", "false", "no")


def board_card_status(card_id: str) -> Optional[str]:
    return _rc().board_card_status(card_id)


def _sig(text: str) -> str:
    return hashlib.sha1(" ".join(str(text or "").split()).encode("utf-8", "replace")).hexdigest()[:12]


def relay_owner(reply: str) -> Optional[dict]:
    """{"cards": [ids named], "phrase": evidence} when the reply relays an owner's unfinished work, else None."""
    text = (reply or "")[-4000:]
    if not text.strip():
        return None
    rc = _rc()
    cards = list(dict.fromkeys(CARD_RE.findall(text)))
    m = next((m for m in rc.OWNER_HANDOFF_RE.finditer(text) if not rc._quoted(text, m.start(), m.end())), None)
    m = m or rc.OWNER_CARD_RE.search(text)
    if m:
        return {"cards": cards, "phrase": m.group(0).strip()[:160]}
    un = next((u for u in UNFINISHED_RE.finditer(text) if not rc._quoted(text, u.start(), u.end())), None)
    if un and OWNER_NOUN_RE.search(text):
        return {"cards": cards, "phrase": un.group(0).strip()[:160]}
    return None


def _automated(user_text: str) -> bool:
    return bool(_rc().automation_prompt(user_text) or _SYNTHETIC_RE.match(user_text or ""))  # == rc.SYNTHETIC_USER_RE


def repeat_ask(user_text: str) -> bool:
    """A person asking again about the same state (never an automation notice or a worker prompt)."""
    if not isinstance(user_text, str) or not user_text.strip():
        return False
    if _automated(user_text):
        return False
    t = user_text[:1500]
    return bool(_fl().prefilter(t) or REPEAT_RE.search(t))


def assess(state: dict, turn: int, user_text: str,
           card_status: Optional[Callable[[str], Optional[str]]] = None) -> Optional[dict]:
    """state: the session's coach loop state (last_reply, relay*). Returns {"n", "owner", "cards", "phrase", "hint"}
    at most once per turn, else None. Fail-open: an unreadable board counts as not completed."""
    if not enabled() or not isinstance(user_text, str):
        return None
    prev = state.get("last_reply") or ""
    if _automated(user_text):
        done = DONE_NOTICE_RE.findall(user_text)
        if done:
            state["relay_done"] = list(dict.fromkeys((state.get("relay_done") or []) + done))[-50:]
            state["relay_done_after"] = _sig(prev)  # a completion arrived after this reply
        return None
    own = relay_owner(prev)
    if not own:
        state.pop("relay", None)  # the chain of relays broke: the agent acted or answered something else
        return None
    cards = own["cards"]
    done_ids = set(state.get("relay_done") or [])
    if cards:
        lookup = card_status or (lambda c: board_card_status(c))
        for c in cards:
            if c in done_ids:
                return None
            try:
                st = lookup(c)
            except Exception:
                st = None
            if st and str(st).lower() in CLOSED:
                return None
    elif state.get("relay_done_after") == _sig(prev):
        return None
    if not repeat_ask(user_text):
        return None
    if state.get("relay_repeat_turn") == turn:
        return None
    rel = state.get("relay")
    rel = rel if isinstance(rel, dict) else {}
    asks = [t for t in (rel.get("asks") or []) if isinstance(t, int) and t != turn] + [turn]
    state["relay"] = {"cards": cards, "asks": asks[-20:], "phrase": own["phrase"]}
    state["relay_repeat_turn"] = turn
    n = len(asks)
    owner = ", ".join(cards[:3]) if cards else "another owner"
    return {"n": n, "owner": owner, "cards": cards, "phrase": own["phrase"],
            "hint": HINT.format(n=n, s="" if n == 1 else "s", owner=owner, phrase=own["phrase"][:120])}
