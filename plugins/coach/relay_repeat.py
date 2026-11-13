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
      or a capability correction ("you can do things on my machine via ssh", "you have ssh access": agent-01e0b4b066),
      typed by a person (never a notice or worker prompt);
  (b) the previous final reply relayed an unfinished owner: OWNER_HANDOFF_RE / OWNER_CARD_RE, or an unfinished marker
      ("underway", "not deployed", "still being tested", "waits for capacity") with an owner noun (a card id, a
      worker/agent/session/lane, "the repair"); and
  (c) that owner has not completed since: no card the reply names is done/archived on this host's boards, and no
      Kanban completion notice for it (or, when the reply named no card, any completion notice) arrived after it.
The hint tells the agent to take the next step itself or delegate with fix authority, and to link an unlinked card to
the chat. The hook marks the turn (loop state `relay_repeat_turn`) so the reply check always runs the report_only
second opinion on that turn's reply (reply_check.check(relay_repeat=True); report_only_scored gets relay_repeat: true).

Second trigger, self-verify (self-heal agent-954fae4dfc, 2026-11-10): a follow-up "verified" a fix by reading old
test reports ("Did not repeat them or create another fixture", "No duplicate test run was needed") and left production
recovery NO_PROOF; David wrote "do this yourself". No owner was relayed and it is no repeat status ask, so the rule
above stayed silent. Fires (once per turn, sharing `relay_repeat_turn` and so the forced report_only second opinion) when
  (a) a person explicitly asks the agent to do / verify / run it itself (self_do_ask), and
  (b) the previous final reply left verification unresolved: it reviewed prior evidence instead of executing the
      check, or says the result is NO_PROOF / unproven / not yet verified (unresolved_verification), and did not
      state an exact safety or permission limit for not running it.
A completed implementation card is never completed verification: this path reads no card status. NO_PROOF alone, with
no correction from the user, never fires.

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
# list and system notes re-injected after compaction, coach nudges, ready-wait notices): never a person asking again.
# A backtest of the rule on 10 days of hub chats fired on all of these before this exclusion.
_SYNTHETIC_RE = re.compile(r"^\s*(?:[\u2714\u2713\u2716\u2717\u23f3\u23f8\u26a0\u2757]|\[(?!image|screenshot)[^\]\n]{1,80}[\]:])", re.I)

HINT = ("[coach] The user has asked again ({n} time{s}) while your replies relayed {owner}'s unfinished progress "
        "(\"{phrase}\"). A repeat ask in a live chat ends that owner's budget: take the next step yourself this turn "
        "(reproduce, fix, deploy, verify on the user's path), or delegate_task with fix authority, instead of another "
        "status relay. If that card is not linked to this chat, link it this turn (kanban_origin_adopt, or "
        "session_outcome wait) so the user can see the work.")

# --- capability correction (agent-01e0b4b066) ----------------------------------------------------------------------
# A chat answered two "parent ready with no worker" notices by raising a queued card's priority ("It remains queued
# behind seven running workers"); David wrote "i dont understand. you can do things on my macnine via ssh". That is
# neither a repeat status ask nor a do-it-yourself verification ask, so nothing fired. A person telling the agent it
# already has the access (ssh, the machine, the host) after a relay of a queued/unfinished owner ends that owner's
# budget the same way a repeat ask does.
CAPABILITY_RE = re.compile(
    r"\byou (?:can|could|are able to|'re able to)\s+(?:just\s+)?(?:do|run|reach|access|use|fix|ssh|get (?:in|on)to)\b"
    r"[^.?!\n]{0,60}?\b(?:via|over|through|with|using|from|on|into|onto)\s+(?:ssh|my (?:mac|machine|macbook|mini|laptop|"
    r"host|box|computer)|the (?:hub|host|machine|mac|mini|gateway)|here|this chat)"
    r"|\byou can ssh\b|\byou (?:already )?(?:have|got) (?:ssh|shell|terminal|root|sudo|the) ?access\b"
    r"|\byou (?:can|could) (?:just )?do (?:it|this|that|things) (?:your ?self|from here|here|in this chat)\b",
    re.I)

CAPABILITY_HINT = (
    "[coach] The user says you already have the access to do this (\"{ask}\") while your last reply relayed {owner}'s "
    "queued or unfinished work (\"{phrase}\"). That ends the owner's budget: take the next step yourself this turn "
    "with that access (ssh, the host terminal), or delegate_task with fix authority, instead of another priority "
    "bump or queue report. If one exact limit stops you (a permission you lack, an unsafe action), name it.")


def capability_correction(user_text: str) -> Optional[str]:
    """The matched phrase when a person tells the agent it already has the access to act, else None."""
    if not isinstance(user_text, str) or not user_text.strip() or _automated(user_text):
        return None
    m = CAPABILITY_RE.search(user_text[:1500])
    return m.group(0).strip()[:120] if m else None


# --- self-verify (agent-954fae4dfc) ---------------------------------------------------------------------------------
# a person telling the agent to do / verify / run the thing itself
SELF_ASK_RE = re.compile(
    r"\b(?:do|verify|check|test|run|prove|confirm|execute|try)\s+(?:(?:it|this|that|them|the (?:check|test|run|fix))\s+)?"
    r"(?:your ?self|personally)\b"
    r"|\byou do (?:it|this|that)\b"
    r"|\b(?:can|could|would) you (?:just |please )?(?:run|verify|test|execute) (?:it|this|that|the (?:check|test))\b"
    r"|\bwhy (?:didn'?t|didnt|haven'?t|havent) you (?:just )?(?:run|verify|test|execute|check)\b"
    r"|\b(?:stop|don'?t|dont) (?:handing (?:it |this )?off|hand (?:it |this )?off|delegating|delegate)\b",
    re.I)
# the reply reviewed prior evidence instead of executing the check now ...
REVIEW_ONLY_RE = re.compile(
    r"\bdid(?: not|n'?t) (?:repeat|re-?run|run|execute|reproduce|exercise)\b"
    r"|\bha(?:ve|s)(?:n'?t| not) (?:yet )?(?:run|re-?run|executed|exercised)\b"
    r"|\bno (?:duplicate |new |additional |further |extra )?(?:test |live )?(?:run|re-?run|execution|check|fixture)s? "
    r"(?:was |were |is )?(?:needed|required|necessary)\b"
    r"|\b(?:the )?existing (?:tests?|test reports?|coverage|runs?|evidence) (?:already )?"
    r"(?:cover|covers|covered|show|shows|prove|proves)\b"
    r"|\b(?:reviewed|inspected|read|checked) (?:the )?(?:existing|earlier|prior|previous|old) (?:tests?|test (?:reports?|runs?)|"
    r"logs?|runs?|evidence|coverage|reports?)\b"
    r"|\bwithout (?:re-?)?running\b",
    re.I)
# ... or left the outcome explicitly unproven
UNPROVEN_RE = re.compile(
    r"\bNO_PROOF\b|\bunproven\b|\bunverified\b"
    r"|\bnot (?:yet )?(?:been )?(?:proven|observed|verified|exercised|reproduced)\b"
    r"|\bremains? unobserved\b",
    re.I)
# an exact limit on executing it: honest, not a gap to correct
LIMIT_RE = re.compile(
    r"\b(?:unsafe|not safe)\b|\bwould (?:require|need) (?:a |an )?(?:production |live )?fault injection\b"
    r"|\bneeds? your (?:approval|permission|sudo|password|credentials?|sign-?in|login|ok)\b"
    r"|\brequires? (?:your )?(?:sudo|approval|permission|credentials?|a password)\b"
    r"|\b(?:not|isn'?t|aren'?t) (?:permitted|allowed|authori[sz]ed)\b|\bno (?:permission|access) to\b",
    re.I)

SELF_VERIFY_HINT = (
    "[coach] The user asked you to do this yourself after your last reply left verification unresolved "
    "(\"{phrase}\"). Run the promised check yourself this turn: a bounded, safe execution on the user's path, not "
    "another review of earlier reports or a review-only follow-up. In the reply, separate checks executed now, prior "
    "evidence reviewed, and production conditions still unobserved. If running it is unsafe or needs a permission you "
    "lack, state that exact limit instead of implying it ran.")


def self_do_ask(user_text: str) -> bool:
    """A person explicitly asking the agent to do / verify / run it itself (never a notice or worker prompt)."""
    if not isinstance(user_text, str) or not user_text.strip() or _automated(user_text):
        return False
    return bool(SELF_ASK_RE.search(user_text[:1500]))


def unresolved_verification(reply: str) -> Optional[dict]:
    """{"phrase": evidence} when the reply left verification unresolved (reviewed prior evidence instead of executing,
    or reported the outcome unproven) without stating an exact safety/permission limit, else None."""
    text = (reply or "")[-4000:]
    if not text.strip() or LIMIT_RE.search(text):
        return None
    rc = _rc()
    for rx in (REVIEW_ONLY_RE, UNPROVEN_RE):
        m = next((m for m in rx.finditer(text) if not rc._quoted(text, m.start(), m.end())), None)
        if m:
            return {"phrase": m.group(0).strip()[:160]}
    return None


def _assess_self_verify(state: dict, turn: int, user_text: str) -> Optional[dict]:
    if state.get("relay_repeat_turn") == turn or not self_do_ask(user_text):
        return None
    un = unresolved_verification(state.get("last_reply") or "")
    if not un:
        return None
    asks = [t for t in (state.get("self_verify_asks") or []) if isinstance(t, int) and t != turn] + [turn]
    state["self_verify_asks"] = asks[-20:]
    state["relay_repeat_turn"] = turn
    return {"n": len(asks), "kind": "self_verify", "owner": "", "cards": [], "phrase": un["phrase"],
            "hint": SELF_VERIFY_HINT.format(phrase=un["phrase"][:120])}


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
    """state: the session's coach loop state (last_reply, relay*). Returns {"n", "kind", "owner", "cards", "phrase",
    "hint"} at most once per turn, else None: kind "relay" (owner relay + repeat ask) or "self_verify" (a do-it-yourself
    ask after an unresolved verification report). Fail-open: an unreadable board counts as not completed."""
    if not enabled() or not isinstance(user_text, str):
        return None
    return _assess_relay(state, turn, user_text, card_status) or _assess_self_verify(state, turn, user_text)


def _assess_relay(state: dict, turn: int, user_text: str,
                  card_status: Optional[Callable[[str], Optional[str]]] = None) -> Optional[dict]:
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
    rep = repeat_ask(user_text)
    cap = None if rep else capability_correction(user_text)
    if not (rep or cap):
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
    if cap:
        return {"n": n, "kind": "relay", "owner": owner, "cards": cards, "phrase": own["phrase"], "capability": cap,
                "hint": CAPABILITY_HINT.format(ask=cap, owner=owner, phrase=own["phrase"][:120])}
    return {"n": n, "kind": "relay", "owner": owner, "cards": cards, "phrase": own["phrase"],
            "hint": HINT.format(n=n, s="" if n == 1 else "s", owner=owner, phrase=own["phrase"][:120])}
