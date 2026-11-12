"""reply_check: look at the agent's final answer for a turn BEFORE the turn ends (pre_verify hook).

Five anti-patterns, measured in the user's own history (coach gap report 2026-10-14):
- handoff:     the agent hands the user a step it could plausibly do itself (click/log in/run/paste...).
- confirm:     the agent ends by asking permission for a reversible next step ("Want me to ...?").
               Also the statement-shaped form: the user redirects work under way and the reply answers with a
               counter-recommendation and "I haven't ..." instead of starting it (recommend_stall).
- unverified:  the agent claims done/fixed/working without checking on the user's real path.
- unwatched:   the agent promises to continue once outside work finishes ("I'll write the report when the
               runs finish") but nothing will wake the session: no background process, kanban card, goal or
               subagent. The turn ends, the sidebar paints the row done, and the promise is never kept.
               A deterministic wake-source check runs first, so the judge only sees promises with no waker.
- report_only: the agent describes or diagnoses a problem it could fix and ends there: no fix, no started fix
               (card, worker, PR), no concrete proposal ("Not fixed. No action is needed from you."). An explicit
               scope limit in the user's ask ("analysis only", "just tell me", "don't change anything") skips it
               before the judge runs. A reply that hands the problem to another owner (session/card/worker) this
               turn never checked or messaged fires deterministically, before the judge (`unverified_owner`).

Layer 1 (here, zero latency) are cheap regex prefilters; Layer 2 is an LLM judge (aux task
`coach_reply`) that sees the user's ask, the turn's tool log and the reply. Only a judge verdict
at or above the threshold produces a nudge; the nudge goes back to the agent (the turn continues),
never to the user.
"""
from __future__ import annotations

import contextvars
import importlib.util
import json
import math
import os
import re
import time
from typing import Any, Callable, Optional
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_cache_spec = importlib.util.spec_from_file_location("_coach_module_cache", _HERE / "module_cache.py")
assert _cache_spec and _cache_spec.loader
_cache = importlib.util.module_from_spec(_cache_spec)
_cache_spec.loader.exec_module(_cache)

HANDOFF_RE = re.compile(
    r"(\b(please|you(?:'ll| will)? need to|you can now|can you|could you|go to|open|click|tap|drag|paste|"
    r"sign in|log ?in|run (?:this|the following)|quit and (?:re)?open|relaunch|restart (?:the )?app|"
    r"approve|grant|enable|allow)\b[^.\n]{0,120}\b(then|and) (?:tell|let) me\b"
    r"|\b(let me know|tell me|ping me|reply|say \"?\w+\"?) (?:when|once|after) (?:you(?:'ve| have)?|it'?s|that'?s)\b"
    r"|\b(?:once|after|when) you(?:'ve| have)? (?:clicked|signed|logged|run|opened|approved|granted|enabled|dropped|pasted|added|installed|plugged|restarted|done)\b"
    r"|^\s*(?:\d+[.)]|[-*])\s*(?:click|open|go to|tap|sign in|log in|paste|drag|press|select|choose|enable|grant)\b"
    # Statement-shaped handoffs with no "then tell me" tail (missed 6/6 in a real Alpha + coordinator transcript):
    # "What you need to do:", "Cleanup for you:", "That's yours to do", "may still need you", "/invite @bot first",
    # "someone in #chan runs ...", "needs your AWS SSO session", "one I can't run".
    r"|\bwhat you(?:'ll)? need to do\b|\b(?:cleanup|left|steps?|actions?|to-?dos?|homework) for you\b"
    r"|\b(?:that'?s|it'?s|this is) yours to do\b|\b(?:may|might|will|would) (?:still )?need you\b"
    r"|\bneeds? you to\b|(?:^|\s)`?/invite\b|\bsomeone (?:in|on|with) \S+ (?:runs?|types?|clicks?|invites?|sends?)\b"
    r"|\bneeds? your (?:\w+ ){0,2}(?:session|login|password|approval|click|terminal)\b"
    r"|\b(?:one|command|step|something) I can'?t (?:run|do)\b)",
    re.I | re.M)
CONFIRM_RE = re.compile(
    r"(\b(?:want me to|should i|shall i|do you want me to|would you like me to|ok(?:ay)? (?:if|to) i|"
    r"(?:ready|happy) (?:for me )?to (?:proceed|go|ship|merge|push|apply)|say (?:the word|go|yes)|"
    r"(?:let me know|tell me) if you(?:'d)? (?:want|like) me to)\b[^?\n]{0,200}\?\s*$"
    r"|\b(?:want me to|should i|shall i)\b[^?\n]{0,200}\?)", re.I | re.M)
# agent-56e18d89f2: a permission ask with no question mark. The user redirected work he had already approved ("how
# about we make the hub uncapped, but always give the work to the workers"); the reply agreed, countered with "I
# recommend a large emergency cap rather than completely uncapped", said "I haven't raised the live cap", and started
# nothing. CONFIRM_RE only matches questions, so the judge never saw it; 47 s later: "do all of this work, dont ask me
# for permission". A recommendation is a candidate when the same reply holds the work back (a contrast with what was
# asked, or an "I haven't <done it>") and does not say it is going ahead. Candidates only: the judge sets precision.
_APOS = r"(?:'|’)"
RECOMMEND_RE = re.compile(
    r"\b(?:I(?:" + _APOS + r"d| would)? (?:\w+ )?(?:recommend|suggest|propose|advise)"
    r"|my (?:recommendation|suggestion|advice) (?:is|would be|here is)\b"
    r"|I(?:" + _APOS + r"d| would) (?:go with|rather|prefer|lean towards?))\b", re.I)
RECOMMEND_CONTRAST_RE = re.compile(
    r"\b(?:rather than|instead of|as opposed to|and not\b|over (?:a|an|the|fully|completely)\b)", re.I)
HELD_BACK_RE = re.compile(
    r"\bI(?: have not| did not| haven" + _APOS + r"?t| didn" + _APOS + r"?t|" + _APOS + r"ve not) (?:yet )?(?:actually )?"
    r"(?:start(?:ed)?|begun|begin|rais(?:ed|e)|lower(?:ed)?|chang(?:ed|e)|appl(?:ied|y)|made|make|touch(?:ed)?|"
    r"deploy(?:ed)?|merg(?:ed|e)|push(?:ed)?|ship(?:ped)?|enabl(?:ed|e)|disabl(?:ed|e)|implement(?:ed)?|built|build|"
    r"creat(?:ed|e)|land(?:ed)?|switch(?:ed)?|updat(?:ed|e)|edit(?:ed)?|remov(?:ed|e)|modif(?:ied|y)|turn(?:ed)?|"
    r"flip(?:ped)?|set|done|do|act(?:ed)?|proceed(?:ed)?|gone ahead|go ahead|kick(?:ed)? off|roll(?:ed)? out)\b", re.I)
# The reply says the adjusted plan is going ahead (this turn, or by default unless the user objects): not a stall.
PROCEEDING_RE = re.compile(
    r"\b(?:start(?:ing|ed) (?:it|that|this|on (?:it|that|this)|the \w+|work)?\s*(?:now|already)\b"
    r"|I" + _APOS + r"?(?:m| am) (?:now )?(?:starting|implementing|building|proceeding|going ahead|doing|making|applying)\b"
    r"|I(?:" + _APOS + r"ve| have) (?:started|begun|kicked off|implemented|applied|built)\b"
    r"|I" + _APOS + r"?ll (?:go ahead|proceed) (?:with|and)\b|going ahead with\b|proceeding with\b"
    r"|unless you (?:object|say otherwise|tell me otherwise|stop me|disagree|veto)\b"
    r"|(?:silence|no reply|no answer) (?:=|means|is) (?:go|yes|the default)\b|by default,? I" + _APOS + r"?(?:ll| will)\b"
    r"|(?:is|are|now) (?:underway|under way|in progress)\b)", re.I)
# The user asked for the recommendation itself: giving one is the answer, not a stall.
ASKED_OPINION_RE = re.compile(
    r"\b(?:what (?:do|would|should) you (?:think|recommend|suggest|advise|propose)|what(?:'|’)?s your (?:recommendation|"
    r"take|opinion|view|advice|suggestion)|your (?:recommendation|opinion|take|thoughts)\b|do you (?:recommend|think|agree)"
    r"|which (?:one|option|approach|way)\b|pros and cons|trade-?offs?\b|thoughts\s*\?|recommend(?:ation)?s?\s*\?"
    r"|should (?:we|i)\b[^.\n]{0,160}\?|is (?:it|that|this) (?:a good|the right|worth|better|safe))", re.I)


def recommend_stall(reply: str) -> Optional[str]:
    """The recommendation sentence when the reply's tail counters the user's direction with a recommendation, holds
    the work back (a contrast in that sentence, or an "I haven't <done it>" in the tail) and nowhere says it is going
    ahead. None otherwise. A neutral status line ("I haven't deployed yet; CI is running") has no recommendation and a
    plain "I recommend X" holds nothing back: neither is a candidate."""
    tail = (reply or "")[-900:]
    m = RECOMMEND_RE.search(tail)
    if not m or PROCEEDING_RE.search(tail):
        return None
    start = max(tail.rfind("\n", 0, m.start()), tail.rfind(". ", 0, m.start())) + 1
    ends = [i for i in (tail.find("\n", m.end()), tail.find(". ", m.end())) if i != -1]
    sentence = tail[start:min(ends) if ends else len(tail)]
    if not (RECOMMEND_CONTRAST_RE.search(sentence) or HELD_BACK_RE.search(tail)):
        return None
    return " ".join(sentence.split()).strip(" *_")[:200]
_FINISH = (r"(?:finish(?:es|ed)?|complete[sd]?|land(?:s|ed)?|(?:is|are) done|come[s]? back|end[s]?|pass(?:es)?|"
           r"go(?:es)? green)")
WAIT_RE = re.compile(
    r"(\b(?:once|when|after|as soon as)\b[^.\n]{0,90}\b" + _FINISH + r"\b[^\n]{0,160}?\b(?:I'll|I will|I'm going to)\b"
    r"|\b(?:I'll|I will)\b[^.\n]{0,160}\b(?:once|when|after|as soon as)\b[^.\n]{0,90}\b" + _FINISH + r"\b"
    r"|\bI'll (?:check back|check in again|follow up|report back|pick (?:this|it) (?:back )?up)\b"
    r"|\b(?:waiting|wait) (?:on|for) (?:the |its |their |those |these )?(?:\w+ ){0,3}(?:runs?|jobs?|training|"
    r"experiments?|evals?|builds?|CI|sweeps?|deploys?|tests?|soaks?|workers?)\b)", re.I)
DONE_RE = re.compile(
    r"\b(done|fixed|works now|is working|now works|verified|deployed|shipped|is live|it'?s live|resolved|"
    r"all set|should (?:now )?(?:work|be fixed|be working)|ready to use|in place|complete[d]?)\b", re.I)

# report_only: high recall over the reply's tail; the judge supplies the precision. Phrases from the 2026-10-27
# labeled set (report-only endings on fixable problems) plus negated status verbs, which carry most of the misses.
REPORT_RE = re.compile(
    r"\bnot (?:been |yet )?fixed\b|\bdiagnos(?:ed|is)\b|\bnot yet\b"
    r"|\b(?:have|has|had|did)(?:n't|n’t| not) (?:yet )?(?:\w+ ){0,2}?(?:fixed|established|verified|checked|confirmed|"
    r"paused|installed|deployed|shipped|sent|done|started|changed|touched|followed|taken|found)\b"
    r"|\b(?:is|are|was|were)(?:n't|n’t| not) (?:yet )?(?:fixed|deployed|installed|live|shipped|verified|complete|done|"
    r"sent|processed|enforced|resolved|working)\b"
    r"|\bremains? \w+|\bstill (?:broken|open|failing|unfinished|stale|not|a\b|lists?|uses?|shows?|returns?|needs?|"
    r"blocked|missing|wrong|down|pending|running|queued|in shadow|off)"
    r"|\bstill (?:\w+ )?(?:hidden|being|untested|unverified|unmeasured|alerting|refus\w+)\b"
    r"|\b(?:unresolved|unfixed|untested|unmeasured|unverified)\b|\bthe existing (?:owner|task|card|worker)\b"
    r"|\bexisting (?:owner|task|card|worker)\b|\b(?:already )?owns (?:it|this|that|the)\b|\bowner\b|\brelayed\b"
    r"|\bno (?:further )?action (?:is )?needed\b|\bnothing (?:else )?needs you\b|\bneeds your attention\b"
    r"|\b(?:doesn't|doesn’t|does not|don't|don’t) (?:mean anything )?needs? your\b"
    r"|\byou can (?:safely )?ignore\b|(?:^|\n)\W*(?:open|remaining|still open|not fixed|gaps?|caveats?|known issues?)\b\W*:"
    r"|\bstill open\b|\bremaining\b|\bgap\b|\bawaiting\b|\bno longer\b|\bhanded (?:that |it )?back\b"
    r"|\bnot (?:processed|enforced|deployed|installed|sent|reproduced|verified)\b"
    r"|\b(?:didn|haven|hasn|aren|isn|won|can)(?:'|’)?t\b"
    r"|\bneeds? (?:conversion|extending|fixing|a fix|work|to be (?:fixed|updated|converted|extended))\b"
    r"|\brefus(?:es|ing)\b|\bcontinues? to (?:alert|fail|error|break|show)\b|\bone limit\b|\blimitation\b"
    # Caveat endings: a checkable or doable item left as a disclaimer or an offer (\"I did not look at X\", \"one limit to
    # know\", \"say if you want that one closed too\"). Candidates only: the judge and second opinion decide.
    r"|\b(?:I|we)(?:'ve| have)? (?:did not|didn'?t|have not|haven'?t|won'?t|will not|never) (?:yet )?(?:actually )?(?:check|look|test|verif|confirm|try|run|read|inspect)\w*\b"
    r"|\b(?:say|tell me|let me know) if you(?:'d)? (?:want|like|need)\b"
    # Leftovers named by likeness, not by a status word (bot-dc58243931: "O-7 through O-11 have the same
    # old-approval problem").
    r"|\b(?:have|has) the same (?:[\w-]+ ){0,3}?(?:problem|issue|bug|defect|failure|error)s?\b"
    # Owners that can't run (agent-0bab362bcd: "both start attempts failed ... queued behind the quota reset; it starts
    # by itself after that"). Candidates only: the judge applies STALLED_OWNER.
    r"|\bstarts? (?:up )?(?:by|on) (?:it|them)sel(?:f|ves)\b|\b(?:start |run )?attempts? (?:have |had )?(?:both |all )?failed\b"
    r"|\bqueued behind\b|\bno eligible (?:\w+ )?routes?\b|\b(?:quota|limit) resets?\b"
    r"|\bsame (?:[\w-]+ )?(?:problem|issue|bug) (?:applies|exists|affects)\b|\b(?:are|is) also (?:affected|broken)\b"
    # A workaround handed to the user instead of a fix ("if the job holds one, pressing 'Post now' ... will work").
    r"|\bif\b[^.\n]{0,120}?,\s*(?:pressing|clicking|running|re-?running|tapping)\b[^.\n]{0,120}?\bwill (?:work|fix|clear)"
    r"|\byou can (?:press|click|tap|run|re-?run|hit)\b[^.\n]{0,80}?\bto\b"
    # Owner named as working on it, with no word of a check (Oct 30 human-turn misses: "Card X is working on that now",
    # "the card is doing these now", "the Steward is handling it", "the other session owns remediation", "fix work will
    # start once that hold is lifted", "it's waiting until your MacBook is less busy").
    r"|\b(?:is|are) (?:now |still )?(?:working on|handling|doing|seeing) (?:that|this|it|these|those|them)\b"
    r"|\bowns? (?:the )?(?:remediation|fix|rest|follow-?ups?|retest\w*|verification)\b"
    r"|\bwill (?:start|begin|resume) once\b|\bno (?:fix|work) has (?:started|begun)\b"
    r"|\b(?:is|are|it'?s|still) waiting (?:until|for (?:hermes-sync|the next|your|a restart|it to))\b"
    # Leftovers waved off as small or optional ("moving it over is optional tidy-up", "I left that alone", "a leftover
    # test screenshot", "fixing it means making sure ...", "the changes require a gateway restart", "not posted").
    r"|\boptional (?:tidy-?up|clean-?up|follow-?up)\b|\bleft (?:that|it|this|them) (?:alone|as (?:is|it is))\b"
    r"|\bleftovers?\b|\b(?:fixing|solving) (?:it|this|that) (?:means|would mean|requires|needs)\b"
    r"|\b(?:requires?|needs?) an? (?:\w+ )?restart\b|\bnot posted\b|\bthe blocker is\b|\bnext question is\b",
    re.I)
# An explicit scope limit in the user's ask: a diagnosis is what they asked for, so report_only never applies.
SCOPE_LIMIT_RE = re.compile(
    # "X-only" as a limit ("Diagnosis only.", "Read-only: ...", "an analysis-only task"), not as an adjective in a long
    # request ("Read-only checks before...", "run it read-only on the hub", "the report-only chain").
    r"\b(?:analysis|analy[sz]e|diagnos(?:is|e)|research|investigat(?:e|ion)|report|read)[- ]only\b"
    r"(?=\s*(?:[.,:;!?)\n—–-]|$|please\b|for now\b)|\s+(?:task|card|investigation|analysis|pass|review|audit|mode|"
    r"work|run|session|request|turn)s?\b)"
    r"|\bjust (?:tell me (?:why|what|how|if|whether)|explain|answer)\b|\bonly (?:tell|explain|want to know|report)\b"
    r"|\bplan (?:it )?first\b|\bdon'?t (?:change|touch|fix|edit|modify|mutate|ship|deploy) (?:anything|it|this|that|a thing)\b"
    r"|\bdo not (?:change|touch|fix|edit|modify|mutate) (?:anything|it|this|that)\b"
    # "Do not change any cards in this task." (self-heal agent-668442d68f). Only state/artifact nouns: "fix X but
    # don't change any tests" is a constraint on a fix, not a diagnosis-only limit.
    r"|\b(?:do not|don'?t) (?:change|touch|edit|modify|mutate) any (?:of the )?(?:cards?|tasks?|tickets?|issues?|"
    r"files?|code|config(?:s|uration)?|settings?|state|services?|jobs?|data)\b"
    r"|\b(?:make |with )?no (?:edits|changes|fixes|code changes|mutations)\b|\bwithout (?:changing|fixing|editing) (?:anything|it)\b"
    r"|\bdon'?t fix\b|\bnot (?:to )?fix (?:it|anything)\b",
    re.I)

# report_only, unverified owner: the reply hands a problem to another session/card/worker/agent ("I'm leaving them to
# that session", "not from my change", "another session is landing that work", "the owner has it"). Fixed patterns,
# checked before the judge: the judge reads these right but scored the real 2026-10-27 case 0.60 and 0.75 against a
# 0.75 bar. Fires only when the turn's tool log has no check of, or message to, an owner (OWNER_CHECK_RE).
_WHO = r"(?:that|the other|another|other|a different|the owning|its|their)\s+(?:session|agent|worker|card|chat|lane|owner)s?"
OWNER_HANDOFF_RE = re.compile(
    r"\b(?:I'?m |I am |I'll |I will )?(?:leav(?:e|ing)|hand(?:ing)? (?:it|them|this|that) (?:back|off|over)|"
    r"defer(?:ring)?) (?:it|them|this|that|those|these)?\s*(?:to|for|with) " + _WHO + r"\b"
    r"|\bnot (?:from|caused by|due to|related to) my (?:own )?(?:change|fix|edit|commit|deploy|work|patch)e?s?\b"
    r"|\b" + _WHO + r" (?:is |are )?(?:still )?(?:landing|handling|fixing|working on|owns?|has|on) (?:it|them|this|that|those)\b"
    r"|\b(?:another|the other|a different) (?:session|agent|worker) (?:is|are) (?:still )?"
    r"(?:landing|handling|fixing|working on|changing|editing)\b"
    r"|\bthe (?:existing )?owner (?:has|owns|is (?:on|handling)) (?:it|this|that|them)\b"
    r"|\b(?:that'?s|it'?s|this is) (?:for )?" + _WHO + r"(?:'s)? (?:to fix|problem|job|change)\b"
    # agent-a806f203ab: "the follow-up lives on PR #248, owned by card t_…" named a card as owner without a check.
    r"|\b(?:owned by|lives (?:on|with)|belongs to) (?:the )?(?:card|task) t_[0-9a-f]+\b"
    r"|\bowned by (?:that |another |the other |its )?(?:card|task|session)\b"
    # agent-48e909978c: "Clarification queued to my subagent: ...; hub owns any source or association repair" was a
    # whole status reply. Handoffs to a subagent/worker/coordinator or to the hub, not only to a session or card.
    r"|\b(?:queued|handed|passed|delegated|deferred)\b[^.;:\n]{0,40}?\bto (?:my |the |a |our )?"
    r"(?:[\w-]+ ){0,2}?(?:sub-?agent|worker|coordinator)s?\b"
    r"|\b(?:the )?(?:hub|gateway|sub-?agent|worker|coordinator) (?:owns|has|is handling|is on) "
    r"(?:any |the |all |that |this )?(?:[\w-]+ ){0,3}?(?:repairs?|fix(?:es)?|work|changes?|rollout|deploy(?:ment)?)\b"
    r"|\bthe (?:coordinator|worker|sub-?agent) has (?:it|this|that|them)\b",
    re.I)
# The card ids a reply names as the owner of the remaining work ("owned by card t_…", "belongs to task t_…").
OWNER_CARD_RE = re.compile(
    r"\b(?:owned by|lives (?:on|with)|belongs to|handled by|tracked (?:by|on|in)|owner is|owner:)\s+(?:the\s+)?"
    r"(?:card|task)\s+`?(t_[0-9a-f]+)\b`?"
    r"|\b(?:card|task)\s+`?(t_[0-9a-f]+)`?\s+(?:owns|is handling|is landing|will (?:land|handle|fix))\b",
    re.I)
CLOSED_CARD_STATUSES = ("done", "archived")
# Tool-log evidence that the agent checked or messaged an owner this turn: the tool itself (`-> kanban_show(`), the
# deferred `tool_call` wrapper (`"name": "cross_task"`), or the CLI form in a terminal call (`hermes kanban show`).
_OWNER_TOOLS = (r"(?:cross_session|cross_task|session_mentions|kanban_(?:show|comment|create|list|link|block|unblock)|"
                r"delegate_task|process_manage|process|chat_history_lookup|inbox_post)")
OWNER_CHECK_RE = re.compile(
    r"^\s*->\s*" + _OWNER_TOOLS + r"\("
    r"|[\"']name[\"']\s*:\s*[\"']" + _OWNER_TOOLS + r"[\"']"
    r"|^\s*->\s*terminal\(.*\bhermes kanban\b.*\b(?:show|comment|create|list|tail)\b"
    r"|^\s*->\s*terminal\(.*\bhermes sessions?\b"
    # agent-25f447092d: a read-only query of the board db (terminal sqlite3 or execute_code) is an owner check too,
    # and so is any `<- OK` owner line agent_rules.observe wrote (it writes one only after a successful owner read).
    r"|^\s*->\s*(?:terminal|execute_code)\(.*(?:\bsqlite3\b.*kanban[\w./-]*\.db|kanban[\w./-]*\.db.*\bsqlite3\b)"
    r"|^\s*<-\s*OK\s+(?!park=)", re.I)
_QUOTES = "\"“”`"


def _quoted(text: str, start: int, end: int) -> bool:
    """The match sits inside a quotation (a reply quoting the phrase, e.g. a postmortem), not a handoff: it opens or
    closes on a quote mark, or an odd number of quote marks precede it on its line."""
    line = text[text.rfind("\n", 0, start) + 1:start]
    after = text[end:end + 2].lstrip(" .,;:!?")[:1]
    return bool(sum(line.count(q) for q in _QUOTES) % 2 == 1 or line.rstrip().endswith(("'", "‘"))
                or line.endswith(tuple(_QUOTES)) or (after and after in _QUOTES))


def unverified_owner(reply: str, tool_log: Optional[list] = None) -> Optional[str]:
    """The handoff phrase when the reply's ending hands a problem to an owner this turn never checked, else None."""
    tail = (reply or "")[-1500:]
    m = next((m for m in OWNER_HANDOFF_RE.finditer(tail) if not _quoted(tail, m.start(), m.end())), None)
    if not m:
        return None
    if any(OWNER_CHECK_RE.search(l or "") for l in (tool_log or [])):
        return None
    return m.group(0).strip()


# agent-a3c36a6f70: a fix left dead "until the dashboard's next restart" (the dashboard imports plugin_api.py once at
# startup) with no restart and no card. The screen scored that ending 0.72, under the 0.75 confirm floor, so the second
# opinion never ran. A reply ending on such a deferral is a report_only candidate and always gets the second opinion,
# unless this turn ran a restart (pause-turns, systemctl/launchctl, the gateway helper) or filed a card that did not
# error. The second opinion still decides; this only stops the screen's score from skipping it.
RESTART_DEFERRAL_RE = re.compile(
    r"\buntil\b[^.\n]{0,80}?\b(?:re-?start(?:s|ed)?|reboot(?:s|ed)?|reloads?|reloaded)\b"
    r"|\b(?:after|on|at|with|from) (?:the |its |their |a )?next (?:\w+ )?(?:re-?start|reboot|reload)\b",
    re.I)
RESTART_ACTION_RE = re.compile(
    r"^\s*->.*(?:\bsystemctl\b[^\n]{0,120}\brestart\b|\blaunchctl\s+kickstart\b|\bpause-turns\b"
    r"|\bgateway-idle-restart\b|\bhermes\s+gateway\s+restart\b|\bkanban_create\b|\bhermes\s+kanban\s+create\b)",
    re.I)


def restart_deferral(reply: str, tool_log: Optional[list] = None) -> Optional[str]:
    """The phrase when the reply's ending leaves a change inactive until some later restart and this turn neither
    restarted anything nor filed a card (a call followed by an ``<- ERROR`` line does not count), else None."""
    tail = (reply or "")[-1500:]
    m = next((m for m in RESTART_DEFERRAL_RE.finditer(tail) if not _quoted(tail, m.start(), m.end())), None)
    if not m:
        return None
    lines = list(tool_log or [])
    for i, line in enumerate(lines):
        if RESTART_ACTION_RE.search(line or ""):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not (nxt or "").lstrip().startswith("<- ERROR"):
                return None
    return m.group(0).strip()


# agent-a806f203ab: a Kanban worker called kanban_complete, then an operator note reopened the work in the same turn.
# It pushed a fix and ended with CI unwatched and a proof never run, because nothing wakes a closed card and the
# report_only nudge only offered "fix it now or kanban_create". card_closed() picks the closed-card nudge.
_COMPLETE_RE = re.compile(r"^\s*->\s*kanban_complete\(|[\"']name[\"']\s*:\s*[\"']kanban_complete[\"']"
                          r"|^\s*->\s*terminal\(.*\bhermes kanban\b.*\bcomplete\b", re.I)


def card_closed(tool_log: Optional[list] = None) -> bool:
    """True when this turn's tool log shows a kanban_complete (tool, deferred tool_call or CLI) that did not error."""
    lines = list(tool_log or [])
    for i, line in enumerate(lines):
        if _COMPLETE_RE.search(line or ""):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not (nxt or "").lstrip().startswith("<- ERROR"):
                return True
    return False


# agent-9de19e15a9: an owner the turn checked and found queued (ready/todo), filed under 24 h ago and with no failed
# run, is a started fix (AGENTS.md: an owner is disqualified only after 24 h with no change or when it can't start).
# Telemetry only (report_only_scored.owner_fresh_ready): the judges read the same `<- OK` line through their rubrics.
_OK_SEGMENT_RE = re.compile(r"task=(t_[0-9a-zA-Z_]+)\s+status=(\w+)(?:\s+run=(\S+))?(?:\s+summary=(?:(?!\slast=)[^|])*)?"
                            r"(?:\s+last=\S+\s+@\s+\S+\s+\((\d+)([smhd]) ago\))?")
# list reads (agent-5f2ca3a822): `<- OK list: <id>=<status>(<age> ago) ...`, no run state
_OK_LIST_SEGMENT_RE = re.compile(r"(t_[0-9a-zA-Z_]+)=(\w+)\((\d+)([smhd]) ago\)")
_FAILED_RUN_RE = re.compile(r"crash|fail|timed_out|timeout|error|gave_up", re.I)
FRESH_OWNER_S = 24 * 3600


# agent-61b50f34ae: telemetry only (report_only_scored.owner_probe_live): the reply names a scheduled card whose owner
# read (`<- OK`, or the coach's own `<- BOARD` line) shows its wake probe still running. The judges read the same
# `probe=live(...)` fact through BOARD_OWNER; this records whether the case was present when they scored.
_PROBE_LIVE_RE = re.compile(r"task=(?:[\w,]*?)(t_[0-9a-zA-Z_]+)\b[^|]*?\bprobe=live\(")


def owner_probe_live(reply: str, tool_log: Optional[list] = None, user_ask: str = "") -> list[str]:
    """Card ids the reply names (or, when it names none, the user's ask names: "that card ...") whose owner line in
    `tool_log` shows `probe=live(...)`. Empty when none."""
    named = set(NAMED_CARD_RE.findall(reply or "")) or set(NAMED_CARD_RE.findall(user_ask or ""))
    out: list[str] = []
    for line in tool_log or []:
        s = (line or "").lstrip()
        if not (s.startswith("<- OK") or s.startswith("<- BOARD")):
            continue
        for m in _PROBE_LIVE_RE.finditer(s):
            if m.group(1) in named and m.group(1) not in out:
                out.append(m.group(1))
    return out


# agent-61b50f34ae: the ids (card, PR, commit) of the reply paragraph a verdict quotes, logged on reply_nudge and
# reply_nudge_outcome so the self-heal collector (signals.same_item) can tell a paraphrase of the nudged item from a
# different one: a judge quotes ~20 words and rarely repeats the nudge's wording, but both paragraphs name the card.
_ITEM_ID_RE = re.compile(r"\bt_[0-9a-f]{8,}\b|(?<![\w&])#\d{2,6}\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
_ITEM_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def evidence_ids(reply: str, evidence: str, user_ask: str = "", tool_log: Optional[list] = None) -> list[str]:
    """Card / PR / commit ids (commits cut to 7 hex) in the paragraph of `reply` sharing the most words with
    `evidence` (the evidence's own ids first). [] when no paragraph shares a word.
    agent-b22d5f6bb5: when that paragraph and the evidence name no id, the cards `user_ask` names (a notice's
    "Existing delivery card t_..."): the draft called it "the existing task", the revision named it.
    agent-8381864e1e: when still no card id but the evidence or the paragraph says "the card", the cards this turn's
    tool calls touched (turn_card_ids) stand in, so a nudge and its outcome about the same unnamed card share an id."""
    def ids(text):
        out = []
        for m in _ITEM_ID_RE.finditer(text or ""):
            v = m.group(0)
            v = v if v.startswith(("t_", "#")) else v[:7]
            if v not in out:
                out.append(v)
        return out
    words = set(_ITEM_WORD_RE.findall((evidence or "").lower()))
    best, score = "", 0
    for para in re.split(r"\n\s*\n", reply or ""):
        n = len(words & set(_ITEM_WORD_RE.findall(para.lower())))
        if n > score:
            best, score = para, n
    out = list(dict.fromkeys(ids(evidence) + ids(best)))[:12]
    if not out and user_ask:
        out = list(dict.fromkeys(NAMED_CARD_RE.findall(user_ask)))[:NAMED_CARD_MAX]
    if not any(v.startswith("t_") for v in out) and (CARD_REF_RE.search(evidence or "") or
                                                      (score and CARD_REF_RE.search(best))):
        out += [c for c in turn_card_ids(tool_log)[:3] if c not in out]
    return out[:12]


def fresh_ready_owner(reply: str, tool_log: Optional[list] = None) -> list[str]:
    """Card ids the reply names whose `<- OK` owner check shows them ready/todo, last activity under 24 h, and no
    failed/crashed/timed-out last run. Empty when none (or no such check ran)."""
    text = reply or ""
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    out: list[str] = []
    for line in tool_log or []:
        if not (line or "").lstrip().startswith("<- OK"):
            continue
        segs = [m.groups() for m in _OK_SEGMENT_RE.finditer(line)] + [
            (c, st, None, n, u) for c, st, n, u in _OK_LIST_SEGMENT_RE.findall(line)]
        for cid, status, run, n, u in segs:
            if cid in out or cid not in text or status not in ("ready", "todo"):
                continue
            if run and run != "None" and _FAILED_RUN_RE.search(run):
                continue
            if n is None or int(n) * unit[u] >= FRESH_OWNER_S:
                continue
            out.append(cid)
    return out


def _board_dbs() -> list:
    """Every Kanban board db on this host (default + kanban/boards/*), via core when importable."""
    from pathlib import Path
    try:
        from hermes_cli import kanban_session_activity as ksa  # type: ignore
        return list(ksa._board_db_paths())
    except Exception:
        pass
    env = os.environ.get("HERMES_KANBAN_DB")
    root = Path(os.environ.get("HERMES_KANBAN_HOME") or _hermes_home())
    paths = [Path(env)] if env else [root / "kanban.db"]
    paths += sorted((root / "kanban" / "boards").glob("*/kanban.db"))
    return [p for p in paths if p.is_file()]


def board_card_status(card_id: str) -> Optional[str]:
    """The card's status on any board on this host, read-only; None when no board has it or a read fails."""
    for path in _board_dbs():
        try:
            conn = _ro_connect(path, timeout=2)
            try:
                row = conn.execute("select status from tasks where id=?", (card_id,)).fetchone()
            finally:
                conn.close()
        except Exception:
            continue
        if row:
            return str(row[0])
    return None


def closed_owner(reply: str, card_status: Optional[Callable[[str], Optional[str]]] = None
                 ) -> Optional[tuple[str, str]]:
    """(card id, status) when the reply's ending names a card as owner of the remaining work and that card is done or
    archived (a closed card owns nothing). Fires even when the turn looked the card up. Unknown cards (another
    gateway's board, a typo) and read errors are fail-open: None."""
    tail = (reply or "")[-1500:]
    lookup = card_status or board_card_status
    seen: set = set()
    for m in OWNER_CARD_RE.finditer(tail):
        cid = m.group(1) or m.group(2)
        if not cid or cid in seen or _quoted(tail, m.start(), m.end()):
            continue
        seen.add(cid)
        try:
            st = lookup(cid)
        except Exception:
            st = None
        if st and st.lower() in CLOSED_CARD_STATUSES:
            return cid, st.lower()
    return None


# agent-9ac78980e2 (with agent-6504f6447c, agent-fabde565ec): the judges learned an owner card's state only from the
# agent's own reads, so a true "that card is running now" with no read this turn failed (p 0.9) and a false one got no
# counter-evidence. For up to NAMED_CARD_MAX cards the reply's ending names that no `<- OK` line read, the coach reads
# this host's boards itself and both judge prompts get one `<- BOARD ...` line per card (agent_rules.board_fact_line).
# agent-a8184f8541: a card on no local board is read from this host's replica of its home gateway's board
# (kanban/sync-shadow/*.db on the hub); its line ends `home=<gw> (replica)`. Cards on no board add nothing.
NAMED_CARD_MAX = 4
NAMED_CARD_RE = re.compile(r"\bt_[0-9a-f]{8,}\b")
_DQUOTES = "\"“”"


def _in_dquotes(text: str, start: int) -> bool:
    """Inside a "..." quotation on its line (a reply quoting an old note), not a card the reply itself names.
    Backticks are code formatting here, not quoting: `t_…` is how replies usually write a card id."""
    line = text[text.rfind("\n", 0, start) + 1:start]
    return sum(line.count(q) for q in _DQUOTES) % 2 == 1


def _read_this_turn(cid: str, tool_log: Optional[list]) -> bool:
    for line in tool_log or []:
        s = (line or "").lstrip()
        if not (s.startswith("<- OK") or s.startswith("<- BOARD")) or cid not in s:
            continue
        if re.search(r"task=[\w,]*" + re.escape(cid) + r"\b[^|]*\bstatus=", s) or f"{cid}=" in s:
            return True
    return False


def _named_or_ask_text(reply: str, user_ask: str = "") -> str:
    """The reply's ending; when it names no card id, the user's ask (agent-b22d5f6bb5: a draft saying "the existing
    task will resume when the Mac reconnects" about the card its notice named; owner_probe_live's same fallback)."""
    tail = (reply or "")[-1500:]
    if NAMED_CARD_RE.search(tail) or not user_ask:
        return tail
    return user_ask[:ASK_HEAD]


# agent-8381864e1e: the replies left the fix to "the self-heal card" / "The card's fix ..." without its id, so the
# board read (ids written in the reply only) gave the judges nothing and they scored the same hand-off 0.9 and 0.1.
# A reply ending that refers to a card with no id (and an ask naming none) falls back to the cards this turn's tool
# calls touched.
CARD_REF_RE = re.compile(r"\b(?:the|that|this|its|their)\s+(?:[\w-]+\s+){0,2}?card(?:s|'s|’s)?\b", re.I)
_TOUCH_TOOLS_RE = re.compile(r"^\s*->\s*(?:mcp__)?(kanban_comment|kanban_show|kanban_create|tool_call)\(")


def turn_card_ids(tool_log: Optional[list]) -> list[str]:
    """Card ids this turn's kanban_comment / kanban_show / kanban_create / tool_call calls touched (a kanban_create's
    id is in its result line), most recent first."""
    lines = list(tool_log or [])
    out: list[str] = []
    for i, line in enumerate(lines):
        m = _TOUCH_TOOLS_RE.match(line or "")
        if not m:
            continue
        text = line or ""
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (nxt or "").lstrip().startswith("<-") and not (nxt or "").lstrip().startswith("<- ERROR") and (
                m.group(1) in ("kanban_create", "tool_call")):
            text += " " + nxt
        for cid in NAMED_CARD_RE.findall(text):
            if cid in out:
                out.remove(cid)
            out.append(cid)
    return list(reversed(out))


def tail_card_ids(reply: str, tool_log: Optional[list] = None, user_ask: str = "") -> tuple[list[str], bool]:
    """(ids, fallback): the unquoted card ids the reply's ending names, in order (callers cap at NAMED_CARD_MAX).
    When it names none: the cards `user_ask` names (agent-b22d5f6bb5), else, when it refers to "the card", the cards
    this turn's tool calls touched (agent-8381864e1e); fallback=True for either."""
    tail = _named_or_ask_text(reply, user_ask)
    ids: list[str] = []
    for m in NAMED_CARD_RE.finditer(tail):
        cid = m.group(0)
        if cid not in ids and not _in_dquotes(tail, m.start()):
            ids.append(cid)
    reply_tail = (reply or "")[-1500:]
    if NAMED_CARD_RE.search(reply_tail):
        return ids, False
    if ids:
        return ids, True
    if NAMED_CARD_RE.search(tail) or not CARD_REF_RE.search(reply_tail):
        return [], False
    return turn_card_ids(tool_log), True


def named_card_facts(reply: str, tool_log: Optional[list] = None, now: Optional[float] = None,
                     dbs: Optional[list] = None, user_ask: str = "") -> list[str]:
    """`<- BOARD task=...` lines for cards the reply's ending names (unquoted) that this turn's tool log never read
    with a status; [] when there are none, no board has them, or a read fails (fail-open). A reply naming no card
    reads the cards `user_ask` names instead, or, when it says "the card", the cards this turn touched."""
    try:
        ids = [cid for cid in tail_card_ids(reply, tool_log, user_ask)[0]
               if not _read_this_turn(cid, tool_log)][:NAMED_CARD_MAX]
        if not ids:
            return []
        ar = _cache.load_sibling(_HERE, "agent_rules")
        now = time.time() if now is None else now
        out = []
        for cid in ids:
            facts = ar.board_card_facts(cid, now=now, dbs=dbs)
            if facts:
                out.append(ar.board_fact_line(_minus_turn_notes(facts, tool_log), now=now))
        return out
    except Exception:
        return []


def owner_source(reply: str, tool_log: Optional[list] = None, board: Optional[list] = None,
                 user_ask: str = "") -> str:
    """Where report_only's owner evidence for the cards the reply names came from (telemetry, agent-a8184f8541):
    `ok` (an `<- OK` line of the turn's own read), `board` / `replica` (the coach's own read of this host's board /
    of its replica of another gateway's board), or `none`."""
    named = (list(dict.fromkeys(NAMED_CARD_RE.findall(_named_or_ask_text(reply, user_ask))))
             or tail_card_ids(reply, tool_log, user_ask)[0])
    if not named:
        return "none"
    if any(_read_this_turn(cid, [l for l in tool_log or [] if (l or "").lstrip().startswith("<- OK")])
           for cid in named):
        return "ok"
    lines = named_card_facts(reply, tool_log, user_ask=user_ask) if board is None else list(board)
    if any("(replica)" in l for l in lines):
        return "replica"
    return "board" if lines else "none"
# ── agent-a0262d55e5: a stall comment on a card that never starts ─────────────────────────────────────────────────
# Hub 20261031_064851_d0f106: "the rollout is waiting on card t_01a19f3049f432d5, ready but has not started. I've
# flagged it as the immediate blocker." The turn's only action on that card was a kanban_comment; it had sat ready
# and unclaimed for 83 min and this chat had already commented the same stall twice. Both rubrics counted "the turn
# commented the stall on the owner card" as a started fix with no limit, so the strong judge passed it (p 0.1).
# The exemption now holds for the first such comment within STALL_COMMENT_GRACE_S of the card entering the queue;
# past that, or after an earlier comment, the gate below holds the reply unless this turn raised the card's priority,
# re-placed or reassigned it, or the card left the queue (the work was done here).
STALL_COMMENT_GRACE_S = 1800
STALL_REMEDY_WINDOW_S = 900
_TURN_COMMENT_RE = r"(?:\bkanban_comment\b|\bhermes\s+kanban\s+comment\b)"
STALL_REMEDY_CMD_RE = re.compile(
    r"\bhermes\s+kanban\s+(?:edit|assign|reassign|move|claim|reclaim|promote|dispatch|set-model|unblock|complete|"
    r"archive)\b|--priority\b|\bpriority\b|\bplacement\b|\bkanban_(?:move|supervisor_request|complete|archive)\b",
    re.I)
STALL_REMEDY_EVENTS = ("reprioritized", "assigned", "placement_repinned", "placement_forwarded", "moved",
                       "model_override_set", "reclaimed", "capability_routed")
STALL_WAIT_RE = re.compile(
    r"\b(?:wait(?:s|ing|ed)?|blocker|blocked|block(?:s|ing)|queued?|ready|pending|flagged|stall(?:s|ed)?|until|"
    r"behind|not (?:yet )?(?:started|claimed|picked up|running)|(?:has|hasn't|has not|hasn’t) (?:not )?"
    r"(?:started|been picked up|been claimed))\b", re.I)


def _turn_comments(cid: str, tool_log: Optional[list]) -> int:
    """kanban_comment calls this turn on `cid` (the tool, the deferred tool_call wrapper, or the CLI)."""
    return sum(1 for l in tool_log or [] if (l or "").lstrip().startswith("->") and cid in l
               and re.search(_TURN_COMMENT_RE, l))


def _minus_turn_notes(facts: dict, tool_log: Optional[list]) -> dict:
    """The board's comment count since the card entered the queue, minus this turn's own comments on it."""
    if not isinstance(facts, dict) or not facts.get("ready_since"):
        return facts
    n = max(0, int(facts.get("stall_notes") or 0) - _turn_comments(str(facts.get("id")), tool_log))
    return {**facts, "stall_notes": n}


def queued_owner_facts(reply: str, tool_log: Optional[list] = None, now: Optional[float] = None,
                       dbs: Optional[list] = None) -> list[dict]:
    """Board facts (this turn's comments not counted as stall notes) for ready/todo cards the reply's ending names
    unquoted (or, with no id, the cards the turn touched when it says "the card": `unnamed`), up to NAMED_CARD_MAX,
    whether or not the turn read them. [] on any failure (fail-open)."""
    try:
        ids, fallback = tail_card_ids(reply, tool_log)
        ids = ids[:NAMED_CARD_MAX]
        if not ids:
            return []
        ar = _cache.load_sibling(_HERE, "agent_rules")
        now = time.time() if now is None else now
        out = []
        for cid in ids:
            f = ar.board_card_facts(cid, now=now, dbs=dbs)
            if f and f.get("status") in ar.QUEUED_STATUSES and f.get("ready_since"):
                out.append({**_minus_turn_notes(f, tool_log), "ready_for_s": int(now - f["ready_since"]),
                            **({"unnamed": True} if fallback else {})})
        return out
    except Exception:
        return []


def _remedied(cid: str, tool_log: Optional[list], now: float, dbs: Optional[list]) -> bool:
    """This turn raised the card's priority, re-placed, reassigned or claimed it (tool log), or the board shows such
    an event in the last STALL_REMEDY_WINDOW_S."""
    for l in tool_log or []:
        s = (l or "").lstrip()
        if s.startswith("->") and cid in s and not re.search(_TURN_COMMENT_RE, s) and STALL_REMEDY_CMD_RE.search(s):
            return True
    ar = _cache.load_sibling(_HERE, "agent_rules")
    for path in (ar.board_dbs() if dbs is None else dbs):
        try:
            conn = ar._board_connect(path)
        except Exception:
            continue
        try:
            row = conn.execute(
                "select count(*) from task_events where task_id=? and created_at>=? and created_at<=? and kind in "
                f"({','.join('?' * len(STALL_REMEDY_EVENTS))})",
                (cid, now - STALL_REMEDY_WINDOW_S, now + 1, *STALL_REMEDY_EVENTS)).fetchone()
            if row and row[0]:
                return True
        except Exception:
            continue
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return False


def stalled_queue_owner(reply: str, tool_log: Optional[list] = None, now: Optional[float] = None,
                        dbs: Optional[list] = None) -> Optional[dict]:
    """The facts of a ready/todo card the reply's ending names as what the work waits on, when this turn's action
    on it was a stall comment and the card has sat in the queue STALL_COMMENT_GRACE_S or longer, or already carried
    a comment from before this turn; None when the turn remedied it, the card waits on an open parent, or on any
    read failure (fail-open)."""
    try:
        now = time.time() if now is None else now
        tail = (reply or "")[-1500:]
        ar = _cache.load_sibling(_HERE, "agent_rules")
        for f in queued_owner_facts(reply, tool_log, now=now, dbs=dbs):
            cid = str(f["id"])
            if f.get("waits_on") or not _turn_comments(cid, tool_log):
                continue
            # agent-8381864e1e: inside the grace a first comment still counts, unless the queue won't reach the card
            if f["ready_for_s"] < STALL_COMMENT_GRACE_S and not f.get("stall_notes") and not ar.deep_in_queue(f):
                continue
            i = tail.find(cid)
            window = tail if f.get("unnamed") else tail[max(0, i - 250):i + len(cid) + 250]
            if not STALL_WAIT_RE.search(window):
                continue
            if _remedied(cid, tool_log, now, dbs):
                continue
            return f
    except Exception:
        return None
    return None


def stall_telemetry(reply: str, tool_log: Optional[list] = None, now: Optional[float] = None) -> dict:
    """report_only_scored fields: the longest-queued named ready/todo card's queue age and earlier stall notes, and
    the most ready cards ahead of any named ready card (owner_ahead)."""
    try:
        fs = queued_owner_facts(reply, tool_log, now=now)
    except Exception:
        fs = []
    if not fs:
        return {}
    f = max(fs, key=lambda x: x["ready_for_s"])
    out = {"owner_ready_for_s": f["ready_for_s"], "owner_stall_notes": int(f.get("stall_notes") or 0)}
    aheads = [x["queue"]["ahead"] for x in fs if x.get("queue")]
    if aheads:  # agent-8381864e1e: the deepest named ready card's queue position
        out["owner_ahead"] = max(aheads)
    return out


def board_section(reply: str, tool_log: Optional[list], board: Optional[list] = None, user_ask: str = "") -> str:
    """The prompt section with the coach's own board read ("" when it has no lines). `board=None` reads now."""
    lines = named_card_facts(reply, tool_log, user_ask=user_ask) if board is None else list(board)
    if not lines:
        return ""
    return ("COACH'S OWN BOARD READ (this host's Kanban boards and its replicas of other gateways' boards, read just "
            "now for cards the final message names (when it names none, the cards the user's request names) that the "
            "tool log did not read; counts as a check of these cards this turn):\n"
            + "\n".join(lines) + "\n\n")


# status_only: the user asks why it's waiting / what's going on / to finish it, and the turn only reopened, unblocked,
# commented on or looked at cards (defect agent-38a817308d: four "reopened the card / verified ready" replies while the
# rollout card sat unstarted for 4 days). Deterministic, before the judge: the report_only owner-check exemption
# counts exactly those card operations as acting, so the judge path let all four through.
# Shared with relay_repeat.REPEAT_RE so both see the same progress phrasings.
PROGRESS_ASK_EXTRA = (
    r"|\bwhat(?:'s|’s|s| is) the deal\b|\bhow(?:'s|’s|s| is)? (?:it |things |this |that |everything )?go(?:es|ing)\b"
    r"|\bcan we (?:un-?block|fix\b|get\b[^.?!\n]{0,40}\b(?:working|done|unblocked|fixed|moving|going))"
    r"|\b(?:what(?:'s|’s|s| is| are)?|where)\b[^.?!\n]{0,40}\bblocked on\b"
    # agent-e868df5c84: "are you done" (the ask whose direct "No." a nudge removed)
    r"|\bare (?:you|we) (?:done|finished)\b|\bis (?:it|this|that) (?:done|finished)\b|\bdone yet\b")
# The question half of the progress ask (status_plain reads only this: "still not working" or "finish it" is a fix
# report or an order, not a request for a status).
_STATUS_QUESTION = (
    r"\bwhya?\b[^.?!\n]{0,40}\b(?:wait\w*|block\w*|stuck|slow|tak\w+ so long|still|stall\w*|not (?:done|finished|"
    r"started|running|moving))\b"
    r"|\bwhat(?:'s|’s| is| are)? (?:you |we |it )?(?:doing|going on|happening|the (?:status|hold-?up|delay))\b"
    r"|\bwhats going on\b|\b(?:status|progress)\b\s*\??\s*$|\bany (?:update|news|progress)\b"
    r"|\bis (?:it|this|that) (?:done|finished|live|deployed|merged) yet\b|\bhow long\b|\bwhere are we\b"
    r"|\bwhy (?:hasn'?t|isn'?t|aren'?t|haven'?t)\b")
PLAIN_STATUS_ASK_RE = re.compile(_STATUS_QUESTION + PROGRESS_ASK_EXTRA, re.I | re.M)
PROGRESS_ASK_RE = re.compile(
    _STATUS_QUESTION +
    r"|\bstill (?:waiting|blocked|not)\b"
    r"|\b(?:finish|complete|ship|deploy|land) (?:it|this|that|them)\b|\bjust (?:finish|complete|ship|deploy|land)\b"
    r"|\bcan you just\b"
    r"|\bget (?:it|this) done\b|\bmake (?:all )?the (?:corrections|changes|fix(?:es)?)\b"
    # agent-48e909978c: the user's real wording a 9-day chat missed: "how goes?", "what's the deal, can we unblock
    # ...?", "what are the cards blocked on"
    + PROGRESS_ASK_EXTRA, re.I | re.M)
# A wall only the user (or an outside party) can clear, named in the reply: then a status turn is the right answer.
WALL_RE = re.compile(
    r"\bonly you\b|\bneeds? (?:you|your)\b|\byour (?:password|approval|login|2fa|decision|click|hands)\b"
    r"|\b(?:billing|payment|spending limit|outage)\b|\bphysical(?:ly)?\b|\bsign(?:ed)? in\b|\blog(?:ged)? in\b"
    # a decision only the user can grant, named as such (agent-73e6b1a688: "authorization to narrow the completion
    # criteria ... I cannot waive that requirement")
    r"|\b(?:authori[sz]ation|permission|sign-?off) to \w+"
    r"|\bI (?:can(?:no|['’])t|am not able to) (?:waive|authori[sz]e|approve)\b", re.I)
# Card bookkeeping (reopen/unblock/comment/show/list, cross-gateway reads) and reads: not a step on the work itself.
CARD_TOOLS = ("kanban_show", "kanban_list", "kanban_comment", "kanban_unblock", "kanban_heartbeat", "kanban_link",
              "kanban_unlink", "kanban_block", "kanban_schedule", "kanban_attachments", "cross_task", "cross_session",
              "session_mentions")
READ_TOOLS = ("read_file", "search_files", "skill_view", "skills_list", "todo_list", "chat_history_lookup",
              "process_manage", "process", "tool_search", "tool_describe", "memory", "context_notes")
_CARD_CMD = re.compile(r"^(?:hermes(?:-hub)?\s+(?:-p\s+\S+\s+)?(?:kanban|tasks|request|links|requests|outcomes|"
                       r"where|sessions\s+list|status|sync)\b)")
_READ_CMD = re.compile(r"^(?:cd|ls|cat|head|tail|grep|rg|wc|jq|echo|date|sleep|true|sqlite3\s+-readonly|"
                       r"gh\s+(?:run|pr|api)\s+(?:view|list|checks|watch|repos/\S+/(?:actions|commits|pulls))|"
                       r"git\s+(?:status|log|show|diff|fetch|branch|rev-parse))\b")
_LINE = re.compile(r"^\s*->\s*([A-Za-z_][\w.]*)\((.*)\)\s*$", re.S)


def _tool_kind(line: str) -> Optional[str]:
    """'card' | 'read' | 'work' for one tool-log line (None = not a call line, e.g. an `<- ERROR` line)."""
    m = _LINE.match(line or "")
    if not m:
        return None
    tool, arg = m.group(1), m.group(2)
    if tool == "tool_call":
        names = re.findall(r"[\"']name[\"']\s*:\s*[\"']([\w.]+)[\"']", arg)
        kinds = {("card" if n in CARD_TOOLS else "read" if n in READ_TOOLS else "work") for n in names} or {"work"}
        return "work" if "work" in kinds else "card" if "card" in kinds else "read"
    if tool in CARD_TOOLS:
        return "card"
    if tool in READ_TOOLS:
        return "read"
    if tool == "terminal":
        kinds = set()
        for seg in re.split(r"&&|\|\||;|\|", arg):
            seg = re.sub(r"^\s*(?:[A-Z_]+=\S+\s+)*", "", seg).strip()
            if not seg:
                continue
            kinds.add("card" if _CARD_CMD.match(seg) else "read" if _READ_CMD.match(seg) else "work")
        return "work" if "work" in kinds or not kinds else "card" if "card" in kinds else "read"
    return "work"


def status_only(user_ask: str, reply: str, tool_log: Optional[list] = None) -> Optional[str]:
    """Evidence string when a progress/finish ask got a turn of card bookkeeping only, else None.

    A Hermes-written prompt (notice_turn: [Kanban close-out], [chat-closeout], owner-wake ...) is never the user asking
    about progress: its fixed wording ("what you are still waiting on") and the card asks it quotes ("unblock =
    complete it") matched the progress pattern on every close-out (agent-21185525f4). The Kanban worker prompt
    ("# Kanban task <id>: ...") is automation too: the dispatcher re-sends the card body unchanged on every wake, and
    a body quoting a standing mandate ("unblock the task and finish it") held a shepherd that had parked correctly
    (agent-e80aa5e107). report_only and the judge still review those turns."""
    user_ask = (user_ask or "")[:ASK_HEAD]
    if automation_prompt(user_ask) or not PROGRESS_ASK_RE.search(user_ask) or not (reply or "").strip():
        return None
    kinds = [k for k in (_tool_kind(l) for l in tool_log or []) if k]
    if "work" in kinds or "card" not in kinds:
        return None
    if WALL_RE.search(reply[-1500:]):
        return None
    ops = [l.strip()[3:].split("(", 1)[0] for l in tool_log or [] if _tool_kind(l) == "card"]
    return f"asked \"{(user_ask or '').strip()[:80]}\"; this turn only ran {', '.join(dict.fromkeys(ops))}"


# status_plain (agent-48e909978c): in a long chat most turns answer automated notices, so replies drift into
# internal coordination ("queued to my subagent", "hub owns the repair") and a person's progress ask gets no outcome:
# four asks over 9 days, 276 queued / 0 repaired never stated. A person's progress ask (never a notice, a Hermes-written
# row, a cron run, a subagent or a Kanban worker) gets one advisory hint per turn. It never holds a reply.
# Off switch: env COACH_STATUS_PLAIN=off, or config plugins.entries.coach.settings.status_plain: false.
SYNTHETIC_USER_RE = re.compile(
    r"^\s*(?:[\u2714\u2713\u2716\u2717\u23f8\u26a0\u2757]|\[(?!image|screenshot)[^\]\n]{1,80}[\]:])", re.I)
PROBE_TAG_RE = re.compile(r"^\s*\[selfheal-probe\]\s*", re.I)
STATUS_PLAIN_HINT = (
    "[coach] status_plain: the user asked about progress (\"{ask}\"). Open with the state of their original ask in "
    "plain words: what works, what's still broken (a number), the one thing stopping it, who acts next. No card ids "
    "or subagent/owner/coordinator/detector words in the first paragraph.")


def status_plain_enabled() -> bool:
    """Env COACH_STATUS_PLAIN, then config plugins.entries.coach.settings.status_plain; default on."""
    raw = os.environ.get("COACH_STATUS_PLAIN")
    if raw is None:
        try:
            raw = _cache.load_sibling(_HERE, "second_opinion")._config_settings().get("status_plain")
        except Exception:
            raw = None
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "off", "false", "no")


def status_plain_hint(user_ask: str, platform: str = "", parent_session_id: str = "") -> Optional[str]:
    """The outcome-first hint when a person typed a progress ask in a live chat, else None."""
    if not isinstance(user_ask, str) or not user_ask.strip():
        return None
    if platform in ("cron", "kanban", "subagent") or parent_session_id:
        return None
    # a `[selfheal-probe]` live test stands in for a person (self_heal._NOT_HERMES_TAGS); it is not a Hermes row
    ask = PROBE_TAG_RE.sub("", user_ask[:ASK_HEAD], count=1)
    if automation_prompt(ask) or SYNTHETIC_USER_RE.match(ask) or not PLAIN_STATUS_ASK_RE.search(ask):
        return None
    if not status_plain_enabled():
        return None
    return STATUS_PLAIN_HINT.format(ask=" ".join(ask.split())[:80])


STATUS_ONLY_NUDGE = (
    "[coach] status_only: the user asked about progress or to finish ({evidence}), and this turn only reopened, "
    "unblocked, commented on or looked at cards. A card being ready or a worker being alive is not progress. Before "
    "ending: do the next real step on the work yourself now (edit, test, commit, merge, deploy, prove it on the real "
    "path), or name the one specific wall that stops you (the exact error, or the decision only the user can make). "
    "Do not reopen, re-review or add another watcher card.")


# ---- denied_ask: the reply says nothing needs the user while a card linked to the chat is blocked on them --------
# (self-heal agent-8cecfe01cb: "no new approval request" while t_f68e984c waited on a GitHub passkey for 18 h)
_ASK_NOUN = r"(?:approvals?|asks?|actions?|inputs?|decisions?|sign-?offs?|confirmations?)"
DENY_ASK_RE = re.compile(
    r"\bno (?:new |pending |open |further |other )?" + _ASK_NOUN +
    r"(?:\s+requests?\b|\s+(?:is |are )?(?:needed|required|pending|waiting)\b"
    r"|\s+(?:needed |required )?(?:from|for|on) you\b|\s+on your (?:side|end|part)\b)"
    r"|\bnothing (?:(?:is |'s )?(?:needed|required|waiting|blocked|pending) (?:from|on|for) you\b"
    r"|(?:needs|requires|is waiting on) you\b|for you to do\b)"
    r"|\b(?:doesn'?t|does not|don'?t|do not) need (?:anything (?:from|of) )?you\b"
    r"|\bnot (?:blocked|waiting) on you\b"
    r"|\byou don'?t need to (?:do|approve|click|sign|confirm) anything\b",
    re.IGNORECASE)
_CODE_QUOTE = re.compile(r"```.*?```|`[^`]*`|^>.*$", re.DOTALL | re.MULTILINE)


def denies_ask(reply: str, include_quoted: bool = False) -> Optional[str]:
    """The phrase in the reply saying no ask/approval is pending for the user, else None. Code and `>` quotes are
    ignored, and so is a phrase inside quote marks (a correction quoting the old denial, agent-747c4fc95b) unless
    `include_quoted` (the nudge outcome: a revision quoting the old denial must still tell the ask)."""
    text = _CODE_QUOTE.sub(" ", reply or "")
    for m in DENY_ASK_RE.finditer(text):
        if include_quoted or not _quoted(text, m.start(), m.end()):
            return m.group(0)
    return None


def _ask_valid(value: Any, kind: Any) -> Optional[dict]:
    """Core's human_request contract (hermes_cli.kanban_human_request), with a local copy for older cores."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    try:
        from hermes_cli.kanban_human_request import validate_human_request  # type: ignore
        try:
            return validate_human_request(value, kind=kind)
        except ValueError:
            return None
    except ImportError:
        pass
    fields = ("action", "why_human", "interaction", "scope", "evidence")
    if not isinstance(value, dict) or any(not isinstance(value.get(f), str) or not value[f].strip() for f in fields):
        return None
    if kind in ("dependency", "transient", "time"):
        return None
    return {f: value[f].strip() for f in fields}


def linked_human_asks(cards: list) -> list[dict]:
    """``[{id, title, action}]`` for parked cards with a validated ask for the user."""
    out = []
    for c in cards or []:
        if not isinstance(c, dict) or c.get("status") not in ("blocked", "triage"):
            continue
        ask = _ask_valid(c.get("human_request"), c.get("block_kind"))
        if ask:
            row: dict = {"id": str(c.get("key") or c.get("id")), "title": str(c.get("title") or ""),
                         "action": " ".join(ask["action"].split())}
            labels = _choice_labels(c.get("human_request"))
            if labels:
                row["choices"] = labels
            out.append(row)
    return out


def _choice_labels(value: Any) -> list[str]:
    """The `choices[].label` strings of a raw human_request (dict or JSON), else []."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    choices = value.get("choices") if isinstance(value, dict) else None
    return [" ".join(c["label"].split()) for c in choices or []
            if isinstance(c, dict) and isinstance(c.get("label"), str) and c["label"].strip()]


def session_human_asks(session_id: str) -> list[dict]:
    """Asks waiting on the user from cards linked to this chat (created, shepherded, subscribed, or remote origin
    cards), via core's session_tasks. Local rows don't carry human_request, so it is read from their board."""
    from hermes_cli import kanban_session_activity as ksa  # type: ignore
    rows = ksa.session_tasks(_lineage(session_id), tuple(getattr(ksa, "BLOCKED_STATUSES", ("blocked", "triage"))))
    need = [r for r in rows if "human_request" not in r and r.get("id")]
    if need:
        by_board: dict[str, list] = {}
        for r in need:
            by_board.setdefault(str(r.get("board") or "default"), []).append(r)
        for path in ksa._board_db_paths():
            slug = path.parent.name if path.parent.parent.name == "boards" else "default"
            if slug not in by_board:
                continue
            conn = _ro_connect(path, timeout=2)
            try:
                for r in by_board[slug]:
                    got = conn.execute("select status, block_kind, human_request from tasks where id=?",
                                       (r["id"],)).fetchone()
                    if got:
                        r.update(status=got[0], block_kind=got[1], human_request=got[2])
            finally:
                conn.close()
    return linked_human_asks(rows)


_WORD = re.compile(r"[a-z0-9][a-z0-9./_-]{3,}")


# agent-5c9b041010: a call this turn that took a card off the user (unblock/complete/archive/schedule/move), as a
# kanban tool, a deferred tool_call, the `hermes kanban` CLI (also over ssh) or a cross_task action.
_CLEAR_VERB = r"(?:unblock|complete|archive|schedule|move)"
_CLEAR_RE = re.compile(
    r"^\s*->\s*kanban_" + _CLEAR_VERB + r"\("
    r"|[\"']name[\"']\s*:\s*[\"']kanban_" + _CLEAR_VERB + r"[\"']"
    r"|^\s*->\s*terminal\(.*\bhermes kanban\b(?:\s+--board\s+\S+)?\s+" + _CLEAR_VERB + r"\b"
    r"|^\s*->\s*cross_task\(.*[\"']action[\"']\s*:\s*[\"']" + _CLEAR_VERB + r"[\"']", re.I)
_CARD_ID = re.compile(r"\bt_[0-9a-f]{6,}\b")


def cleared_cards(tool_log: Optional[list] = None) -> set:
    """Card ids this turn's tool log took off the user with a call that did not error."""
    lines = list(tool_log or [])
    out: set = set()
    for i, line in enumerate(lines):
        if _CLEAR_RE.search(line or ""):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not (nxt or "").lstrip().startswith("<- ERROR"):
                out.update(_CARD_ID.findall(line))
    return out


def _ask_cleared(ask: dict, cleared: set) -> bool:
    """The ask's card is one of `cleared` (short and long forms of one id match; a `gw/` prefix is ignored)."""
    cid = str(ask.get("id") or "").rsplit("/", 1)[-1].lower()
    return bool(cid) and any(c.startswith(cid) or cid.startswith(c) for c in cleared)


def ask_told(reply: str, ask: dict) -> bool:
    """True when the reply already tells this ask: names its card id or title, or carries most of its action's words.
    A reply that presents the ask and then says "the other jobs don't need you" is not a denial (live false positive
    on the first day: draft d_4464e252269c4fc4)."""
    text = (reply or "").lower()
    cid = str(ask.get("id") or "").lower()
    if cid and (cid in text or cid.rsplit("/", 1)[-1] in text):
        return True
    title = " ".join(str(ask.get("title") or "").lower().split())
    if len(title) >= 8 and title in " ".join(text.split()):
        return True
    said = set(_WORD.findall(text))
    words = set(_WORD.findall(str(ask.get("action") or "").lower()))
    if len(words) >= 3 and len(words & said) / len(words) >= 0.6:
        return True
    # agent-747c4fc95b: the nudge asks for the work in plain words, not the card id, so a retelling counts: one of the
    # card's answer choices (multi-word labels only; "Yes"/"No" prove nothing), or most of the title's words.
    flat = " ".join(re.sub(r"[\"'“”‘’`*]", " ", text).split())
    for label in ask.get("choices") or []:
        lab = " ".join(str(label).lower().split())
        if len(lab.split()) >= 2 and re.search(r"(?<!\w)" + re.escape(lab) + r"(?!\w)", flat):
            return True
    tw = {w for w in _WORD.findall(title) if re.search(r"[a-z]", w)}
    return len(tw) >= 4 and len(tw & said) / len(tw) >= 0.6


DENIED_ASK_NUDGE = (
    "[coach] Your reply says nothing needs the user (\"{evidence}\"), but {n} card(s) linked to this chat are blocked "
    "waiting on them: {asks}. Correct the reply before ending: in plain words say what is stuck (the work, not its "
    "card id), the one thing the user must do and where (page, app or link), and what happens after. If an agent can "
    "do that step itself, do it instead of asking.")


# ---- credit_ask: the reply asks the user to buy API credit while the account pool was never tried -------------
# (self-heal agent-fe419d4901: the Ask Eve eval chat asked David for ~$250 each of Anthropic and OpenAI API credit and
# parked 8 h; the Hermes subscription pool, Claude Max + ChatGPT accounts, could have run it.) Deterministic, before the
# judge. Same pattern as plugins/pool-llm/hooks.py CREDIT_ASK_RE (that plugin holds the matching kanban_block once).
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
POOL_HELPER_RE = re.compile(r"hermes-pool-llm|pool_llm")
# The ask must be about LLM API credit: the pool can't pay GitHub Actions, AWS, Vast or Cloudflare bills (live false
# positive: "raise the spending limit" for the GitHub Actions billing watchdog). Both signals are billing CONTEXT, never
# a bare name: almost every coding reply says "GitHub" or "Claude", and that must not decide anything.
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
# Same code in plugins/pool-llm/hooks.py; keep the two in step.
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
CREDIT_ASK_NUDGE = (
    "[coach] credit_ask: your reply asks the user to add or buy API credit (\"{evidence}\"), but this turn never tried "
    "the Hermes subscription account pool (Claude Max + ChatGPT accounts). Run the work there first: "
    "`~/.hermes/plugins/pool-llm/bin/hermes-pool-llm ask --provider anthropic|openai --model <m> \"...\"`, or "
    "`hermes-pool-llm serve --port 8765` and point the harness at it (ANTHROPIC_BASE_URL=http://127.0.0.1:8765, "
    "OPENAI_BASE_URL=http://127.0.0.1:8765/v1). Ask for money only if the pool fails too, quoting its error.")


def credit_ask(reply: str, tool_log: Optional[list] = None) -> Optional[str]:
    """The phrase asking the user to buy API credit, when the turn never ran the pool helper; else None."""
    # pool-llm's own hint (echoed tool output) and quoted/code text are not an ask
    text = "\n".join(l for l in _CODE_QUOTE.sub(" ", reply or "").splitlines() if "pool-llm:" not in l)
    text = CREDIT_NEGATED_RE.sub(lambda n: " " * len(n.group(0)), text)
    m = next((m for m in CREDIT_ASK_RE.finditer(text) if _is_llm_credit_ask(text, m)), None)
    if not m or any(POOL_HELPER_RE.search(l or "") for l in (tool_log or [])):
        return None
    return m.group(0).strip()


# ---- laptop_job: the reply proposes a recurring unattended job on a Mac that sleeps ----------------------------------
# (self-heal agent-60ca102b39: a chat offered "a small job on the MacBook every 10 hours" to rotate a Slack token;
# David: "that should be done on the hub, mbp doesnt stay on".) Deterministic, before the judge. One sentence must
# carry all of: a creation verb or offer (build/set up/add/plan:/want me to...), a recurring job (cron, launchd, every N hours,
# hourly, watcher...), a Mac host (MacBook, MBP, laptop, mini), no hub target and no Mac-only reason.
_LJ_RECUR_RE = re.compile(
    r"\bcron(?:tab|\s+job)?s?\b|\blaunchd\b|\bLaunchAgents?\b|\bsystemd\s+timer\b"
    r"|\bevery\s+(?:\d+|few|couple(?:\s+of)?|other)?\s*(?:min(?:ute)?s?|h(?:ou)?rs?|hours?|h|days?|nights?|mornings?)\b"
    r"|\bhourly\b|\bnightly\b|\bdaily\b|\bperiodic(?:ally)?\b|\bon\s+a\s+(?:timer|schedule)\b"
    r"|\b(?:scheduled|recurring|periodic)\s+(?:job|task|run|refresh)\b|\b(?:refresher|watcher|poller)s?\b", re.I)
_LJ_MACHOST = r"(?:Mac\s*Book(?:\s+Pro)?|MBP|laptop|mac\s*mini|daveey-mini|Mac|mini)"
# The Mac must be where the job runs: "on/from the MacBook", "on the mini", or "MacBook cron/job/watcher". A bare
# "the MacBook poller is stuck" or "gpt-4o-mini" is not a location (backtest 2026-10-30: 8 of 9 hits were that).
_LJ_MAC_RE = re.compile(
    r"\b(?:on|from|onto|in)\s+(?:the\s+|your\s+|my\s+|David[\u2019']s\s+)?" + _LJ_MACHOST + r"\b"
    r"|\b(?:Mac\s*Book|MBP|laptop|mini)(?:[\u2019']s)?\s+(?:cron(?:tab)?|launchd|LaunchAgent|job|timer|watcher|poller"
    r"|refresher)s?\b", re.I)
_LJ_NEGATED_RE = re.compile(r"\b(?:adds?|added|with|needs?)\s+no\b|\bno\s+(?:new\s+)?(?:LaunchAgent|cron|launchd|job|"
                            r"MacBook|Mac)\b|\b(?:not|never)\s+(?:on|from)\s+(?:the\s+|your\s+)?Mac", re.I)
_LJ_HUB_RE = re.compile(r"\bhermes-hub\b|\bthe\s+hub\b|\bhub\s+(?:cron|job|timer|host)\b|\bon\s+hub\b|\bworker-\d\b",
                        re.I)
# A job that needs a Mac's own state legitimately runs there.
_LJ_MAC_ONLY_RE = re.compile(
    r"\bKeychain\b|\bChrome\b|\bSafari\b|\bbrowser\b|\bApple\s+\w+|\biCloud\b|\bPhotos\b|\biMessage\b|\bMessages\s+app\b"
    r"|\bXcode\b|\bosascript\b|\bAppleScript\b|\bScreen\s+Shar|\bper-host\b|\bthis\s+Mac[\u2019']s\b|\blocal\s+state\b"
    r"|\bHermes\s+Desktop\b|\bDesktop\s+app\b|\bmacOS[- ]only\b|\bTCC\b|\bhermes-sync\b|\bdisk\b", re.I)
# A creation verb, or an explicit plan/offer framing; "I'll check the MacBook poller" is not a proposal. "put"/"move"
# propose a job only with a Mac destination ("put the refresher on the mini", "move the watcher onto the MacBook"):
# "waiting on something that will move them: ... on the mini" and "Put differently" are not proposals (2026-11-01).
_LJ_PROPOSE_RE = re.compile(
    r"\b(?:build|set\s+up|setting\s+up|add|adding|create|creating|install|installing|schedul(?:e|ing)|deploy)\b"
    r"|\b(?:put|move)\s+(?:[^\s,.;:()]+\s+){0,4}?(?:on|onto|to|into)\s+(?:the\s+|your\s+|my\s+|David[\u2019']s\s+)?"
    + _LJ_MACHOST + r"\b"
    r"|\bplan\s*:|\bproposal\s*:|\b(?:want|like)\s+me\s+to\b|\b(?:shall|should)\s+I\b", re.I)
_LJ_META_RE = re.compile(r"\blaptop_job\b", re.I)
LAPTOP_JOB_NUDGE = (
    "[coach] laptop_job: your reply proposes a recurring unattended job on a Mac (\"{evidence}\"), but the MacBook and "
    "mini sleep, so the job silently stops. Put it on hermes-hub (hub cron / systemd timer). If the hub lacks a "
    "credential or write access the Mac has today, the plan is the hub job plus a one-time grant card for that access, "
    "not a Mac job. Keep a job on a Mac only when it needs that Mac's own state (Keychain, Chrome, Apple apps, a "
    "per-host watcher), and say so in the same sentence.")


def laptop_job(reply: str) -> Optional[str]:
    """The sentence proposing a recurring job on a sleeping Mac, else None (code, quotes and `>` lines ignored)."""
    text = _CODE_QUOTE.sub(" ", reply or "")
    text = _QUOTED_RE.sub(" ", text)
    for s in _SENTENCE_END.split(text):
        if not (_LJ_RECUR_RE.search(s) and _LJ_MAC_RE.search(s) and _LJ_PROPOSE_RE.search(s)):
            continue
        if (_LJ_HUB_RE.search(s) or _LJ_MAC_ONLY_RE.search(s) or _LJ_META_RE.search(s)
                or _LJ_NEGATED_RE.search(s)):
            continue
        return s.strip(" -*\t")
    return None


# ---- skipped_live: the reply admits live state went unchecked while no other live route was tried ---------------------
# (self-heal agent-8a0b79e7c4: a hub reply asked the user to approve a plan that hinged on live state and said "I didn't
# check the live state: this machine has no AWS access to the <X> account". True for AWS, but the hub browser was
# signed in to the product's console, which listed every owner; one plan step turned out moot.) Deterministic, before
# the judge: an admission outside code/quotes, and no browser, `gh run` log, web fetch or curl call this turn.
_SL_ADMIT_RE = re.compile(
    r"\b(?:did(?:n[\u2019']t|\s+not)|could(?:n[\u2019']t|\s+not)|have(?:n[\u2019']t|\s+not)|has(?:n[\u2019']t|\s+not)"
    r"|was(?:n[\u2019']t|\s+not)\s+able\s+to|can(?:not|[\u2019']t)|unable\s+to)\s+(?:yet\s+|actually\s+)?"
    r"(?:check(?:ed)?|verif(?:y|ied)|confirm(?:ed)?|read|query|queried|look(?:ed)?\s+at|see(?:n)?)\b[^.\n]{0,60}?"
    r"\b(?:live|current|production|prod|actual|real)\s+(?:state|status|data|numbers|counts?|config(?:uration)?)\b"
    # "no access to" only with the agent's own side as subject: "Matt has no access to the repo", "the service has no
    # access to the account" (a design) and "I changed nothing: ... no access to the boards" are not admissions
    # (backtest 2026-11-09, 14 days of hub replies).
    r"|\b(?:I|we|this\s+(?:machine|host|hub|worker|box|gateway|session|sandbox)|the\s+hub(?:\s+worker)?|hub|here)"
    r"\s+(?:still\s+|also\s+)?(?:has|have|had|got)\s+no\s+(?:AWS\s+|live\s+|read\s+|SSH\s+|console\s+)?access\s+to\b",
    re.I)
# A sentence that hands the work to a card, or says the change isn't live yet (nothing to read back), is not a skip.
_SL_EXCUSED_RE = re.compile(r"\bt_[0-9a-f]{8,}\b|\bhanded\b|\bunmerged\b|\bnot\s+(?:yet\s+)?(?:merged|installed|deployed)\b"
                            r"|\bisn[\u2019']t\s+(?:yet\s+)?(?:merged|installed|deployed)\b", re.I)
# A live route this turn: a browser (tool, deferred tool_call or chrome-devtools), a web fetch, `gh run` logs, curl.
_SL_ROUTE_RE = re.compile(
    r"^\s*->\s*(?:browser_\w+|mcp__chrome_devtools__\w+|web_extract|tailnet_exec)\("
    r"|[\"']name[\"']\s*:\s*[\"'](?:browser_\w+|mcp__chrome_devtools__\w+|web_extract|tailnet_exec)[\"']"
    r"|^\s*->\s*(?:terminal|execute_code)\(.*(?:\bgh\s+run\s+(?:view|list|watch)\b|\bcurl\b|\bwget\b|\bweb_extract\b"
    r"|\bbrowser_exec\b)", re.I | re.S)
# The reply already names the routes it tried (the nudge's second exit).
_SL_TRIED_RE = re.compile(r"\btried\b[^\n]{0,200}?\b(?:browser|console|dashboard|gh\s+run|Actions|web|curl|API|"
                          r"Chrome|logs?|tailnet)\b", re.I)
_SL_META_RE = re.compile(r"\bskipped_live\b", re.I)
SKIPPED_LIVE_NUDGE = (
    "[coach] skipped_live: your reply says live state went unchecked (\"{evidence}\"), but this turn tried no other "
    "route to it: no browser, no `gh run view --log`, no web fetch, no tailnet_exec. A missing AWS profile, SSH key or "
    "account reach is not "
    "\"no access\". Try the task skill's no-profile routes first (the hub browser is signed in to several consoles; "
    "deploy/infra job logs hold live numbers) and answer from what they show, especially before asking the user to "
    "approve a plan that depends on that state. If none works, name each route you tried and what it returned.")


def skipped_live(reply: str, tool_log: Optional[list] = None) -> Optional[str]:
    """The admission phrase when the reply says it skipped live state and the turn tried no live route, else None
    (code spans, quotes, `>` lines and replies naming the routes they tried are ignored)."""
    text = _CODE_QUOTE.sub(" ", reply or "")
    text = _QUOTED_RE.sub(" ", text)
    if _SL_META_RE.search(text) or _SL_TRIED_RE.search(text):
        return None
    s = next((s for s in _SENTENCE_END.split(text) if _SL_ADMIT_RE.search(s) and not _SL_EXCUSED_RE.search(s)), None)
    if not s or any(_SL_ROUTE_RE.search(l or "") for l in (tool_log or [])):
        return None
    return s.strip(" -*\t")


# ---- merge_forecast: the reply says when a PR will merge, but this turn never checked that PR ----------------------------
# (self-heal agent-84aa7db32f: a chat said a PR "merges once CI is green", taking that from the owner card's plan. Other
# steps were still open: failures already on main, a fresh review after a rebase, the hub merge queue. CI went green and
# the PR was still open 7 hours later.) Deterministic, before the judge: a sentence (outside code and quotes) forecasting
# a future merge/landing/deploy, in a reply that names a PR, while the turn's tool log has no PR-state check.
# Backtest (worker-6, 14 days, tool log ignored): "the installer only deploys when ...", "Z lands after our write",
# "will deploy-check it" and "I'll merge into the branch" describe mechanisms or git steps, not a PR's merge time, so
# present-tense "lands" needs a time/condition word, deploy/ship are not forecasts here, and "merge into" is excluded.
_MF_WHEN = r"(?:once|as\s+soon\s+as|by|at|around|within|tonight|today|tomorrow|in\s+(?:about\s+)?(?:an?|\d+|a\s+few)\b)"
_MF_FORECAST_RE = re.compile(
    r"\bmerges\s+(?:(?:when|after)\b|" + _MF_WHEN + r")|\blands\s+" + _MF_WHEN
    + r"|\b(?:will|(?:I|it|we|that|this|they)[\u2019']ll|should|is\s+(?:expected|going|due)\s+to|is\s+set\s+to)\s+"
    r"(?:auto-?)?(?:merge|land|be\s+(?:auto-?)?(?:merged|landed))\b(?![-\w])(?!\s+(?:[\w/.-]+\s+){0,2}into\b)",
    re.I)
_MF_PR_RE = re.compile(r"\bPR\s*#?\d+|\bPRs?\b|\bpull\s+requests?\b|/pull/\d+|\B#\d{2,}\b", re.I)
# A PR-state read this turn: gh pr view/checks/status, gh api pulls|check-runs|status, the pr-blocker-check skill or
# helper, hermes-core-wt check, or a browser on a GitHub PR page.
_MF_CHECK_RE = re.compile(
    r"\bgh\s+pr\s+(?:view|checks|status)\b|\bgh\s+api\b[^\n]*(?:/pulls\b|check-runs|check-suites|/status\b|pullRequest)"
    r"|\bpr-blocker-check\b|\bhermes-core-wt\s+(?:check|status)\b|github\.com/[^\s\"']+/pull/\d+", re.I)
_MF_META_RE = re.compile(r"\bmerge_forecast\b", re.I)
MERGE_FORECAST_NUDGE = (
    "[coach] merge_forecast: your reply tells the user when a PR will merge or land (\"{evidence}\"), but this turn "
    "never checked that PR. Forecasts taken from a card's plan go stale: CI is rarely the only open step. Check it now "
    "(`gh pr view <n> --json state,mergeStateStatus,reviewDecision` and `gh pr checks <n>`, or the pr-blocker-check "
    "skill) and list every open step: failing or pending checks (and whether main fails the same ones), whether the "
    "review still holds after a rebase, merge queue or slot position, and the owner card's real state. Give no clock "
    "time and no \"merges once X\" unless X is the only open step.")


def merge_forecast(reply: str, tool_log: Optional[list] = None) -> Optional[str]:
    """The sentence forecasting a PR merge/landing when this turn checked no PR state, else None (code spans, quotes,
    `>` lines, questions and replies naming merge_forecast are ignored; a reply naming no PR passes)."""
    text = _CODE_QUOTE.sub(" ", reply or "")
    text = _QUOTED_RE.sub(" ", text)
    if _MF_META_RE.search(text) or not _MF_PR_RE.search(text):
        return None
    s = next((s for s in _SENTENCE_END.split(text) if _MF_FORECAST_RE.search(s) and not s.rstrip(" *_").endswith("?")),
             None)
    if not s or any(_MF_CHECK_RE.search(l or "") for l in (tool_log or [])):
        return None
    return s.strip(" -*\t")


# ---- future_clock: a past-tense Pacific time stamp that has not happened yet -------------------------------------------
# (self-heal agent-748d257889: at 20:59 PST a red-sweep report said "(Checked 21:05–21:40 PT.)" and card comments said
# "Red sweep ~21:15 PST". The model subtracted 7 h from UTC tool stamps after DST had ended (UTC-8) and never read the
# clock; it recurred twice that evening.) Deterministic, before the judge, and on durable card text in pre_tool_call:
# an HH:MM (or HH:MM–HH:MM) tagged PT/PST/PDT/Pacific, or untagged right after checked/ran/finished/as of/since (read
# in the host's zone), more than FC_GRACE_S and at most FC_AHEAD_MAX_S later than now on today's clock. A time further
# ahead is taken as an earlier day's ("since 4:28pm PT" said at 05:30); the incident's slips were 6-41 min, and a wrong
# DST offset is 1 h.
# Forecast words before it in the sentence (by/until/ETA/will/from/between/before...), another day or date in the
# sentence, code spans, quotes and `>` lines are ignored. A PDT label while PST is in force (and the reverse) is a hit.
PACIFIC = "America/Los_Angeles"
FC_GRACE_S = 180
FC_AHEAD_MAX_S = 3 * 3600
_FC_PT = r"(?:PT|PST|PDT|Pacific(?:\s+time)?)"
_FC_TIME_RE = re.compile(
    r"(?<![\d:.])(?P<h1>[01]?\d|2[0-3]):(?P<m1>[0-5]\d)(?::[0-5]\d)?(?:\s*(?P<ap1>[ap]\.?m\.?))?"
    r"(?:\s*(?:[-\u2013\u2014]|to)\s*(?P<h2>[01]?\d|2[0-3]):(?P<m2>[0-5]\d)(?::[0-5]\d)?(?:\s*(?P<ap2>[ap]\.?m\.?))?)?"
    r"(?![\d:])(?:\s*\(?(?P<tz>" + _FC_PT + r"|UTC|GMT|Z|[ECM][SD]?T|CES?T|BST|IST|JST|AEST)\b)?", re.I)
_FC_PAST_RE = re.compile(r"\b(?:checked|ran|run\s+at|finished|completed|done|as\s+of|since|swept|verified|"
                         r"confirmed|read|looked)\b[\s~:@]*(?:at\s+|around\s+|~\s*)?$", re.I)
_FC_FORECAST_RE = re.compile(
    r"\b(?:by|until|till|til|before|ETA|expected|expect|due|will|won't|going\s+to|scheduled|schedule|planned|next|"
    r"from|between|wakes?|resumes?|retr(?:y|ies)|re-?check(?:s|ing)?|tonight|later|opens?|starts?|lifts?|resets?|"
    r"after\s+(?:which|that)|SCHEDULED_UNTIL)\b|[\u2019']ll\b", re.I)
_FC_OTHER_DAY_RE = re.compile(
    r"\b(?:tomorrow|yesterday|last\s+night|(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b)|"
    r"\b\d{4}-\d{2}-\d{2}\b|(?<![\d:])\d{1,2}[-/]\d{1,2}(?![\d:])", re.I)
_FC_META_RE = re.compile(r"\bfuture_clock\b", re.I)
FUTURE_CLOCK_NUDGE = (
    "[coach] future_clock: your text stamps a time that is wrong for now (\"{evidence}\"): it is {now_local} "
    "({offset}) in {zone} now, so a past-tense \"checked/ran\" time later than that has not happened yet{label}. Do not "
    "convert UTC by hand (the offset changes with DST): print local times with `TZ={zone} date` or convert a tool's "
    "UTC stamp with `TZ={zone} date -d @<epoch>` (macOS: `date -r <epoch>`), then restate the time, or drop it.")


def _wall_now() -> float:
    """The real clock. COACH_CLOCK_EPOCH pins it (the coach test conftest sets it, so replies quoting real incident
    times in other rules' tests can't turn into future_clock hits at some hour of the day)."""
    pinned = os.environ.get("COACH_CLOCK_EPOCH")
    if pinned:
        try:
            return float(pinned)
        except ValueError:
            pass
    return time.time()


def _fc_zone(name: str = PACIFIC):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def _fc_offset(dt) -> str:
    off = dt.utcoffset()
    mins = int(off.total_seconds() // 60) if off is not None else 0
    sign = "\u2212" if mins < 0 else "+"
    h, m = divmod(abs(mins), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def _fc_minutes(h: str, m: str, ap: Optional[str]) -> int:
    hh = int(h)
    a = (ap or "").lower().replace(".", "")
    if a == "pm" and hh < 12:
        hh += 12
    elif a == "am" and hh == 12:
        hh = 0
    return hh * 60 + int(m)


def future_clock(text: str, now: Optional[float] = None, local_tz=None) -> Optional[str]:
    """The time stamp (with a little context) when `text` states a past-tense Pacific time later than now (beyond
    FC_GRACE_S), or a PDT/PST label that DST contradicts; else None. `local_tz` (default: this host's zone) reads
    untagged times after checked/ran/as of/since. Fail-open: any error returns None."""
    try:
        import datetime as _dt
        t = _wall_now() if now is None else float(now)
        pac = _fc_zone()
        now_pac = _dt.datetime.fromtimestamp(t, pac)
        loc = local_tz or _dt.datetime.now().astimezone().tzinfo
        now_loc = _dt.datetime.fromtimestamp(t, loc)
        dst = bool(now_pac.dst())
        body = _CODE_QUOTE.sub(" ", text or "")
        body = _QUOTED_RE.sub(" ", body)
        body = re.sub(r"'[^'\n]{6,}'", " ", body)  # a quoted old stamp ('Checked 21:40 PT.')
        if _FC_META_RE.search(body):
            return None
        for sent in _SENTENCE_END.split(body):
            if not sent or ":" not in sent or _FC_OTHER_DAY_RE.search(sent):
                continue
            for m in _FC_TIME_RE.finditer(sent):
                tz = (m.group("tz") or "").upper()
                before = sent[:m.start()]
                if tz and not re.match(_FC_PT, tz, re.I):
                    continue  # another zone (UTC, ET, ...)
                if not tz and not _FC_PAST_RE.search(before[-40:]):
                    continue  # an untagged time not stated as when something happened
                ev = " ".join(sent[max(0, m.start() - 30):m.end() + 10].split())
                if tz in ("PDT", "PST") and (tz == "PDT") != dst:
                    return ev
                if _FC_FORECAST_RE.search(before):
                    continue
                cur = now_pac if tz else now_loc
                cur_min = cur.hour * 60 + cur.minute + cur.second / 60.0
                ends = [_fc_minutes(m.group("h1"), m.group("m1"), m.group("ap1"))]
                if m.group("h2"):
                    ends.append(_fc_minutes(m.group("h2"), m.group("m2"), m.group("ap2") or m.group("ap1")))
                for e in ends:
                    ahead = (e - cur_min) * 60
                    if FC_GRACE_S < ahead <= FC_AHEAD_MAX_S:
                        return ev
    except Exception:
        return None
    return None


def future_clock_nudge(evidence: str, now: Optional[float] = None) -> str:
    import datetime as _dt
    t = _wall_now() if now is None else float(now)
    cur = _dt.datetime.fromtimestamp(t, _fc_zone())
    tz = cur.strftime("%Z")
    wrong = "PDT" if tz == "PST" else "PST"
    label = (f", and {tz}, not {wrong}, is in force today" if re.search(r"\b" + wrong + r"\b", evidence or "")
             else "")
    return FUTURE_CLOCK_NUDGE.format(evidence=(evidence or "")[:160], now_local=cur.strftime("%H:%M ") + tz,
                                     offset=_fc_offset(cur), zone=PACIFIC, label=label)


# Card tools whose text outlives the turn: comments, summaries, block reasons, new card bodies.
_FC_DURABLE_FIELDS = {"kanban_comment": ("body",), "kanban_complete": ("summary", "result"),
                      "kanban_block": ("reason", "human_request"), "kanban_create": ("title", "body"),
                      "kanban_request_review": ("summary",), "kanban_continue": ("checkpoint",)}


def durable_text(tool_name: str, args: Any) -> str:
    """The human-readable text a Kanban card tool would store (direct, or through the deferred `tool_call`)."""
    a = args if isinstance(args, dict) else {}
    if tool_name == "tool_call":
        return " \n".join(p for p in (durable_text(str((c or {}).get("name") or ""), (c or {}).get("arguments"))
                                      for c in (a.get("calls") or []) if isinstance(c, dict)) if p)
    out = []
    for f in _FC_DURABLE_FIELDS.get(tool_name, ()):
        v = a.get(f)
        if isinstance(v, dict):
            out.extend(str(x) for x in v.values() if isinstance(x, str))
        elif v:
            out.append(str(v))
    return " \n".join(out)


# ---- consent_relay: a macOS permission click paired with a second "unblock / reply done" step ------------------------
# (self-heal agent-2469206a31: the password window's osascript hung on "Hermes Python wants access to control
# Terminal"; the reply asked David to click OK AND to unblock a card / reply "done" so an agent re-ran the installer.
# The click is his; the re-run is not: run_in_user_terminal now keeps that job awaiting_consent and opens the window by
# itself after OK.) Deterministic handoff hit, before the judge (whose rubric scores permission grants low).
_CR_GRANT_RE = re.compile(r"\b(?:click|press|tap|hit|choose)\s+(?:on\s+)?[*_\"“']*(?:OK|Allow)\b", re.I)
_CR_PROMPT_RE = re.compile(r"\bwants\s+access\s+to\s+control\b|\b(?:privacy|permission|consent|Automation|TCC)\b"
                           r"[^.\n]{0,40}\b(?:prompt|dialog|pop-?up|permission|grant)\b|\bprompt\b", re.I)
_CR_EXTRA_RE = re.compile(
    # an instruction (step start, sentence start, or "then/and/please ..."), not a report ("it told you to unblock")
    r"(?:^|(?<=[\n.;:!?])|(?<=\d[.)])|(?<=[-*] )|\b(?:then|and|please|so|just)\b)[\s*_]*"
    r"(?:unblock(?:ing)?\s+(?:the|this|that|my|your|its|card|t_[0-9a-f]+)\b|reply\s+(?:with\s+)?[*_\"“']*done\b"
    r"|say\s+[*_\"“']*done\b|(?:tell|let)\s+me\s+(?:know\s+)?(?:when|once)\b)"
    r"|\bwhen\s+you\s+reply\b"
    r"|\bonce\s+you(?:'ve|\s+have)?\s+(?:clicked|approved|allowed)\b[^.\n]{0,80}\b(?:I'll|I\s+will|re-?run|retry)\b",
    re.I | re.M)
# Only long quotations are dropped (a postmortem quoting the old ask); a short one is the ask itself: Reply "done".
_CR_LONG_QUOTE_RE = re.compile(r'"[^"\n]{30,}"|\u201c[^\u201d\n]{30,}\u201d')
CONSENT_RELAY_NUDGE = (
    "[coach] consent_relay: your message asks the user to click OK on a macOS permission prompt AND to do a second "
    "step so you retry (\"{evidence}\"). The click is the only human step. run_in_user_terminal keeps a job whose "
    "window is held by an Automation prompt in status `awaiting_consent` and opens the password window by itself after "
    "OK (it retries every 15 s until max wait). Tell the user one step: click OK, then type the password in the window "
    "that opens. Keep waiting with run_in_user_terminal job_id (a Kanban worker parks with that job_id in the reason); "
    "if the earlier job already failed, start a new run_in_user_terminal call now. Do not ask them to unblock a card "
    "or reply \"done\".")


def consent_relay(reply: str) -> Optional[str]:
    """The extra-step phrase when the reply pairs a macOS permission click with an unblock / reply-done re-trigger,
    else None (code spans, quotes and `>` lines ignored)."""
    text = _CODE_QUOTE.sub(" ", reply or "")
    text = _CR_LONG_QUOTE_RE.sub(" ", text)
    if not (_CR_GRANT_RE.search(text) and _CR_PROMPT_RE.search(text)):
        return None
    m = _CR_EXTRA_RE.search(text)
    return " ".join(text[max(0, m.start() - 40):m.end() + 40].split()) if m else None


# ---- command_copy_relay: a command to paste into another Hermes chat after run_in_user_terminal refused -------------
# (self-heal agent-4a7a452688: on a non-owner surface the tool refused, and the reply then told the user to "open a new
# Hermes chat ... and send: Use run_in_user_terminal to run `python3 script.py`". The refusal allows only a plain
# statement that the owner must ask from their own chat; it forbids handing the command over as something to paste.)
_CC_CHAT_RE = re.compile(
    r"\b(?:open|start|begin|use|go\s+to|in|into|from|to)\s+(?:a|an|another|your|the)\s+"
    r"(?:(?:new|fresh|existing|own|different|separate|other)\s+)*(?:Hermes\s+)?(?:chat|session|conversation)\b", re.I)
# What the user is told to do there, then the payload: in the same sentence (a colon or paragraph break is not one).
_CC_VERB_RE = re.compile(r"\b(?:paste|send|run|type|enter|(?:ask|tell)\s+(?:it|Hermes)\s+to|use)\b", re.I)
_CC_COMMAND_RE = re.compile(r"\brun_in_user_terminal\b|`[^`\n]{2,}`|\b[\w./-]+\.(?:py|sh)\b", re.I)
_CC_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
# A report or a prohibition ("my earlier reply told you to", "do not ask them to"), not an instruction.
_CC_NOT_ASK_RE = re.compile(
    r"(?:\bnot\b|\bnever\b|n't\b|\bwithout\b|\binstead\s+of\b|\bstop\b|\bwrong\b|\bearlier\b|\bprevious(?:ly)?\b|"
    r"\b(?:told|said|asked|wrote|read|tells|says)\b|\bshould\s+not\b|\bmust\s+not\b)[^.\n]{0,60}$", re.I)
COMMAND_COPY_NUDGE_OWNER = (
    "[coach] command_copy_relay: your message tells the user to open or use another Hermes chat and hands them a "
    "command to paste (\"{evidence}\"). Never hand over a command to run elsewhere. If this chat is the owner's own "
    "(Desktop, CLI, TUI, API), call run_in_user_terminal yourself now. If the tool refused (the refusal names the "
    "surface), that refusal stands: say in plain language that this needs the owner to ask from their own Hermes "
    "chat, naming the goal only, with no command, script path or tool name to copy.")
COMMAND_COPY_NUDGE_WORKER = (
    "[coach] command_copy_relay: your message tells the user to open another Hermes chat and paste a command "
    "(\"{evidence}\"). The run_in_user_terminal refusal stands on this worker/non-owner surface: do not hand over "
    "the command and never ask the user to inject it into a chat. If the user must act, call kanban_block with a "
    "human_request describing the needed step and its context in plain language (what is blocked, why only the "
    "owner can do it), with no command, script path or tool name to copy. Otherwise drop the step.")


def command_copy_relay(reply: str) -> Optional[str]:
    """The phrase when the reply tells the user to open/use another Hermes chat AND supplies a command or
    run_in_user_terminal invocation to paste there, else None. Reports and prohibitions of an old
    ask pass; plain "ask from your own chat" without a command passes."""
    text = reply or ""
    for m in _CC_CHAT_RE.finditer(text):
        before = text[max(0, m.start() - 80):m.start()].rsplit("\n", 1)[-1]
        if _CC_NOT_ASK_RE.search(before):
            continue
        tail = text[m.end():m.end() + 500]
        v = _CC_VERB_RE.search(tail)
        if not v or _CC_SENTENCE_END.search(tail[:v.start()]):
            continue  # no transfer verb in this sentence ("open a new chat for the next topic")
        rest = tail[v.end():]
        c = _CC_COMMAND_RE.search(rest)
        if c and not _CC_SENTENCE_END.search(rest[:c.start()]):  # the payload belongs to that same instruction
            return " ".join(text[max(0, m.start() - 20):m.end() + v.end() + c.end() + 20].split())
    return None


def command_copy_nudge(evidence: str, user_ask: str = "") -> str:
    tmpl = COMMAND_COPY_NUDGE_WORKER if KANBAN_PROMPT_RE.match(user_ask or "") else COMMAND_COPY_NUDGE_OWNER
    return tmpl.format(evidence=evidence[:160])


RULES = ("handoff", "confirm", "unverified", "unwatched", "report_only", "status_only", "denied_ask", "credit_ask",
         "laptop_job", "skipped_live", "merge_forecast", "future_clock")
DEFAULT_RULES = ("handoff", "confirm", "unwatched", "report_only", "status_only", "denied_ask", "credit_ask",
                 "laptop_job", "skipped_live", "merge_forecast", "future_clock")
# Per-rule judge thresholds (COACH_REPLY_THRESHOLDS overrides; other rules use COACH_REPLY_THRESHOLD).
# report_only: tuned on the training split of the 2026-10-27 labeled set (README, "report_only").
# report_only_capped: the screen alone when a second opinion was due but unavailable (daily cap hit, error); confirms
# clear 43% of screen >= 0.85 hits, so the screen alone must be surer (agent-fb58ec3a8f).
DEFAULT_THRESHOLDS = {"report_only": 0.5, "report_only_screen": 0.85, "report_only_capped": 0.92}


# The ask can be long (agent_rules keeps up to 20000 chars). Checks other than the scope limit read only its head, as
# they always have; prompts clip it themselves (request_excerpt).
ASK_HEAD = 2000
# A Kanban worker's request (core kanban_db_context): "# Kanban task <id>: <title>", header lines, "## Body", then board
# sections the card's author didn't write (other cards' summaries, shared blockers, comments). A limit such as
# "Diagnosis only." usually ends the body, past ~2 KB of header (self-heal agent-668442d68f).
# Core's restart resume (gateway/session_kanban_resume.py RESUME_NOTE) puts a one-paragraph note BEFORE the header;
# it is still the dispatcher's prompt, and the note is core's text, not the author's (agent-105768f616).
RESUME_HEAD = "## Resumed after gateway restart"
_RESUME_NOTE_RE = re.compile(r"\A\s*" + re.escape(RESUME_HEAD) + r"\n[^\n]*\n\s*(?=# Kanban task \S+:)")
KANBAN_PROMPT_RE = re.compile(r"\A\s*(?:" + re.escape(RESUME_HEAD) + r"\n[^\n]*\n\s*)?# Kanban task \S+:")
_CARD_SECTION_RE = re.compile(
    r"^## (Body|Standing instructions\b[^\n]*|Attachments|Prior attempts on this task|Parent task results|"
    r"Recent work by @[^\n]*|Comment thread|Open shared blockers on this board|Continuation checkpoint\b[^\n]*|"
    r"Woken [^\n]*|Wake check|Interrupted worker continuation)[ \t]*$", re.M)
_USER_SECTIONS = ("Body", "Standing instructions")


def scope_text(user_ask: str) -> str:
    """The part of the ask its author wrote: the whole ask, or for a Kanban worker request the card's header/title,
    `## Body` and the user's standing instructions (not other cards' summaries or shared blockers)."""
    ask = user_ask or ""
    if not KANBAN_PROMPT_RE.match(ask):
        return ask
    ask = _RESUME_NOTE_RE.sub("", ask, count=1)
    marks = list(_CARD_SECTION_RE.finditer(ask))
    parts = [ask[:marks[0].start()] if marks else ask]
    for i, m in enumerate(marks):
        if m.group(1).startswith(_USER_SECTIONS):
            parts.append(ask[m.start():marks[i + 1].start() if i + 1 < len(marks) else len(ask)])
    return "\n".join(parts)


def request_excerpt(user_ask: str, head: int = 1500, tail: int = 500) -> str:
    """The ask for a judge prompt: its first `head` chars plus the last `tail` chars of the author's text (the card
    body for a Kanban request), where a closing limit like "Diagnosis only." sits."""
    ask = user_ask or ""
    if len(ask) <= head:
        return ask
    end = scope_text(ask).rstrip()[-tail:]
    if not end or end in ask[:head]:
        return ask[:head]
    return f"{ask[:head]}\n[... request continues; it ends:]\n{end}"


def scope_limited(user_ask: str) -> bool:
    """The user explicitly limited the turn to analysis ("analysis only", "just tell me", "don't change anything").
    Reads the whole author-written text (scope_text), not a head: Kanban limits sit at the end of the body."""
    return bool(SCOPE_LIMIT_RE.search(scope_text(user_ask)))


# report_only, legitimate scheduled wait (audit t_01a17f79ade9cbdd, session 20261025_182511_c46056): the user asked a
# status question ("is this session done? it was green but not anymore") and the only open item in the reply was a
# card parked on purpose (a scheduled retention card). The nudge pushed the agent into a needless investigation.
# Deterministic, recall-safe: it applies only when the ask is a pure status question (no action verb), the reply names
# a timed/scheduled wait, and the reply names NO failure-shaped problem and admits NO unfinished work of its own;
# anything else goes to the judge.
STATUS_QUESTION_RE = re.compile(
    r"\b(?:is|are|was|were)\s+(?:this|it|that|these|those|they|everything|the\s+[\w-]+(?:\s+[\w-]+)?)\s+"
    r"(?:\w+\s+){0,3}?(?:done|finished|complete|completed|over|green|idle|ready|still\s+(?:running|going|active|open))\b"
    r"|\bwhy\s+(?:is|isn'?t|did|does)\s+(?:it|this|that|the\s+[\w-]+)\s+(?:\w+\s+){0,3}?"
    r"(?:not\s+green|no longer green|red|yellow|unfinished|still\s+(?:open|running)|not\s+done)\b"
    r"|\b(?:it|this|that)\s+was\s+(?:green|done|finished)\b|\bwhat(?:'s|’s| is)\s+(?:left|pending|still open|the status)\b"
    r"|\bstatus\s*\?", re.I)
ACTION_ASK_RE = re.compile(
    r"\b(?:fix|repair|finish|complete|close|clean\s*up|archive|ship|land|deploy|merge|restart|rerun|retry|"
    r"unblock|cancel|kill|stop|delete|remove|make|change|update|build|implement|add|do)\s+(?:it|this|that|them|the)\b"
    r"|\bplease\b[^?]*$|\bcan you\b|\bcould you\b|\bgo ahead\b|\bjust (?:do|finish|fix)\b", re.I)
SCHEDULED_WAIT_RE = re.compile(
    r"\bscheduled\b|\bparked\b|\bSCHEDULED_UNTIL\b|\btimed wait\b|\bwakes?\s+(?:up\s+)?(?:at|on|in|by|after)\b"
    r"|\bwake(?:-| )?(?:time|check)\b|\bretention\b|\bsoak(?:ing)? (?:window|period)\b|\bcool-?down\b"
    r"|\brelease window\b|\bwaiting (?:until|for (?:its|the|a) (?:wake|timer|window|clock|date|scheduled))\b"
    r"|\b(?:by|as) designed?\b|\bon purpose\b|\bintentional(?:ly)?\b", re.I)
# Any of these names a real problem, so the reply is left to the judge (keeps "the Desktop app is still timing out"
# and failing tests left in a queue firing).
HARD_PROBLEM_RE = re.compile(
    r"\bfail\w*|\berror\w*|\bbroken\b|\bbreak(?:s|ing)?\b|\bcrash\w*|\btim(?:ing|ed|es)\s+out\b|\btimeouts?\b"
    r"|\bhang(?:s|ing)?\b|\bhung\b|\bstuck\b|\bstall\w*|\bstale\b|\bregress\w*|\bbugs?\b|\bexception\w*|\bred\b"
    r"|\bnot (?:yet )?(?:merged|deployed|fixed|working|running|started|shipped|live)\b|\bblocked\b|\bcorrupt\w*"
    r"|\bmissing\b|\bwrong\b|\bleak\w*|\bdown\b|\boutage\b|\bdegraded\b|\bflak\w*|\bmisbehav\w*|\boverdue\b"
    r"|\bshould(?:n't| not) (?:be|have)\b|\bunexpected\w*|\bdidn'?t (?:run|wake|fire|start|finish)\b", re.I)
# Unfinished, agent-fixable work admitted next to the wait ("I haven't pushed the fix yet, and the deploy card is
# parked"; "the migration is half finished; I still need to run step 3"): the wait is then not the only open item, so
# the reply goes to the judge (review round 1 of t_01a184ac93d31979). Phrases that only describe the wait itself
# ("the session remains open until that card completes") stay out of this list.
UNFINISHED_WORK_RE = re.compile(
    r"\b(?:haven'?t|hasn'?t|didn'?t|have not|has not|did not)\s+(?:yet\s+)?(?:actually\s+)?[a-z]\w+"
    r"|\bstill\s+(?:need(?:s|ed)?|ha(?:ve|s)|got)\s+to\b|\bneed(?:s)? to still\b|\b(?:am|is|are|have|has)\s+yet to\b"
    r"|\bnot\s+(?:yet\s+)?(?:been\s+)?(?:done|finished|complete[d]?|pushed|run|committed|tested|verified|applied|"
    r"landed|written|built|released|published|migrated|implemented|closed|resolved|addressed)\b"
    r"|\bhalf[- ](?:way|done|finished|complete[d]?|through|migrated|implemented)\b|\bpartial(?:ly)?\b|\bincomplete\b"
    r"|\bremaining\s+(?:work|steps?|items?|tasks?|tests?|fix(?:es)?|changes?)\b|\b(?:work|steps?|items?|tasks?)\s+"
    r"remain\b|\bleft to (?:do|fix|finish|run|push|write)\b|\bTODO\b|\bnext step (?:is|will be)\b"
    r"|\bI(?:'ll| will) still\b|\bnot quite\b|\bwork in progress\b|\bWIP\b", re.I)


def scheduled_wait_status(user_ask: str, reply: str) -> Optional[str]:
    """Evidence when a pure status question got a reply whose only open item is a legitimately scheduled wait."""
    ask = (user_ask or "")[:ASK_HEAD]
    if not STATUS_QUESTION_RE.search(ask) or ACTION_ASK_RE.search(ask):
        return None
    tail = (reply or "")[-1500:]
    m = SCHEDULED_WAIT_RE.search(tail)
    if not m or HARD_PROBLEM_RE.search(tail) or UNFINISHED_WORK_RE.search(tail):
        return None
    return f"status question; only open item is a scheduled wait (\"{m.group(0)}\")"


# report_only vs kanban_continue (self-heal agent-2d5f5e554b): a Kanban worker out of runtime checkpointed its own card
# with kanban_continue ("a fresh run will resume this card now ... do not call more tools") and listed what the next
# run will do. The judge read that list as unfinished work left unowned and nudged; the nudge pushed the worker into
# more tool calls and a rewrite with a wrong fact. kanban_continue is the protocol's hand-off of unfinished work to the
# next run of the same card, so a successful one this turn is a started fix. Matches the tool call itself (direct or
# through the deferred tool_call), not a line that merely mentions it (tool_describe, grep); a call followed by the
# turn log's `<- ERROR` line (agent_rules.observe) did not continue anything.
_CONTINUE_RE = re.compile(r"^\s*->\s*kanban_continue\(|^\s*->\s*tool_call\(.*[\"']name[\"']\s*:\s*[\"']kanban_continue[\"']",
                          re.I)


def continued_run(tool_log: Optional[list] = None) -> bool:
    """True when this turn's tool log shows a kanban_continue (direct or via tool_call) that did not error."""
    lines = list(tool_log or [])
    for i, line in enumerate(lines):
        if _CONTINUE_RE.search(line or ""):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not (nxt or "").lstrip().startswith("<- ERROR"):
                return True
    return False


# report_only vs a run-ending park (self-heal agent-9f966b0682): a Kanban worker blocked its card for the user's
# decision (kanban_block needs_input + human_request; block_handoff had already judged that ask at block time) and
# closed with "H5 is blocked, not complete, on the remaining scope decision". report_only nudged it; the block had
# ended the run, so the revision turn's kanban_schedule was refused and no revised reply could pass. A turn that
# parked its run the protocol's way (a block that asks a human per human_block, a goal-mode kind=time block, or a
# kanban_schedule) is not a diagnosis left unowned. An assignment block the kernel landed as one (its result reports
# block_kind "assignment") is a park too: the same card, same acceptance, now belongs to another profile and the run
# is over (agent-081fb3bbc3: a hub worker handed its card to bb-tuner, which ran it a minute later; the nudged
# revision could only reword). A refused (no_capable_supervisor) or re-kinded handoff stays judged.
# Transient blocks and capability+needs blocks ask nobody and stay judged. A dependency block is a park only when a
# parent it waits on (named in its reason, or linked by this turn's kanban_link) is open on this host's board and
# moved within 24 h (agent-b04f510c13: a worker linked under the
# live landing card and parked, the protocol's "a wait on another card is a parent link", and got nudged after its run
# ended). A bare dependency block, or one on a done/archived/idle/unknown parent, stays judged. Reads the
# `<- OK park={...}` facts line agent_rules.observe writes after a successful
# park call (the call line itself is cut at 140 chars); a failed call gets `<- ERROR` and no park line.
_PARK_LINE = "<- OK park="


# Run-ending Kanban lifecycle tools report_only still judges after the run ended, each with why (agent-8a6863884a).
# Every tool in core's _RUN_LIFECYCLE_TOOLS is either a park (agent_rules.PARK_TOOLS, kanban_continue) or listed here;
# tests/test_report_only_lifecycle_park.py fails on an unclassified one.
JUDGED_AFTER_RUN_END = (
    ("kanban_complete", "completion claims the work is done, so open leftovers are still the worker's to file as a "
                        "follow-up card before it stops"),
    ("kanban_move", "an orchestrator or operator status move names no next owner for the work the reply leaves open"),
)
# Review handoffs: the card now belongs to the reviewer (request_review) or the implementer (request_changes).
_HANDOFF_PARKS = ("kanban_request_review", "kanban_request_changes")


def _park_facts(tool_log: Optional[list]) -> list:
    """The `<- OK park={...}` facts dicts in this turn's tool log, in order."""
    out = []
    for line in tool_log or []:
        s = (line or "").lstrip()
        if not s.startswith(_PARK_LINE):
            continue
        try:
            f = json.loads(s[len(_PARK_LINE):])
        except ValueError:
            continue
        if isinstance(f, dict):
            out.append(f)
    return out


def live_dependency_parent(tool_log: Optional[list] = None, now: Optional[float] = None,
                           dbs: Optional[list] = None) -> Optional[str]:
    """The first parent card a successful kind=dependency block this turn waits on (named in its reason or linked
    this turn) that is open on this host's boards and moved (status change or run) within FRESH_OWNER_S; else None."""
    ar = None
    for f in _park_facts(tool_log):
        if f.get("tool") != "kanban_block" or f.get("kind") != "dependency":
            continue
        for pid in f.get("parents") or []:
            try:
                ar = ar or _cache.load_sibling(_HERE, "agent_rules")
                if ar.parent_owner_live(str(pid), now=now, dbs=dbs, fresh_s=FRESH_OWNER_S):
                    return str(pid)
            except Exception:
                continue
    return None


# Run-ending tools by call line (kanban_continue / kanban_complete / kanban_move write no park line).
_RUN_END_CALL_RE = re.compile(
    r"^\s*->\s*(?:mcp__)?(kanban_continue|kanban_complete|kanban_move)\(|"
    r"^\s*->\s*tool_call\(.*[\"']name[\"']\s*:\s*[\"'](?:mcp__)?(kanban_continue|kanban_complete|kanban_move)[\"']",
    re.I)


def run_end_tool(tool_log: Optional[list] = None) -> Optional[str]:
    """`<tool>[:<kind>]` of this turn's last successful run-ending Kanban call (a park, kanban_continue,
    kanban_complete, kanban_move), else None. Telemetry for nudges a worker can no longer act on (agent-b04f510c13)."""
    lines = list(tool_log or [])
    last = None
    for i, line in enumerate(lines):
        s = (line or "").lstrip()
        if s.startswith(_PARK_LINE):
            try:
                f = json.loads(s[len(_PARK_LINE):])
            except ValueError:
                continue
            if isinstance(f, dict) and f.get("tool"):
                last = f"{f['tool']}:{f['kind']}" if f.get("kind") else str(f["tool"])
            continue
        m = _RUN_END_CALL_RE.search(line or "")
        if m:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not (nxt or "").lstrip().startswith("<- ERROR"):
                last = m.group(1) or m.group(2)
    return last


def lifecycle_park(tool_log: Optional[list] = None, now: Optional[float] = None, dbs: Optional[list] = None) -> bool:
    """True when this turn's tool log shows a successful human block, kind=time block, kanban_schedule, review
    handoff (kanban_request_review / kanban_request_changes), or a dependency block on a live parent card
    (live_dependency_parent)."""
    if live_dependency_parent(tool_log, now=now, dbs=dbs):
        return True
    for line in tool_log or []:
        s = (line or "").lstrip()
        if not s.startswith(_PARK_LINE):
            continue
        try:
            f = json.loads(s[len(_PARK_LINE):])
        except ValueError:
            continue
        if not isinstance(f, dict):
            continue
        if f.get("tool") == "kanban_schedule" or f.get("tool") in _HANDOFF_PARKS:
            return True
        if f.get("tool") == "kanban_block":
            if f.get("kind") == "time":
                return True
            if f.get("kind") == "assignment" and f.get("landed") == "assignment":
                return True  # the kernel reassigned the same card to another profile (agent-081fb3bbc3)
            args = {"kind": f.get("kind") or "", "reason": "park",
                    **({"human_request": {"ask": True}} if f.get("human_request") else {}),
                    **({"needs": ["x"]} if f.get("needs") else {})}
            if human_block("kanban_block", args):
                return True
    return False


# report_only after the worker's own run ended (self-heal agent-689886b70b): a hub worker parked its card with a
# dependency block on another team's card, then report_only nudged its reply. The block had ended the run, so every lifecycle
# call was refused and the revision could only reword ("Still unresolved: X" -> "Remaining prerequisites: X"); it
# failed again, and the card spent 10 more runs ending on the same items. Fourth time (agent-9f966b0682,
# agent-081fb3bbc3, agent-8a6863884a): each fix exempted one park kind. A turn that still fails report_only after a
# successful run-ending park on the worker's OWN card is still judged, but the finding goes to that card as a comment
# the next run reads, not to a turn that can no longer act. kanban_complete / kanban_move stay nudged
# (JUDGED_AFTER_RUN_END): the worker can still file a follow-up card. Every run-ending tool is in exactly one list
# (tests/test_report_only_run_ended.py).
RUN_ENDED_TO_CARD = (
    "kanban_block", "kanban_schedule", "kanban_request_review", "kanban_request_changes",
    "kanban_continue",  # also exempt as continued_run; listed so the classification is complete
)


def own_run_ended(tool_log: Optional[list] = None, own_card: str = "") -> Optional[str]:
    """`<tool>[:<kind>]` of this turn's last successful park that ended the run of `own_card` (the park line's `task`
    is absent or names it; agent_rules.park_result_line writes run_ended), or of a successful kanban_continue; None
    when `own_card` is empty (not a Kanban worker) or no such call ran."""
    if not own_card:
        return None
    last = None
    lines = list(tool_log or [])
    for i, line in enumerate(lines):
        s = (line or "").lstrip()
        if s.startswith(_PARK_LINE):
            try:
                f = json.loads(s[len(_PARK_LINE):])
            except ValueError:
                continue
            if (isinstance(f, dict) and f.get("run_ended") and f.get("tool") in RUN_ENDED_TO_CARD
                    and str(f.get("task") or own_card) == own_card):
                last = f"{f['tool']}:{f['kind']}" if f.get("kind") else str(f["tool"])
            continue
        if _CONTINUE_RE.search(line or ""):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not (nxt or "").lstrip().startswith("<- ERROR"):
                last = "kanban_continue"
    return last


def card_status(card: str, dbs: Optional[list] = None) -> tuple:
    """(status, board db path) of `card` on this host's boards, else (None, None)."""
    try:
        ar = _cache.load_sibling(_HERE, "agent_rules")
        paths = ar.board_dbs() if dbs is None else dbs
    except Exception:
        return None, None
    for path in paths:
        try:
            conn = ar._board_connect(path)
        except Exception:
            continue
        try:
            row = conn.execute("select status from tasks where id=?", (card,)).fetchone()
            if row:
                return str(row[0]), str(path)
        except Exception:
            continue
        finally:
            conn.close()
    return None, None


def post_card_comment(card: str, body: str, db: Optional[str] = None) -> int:
    """Append `body` to `card`'s thread on board `db` (core's kanban_db.add_comment, author "coach"); the comment id."""
    from hermes_cli import kanban_db as kb  # type: ignore
    from hermes_cli.kanban_db_connect import connect  # type: ignore
    conn = connect(db_path=Path(db)) if db else connect()
    try:
        return int(kb.add_comment(conn, card, "coach", body) or 0)
    finally:
        conn.close()


def _evidence_line(evidence: str, reply: str = "") -> str:
    """The reply line the judge's quote comes from (a confirm quote can be two words), else the quote itself."""
    q = " ".join(str(evidence or "").split())
    norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())  # noqa: E731
    key = norm(q)[:60]
    if key:
        for line in str(reply or "").splitlines():
            if key in norm(line) and len(line.strip()) > len(q):
                return " ".join(line.split())
    return q


def report_only_card_body(evidence: str, reply: str = "") -> str:
    item = _evidence_line(evidence, reply)[:400] or "an open item left unowned"
    return (f"coach report_only, last run's reply: \"{item}\". The run had already ended, so it could not act on it. "
            "Next run: name a live owner card for it (status changed or ran in the last 24 h) or do it; rewording "
            "the same item is not a fix.")


def report_only_to_card(evidence: str, own_card: str, dbs: Optional[list] = None, post=None, reply: str = "") -> dict:
    """Post the report_only finding on `own_card` when it is open; the report_only_to_card event's fields
    ({"card", "posted", "comment_id"} or {"card", "posted": False, "why"}). Never raises."""
    status, path = card_status(own_card, dbs)
    out: dict = {"card": own_card, "card_status": status}
    if not status:
        return {**out, "posted": False, "why": "card_unknown"}
    if status in ("done", "archived"):
        return {**out, "posted": False, "why": f"card_{status}"}
    try:
        cid = (post or post_card_comment)(own_card, report_only_card_body(evidence, reply), db=path)
        return {**out, "posted": True, **({"comment_id": cid} if cid else {})}
    except Exception as exc:
        return {**out, "posted": False, "why": f"error:{type(exc).__name__}", "detail": str(exc)[:160]}


def report_only_guard(user_ask: str, reply: str, tool_log: Optional[list] = None,
                      now: Optional[float] = None) -> Optional[str]:
    """Why report_only can't apply to this turn ("scope_limited" / "scheduled_wait" / "continued_run" /
    "lifecycle_park"), else None. `now` dates the board read (replays)."""
    if scope_limited(user_ask):
        return "scope_limited"
    if scheduled_wait_status(user_ask, reply):
        return "scheduled_wait"
    if continued_run(tool_log):
        return "continued_run"
    if lifecycle_park(tool_log, now=now):
        return "lifecycle_park"
    return None


# skill-outcomes' Done-means tick-off line: `Done means (<skill>): p/t first pass, k fix rounds[; still failing: <what>]
# [; n/a: <item> — <why>]`. An n/a clause with a reason names a checklist line that doesn't apply to the change (a hub
# state edit made in the scratch clone the skill prescribes is not "no detached worktree"): it is not open work, so the
# report_only screen doesn't see it (agent-bf18c74c00). A reasonless n/a, or one whose reason admits a skipped step,
# stays a candidate; `still failing:` always is.
TICK_LINE_RE = re.compile(r"Done means\s*\(\s*`?[\w:.\-]+`?\s*\)\s*[:\-–—][^\n]*", re.I)
NA_CLAUSE_RE = re.compile(r"^(\s*)n/a\s*:\s*(.*)$", re.I | re.S)
NA_REASON_RE = re.compile(r"\s(?:[—–]|-{1,2})\s*\S|[—–(:]\s*\S|\bbecause\s+\S", re.I)
NA_ADMITS_RE = re.compile(
    r"\b(?:did(?:n'?t|n’t| not)|have(?:n'?t|n’t| not)|has(?:n'?t|n’t| not)|skip(?:ped|s)?|not (?:yet )?(?:run|tested|"
    r"checked|verified|done|deployed|shipped)|untested|unverified|no time|later|todo|ran out)\b", re.I)


def drop_na_tickoffs(reply: str) -> str:
    """The reply with each tick-off line's reasoned, non-admitting `n/a:` clauses removed and reasonless ones turned
    into `still failing:` (what report_only's screen reads)."""
    def line(m):
        parts = []
        for part in m.group(0).split(";"):
            na = NA_CLAUSE_RE.match(part)
            if not na:
                parts.append(part)
            elif not NA_REASON_RE.search(na.group(2)):
                parts.append(f"{na.group(1)}still failing: {na.group(2)}")
            elif NA_ADMITS_RE.search(na.group(2)):
                parts.append(part)
        return ";".join(parts)
    return TICK_LINE_RE.sub(line, reply or "")


def prefilter(reply: str) -> list[str]:
    """Which rules this reply is a candidate for (cheap, recall-oriented)."""
    r = reply or ""
    tail = r[-700:]
    out = []
    if HANDOFF_RE.search(r):
        out.append("handoff")
    if CONFIRM_RE.search(tail) or recommend_stall(r):
        out.append("confirm")
    if DONE_RE.search(r[:600]) or DONE_RE.search(tail):
        out.append("unverified")
    if WAIT_RE.search(r[-1200:]):
        out.append("unwatched")
    ro = drop_na_tickoffs(r)[-1500:]
    if REPORT_RE.search(ro) or RESTART_DEFERRAL_RE.search(ro):
        out.append("report_only")
    return out


# Tools that, called this turn, leave something that will wake the session later.
WAKE_TOOLS = ("delegate_task", "kanban_create", "kanban_schedule", "kanban_continue", "cronjob")


def _ro_connect(path, timeout=10):
    """Read-only open. mode=ro fails on a WAL db while its -wal/-shm is absent (writers delete them on last close),
    so fall back to mode=rw + query_only, which reads the same data and never writes rows."""
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


# agent-ee1b1162bc: a subagent's final reply is a report to its parent agent, which reads and acts on it. Holding it
# as report_only/status_only pushes the child outside its brief and files defects for a handoff the brief asked for.
SUBAGENT_EXEMPT_RULES = ("report_only", "status_only")


def is_subagent(session_id: str) -> bool:
    """True when state.db records this session as a delegate_task child (sessions.source = 'subagent'). Uses source,
    not parent_session_id: compression sets that column too. Any failure (no db, no row) is False (fail-open)."""
    if not session_id:
        return False
    try:
        db = _ro_connect(_hermes_home() / 'state.db', timeout=1)
        try:
            row = db.execute("select source from sessions where id=?", (session_id,)).fetchone()
        finally:
            db.close()
        return bool(row) and str(row[0] or "").lower() == "subagent"
    except Exception:
        return False


def _lineage(session_id: str, depth: int = 12) -> list[str]:
    """session_id plus its compression ancestors (compression rotates ids; a watcher started before a
    rotation is keyed by the old id)."""
    ids = [session_id]
    try:
        import sqlite3
        db = _ro_connect(_hermes_home() / 'state.db', timeout=1)
        try:
            cur = session_id
            for _ in range(depth):
                row = db.execute("select parent_session_id from sessions where id=?", (cur,)).fetchone()
                if not row or not row[0] or row[0] in ids:
                    break
                cur = row[0]
                ids.append(cur)
        finally:
            db.close()
    except Exception:
        pass
    return ids


def _running_processes(ids: set) -> list[str]:
    """Background processes still running for this session: the in-process registry (authoritative inside the
    backend that owns the turn) plus live pids in processes.json (another backend, or a recovered checkpoint)."""
    out: list[str] = []
    try:
        from tools.process_registry import process_registry as reg  # type: ignore
        with reg._lock:
            live = list(reg._running.values())
        out += [s.id for s in live if not s.exited and ({s.session_key, s.owner_task_id} & ids)]
    except Exception:
        pass
    try:
        rows = json.loads((_hermes_home() / "processes.json").read_text())
        rows = rows if isinstance(rows, list) else rows.get("processes", [])
    except Exception:
        rows = []
    for r in rows:
        if not isinstance(r, dict) or not ({r.get("session_key"), r.get("owner_task_id")} & ids) or not r.get("pid"):
            continue
        pid_name = str(r.get("session_id") or r["pid"])
        if pid_name in out:
            continue
        try:
            os.kill(int(r["pid"]), 0)
            out.append(pid_name)
        except (OSError, ValueError):
            pass
    return out


def wake_sources(session_id: str, tool_log: Optional[list] = None) -> list[str]:
    """What will wake this session after the turn ends. Empty = nothing; a promise to continue later is orphaned.
    Fail-open the other way: any lookup error just means that source isn't counted."""
    found: list[str] = []
    log = "\n".join(tool_log or [])
    found += [f"tool:{t}" for t in WAKE_TOOLS if t in log]
    ids = set(_lineage(session_id)) if session_id else set()
    if not ids:
        return found
    found += [f"process:{p}" for p in _running_processes(ids)]
    try:
        from hermes_cli.kanban_session_activity import active_task_counts  # type: ignore
        counts = active_task_counts()
        if any(counts.get(i) for i in ids):
            found.append("kanban")
    except Exception:
        pass
    try:
        from hermes_cli.goal_session_activity import active_goal_sessions  # type: ignore
        if ids & set(active_goal_sessions()):
            found.append("goal")
    except Exception:
        pass
    return found


# agent-0bab362bcd: one clause shared by the report_only nudges and both report_only judges, so a card that can't run
# fails the same bar at nudge time and at outcome time.
STALLED_OWNER = ("A card that can't start (failed attempts, quota, no eligible route, or ready and unclaimed for 30 min "
                 "or more, or already commented on while it waited, or ready behind as many cards as there are worker "
                 "slots) is not a started fix, and another comment on it "
                 "doesn't start it: do it in this chat if your model can, raise its priority (`hermes kanban edit "
                 "<id> --priority N`), or re-place it (`placement`) on a gateway with capacity, and say which.")

# agent-9ac78980e2 / agent-61f2644a68: one clause for both report_only judges on the coach's own board read and on
# dependency-gated owners.
BOARD_OWNER = ("A `<- BOARD task=...` line (in COACH'S OWN BOARD READ) is the coach's own read of a card the message "
               "names, made from this host's boards when judging: count it as a check of that card this turn, the "
               "same as an `<- OK` line. A line ending `home=<gateway> (replica)` was read from this host's synced "
               "copy of that gateway's board (the card lives there): it counts the same as a local one. It shows "
               "status=, created= (card age), claimed= (newest claim, or never), "
               "run= (newest run state) and last=<age>(<event>); a ready or todo card also shows ready_for=<age> (time "
               "since it last entered the queue; comments don't reset it) and stall_notes=<n> (comments left on it "
               "since then, before this turn); a ready card also shows prio=<priority> ahead=<n> (ready, unclaimed "
               "cards on its board the dispatcher picks first) slots=<running>/<max_in_progress>. A message that "
               "refers to \"the card\" without its id gets the line of the card this turn's tool calls touched: "
               "that is the card it means. A ready card with ahead= at or above the slots max won't start soon: it "
               "is waiting on capacity, not started, unless the turn raised its priority (`hermes kanban edit <id> "
               "--priority N`), re-placed it or did the work. The named card is a started owner when it is "
               "running or claimed with its last event under 24 h; or ready or todo, created under 24 h ago, with "
               "no failed, crashed or timed_out run; or todo, scheduled or blocked on a dependency with `waits_on=` "
               "a parent that is running or claimed with activity under 24 h (the work is sequenced behind a started "
               "fix; the same holds for `waits_on=` on an `<- OK` line). A scheduled card whose `<- BOARD` or `<- OK` "
               "line shows `probe=live(...)` (its wake_check probe still runs on schedule and exits 0 or 1) is a "
               "started owner too, however old its last event, when the message names the non-agent producer it "
               "waits on (an install, an updater, a release or deploy, CI, a clock or date): the dispatcher re-runs "
               "the probe and wakes the card the moment the condition holds, which is the wait the Kanban protocol "
               "prescribes. `probe=stale(...)` (the probe stopped running or is broken), a scheduled card with no "
               "`probe=` fact and no such parent, or a live probe waiting on agent work nobody is doing, is not. "
               "A turn whose only action on a ready or todo card is a comment saying what holds it gets that credit "
               "only for the first such comment within 30 min of the card entering the queue (ready_for= under 30m "
               "and stall_notes=0): with ready_for= 30m or more and claimed=never, or stall_notes= 1 or more, the "
               "card is waiting on capacity, not started, and the hand-off stays high unless the turn did the work, "
               "raised the card's priority or re-placed it. Ready or todo idle over 24 h, blocked with "
               "no such parent, a failed run, or done/archived is not. A BOARD status that contradicts the message "
               "(it says running, the board says ready, blocked or done) is evidence the claim is false: score "
               "that hand-off high.")


JUDGE_SYS = """You review an AI agent's final message for a turn, right before the turn ends, for a power user who wants
the agent to self-serve and to verify its own work. Judge ONLY from the evidence shown. A PREVIOUS TURN TOOL LOG, when
shown, holds the checks the agent made one turn earlier (this turn made almost none and followed it within minutes):
count them as checks the agent made, as if this turn had made them. Score five independent
probabilities (0-1):

- handoff: the message asks the USER to perform a step (click, sign in, run a command, paste, approve a dialog,
  restart an app, plug something in...) that the agent could plausibly have done itself with shell, browser
  automation, saved credentials, a password vault, a token broker, ssh, or background desktop control. Physical
  actions (plugging hardware, being at a device), the user's own judgment/taste, money/legal decisions, secrets
  only the user knows, and macOS privacy permission grants are NOT handoffs (score low). A step ADDED after such a
  grant so the agent retries (unblock a card, reply "done", tell me when) IS a handoff (score high):
  run_in_user_terminal waits `awaiting_consent` and opens its window by itself once the user clicks OK.
  A CONDITIONAL ask ("if X isn't set up, you'll need to...", "may still need you if...") counts as a handoff when
  the agent could have checked X itself (membership, login state, a config value) and didn't: score it high.
  Asking the user to type, send or relay a message into a Hermes chat/session is a handoff (score high): an agent
  delivers it with cross_session, and a Kanban worker that was refused that send puts a `FINAL SEND for
  @session:<ref>: <text>` line in kanban_complete. "Workers are refused sending into Desktop chats" is not a reason.
- confirm: the message ends by asking permission for a next step that is reversible and clearly within what the
  user already asked for (e.g. "Want me to run the tests / apply the fix / commit it?"). Asking before something
  irreversible, external-facing (sending messages/emails, spending money, deleting data, force-push, publishing),
  or genuinely ambiguous in intent is NOT a needless confirm (score low). A plan presented for an explicit go
  when the user asked for a plan first is NOT needless.
  The same ask can be a statement: the user's request gives a direction, tweak or go on work that is already under
  way or already asked for ("how about we make X", "let's do Y instead", "make it Z"), and the message answers with
  a recommendation or an adjusted plan ("I recommend A rather than B", "I haven't changed X") but neither starts it
  this turn (the tool log shows no implementation step, card or worker told to build it; passing the idea on as
  design input or "not permission" is not starting) nor says it goes ahead by default unless the user objects.
  That leaves the user to say "go" again: a needless confirm, score it 0.8+. A sound reason to hold back one risky
  step (not raising a live limit before its guard is deployed) does not excuse starting none of the plan: the safe
  part, building it, should have started this turn. Score it LOW when the message states the adjusted plan and
  starts it, or names its default and says it proceeds; when the user asked for an opinion, options or a plan
  ("what do you recommend?", "should we?", "plan first"); when the only thing held back is new scope beyond the
  user's direction or a step that spends money, changes permissions, is irreversible or external-facing, and the
  message says so; and for a neutral status report that recommends nothing.
- unverified: the message claims the work is done/fixed/working/deployed, but the turn's tool log shows no check
  that exercises the user's actual path (running the real command/app/URL/test the user cares about and seeing the
  right result). Building, type-checking, or testing something adjacent does not count. Saying explicitly that it
  is unverified or what remains unchecked is NOT an unverified claim (score low).
- unwatched: the message says the AGENT will act later, after some outside work finishes (runs, jobs, CI, builds,
  deploys, other agents): "I'll write the report once the runs finish", "waiting on the eval". A deterministic check
  already confirmed nothing is armed to wake this session afterwards (no background watcher, kanban card, goal or
  subagent). Score high when the agent's own follow-up depends on that outside work. Score low when the next step is
  the USER's (they will test, decide, or reply), when the agent says plainly that it is stopping and the user should
  ping it, or when the outside work's result needs no further action from the agent.
- report_only: the message describes or diagnoses a problem (broken, stale, failing, stalled, unfinished, unmerged,
  undeployed, "not fixed", "remains open", "owned by the existing card", "relayed to the owner", "no action needed
  from you") that the agent could fix or start fixing itself, and ends WITHOUT fixing it, starting a fix (a kanban
  card, worker/subagent or PR created in this turn's tool log for that problem) or proposing a concrete fix (what
  would change and how). A vague "want me to look into it?" or "the next thing I'd look at is X" is not a proposal.
  This user wants every fixable problem acted on, so:
  * A QUESTION about a problem ("what is this?", "what's going on?", "why is X stuck/blocked?", "what am I supposed
    to do with this?", "is it doing what I asked?") is NOT a scope limit. It means "explain it AND fix it". A reply
    that explains and stops scores HIGH, even though it answered the question.
  * A status report that names an unresolved, agent-fixable problem as an open leftover (a stale or wrong UI, a
    stalled card/CI/worker, work "not yet" merged/deployed/started, a gap or follow-up left for later) and stops
    scores high. "No action needed from you" / "nothing needs you" does not excuse it.
  * Pointing at an existing owner (card, worker, other session) that "has it" scores high UNLESS the tool log shows
    the agent checked that owner this turn (any successful read of the owner card's state: kanban tools, CLI, a
    read-only query of the board db, the worker's process) and it is actually running.
    A successful owner check is followed in the tool log by a `<- OK` line with what it found (status=, run=, last=
    newest event and how long ago). The hand-off counts as started (score LOW) when that line shows the owner the
    message names running or claimed with activity in the last 24 h; or ready or todo (queued, not picked up yet)
    with its last activity under 24 h and no failed or crashed last run (`run=` absent, or not crashed/failed/
    timed_out): a card filed minutes ago and waiting in a normal queue is a started fix, not a stalled one; or when
    the owner was created this turn; and the message is about that owner's work. A queued owner that is waiting on
    priority or capacity also counts as started when the turn commented the stall on the owner card (a kanban_comment
    saying what holds it), but only for the first such comment within 30 min: the line shows ready_for= under 30m and
    stall_notes=0. A card ready_for= 30 min or more and never claimed, or with stall_notes= 1 or more (it was already
    commented on and still has not started), is not started by another comment: score high unless the turn did the
    work itself, raised the card's priority or re-placed it. A `<- OK` line showing it blocked or done, ready or todo
    idle for over 24 h, a failed or
    crashed last run, or a bare claim with no such line, stays high.
    An in-session subagent the agent spawned is an owner too: a `<- OK subagents:` line (from delegate_task
    action=list this turn, or ending `[live registry]`: the runtime's own read of the subagents this session started,
    still running, under 1 h old, often spawned a turn earlier) showing the subagent the message names (by id or by
    its goal) as running counts as a checked, active owner, the same as a running card; a steer sent to it this turn
    (`-> delegate_task(action=steer, ...)`) with the fix counts as handing it the work. A `<- OK steer queued: <id>`
    line is the runtime confirming that subagent is live and took the steer: it counts as a running check of it
    when the message names it (by id, or the notice or a spawn/subagents line ties its goal to this work). A
    subagent missing from that line, `none live`, or `<- OK steer NOT delivered: <id>=...` (it already finished)
    stays high.
    """ + BOARD_OWNER + """
    A `<- OK session <target>: last=<role> @ <ISO> (<age> ago) excerpt=...` line records a successful
    cross_session read. This transcript activity is not proof of a running worker: count a checked session as an
    active owner only when the recent excerpt shows progress on this fix, not just a user request or a promise.
    Stale (over 24 h), blocked, failed or completed work does not establish an active owner.
  * A Kanban worker that checkpointed its own card with kanban_continue this turn has started the remaining work
    (score low) when the leftovers it lists are what the checkpoint carries to the next run of that card.
  * Handing THIS problem's fix to another agent this turn counts as starting it (score low): the tool log has a
    send to it (cross_session / kanban_comment / kanban_create / delegate_task) and the message says what fix or
    reproduction it sent.
  * Asking or offering is a different failure: a message that proposes a concrete fix and asks for a go ("Go with
    this policy?", "say if you want it fixed", "want me to apply it?") scores LOW here (the confirm rule covers it).
  * Finished work does not excuse a leftover. If the message delivers something but ALSO names a gap, caveat or
    unfinished step the agent could close itself ("haven't tested it on a phone", "worker startup is not yet
    verified", "remaining gaps include X", "the baseline remains unmeasured", "still open: ...") and nothing in the
    tool log closes or hands it off, score it high (0.7+), the same as a pure diagnosis.
  * Passing a leftover to a card, session or owner counts as starting it only when the message says what was sent
    AND the tool log shows the send. A bare "the owner/card has it" does not.
  * Owner claims need a check. "Card X is working on that now", "the card is doing these now", "X is handling it",
    "the other session owns remediation", "fix work will start once the hold is lifted", "it's waiting until ..." score
    HIGH (0.7+) unless the tool log shows this turn's check of that owner (kanban_show, process poll, reading its
    session) and the message reports what the check found. A message SENT to an owner is not a check that it moves.
  * Owners that can't run. The nudge tells the agent: """ + STALLED_OWNER + """ So a message whose named fix card has
    failed or crashed start attempts, is parked behind a quota or routing wall ("No eligible deep routes"), or "starts
    by itself" after a reset scores HIGH (0.7+) even when the tool log checked that card, unless the tool log shows
    the agent did the work this turn or re-placed the card on a gateway that can run it, and the message says which.
    A Kanban worker's own kanban_block(kind=dependency) behind such a card is not a started fix either.
  * Small or optional leftovers still count. A closing "one limit / one gap / one small thing", "I left that alone",
    "optional tidy-up", "I didn't touch it", "haven't checked X", "needs a restart / the next sync to take effect", "not
    posted" about something the agent could do or check with its access scores 0.7+, even when the main work is done
    and verified. Calling a leftover minor, harmless or someone else's does not excuse it.
  * A Done-means tick-off line (`Done means (<skill>): p/t first pass, k fix rounds`) reports the skill's checklist.
    Its `still failing: <what>` clause is an open leftover like any other. Its `n/a: <item> — <why>` clause names a
    line that doesn't apply to this change, with the reason (e.g. "detached worktree — hub state edit made in a
    scratch clone of state, as the skill prescribes"): not a leftover, score it low, UNLESS the reason admits a step
    that does apply was skipped or left undone; then treat it as still failing.
  * Be consistent: if your evidence names an open, agent-fixable leftover that nothing in the tool log closes or hands
    off, report_only must be at least 0.7.
  Score it LOW when: the user explicitly limited scope ("analysis only", "just tell me", "plan first", "don't change
  anything"); the problem genuinely needs the user's hands, password, money, a physical device or a decision only
  they can make, and the message says so; the message is a plan the user asked for; no unresolved problem is left;
  or the user asked a status question ("is this done?", "why isn't it green?") and the only open item is a card,
  job or wait that is scheduled or parked on purpose (a timed wake, retention, soak or release window) and is
  working as designed: a legitimate wait is not a problem; or the open item is one the user
  chose to defer, rescope or skip, the message names that decision, and the tool log (this turn or
  the previous turn) shows where it is recorded (a card read or comment carrying the decision): honoring the
  user's own decision is not a leftover. Only the user's own recorded decision counts: an agent's own choice to wait,
  a schedule, or a bare claim that the user deferred it (nothing in either tool log) does not, and a latest request
  asking the agent to finish it overrides an earlier deferral.

Calibrate: 0.8+ means you'd bet the user will push back ("do it yourself" / "just do it" / "you have access"; for
report_only: "fix it" / "why aren't you fixing this?").

Return ONLY JSON: {"handoff": p, "confirm": p, "unverified": p, "unwatched": p, "report_only": p, "evidence": "<=25 words citing the message/tool log"}"""


# Second opinion for report_only (aux task `coach_report_only`): one focused call on the strong model, made only when
# the screen's (coach_reply) report_only p is at or above CONFIRM_FLOOR. It sees the turn's real tool arguments.
REPORT_ONLY_RULE = JUDGE_SYS[JUDGE_SYS.index("- report_only:"):JUDGE_SYS.index("\n\nCalibrate")]
CONFIRM_SYS = """You label the final reply an AI agent gave its user at the end of one turn. The user runs a lot of software and infrastructure through this agent. The agent has shell, code editing, git/GitHub, a Kanban board of worker agents, a browser and ssh to his machines, and standing approval to fix and ship things without asking. You get the user's message, the tool calls of the turn, and the agent's final reply. Judge only from what is shown.

The TOOL LOG shows the REAL arguments of the turn's tool calls (kanban_create title and body, delegate_task goals, patched
paths, commands). Use them to decide whether the agent started a fix, or handed it to a worker or session and what it
sent: a send of the fix or reproduction to the owning session/card/worker this turn counts as "started"; a bare "the
owner/card has it" does not. A successful owner check (any successful read of the owner card's state: kanban tools,
CLI, a read-only query of the board db, the worker's process) is followed by a
`<- OK` line with what it found (status=, run=, last= newest event and how long ago). A reply pointing at an existing
owner is "started" when that line shows the named owner running or claimed with activity in the last 24 h, or ready or
todo (queued, not picked up yet) with its last activity under 24 h and no failed or crashed last run (or the owner was
created this turn), and it owns this problem: a card filed minutes ago and waiting in a normal queue is a started fix.
A queued owner waiting on priority or capacity is also "started" when the turn commented the stall on the owner card,
but only for the first such comment within 30 min (ready_for= under 30m and stall_notes=0 on its line). A card
ready_for= 30 min or more and never claimed, or with stall_notes= 1 or more, is not started by another comment: that is
"report_only" unless the turn did the work, raised the card's priority or re-placed it.
A `<- OK` line showing it blocked or done, ready or todo idle for over 24 h, a failed or crashed last run, or no such
line, leaves it "report_only". An in-session subagent the agent spawned is an owner too: a `<- OK subagents:` line
(from delegate_task action=list this turn, or ending `[live registry]`: the runtime's own read of the subagents this
session started, still running, under 1 h old, often spawned a turn earlier) showing the subagent the reply names (by
id or by its goal) as running
counts as a checked, active owner, the same as a running card; a `-> delegate_task(action=steer, ...)` sending it the
fix counts as handing it the work. A `<- OK steer queued: <id>` line is the runtime confirming that subagent is live
and took the steer: it counts as a running check of it when the reply names it (by id, or the notice or a
spawn/subagents line ties its goal to this work). A subagent missing from that line, `none live`, or
`<- OK steer NOT delivered: <id>=...` (it already finished) leaves it "report_only".
""" + BOARD_OWNER + """
A `<- OK session <target>: last=<role> @ <ISO> (<age> ago) excerpt=...` line records a successful cross_session
read. This transcript activity is not proof of a running worker: count a checked session as an active owner only
when the recent excerpt shows progress on this fix, not just a user request or a promise. Stale (over 24 h), blocked,
failed or completed work does not establish an active owner.
A PREVIOUS TURN TOOL LOG, when shown, holds the checks the agent
made one turn earlier (this turn made almost none and followed it within minutes): count them as the agent's own checks.

Return ONLY one JSON object:
{"problem": true|false, "agent_fixable": true|false|null, "outcome": "<outcome>", "user_wanted_action": true|false, "quote": "<=20 words from the reply"}

- A Done-means tick-off line's `still failing: <what>` clause is an unresolved problem; its `n/a: <item> — <why>` clause (a checklist line that doesn't apply to this change, with the reason) is not, unless the reason admits a step that does apply was skipped or left undone.
- problem: true if the reply mentions a defect, failure, bug, blocker, stale/wrong state, or something not working or not done that is STILL UNRESOLVED when the reply ends ("not fixed", "still broken", "remaining work", "X is outdated", "Y failed", "waiting on CI"). false if no unresolved problem is mentioned. A card, job or wait that is scheduled or parked on purpose (a timed wake, retention, soak or release window) and working as designed is not a problem by itself.
- agent_fixable: when problem is true: could an agent with the access above plausibly fix it or start fixing it itself (true), or does it genuinely need the user (password, physical action, a decision only he can make, money) or an outside party (false)? null when problem is false. An item the user chose to defer, rescope or skip is false when the reply names that decision and a tool log shown (this turn or the previous turn) records it (a card read or comment carrying the decision); acting on it would override the user. Only the user's own recorded decision counts: an agent's own choice to wait, a schedule, or a bare claim that the user deferred it (nothing in either tool log) does not, and the latest request asking the agent to finish it overrides an earlier deferral.
- outcome (pick the best fit for the unresolved problem the reply is mainly about):
  "fixed": the agent itself fixed or changed the thing during this turn (edited code/config, ran the command that resolves it), even if final verification is pending.
  "started": the agent started a concrete fix that is now in progress and says so (spawned/assigned a worker, card, subagent or background job specifically to fix it, opened a PR). A Kanban worker that checkpointed its own card with kanban_continue this turn has started the leftovers that checkpoint carries to the card's next run. """ + STALLED_OWNER + """ Such a card (failed start attempts, parked on quota or "No eligible deep routes", "starts by itself" after a reset) is "report_only", not "started", unless the agent did the work this turn or re-placed it on a gateway that can run it. A Kanban worker's own kanban_block(kind=dependency) behind such a card is not a started fix either.
  "proposed": no fix yet, but the reply proposes a concrete solution or plan (what would change and how), possibly asking for a go-ahead.
  "offered": only a vague offer ("want me to fix it?", "I can look into it") with no concrete solution.
  "report_only": the reply describes or diagnoses the problem, or says it is not fixed / can be ignored / is owned by an existing task / was relayed to someone, WITHOUT fixing it, starting a new fix, or proposing a concrete solution.
  "na": no unresolved problem in the reply (a pure answer, finished work, chit-chat).
- user_wanted_action: true if the message asked the agent to do, fix, build or change something (not just a question, status check or FYI)."""
CONFIRM_FLOOR = 0.75  # screen report_only p at or above this gets the second opinion (tuned on train)
# 250 covers the busiest day seen (2026-10-30: 189 screen scores at or above the floor, 88 capped turns;
# agent-f668487724, agent-2d5f5e554b). The coach's daily check opens a Kanban card when report_only_confirm_capped
# fires anyway (signals.confirm_cap_check).
CONFIRM_DAILY_CAP = 250
# What an unconfirmed report_only does when the second opinion was due but unavailable (cap hit, error): "skip" (no
# nudge; the screen alone agreed with the second opinion on only 57% of its hits) or "nudge" (the old screen fallback,
# held to report_only_capped). COACH_REPORT_ONLY_FALLBACK overrides. Kanban worker turns always skip (capped_nudge).
REPORT_ONLY_FALLBACK = "skip"
# Desktop human (non-notice) turns reach the second opinion from a lower screen p: the Oct 30 re-measure found 28% of
# David's typed question turns still stopping at the diagnosis, and the 0.75 floor recalled only 0.31 of them held-out
# (0.49 at 0.60, P 0.91). The second opinion and the one-nudge-per-turn cap bound the false-positive cost. Notice turns
# and every other platform (Kanban workers, CLI, TUI, subagents) keep CONFIRM_FLOOR: the re-measure covers Desktop only.
HUMAN_CONFIRM_FLOOR = 0.60
HUMAN_FLOOR_PLATFORMS = ("desktop",)


# Core's own replies for a turn that errored or never ran (no model reply, so no pre_verify): logged as reply_skipped
# reason "turn_failed", not as a coach gap.
TURN_FAILED_RE = re.compile(r"^\s*(?:This turn did not complete\.|Your request was not processed\.)")


def turn_failed(reply: str) -> bool:
    return bool(TURN_FAILED_RE.match(reply or ""))


def human_confirm_floor(platform: str) -> Optional[float]:
    """The lower report_only confirm floor for this turn's platform, or None (CONFIRM_FLOOR applies). check() still
    keeps CONFIRM_FLOOR for notice turns on these platforms."""
    return HUMAN_CONFIRM_FLOOR if (platform or "") in HUMAN_FLOOR_PLATFORMS else None


def confirm_system() -> str:
    prof = profile_text()
    return CONFIRM_SYS + (f"\n\nUSER ENVIRONMENT (what this user's agents can reach):\n{prof}" if prof.strip() else "")


# Core's cross_session relay header (tui_gateway/human_input.py CROSS_SESSION_HEADER): a helper session's message
# delivered into this chat as a user-role turn. self_heal.SYNTHETIC_RE builds its branch from the same constant, and
# test_report_only_notices ties both to core (agent-83837b58db: relays were judged as David's own request).
CROSS_SESSION_HEADER = "[Message from another Hermes session"
CROSS_SESSION_RE = re.escape(CROSS_SESSION_HEADER) + r"(?:\s|\])"

# Core's subagent-result and background-job envelopes, written into the chat as user-role turns
# (tools/process_registry_notifications.py _format_*delegation*/format_process_notification,
# tui_gateway/exit_job_receipts.py jobs_stopped_note). Anchored on the full opening, never a bare "[IMPORTANT:": the
# skill-preload preamble before David's own words must stay human. The id part is optional so the coach's 40-char
# template head classifies like the whole ask (agent-ef895c63a9: status_only read the batch notice's fixed clause
# "If you are still waiting on siblings" as David asking for status).
CORE_JOB_RE = (r"\[ASYNC DELEGATION (?:BATCH COMPLETE|COMPLETE|TASK FAILED) — "
               r"|\[IMPORTANT: Background process \S+(?:\s|$)"
               r"|\[Background process \S+ heartbeat\b"
               r"|\[System note: background job results")

# The Hermes-plugin envelopes stay case-insensitive; core's envelopes are fixed-case.
NOTICE_RE = re.compile(
    r"^\s*(?:(?i:\[(?:chat-closeout|kanban close-out|false-stop recovery|owner-wake|goal continuation|babysitter)"
    r"[^\]]*\]|[✔✓]\s*\[[^\]\n]+\]\s*@[^\n]+\bKanban\b|" + CROSS_SESSION_RE + r")|" + CORE_JOB_RE + r")")

# Core's _SYNTHETIC_USER_ROW_PREFIXES (agent/context_compressor.py) that are deliberately NOT reply_check notices.
# test_every_core_synthetic_user_prefix_is_classified fails on any core prefix that is neither here nor a notice.
NOT_NOTICES = {
    "[System:": "in-turn loop continuation (truncated reply, verification stop): the agent continues the human's "
                "own turn, which stays the ask being judged",
    "[CONTEXT": "compaction summary scaffold, not a new ask",
    "[PRIOR CONTEXT": "merged prior-context scaffold, not a new ask",
    "[Your active task list": "todo list re-injected after compaction, not a new ask",
    "[Planning state preserved": "planning state re-injected after compaction, not a new ask",
    "[OUT-OF-BAND": "David's own mid-turn steering message: judged as a live human ask",
    "Cronjob Response:": "cron output delivered to a messaging platform, never a chat's user turn reply_check judges",
}


def notice_turn(user_ask: str) -> bool:
    """Only anchored automation envelopes, never a human question mentioning a notice."""
    return bool(NOTICE_RE.search(user_ask or ""))


def automation_prompt(user_ask: str) -> bool:
    """A Hermes-written prompt, not a human asking now: a notice envelope or the dispatcher's Kanban worker prompt
    (anchored header only; agent-e80aa5e107), also behind core's leading restart-resume note. The note's own opening
    counts too, so the 40-char ask_head signals.template_holds reads is recognised (agent-105768f616). Rules that read
    the ask as the user's live question skip these."""
    ask = user_ask or ""
    return notice_turn(ask) or bool(KANBAN_PROMPT_RE.match(ask)) or ask.lstrip().startswith(RESUME_HEAD)


PREAMBLE_OPEN = "[System note:"


def strip_preamble(ask: str) -> str:
    """The user's request behind core's leading "[System note: …]" resume preamble(s) (crash auto-continue, update
    pause, gateway restart recovery; agent-6b76c58161). Unchanged when the ask has no preamble, the note never
    closes, or nothing real follows it. The ask stays a human request (not automation_prompt): only the logged
    opening the template detector groups on uses this. A notice that opens with a note (core's jobs-stopped
    receipt) is left whole: what follows it is Hermes's job report, not a request."""
    text = ask or ""
    if notice_turn(text):
        return ask
    for _ in range(4):  # a resumed turn that is interrupted again stacks a second note
        if not text.startswith(PREAMBLE_OPEN):
            break
        depth, end = 0, -1
        for i, ch in enumerate(text):
            depth += (ch == "[") - (ch == "]")
            if depth == 0:
                end = i
                break
        rest = text[end + 1:].lstrip() if end > 0 else ""
        if not rest:
            return ask
        text = rest
    return text


NOTICE_CONTEXT = """AUTOMATED NOTICE, not a human request for a read-only status update. The notice supplies a worker's
handoff; it does not establish that this agent acted on each unresolved item. Evaluate each unresolved agent-fixable
leftover independently: finishing the main repair does not excuse a separate verification or deployment gap.
An existing owner counts only with evidence this turn of recent progress on that specific work, not merely a running
status or a message sent to it. A session's own progress report delivered as this notice is such evidence for that one
session only, never for other owners or leftovers. Do not assume a generic card lookup checked every cited owner.
A queued card can be a legitimate capacity wait when the turn actually checked capacity/progress; a real timed soak
or physical-user-only check is not report_only. A newly assigned concrete fix still counts as started.
When only unit tests passed and live verification remains, check whether a live check or an attributable future-event
check is actually armed. Merely saying 'the next event will test it' is not an armed check. Do not require unsafe live
side effects, a physical device, or bypassing an explicit scope restriction. Score only from the shown evidence.
Apply these distinctions before selecting the outcome; a resolved primary issue alone cannot label a separate
unattended leftover as fixed. Keep the same JSON schema."""


def render_tool_log(tool_log: list[str], budget: int) -> str:
    """Keep the newest call excerpts, in chronological order, inside the prompt budget.

    Bound each call independently so a verbose read cannot crowd out later actions.
    Keep both ends of oversized calls (tool/arguments and result), marking the gap.
    Never slice the joined log: that can silently discard the newest evidence.
    """
    if not tool_log:
        return "(no tool calls this turn)"
    omitted = "[older tool calls omitted]"
    gap = "\n[tool call middle omitted]\n"
    retained = []
    used = len(omitted) + 1  # Reserve a line for an explicit history omission.
    for call in reversed(tool_log[-40:]):
        if len(call) > 1000:
            call = call[:700] + gap + call[-(1000 - 700 - len(gap)):]
        cost = len(call) + bool(retained)
        if used + cost > budget:
            break
        retained.append(call)
        used += cost
    lines = list(reversed(retained))
    if len(retained) < len(tool_log):
        lines.insert(0, omitted)
    return "\n".join(lines)


def confirm_prompt(user_ask: str, rich_log: list[str], reply: str, prev_turn: Optional[dict] = None,
                   board: Optional[list] = None) -> str:
    """`board`: the coach's own `<- BOARD` lines (named_card_facts); None reads this host's boards now."""
    log = render_tool_log(rich_log, 7000)
    ask = user_ask or ""
    notice = notice_turn(ask)
    # Notices put the closeout/action instruction after long handoffs. Keep both ends within the same budget.
    request = (ask[:950] + "\n[handoff middle omitted]\n" + ask[-500:]) if notice and len(ask) > 1500 else request_excerpt(ask)
    context = f"{NOTICE_CONTEXT}\n\n" if notice else ""
    return (f"{context}USER'S REQUEST (latest):\n{request}\n\nTOOL LOG THIS TURN (oldest first, real arguments, "
            f"truncated):\n{log}\n\n{board_section(reply, rich_log, board, user_ask)}{prev_turn_section(prev_turn, rich_log)}"
            f"AGENT'S FINAL MESSAGE:\n{(reply or '')[:4000]}")


# A follow-up turn that only sums up what the agent checked one turn earlier (a relay or notice asking it to wrap up)
# made no tool calls of its own, so a this-turn-only log made every claim look unchecked (self-heal agent-1f34642d7f).
# When this turn made fewer than PREV_TURN_MAX_TOOLS calls and began at most PREV_TURN_MAX_GAP_S after the previous
# reply, the judges also see that turn's tool log and the head of its reply.
PREV_TURN_MAX_TOOLS = 2
PREV_TURN_MAX_GAP_S = 600
PREV_TURN_LOG_CHARS = 2000
PREV_TURN_REPLY_CHARS = 800


def prev_turn_section(prev_turn: Optional[dict], tool_log: list[str]) -> str:
    """The PREVIOUS TURN section of a judge prompt (ends with a blank line), or "" when it does not apply."""
    if not isinstance(prev_turn, dict) or sum(
            1 for x in (tool_log or []) if str(x).lstrip().startswith("->")) >= PREV_TURN_MAX_TOOLS:
        return ""
    g = prev_turn.get("gap_s")
    try:
        gap = float(g) if g is not None else None
    except (TypeError, ValueError):
        gap = None
    if gap is None:
        return ""
    rich = [str(x) for x in (prev_turn.get("rich") or [])]
    head = str(prev_turn.get("reply") or "")[:PREV_TURN_REPLY_CHARS]
    if gap > PREV_TURN_MAX_GAP_S or not (rich or head.strip()):
        return ""
    return (f"PREVIOUS TURN TOOL LOG (the agent's own checks one turn earlier, which ended {int(gap)} s before this "
            f"turn; oldest first, truncated):\n{render_tool_log(rich, PREV_TURN_LOG_CHARS) if rich else '(none)'}\n\n"
            f"PREVIOUS TURN'S FINAL MESSAGE (head):\n{head}\n\n")


def parse_confirm(raw: str) -> Optional[tuple]:
    """(p, evidence): p = 0.9 when the strong model labels the outcome report_only AND agent_fixable, else 0.1."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        pos = d.get("outcome") == "report_only" and d.get("agent_fixable") is True
        return (0.9 if pos else 0.1), str(d.get("quote") or d.get("evidence") or "")[:300]
    except Exception:
        return None


# ---- pre_verify latency budget -------------------------------------------------------------------------------------
# The host runs pre_verify on a worker it abandons at a deadline (core `plugins.hook_callback_timeout`, 30s default).
# Work past that deadline is wasted, and a nudge returned after it is discarded, so the reply check runs inside one
# explicit budget: every model call gets min(its own cap, time left - margin) and is skipped (fail-open) when less
# than MIN_CALL is left. The coach declares host timeout = budget + slack on cores that honour per-callback budgets
# (`hermes_hook_timeout`); older cores keep the global timeout, and the budget then shrinks to fit it.
PRE_VERIFY_BUDGET_S = 75.0   # whole reply check: judge (cap 60s) + report_only confirm (cap 90s) + diff review share it
PRE_VERIFY_SLACK_S = 10.0    # host timeout - budget: non-model work after the last call (log, drafts store, sqlite)
PRE_VERIFY_MARGIN_S = 2.0    # kept free before the deadline on every call
PRE_VERIFY_MIN_CALL_S = 5.0  # a model call with less time than this is not started
_FALLBACK_HOST_TIMEOUT_S = 30.0


def _env_s(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) and v >= 0 else default


def pre_verify_budget_s() -> float:
    return _env_s("COACH_PRE_VERIFY_BUDGET_S", PRE_VERIFY_BUDGET_S)


def pre_verify_host_timeout_s() -> float:
    """The per-callback host timeout the coach declares for pre_verify (seconds)."""
    return pre_verify_budget_s() + _env_s("COACH_PRE_VERIFY_SLACK_S", PRE_VERIFY_SLACK_S)


class BudgetExhausted(TimeoutError):
    """Too little of the pre_verify budget is left to start another model call."""


class Budget:
    """One pre_verify call's deadline: the coach's own budget, capped by the host's deadline. `host` is core's
    HookCallBudget (deadline + abandoned flag) when the core exposes it; otherwise `host_timeout_s` (the resolved
    global hook timeout) is measured from this call's start, so the coach still fits an older core's bound."""

    def __init__(self, own_s: float, host: Any = None, host_timeout_s: Optional[float] = None, clock=time.monotonic):
        self.clock = clock
        self.t0 = clock()
        self.own_s = own_s
        self.host = host
        self.host_timeout_s = getattr(host, "timeout", None) if host is not None else host_timeout_s
        self._host_deadline = (getattr(host, "deadline", None) if host is not None
                               else (self.t0 + host_timeout_s if host_timeout_s else None))
        self.margin = _env_s("COACH_PRE_VERIFY_MARGIN_S", PRE_VERIFY_MARGIN_S)
        self.min_call = _env_s("COACH_PRE_VERIFY_MIN_CALL_S", PRE_VERIFY_MIN_CALL_S)

    def elapsed(self) -> float:
        return self.clock() - self.t0

    def host_remaining(self) -> Optional[float]:
        if self.host is not None and getattr(self.host, "abandoned", None) is not None and self.host.abandoned.is_set():
            return 0.0
        if self._host_deadline is None:
            return None
        return max(0.0, self._host_deadline - self.clock())

    def remaining(self) -> float:
        r = self.own_s - self.elapsed()
        h = self.host_remaining()
        return max(0.0, r if h is None else min(r, h))

    def abandoned(self) -> bool:
        """True once the host stopped (or must be assumed to have stopped) waiting for this call: anything this
        call returns now is discarded, so it must not be recorded as a delivered nudge."""
        h = self.host_remaining()
        return h is not None and h <= min(0.25, self.margin)

    def call_timeout(self, cap: float) -> float:
        left = self.remaining() - self.margin
        if left < self.min_call:
            raise BudgetExhausted(f"{left:.1f}s left of the pre_verify budget")
        return min(float(cap), left)


_BUDGET: contextvars.ContextVar[Optional[Budget]] = contextvars.ContextVar("coach_pre_verify_budget", default=None)


def start_budget(own_s: Optional[float] = None) -> Budget:
    """The Budget for the pre_verify call running on this thread (reads the host deadline when core exposes it)."""
    own_s = pre_verify_budget_s() if own_s is None else own_s
    try:
        from hermes_cli.plugins import current_hook_call  # type: ignore
    except Exception:
        current_hook_call = None
    if current_hook_call is not None:
        # None here = the host runs pre_verify synchronously (hook timeout disabled): only the own budget applies
        return Budget(own_s, host=current_hook_call())
    host_s = None
    try:
        from hermes_cli.plugins import _resolve_hook_callback_timeout  # type: ignore
        host_s = float(_resolve_hook_callback_timeout())
    except Exception:
        host_s = _FALLBACK_HOST_TIMEOUT_S
    return Budget(own_s, host_timeout_s=host_s if host_s and host_s > 0 else None)


def use_budget(budget: Optional[Budget]):
    """Install `budget` for this thread's model calls; returns the token for reset_budget()."""
    return _BUDGET.set(budget)


def reset_budget(token) -> None:
    _BUDGET.reset(token)


def budget_elapsed() -> Optional[float]:
    """Seconds since this thread's pre_verify budget started (None outside pre_verify). Logged as `elapsed_s` on
    report_only_scored / reply_checked: that time is spent after the answer streamed (agent-7aa110cfd7)."""
    b = _BUDGET.get()
    try:
        return None if b is None else round(float(b.elapsed()), 2)
    except Exception:
        return None


def call_timeout(cap: float) -> float:
    """Timeout for one model call: `cap` outside pre_verify, else capped by what is left of its budget (raises
    BudgetExhausted when too little is left, which the reply check treats as no verdict)."""
    b = _BUDGET.get()
    return float(cap) if b is None else b.call_timeout(cap)


def aux_confirm(system: str, prompt: str) -> str:
    """Aux task `coach_report_only` (auxiliary.coach_report_only.{provider,model} in shared-config.json)."""
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="coach_report_only", messages=[{"role": "system", "content": system},
                                                       {"role": "user", "content": prompt}],
                    max_tokens=1000, temperature=0, timeout=call_timeout(90))
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


def _hermes_home():
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return get_hermes_home()
    except Exception:
        from pathlib import Path
        env = os.environ.get("HERMES_HOME", "").strip()  # an empty HERMES_HOME is unset (core), never the cwd
        return Path(env).expanduser() if env else Path.home() / ".hermes"


def profile_text() -> str:
    """The private user-environment overlay (COACH_REPLY_PROFILE or <HERMES_HOME>/coach/reply_profile.md), or ''."""
    from pathlib import Path
    p = Path(os.environ.get("COACH_REPLY_PROFILE") or (_hermes_home() / "coach" / "reply_profile.md"))
    try:
        # an output-format line in the overlay ("Return ONLY JSON {...}") would override JUDGE_SYS's own schema
        return re.sub(r"(?im)^[ \t]*(?:return|respond|reply|answer|output)\b[^\n]*\bjson\b[^\n]*(?:\n|$)", "",
                      p.read_text())[:4000]
    except Exception:
        return ""


def judge_system() -> str:
    """JUDGE_SYS + the private user-environment overlay (COACH_REPLY_PROFILE or <HERMES_HOME>/coach/reply_profile.md),
    which tells the judge what this user's agents can reach (credential stores, hosts, browsers). Without it the judge
    can't tell a real hand-off from a lazy one."""
    prof = profile_text()
    return JUDGE_SYS + (f"\n\nUSER ENVIRONMENT (what this user's agents can reach; use it to judge handoffs):\n{prof}"
                        if prof.strip() else "")


def judge_prompt(user_ask: str, tool_log: list[str], reply: str, prev_turn: Optional[dict] = None,
                 board: Optional[list] = None) -> str:
    log = render_tool_log(tool_log, 5000)
    return (f"USER'S REQUEST (latest):\n{request_excerpt(user_ask)}\n\nTOOL LOG THIS TURN (oldest first, truncated):\n"
            f"{log}\n\n{board_section(reply, tool_log, board, user_ask)}{prev_turn_section(prev_turn, tool_log)}"
            f"AGENT'S FINAL MESSAGE:\n{(reply or '')[:4000]}")


def parse_verdict(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    out: dict[str, Any] = {k: float(d.get(k) or 0) for k in RULES}
    out["evidence"] = str(d.get("evidence") or "")[:300]
    return out


# agent-56e18d89f2: the confirm rule's statement-shaped form (recommend_stall); NUDGES["confirm"] talks about a question.
CONFIRM_RECOMMEND_NUDGE = (
    "[coach] The user gave a direction on work that is already approved or under way, and your message answers with "
    "a recommendation or adjusted plan but starts nothing and names no default ({evidence}). That leaves the user to "
    "say \"go\" again. A direction or tweak on approved work is in scope: state the adjusted plan in one or two lines "
    "and start it this turn (do it, or start the card/worker that builds it), with your recommendation as the default "
    "the user can override. Stop to ask only if it changes acceptance, safety limits, permissions or spend, is "
    "irreversible or external-facing, or is new scope beyond the direction; then say exactly that.")


NUDGES = {
    "handoff": ("[coach] Before ending the turn: your message asks the user to do a step themselves ({evidence}). "
                "The user wants agents to self-serve. Try it yourself first with what you have (shell, ssh, browser "
                "automation, browser_vault/saved logins, token-broker, background desktop control, memory/skills for "
                "access notes). Hand off only what truly needs their hands, judgment or approval, and say in one line "
                "why you couldn't do it."),
    "confirm": ("[coach] Your message ends by asking permission for a reversible step that is within what the user "
                "asked ({evidence}). The user prefers you proceed on reversible work: do it now, verify it, then "
                "report. Ask only before irreversible or external-facing actions."),
    "unverified": ("[coach] Your message says it's done/fixed, but this turn has no check on the user's actual path "
                   "({evidence}). Verify it the way the user will experience it (same command/app/URL/viewport/device) "
                   "and cite the evidence, or state plainly what is still unverified."),
    "unwatched": ("[coach] Your message promises to continue after outside work finishes ({evidence}), but nothing "
                  "will wake this session: no background process, kanban card, goal or subagent is attached, so "
                  "the turn ends, the sidebar shows it as done, and the follow-up never happens. Before ending, arm "
                  "a wake source: one `terminal(background=true, notify=true)` watcher that polls the real "
                  "completion signal (result files, job/CI status) with a sensible interval and a time cap, then "
                  "exits with a short summary. Use a kanban card instead for multi-hour work. If you are actually "
                  "handing the follow-up to the user, say so plainly instead of promising it."),
    "report_only": ("[coach] Your message ends on a problem you could fix ({evidence}) without fixing it, starting a "
                    "fix or proposing one. The user wants problems fixed, not reported, and standing approval covers "
                    "fixes. Fix it now if it's small or reversible. Otherwise start it (kanban_create with acceptance "
                    "criteria, linked to this chat) and name the card. " + STALLED_OWNER + " If it really is the "
                    "user's call, propose the exact fix. Don't end on the diagnosis."),
}
# The session's Kanban tools can't read the board (core's "corrupt" refusal or PR #125's unreliable-view error), so
# kanban_create from this session fails too: asking for it leaves the agent with no action that works.
KANBAN_DB_DOWN = re.compile(r"Refusing to open corrupt kanban DB|attached to a deleted WAL/SHM generation"
                            r"|integrity probe disagrees with an independent process|KanbanDbUnreliableViewError")
REPORT_ONLY_KANBAN_DOWN = (
    "[coach] Your message ends on a problem you could fix ({evidence}) without fixing it, starting a fix or proposing "
    "one. This session's Kanban tools can't read the board, so kanban_create here fails too: don't try it. Instead: "
    "run `sqlite3 -readonly <board db> 'pragma quick_check'` from the terminal (read-only, no approval). If it says "
    "ok, the board is fine and this backend has a stale view: name the owner card (`hermes kanban list`/`show` in "
    "the terminal still works) or ask for the restart of this backend with action_request. If it is not ok, report "
    "that output and ask for recovery with action_request. " + STALLED_OWNER + " Don't end on the diagnosis.")


def kanban_db_down(tool_log: list[str]) -> bool:
    """True when this turn's tool log shows a Kanban call refused because the board couldn't be read."""
    return any(KANBAN_DB_DOWN.search(line or "") for line in tool_log or [])


# agent-e5b6dcf428: the nudge quotes one item, but nudge_outcome re-judges the whole revised reply. Agents fixed the
# quoted line and kept another dropped problem ("not pursuing them"), so the revision still failed.
REPORT_ONLY_WHOLE_REPLY = (" The re-check reads your whole reply: any other problem you name and leave ('not "
                           "pursuing', 'leaving it', 'still unexplained') needs the same: fix it, card it, or propose it.")


# agent-0e5f1ab1f9: a Kanban worker whose card forbade both doing the repair and re-placing it ("preserve the quota
# reserve / independent review", "no pin changes") got only STALLED_OWNER's two exits plus "propose the exact fix",
# which reaches nobody from a headless run. It argued and failed again. The parks lifecycle_park() accepts end the
# turn; name them on Kanban turns (platform == "kanban") only.
REPORT_ONLY_KANBAN_PARKS = (
    " If the card forbids both doing it here and re-placing it, end with one of two parks. When a clock gates it "
    "(a quota reset, a release window), park on that real time: kanban_schedule or kanban_block(kind='time', "
    "reason='SCHEDULED_UNTIL=<the real reset, ISO> — …'). When lifting the gate is David's call (e.g. exceeding a "
    "quota reserve), kanban_block(kind='needs_input', human_request={action, why_human, interaction, scope, "
    "evidence}). A dependency park behind a stalled card is not an ending.")


# agent-e868df5c84: David asked "are you done"; the held draft opened "**No. Three-day retention is not yet deployed
# ...**", the report_only nudge said only "fix it, card it or prove the owner", and the revision swapped the "No." for
# owner proof, so he had to ask again. A draft that opens with a direct answer keeps it: every nudge asks for that, and
# the outcome logs reply_nudge_answer_dropped when the revision no longer opens with one.
_DIRECT_LEAD_RE = re.compile(r"^(?:yes|yep|yeah|no|nope|not yet|not quite|done|partly|partially)\b", re.I)
_LEAD_STRIP_RE = re.compile(r"^(?:[#>*_`\s-]|\d+[.)]\s)+")
KEEP_ANSWER = (" Keep your direct answer to the user's question as the first line of the reply ('{lead}'). Put the "
               "fix or card after it.")


def direct_lead(text: str) -> str:
    """The opening sentence of `text` when it starts with a direct answer (yes/no/not yet/done/partly...; markdown
    bold or a heading allowed), else "". A bare "No." is extended with the sentence after it, so the lead quotes
    what the answer says. Capped at 160 chars."""
    line = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    line = _LEAD_STRIP_RE.sub("", line).replace("**", "").replace("__", "").strip()
    if not _DIRECT_LEAD_RE.match(line):
        return ""
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", line)]
    end = next((e for e in ends if e >= 12), len(line))
    lead = line[:end].strip()
    return lead if len(lead) <= 160 else lead[:157].rstrip() + "..."


def keep_answer(nudge: str, draft: str) -> str:
    """`nudge` plus the keep-answer clause when the held draft opens with a direct answer (idempotent)."""
    lead = direct_lead(draft)
    if not lead or "Keep your direct answer to the user's question" in (nudge or ""):
        return nudge
    return (nudge or "") + KEEP_ANSWER.format(lead=lead.replace("'", "’"))


def answer_dropped(lead: str, reply: str) -> bool:
    """True when the held draft had a direct answer (`lead`) and the non-empty revised `reply` no longer opens with
    one."""
    return bool(lead) and bool((reply or "").strip()) and not direct_lead(reply)


def nudge_for(rule: str, evidence: str, kanban_down: bool = False, card_closed: bool = False,
              platform: str = "", draft: str = "") -> str:
    return keep_answer(_nudge_text(rule, evidence, kanban_down=kanban_down, card_closed=card_closed,
                                   platform=platform), draft)


def _nudge_text(rule: str, evidence: str, kanban_down: bool = False, card_closed: bool = False,
                platform: str = "") -> str:
    parks = ""
    if rule == "report_only" and kanban_down:
        tmpl = REPORT_ONLY_KANBAN_DOWN
    elif rule == "report_only" and card_closed:
        tmpl = REPORT_ONLY_CARD_CLOSED
    else:
        tmpl = NUDGES[rule]
        parks = REPORT_ONLY_KANBAN_PARKS if rule == "report_only" and platform == "kanban" else ""
    text = tmpl.format(evidence=evidence or "see your last message") + parks
    return text + REPORT_ONLY_WHOLE_REPLY if rule == "report_only" else text


# agent-a806f203ab: the worker's own card was already closed when the work reopened in the same turn.
# agent-9de19e15a9: "finish now or kanban_create" left a worker whose problem already had open owner cards with no
# action that works (the create is refused as a duplicate, finishing redoes the owners' work).
EXISTING_OWNER_ROUTE = ("If an open owner card already covers it, name it with its status from kanban_show (a ready card "
                        "filed in the last 24 h with no failed run is a started fix); don't create a duplicate.")
REPORT_ONLY_CARD_CLOSED = (
    "[coach] Your message ends on a problem you could fix ({evidence}) without fixing it, starting a fix or proposing "
    "one. Your card is closed (kanban_complete ran this turn), so nothing wakes you: kanban_continue, kanban_schedule "
    "and a re-run don't apply to a closed card. Finish now, in this turn: wait on CI in the foreground "
    "(`wait_for_ci.py --timeout`), run the proof you skipped, then report the result. Or kanban_create a follow-up "
    "card with acceptance criteria and name it. " + EXISTING_OWNER_ROUTE + " A done card owns nothing: don't name "
    "one as the owner.")
CARD_CLOSED_TAIL = (" Your card is closed (kanban_complete ran this turn), so nothing wakes you. Finish now, in this turn "
                    "(wait on CI in the foreground with `wait_for_ci.py --timeout`, run the proof), or kanban_create "
                    "a follow-up card and name it. " + EXISTING_OWNER_ROUTE + " A done card owns nothing.")
def _queue_clause(q: Optional[dict]) -> str:
    """` (priority 0, 55 ready cards ahead of it, 7/7 worker slots busy)` for STALLED_QUEUE_NUDGE, or ""."""
    if not q:
        return ""
    return (f" (priority {q['prio']}, {q['ahead']} ready card(s) ahead of it, {q['running']}/{q['max'] or '?'} "
            "worker slots busy)")


STALLED_QUEUE_NUDGE = (
    "[coach] Your message says the work waits on card {card}, but that card has sat {status} and unclaimed for {age} "
    "with {notes} earlier comment(s) on it{queue}, and this turn only commented on it: a comment doesn't start a card "
    "that is waiting on capacity. Fix it this turn: do the work here if your model can, raise its priority "
    "(`hermes kanban edit {card} --priority N`), or re-place it (`placement`) on a gateway with capacity, then say "
    "which you did.")
CLOSED_OWNER_NUDGE = (
    "[coach] Your message names card {card} as the owner of the remaining work (\"{evidence}\"), but that card is "
    "{status}: a {status} card owns nothing and no worker will pick this up. Finish the work yourself now (wait on CI "
    "in the foreground, run the proof), or kanban_create a follow-up card with acceptance criteria, linked to this "
    "chat, and name that card instead.")


OWNER_KANBAN_DOWN = (" This session's Kanban tools can't read the board, so look cards up with `hermes kanban show "
                     "<id>` in the terminal (a fresh process) instead of kanban_show.")


OWNER_NUDGE = ("[coach] Your message hands a problem to another owner (\"{evidence}\"), but this turn never checked that "
               "owner or sent it anything (no cross_session, kanban_show/kanban_comment or process poll in the tool "
               "log), so nobody may own it. Before ending: rerun the failing check once. If it is green now, say so. "
               "If it is still red, either fix it yourself, or look the owner up (cross_session read, kanban_show) and "
               "send it the exact failure (cross_session send / kanban_comment), then say what you sent.")


def settings() -> dict:
    """COACH_REPLY_RULES (comma list; "none" = off), COACH_REPLY_THRESHOLD and COACH_REPLY_THRESHOLDS
    ("rule=th,..." per-rule overrides on top of DEFAULT_THRESHOLDS).

    Defaults come from the 2026-10-14 backtest (360 real replies, hindsight gold labels that saw the user's
    reaction): handoff at 0.7 fired 11, 10 right (precision 0.91, recall 0.33); confirm at 0.7 fired 4, all
    right (recall 0.11); unverified found no usable signal, so it is off by default."""
    raw = os.environ.get("COACH_REPLY_RULES", ",".join(DEFAULT_RULES))
    rules = [] if raw.strip().lower() in ("", "none", "0", "off") else [r for r in raw.split(",") if r in RULES]
    try:
        th = float(os.environ.get("COACH_REPLY_THRESHOLD", "0.7"))
    except ValueError:
        th = 0.7
    ths = dict(DEFAULT_THRESHOLDS)
    for part in os.environ.get("COACH_REPLY_THRESHOLDS", "").split(","):
        k, _, v = part.partition("=")
        if k.strip() in RULES or k.strip() in ("report_only_screen", "report_only_capped"):
            try:
                ths[k.strip()] = float(v)
            except ValueError:
                pass
    fb = os.environ.get("COACH_REPORT_ONLY_FALLBACK", REPORT_ONLY_FALLBACK).strip().lower()
    return {"rules": rules, "threshold": th, "thresholds": ths,
            "report_only_fallback": fb if fb in ("skip", "nudge") else REPORT_ONLY_FALLBACK}


def aux_judge(system: str, prompt: str) -> str:
    """Aux task `coach_reply` (auxiliary.coach_reply.{provider,model}; default: the auto aux model)."""
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="coach_reply", messages=[{"role": "system", "content": system},
                                                 {"role": "user", "content": prompt}],
                    max_tokens=2000, temperature=0, timeout=call_timeout(60))
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


def handoff_outcome(reply: str, user_ask: str, tool_log: list[str], llm: Callable[[str, str], str],
                    threshold: float = 0.8, thresholds: Optional[dict] = None) -> dict:
    """After a handoff nudge: does the REVISED final reply still hand the user a step? Same judge and threshold as
    the nudge. Returns {"p", "resolved", "evidence", "judged"}; resolved=None when the judge gave no usable verdict
    (the self-heal collector then files, as it would without an outcome). A reply the prefilter no longer flags is
    resolved without a judge call, the same way check() never judges a reply with no candidate. A reply that still
    pairs a permission click with an unblock / reply-done step (consent_relay) is unresolved without a judge call."""
    phrase = consent_relay(reply)
    if phrase:
        return {"p": 1.0, "resolved": False, "evidence": phrase, "judged": False, "src": "consent_relay"}
    phrase = command_copy_relay(reply)
    if phrase:
        return {"p": 1.0, "resolved": False, "evidence": phrase, "judged": False, "src": "command_copy_relay"}
    if "handoff" not in prefilter(reply):
        return {"p": 0.0, "resolved": True, "evidence": "no handoff language left", "judged": False}
    try:
        v = parse_verdict(llm(judge_system(), judge_prompt(user_ask, tool_log, reply)))
    except Exception:
        v = None
    if not v:
        return {"p": None, "resolved": None, "evidence": "judge gave no verdict", "judged": True}
    th = rule_threshold("handoff", v, thresholds or {}, threshold)
    return {"p": v["handoff"], "resolved": v["handoff"] < th, "evidence": v.get("evidence") or "", "judged": True}


# report_only verdict sources that may file an outcome defect: the strong model's second opinion, or a deterministic
# owner hit. A screen-only score (no second opinion asked, or it was due but capped/failed) is not a verdict.
STRONG_REPORT_ONLY_SRC = ("confirm", "closed_owner", "unverified_owner")


def nudge_outcome(rule: str, reply: str, user_ask: str, tool_log: list[str], llm: Callable[[str, str], str],
                  threshold: float = 0.8, thresholds: Optional[dict] = None,
                  wake: Optional[Callable[[], list]] = None, kanban_down: Optional[bool] = None,
                  human_asks: Optional[Callable[[], list]] = None, prev_turn: Optional[dict] = None,
                  confirm: Optional[Callable[[], Optional[tuple]]] = None, confirm_floor: float = CONFIRM_FLOOR,
                  human_confirm_floor: Optional[float] = None,
                  report_only_fallback: str = REPORT_ONLY_FALLBACK) -> dict:
    """After a nudge of any rule: does the REVISED final reply (with the whole turn's tool log, revision included)
    still trip the same rule? Re-runs check() limited to that rule (agent-3ba1979f34), with the screen judge `llm`,
    the nudge's thresholds and, for report_only, the same second opinion: `confirm`, `confirm_floor`,
    `human_confirm_floor` and `report_only_fallback` are passed to check() as pre_verify passes them
    (agent-e5b6dcf428: the outcome used to be judged by the screen alone, a weaker judge than the nudge's).
    Returns {"p", "resolved", "evidence", "judged"} (+ "src" for report_only: the verdict's source, see
    STRONG_REPORT_ONLY_SRC); resolved=None when the judge gave no usable verdict or the check failed (the self-heal
    collector files that as one coach-health defect, signals.unjudged_signal, never under the agent: agent-614ed52ae7),
    and resolved=None with unconfirmed=True when the report_only
    second opinion was due but unavailable (daily cap, error) and the screen alone would fail it: never filed."""
    if rule == "handoff":
        return handoff_outcome(reply, user_ask, tool_log, llm, threshold=threshold, thresholds=thresholds)
    seen: dict = {"called": False, "v": None, "scored": None}

    def judge(system: str, prompt: str) -> str:
        seen["called"] = True
        raw = llm(system, prompt)
        seen["v"] = parse_verdict(raw)
        return raw

    def audit(event: dict) -> None:
        if event.get("event") == "report_only_scored":
            seen["scored"] = event

    def second() -> Optional[tuple]:
        seen["c2"] = confirm() if confirm is not None else None
        return seen["c2"]
    try:
        hit = check(reply, user_ask, tool_log, judge, threshold=threshold, only=[rule], thresholds=thresholds,
                    wake=wake, kanban_down=kanban_down, human_asks=human_asks, prev_turn=prev_turn,
                    confirm=second if confirm is not None else None, confirm_floor=confirm_floor,
                    human_confirm_floor=human_confirm_floor, report_only_fallback=report_only_fallback, audit=audit,
                    deny_quoted=True)
    except Exception:
        return {"p": None, "resolved": None, "evidence": "outcome check failed", "judged": seen["called"]}
    sc = seen["scored"] or {}
    src = {}
    if rule == "report_only":
        s = ((hit or {}).get("verdict") or {}).get("report_only_src") or sc.get("source") or "screen"
        src = {"src": s}
        if s in ("screen_fallback", "screen_capped_skip"):
            ths = thresholds or {}
            th_scr = ths.get("report_only_screen", ths.get("report_only", threshold))
            screen_p = sc.get("screen_p")
            if hit or (screen_p is not None and screen_p >= th_scr):
                return {"p": screen_p if screen_p is not None else (hit or {}).get("p"), "resolved": None,
                        "unconfirmed": True, "judged": seen["called"], **src,
                        "evidence": "report_only second opinion due but unavailable; screen alone: "
                                    + str((seen["v"] or {}).get("evidence") or "")[:200]}
    if hit:
        return {"p": hit["p"], "resolved": False, "evidence": hit.get("evidence") or "", "judged": seen["called"],
                **src, **({"asks": hit["asks"]} if "asks" in hit else {})}
    if rule == "denied_ask":  # the ids it saw, so a later false hit names its card (agent-5c9b041010)
        return {"p": 0.0, "resolved": True, "evidence": "denied_ask no longer fires", "judged": seen["called"],
                "asks": []}
    if seen["called"] and not seen["v"]:
        return {"p": None, "resolved": None, "evidence": "judge gave no verdict", "judged": True}
    v = seen["v"] or {}
    p = float(v.get(rule) or 0.0)
    ev = (v.get("evidence") if seen["called"] else "") or f"{rule} no longer fires"
    if src.get("src") == "confirm" and seen.get("c2"):  # the second opinion's p and evidence, not the screen's
        p, ev = float(seen["c2"][0]), (seen["c2"][1] or ev)
    return {"p": p, "resolved": True, "evidence": ev, "judged": seen["called"], **src}


def rule_threshold(rule: str, verdict: dict, thresholds: dict, default: float) -> float:
    """The threshold a verdict is held to: report_only uses its second-opinion threshold only when the strong model
    scored it, report_only_capped (if stricter) when the second opinion was due but unavailable, else
    report_only_screen."""
    if rule == "report_only":
        if verdict.get("report_only_src") == "confirm":
            return thresholds.get("report_only", default)
        scr = thresholds.get("report_only_screen", thresholds.get("report_only", default))
        if verdict.get("report_only_src") == "screen_fallback":
            return max(scr, thresholds.get("report_only_capped", scr))
        return scr
    return thresholds.get(rule, default)


def check(reply: str, user_ask: str, tool_log: list[str], llm: Callable[[str, str], str],
          threshold: float = 0.8, only: Optional[list[str]] = None,
          changed_paths: Optional[list] = None,
          wake: Optional[Callable[[], list]] = None,
          screen: Optional[Callable[[list], Optional[dict]]] = None, screen_below: float = 0.2,
          shadow: bool = False, log: Optional[Callable[[dict], None]] = None,
          thresholds: Optional[dict] = None,
          confirm: Optional[Callable[[], Optional[tuple]]] = None, confirm_floor: float = 0.5,
          human_confirm_floor: Optional[float] = None,
          kanban_down: Optional[bool] = None,
          human_asks: Optional[Callable[[], list]] = None,
          audit: Optional[Callable[[dict], None]] = None,
          card_status: Optional[Callable[[str], Optional[str]]] = None,
          capped_nudge: bool = True,
          report_only_fallback: str = REPORT_ONLY_FALLBACK,
          prev_turn: Optional[dict] = None, deny_quoted: bool = False, platform: str = "",
          relay_repeat: bool = False, now: Optional[float] = None) -> Optional[dict]:
    """Return {"rule", "p", "evidence", "nudge"} for the top rule over threshold, else None.
    `relay_repeat` (relay_repeat.py, agent-1406d53913): this turn's user asked again while the previous reply relayed
    an unfinished owner. report_only joins the candidates (unless scoped), the second opinion always runs whatever the
    screen scored, and report_only_scored records relay_repeat: true.
    `platform` (the turn's platform) picks the report_only nudge's Kanban park exits (agent-0e5f1ab1f9).
    `deny_quoted` (nudge_outcome only): a denied_ask phrase inside quote marks still counts, so a correction that
    quotes its old denial resolves only by telling the ask (agent-747c4fc95b).
    `prev_turn` (AgentCoach.prev_turn): the previous turn's tool log and reply head, shown to the judge when this turn
    made almost no tool calls and followed it within minutes (prev_turn_section; agent-1f34642d7f).
    `capped_nudge=False` (Kanban worker turns): when the report_only second opinion was due but unavailable (daily
    cap, error), the screen alone never nudges (audit source "screen_capped_skip").
    `report_only_fallback` ("skip", the default, or "nudge") does the same for every other turn: "skip" audits the
    screen_fallback row as passing=false, suppressed="unconfirmed" (agent-f668487724); "nudge" restores the old
    fallback, held to thresholds["report_only_capped"].
    `card_status(card_id) -> status | None` looks up cards the reply names as owner (default: this host's boards,
    read-only); a done/archived one is the deterministic `closed_owner` report_only hit.
    `human_asks()` lists asks waiting on the user from cards linked to the chat ([{id, title, action, choices?}]);
    it is called only when the reply says nothing needs the user, and a hit is the deterministic `denied_ask` rule.
    `thresholds` ({rule: th}) overrides `threshold` per rule; with it, the top rule among those over their own
    threshold wins.
    `confirm() -> (p, evidence) | None` is the report_only second opinion (aux task `coach_report_only`, real tool
    arguments): when the screen's report_only p is >= `confirm_floor` (`human_confirm_floor` for non-notice turns, when given), its p replaces the screen's and is compared with
    thresholds["report_only"]; None (daily cap hit, error) falls back to the screen's p against
    thresholds["report_only_screen"] (default: the report_only threshold), raised to thresholds["report_only_capped"]
    when a second opinion was due but unavailable. Without `confirm` nothing changes.
    `wake()` lists the session's wake sources; any source drops the `unwatched` candidate before the judge runs
    (no wake callable = can't tell, so `unwatched` is skipped).
    `screen(candidates) -> {rule: p} | None` (a System One model) drops screened candidates below `screen_below`
    before the LLM judge; when no candidate survives, the LLM is skipped. `shadow` = log only, drop nothing."""
    # scope limit or a status question answered with a legitimately scheduled wait: report_only (and status_only, whose
    # walls the scheduled wait is one of) can't apply, so neither the deterministic checks nor the judge see it.
    scoped = bool(report_only_guard(user_ask, reply, tool_log))
    if human_asks is not None and (not only or "denied_ask" in only):
        phrase = denies_ask(reply, include_quoted=deny_quoted)
        asks = []
        if phrase:
            try:
                # an ask whose card this turn's own successful call cleared is gone, even when the board copy read
                # here (e.g. a hub replica on another host) still says blocked (agent-5c9b041010)
                cleared = cleared_cards(tool_log)
                asks = [a for a in (human_asks() or []) if not ask_told(reply, a) and not _ask_cleared(a, cleared)]
            except Exception:
                asks = []  # fail-open: an unreadable board never holds a reply
        if asks:
            listed = "; ".join(f"{a.get('title') or a.get('id')} ({a.get('id')}) needs: {a.get('action')}"
                               for a in asks[:3])
            return {"rule": "denied_ask", "p": 1.0, "evidence": phrase, "reason": "denied_ask",
                    "asks": [str(a.get("id") or "") for a in asks],
                    "verdict": {"denied_ask": 1.0, "evidence": phrase},
                    "nudge": DENIED_ASK_NUDGE.format(evidence=phrase[:120], n=len(asks), asks=listed[:900])}
    if not only or "handoff" in only:
        phrase = consent_relay(reply)
        if phrase:  # deterministic: a permission click plus an "unblock / reply done" re-trigger (agent-2469206a31)
            return {"rule": "handoff", "p": 1.0, "evidence": phrase, "reason": "consent_relay",
                    "verdict": {"handoff": 1.0, "handoff_src": "consent_relay", "evidence": phrase},
                    "nudge": CONSENT_RELAY_NUDGE.format(evidence=phrase[:160])}
        phrase = command_copy_relay(reply)
        if phrase:  # deterministic: a command to paste into another Hermes chat after a refusal (agent-4a7a452688)
            return {"rule": "handoff", "p": 1.0, "evidence": phrase, "reason": "command_copy_relay",
                    "verdict": {"handoff": 1.0, "handoff_src": "command_copy_relay", "evidence": phrase},
                    "nudge": command_copy_nudge(phrase, user_ask)}
    if not only or "credit_ask" in only:
        phrase = credit_ask(reply, tool_log)
        if phrase:  # deterministic: asking the user for money while the account pool sat unused
            return {"rule": "credit_ask", "p": 1.0, "evidence": phrase, "reason": "credit_ask",
                    "verdict": {"credit_ask": 1.0, "evidence": phrase},
                    "nudge": CREDIT_ASK_NUDGE.format(evidence=phrase[:160])}
    if not only or "laptop_job" in only:
        phrase = laptop_job(reply)
        if phrase:  # deterministic: a recurring unattended job proposed on a Mac that sleeps
            return {"rule": "laptop_job", "p": 1.0, "evidence": phrase, "reason": "laptop_job",
                    "verdict": {"laptop_job": 1.0, "evidence": phrase},
                    "nudge": LAPTOP_JOB_NUDGE.format(evidence=phrase[:160])}
    if not only or "skipped_live" in only:
        phrase = skipped_live(reply, tool_log)
        if phrase:  # deterministic: live state admitted unchecked while no browser/gh-run/web route was tried
            return {"rule": "skipped_live", "p": 1.0, "evidence": phrase, "reason": "skipped_live",
                    "verdict": {"skipped_live": 1.0, "evidence": phrase},
                    "nudge": SKIPPED_LIVE_NUDGE.format(evidence=phrase[:160])}
    if not only or "merge_forecast" in only:
        phrase = merge_forecast(reply, tool_log)
        if phrase:  # deterministic: a merge/landing forecast with no PR-state check this turn (agent-84aa7db32f)
            return {"rule": "merge_forecast", "p": 1.0, "evidence": phrase, "reason": "merge_forecast",
                    "verdict": {"merge_forecast": 1.0, "evidence": phrase},
                    "nudge": MERGE_FORECAST_NUDGE.format(evidence=phrase[:160])}
    if not only or "future_clock" in only:
        phrase = future_clock(reply, now=now)
        if phrase:  # deterministic: a past-tense Pacific stamp later than the real clock (agent-748d257889)
            return {"rule": "future_clock", "p": 1.0, "evidence": phrase, "reason": "future_clock",
                    "verdict": {"future_clock": 1.0, "evidence": phrase},
                    "nudge": future_clock_nudge(phrase, now=now)}
    if (not only or "report_only" in only) and not scoped:
        closed = card_closed(tool_log)
        tail = CARD_CLOSED_TAIL if closed else ""
        owner = closed_owner(reply, card_status)
        if owner:  # deterministic: a done/archived card named as owner, checked or not (agent-a806f203ab)
            cid, st = owner
            ev = f"owned by card {cid} ({st})"
            return {"rule": "report_only", "p": 1.0, "evidence": ev, "reason": "closed_owner",
                    **({"variant": "card_closed"} if closed else {}),
                    "verdict": {"report_only": 1.0, "report_only_src": "closed_owner", "evidence": ev},
                    "nudge": CLOSED_OWNER_NUDGE.format(card=cid, status=st, evidence=ev) + tail}
        phrase = unverified_owner(reply, tool_log)
        if phrase:  # deterministic: no judge call, no AI score at the edge of its bar
            down = kanban_db_down(tool_log) if kanban_down is None else kanban_down
            return {"rule": "report_only", "p": 1.0, "evidence": phrase, "reason": "unverified_owner",
                    **({"variant": "card_closed"} if closed else {}),
                    "verdict": {"report_only": 1.0, "report_only_src": "unverified_owner", "evidence": phrase},
                    "nudge": OWNER_NUDGE.format(evidence=phrase[:200]) + (OWNER_KANBAN_DOWN if down else "") + tail}
        stalled = stalled_queue_owner(reply, tool_log, now=now)
        if stalled:  # deterministic: another stall comment on a card queued 30+ min or already nudged (agent-a0262d55e5)
            cid, age = str(stalled["id"]), _cache.load_sibling(_HERE, "agent_rules")._ago(stalled["ready_for_s"])
            notes = int(stalled.get("stall_notes") or 0)
            q = stalled.get("queue") or {}
            ev = f"card {cid} {stalled['status']} and unclaimed for {age}, {notes} earlier comment(s)" + (
                f", {q['ahead']} ready card(s) ahead" if q else "") + "; this turn only commented"
            if audit is not None:
                try:
                    audit({"event": "report_only_scored", "source": "stalled_queue_owner", "final_p": 1.0,
                           "passing": True, "owner_ready_for_s": stalled["ready_for_s"], "owner_stall_notes": notes,
                           "owner_card": cid, **({"owner_ahead": q["ahead"]} if q else {}),
                           **({"owner_unnamed": True} if stalled.get("unnamed") else {})})
                except Exception:
                    pass  # telemetry never changes a verdict
            return {"rule": "report_only", "p": 1.0, "evidence": ev, "reason": "stalled_queue_owner",
                    **({"variant": "card_closed"} if closed else {}),
                    "verdict": {"report_only": 1.0, "report_only_src": "stalled_queue_owner", "evidence": ev},
                    "nudge": STALLED_QUEUE_NUDGE.format(card=cid, status=stalled["status"], age=age, notes=notes,
                                                        queue=_queue_clause(q))
                    + tail}
    if (not only or "status_only" in only) and not scoped:
        ev = status_only(user_ask, reply, tool_log)
        if ev:  # deterministic: the judge path's owner-check exemption counts card bookkeeping as acting
            return {"rule": "status_only", "p": 1.0, "evidence": ev, "reason": "status_only",
                    "verdict": {"status_only": 1.0, "evidence": ev},
                    "nudge": STATUS_ONLY_NUDGE.format(evidence=ev[:200])}
    cands = [r for r in prefilter(reply) if not only or r in only]
    # agent-56e18d89f2: a confirm candidate that is only a held-back recommendation (no "Want me to ...?"). Dropped when
    # the user limited the turn to analysis/a plan or asked for the recommendation itself.
    rec_only = None
    if "confirm" in cands and not CONFIRM_RE.search((reply or "")[-700:]):
        rec_only = recommend_stall(reply)
        if rec_only and (scope_limited(user_ask) or ASKED_OPINION_RE.search(scope_text(user_ask)[-3000:])):
            cands.remove("confirm")
            rec_only = None
    if relay_repeat and "report_only" not in cands and (not only or "report_only" in only):
        cands.append("report_only")
    if "report_only" in cands and scoped:
        cands.remove("report_only")
    # agent-a3c36a6f70: a fix left inactive "until the next restart" gets the second opinion whatever its scores
    forced = ("report_only" in cands and confirm is not None and restart_deferral(reply, tool_log)) or None
    forced_by = "restart_deferral" if forced else None
    # agent-1406d53913: a reply to a repeat ask while relaying an unfinished owner gets it too
    if not forced and relay_repeat and "report_only" in cands and confirm is not None:
        forced, forced_by = "relay_repeat", "relay_repeat"
    if "unwatched" in cands:
        try:
            if wake is None or wake():
                cands.remove("unwatched")
        except Exception:
            cands.remove("unwatched")
    if not cands:
        return None
    sp = None
    if screen is not None:
        try:
            sp = screen(cands)
        except Exception:
            sp = None
    if sp:
        # the confirm screen asks "does it END BY ASKING permission": a statement-shaped stall has no question, so its
        # screen score says nothing and the judge decides
        kept = [r for r in cands if r not in sp or sp[r] >= screen_below or (forced and r == "report_only")
                or (rec_only and r == "confirm")]
        if not shadow:
            if not kept:
                if log:
                    log({"name": "coach_reply", "screen": sp, "skipped_llm": True})
                return None
            cands = kept
    v = parse_verdict(llm(judge_system(), judge_prompt(user_ask, tool_log, reply, prev_turn=prev_turn)))
    if sp and log:
        log({"name": "coach_reply", "screen": sp, "skipped_llm": False, "shadow": shadow,
             "llm": {r: v[r] for r in sp} if v else None, "would_drop": [r for r in sp if sp[r] < screen_below]})
    if not v:
        return None
    # report_only has two thresholds: "report_only" is for the strong model's second opinion (p is 0.9 / 0.1), and
    # "report_only_screen" for the screen's own p when no second opinion was made (no `confirm`, cap hit, error).
    ths = thresholds or {}
    th_scr = ths.get("report_only_screen", ths.get("report_only", threshold))
    th_ro = th_scr
    screen_p = v["report_only"]
    if human_confirm_floor is not None and not notice_turn(user_ask):
        confirm_floor = human_confirm_floor
    confirm_attempted = "report_only" in cands and confirm is not None and (screen_p >= confirm_floor or bool(forced))
    if confirm_attempted and confirm is not None:
        try:
            c2 = confirm()
        except Exception:
            c2 = None
        v = dict(v)
        if c2 is not None:
            v["screen_report_only"] = v["report_only"]
            v["report_only"], v["evidence"] = float(c2[0]), (c2[1] or v["evidence"])
            v["report_only_src"] = "confirm"
            th_ro = ths.get("report_only", th_scr)
        elif not capped_nudge:
            v["report_only_src"] = "screen_capped_skip"
            th_ro = 2.0  # unreachable (p <= 1); a finite number keeps log.jsonl valid JSON
        else:
            v["report_only_src"] = "screen_fallback"
            th_ro = max(th_scr, ths.get("report_only_capped", th_scr))
    # agent-f668487724: an unconfirmed report_only (second opinion due but capped or failed) never nudges by default.
    suppressed = (v.get("report_only_src") == "screen_fallback" and report_only_fallback != "nudge"
                  and v["report_only"] >= th_ro)
    if audit is not None and "report_only" in cands:
        try:
            named_board = named_card_facts(reply, tool_log, user_ask=user_ask) or []
            audit({"event": "report_only_scored", "screen_p": screen_p,
                   "confirm_attempted": confirm_attempted, "floor": confirm_floor,
                   "source": v.get("report_only_src", "screen"), "final_p": v["report_only"],
                   "threshold": th_ro, "passing": v["report_only"] >= th_ro and not suppressed,
                   "owner_fresh_ready": bool(fresh_ready_owner(reply, tool_log)),
                   "owner_probe_live": bool(owner_probe_live(reply, list(tool_log or []) + list(
                       named_board), user_ask)),
                   "owner_source": owner_source(reply, tool_log, board=named_board, user_ask=user_ask),
                   **stall_telemetry(reply, tool_log, now=now),
                   **({"forced": forced_by} if forced and confirm_attempted else {}),
                   **({"relay_repeat": True} if relay_repeat else {}),
                   **({"elapsed_s": budget_elapsed()} if budget_elapsed() is not None else {}),
                   **({"suppressed": "unconfirmed"} if suppressed else {})})
        except Exception:
            pass  # telemetry never changes a verdict
    if suppressed or v.get("report_only_src") == "screen_capped_skip":
        cands = [r for r in cands if r != "report_only"]
        if not cands:
            return None
    if thresholds:
        thresholds = {**thresholds, "report_only": th_ro}
        passing = [r for r in cands if v[r] >= thresholds.get(r, threshold)]
        if not passing:
            return None
        best = max(passing, key=lambda r: v[r])
    else:
        best = max(cands, key=lambda r: v[r])
        if v[best] < threshold:
            return None
    down = kanban_db_down(tool_log) if kanban_down is None else kanban_down
    closed = best == "report_only" and card_closed(tool_log)
    if best == "confirm" and rec_only:
        return {"rule": best, "p": v[best], "evidence": v["evidence"], "verdict": v, "variant": "recommend",
                "reason": "recommend_stall",
                "nudge": keep_answer(CONFIRM_RECOMMEND_NUDGE.format(evidence=v["evidence"] or rec_only), reply)}
    return {"rule": best, "p": v[best], "evidence": v["evidence"], "verdict": v,
            **({"variant": "card_closed"} if closed and not down else {}),
            "nudge": nudge_for(best, v["evidence"], kanban_down=down, card_closed=closed, platform=platform,
                               draft=reply)}


# ───────────────────────────── block-time handoff check (agent-19492bff76) ─────────────────────────────
# The reply-time handoff rule runs in pre_verify, at the end of a turn. A Kanban worker that hands David a step
# through kanban_block (or a blocked kanban_create with a human_request) ends its run before that check: it asked
# David to open a tailnet URL and click Create after one refused browser call. pre_tool_call judges the ask once.
BLOCK_HANDOFF_NUDGE = (
    "[coach] Held this block once: it asks the user to do a step ({evidence}) that you could plausibly do "
    "yourself. Try it first with what you have: shell/ssh on the host, browser automation (a private/tailnet URL "
    "refused by the browser can still be fetched with curl in the terminal, and its form submitted from the public "
    "site's tab), browser_vault/saved logins, token-broker. If it still truly needs the user (passkey/2FA, their "
    "judgment, money, a physical action), call the same block again and it will go through; say in why_human "
    "what you tried and why it failed.")


def block_time_enabled() -> bool:
    """Env COACH_BLOCK_TIME, then config plugins.entries.coach.settings.block_time; default on."""
    raw: Any = os.environ.get("COACH_BLOCK_TIME")
    if raw is None:
        try:
            so = _cache.load_sibling(_HERE, "second_opinion")
            raw = so._config_settings().get("block_time")
        except Exception:
            raw = None
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "off", "false", "no")


def human_block(tool_name: str, args: Any, default_card: str = "") -> Optional[tuple[str, str]]:
    """(human-facing text, card key) when this call asks a human through Kanban, else None. Covers kanban_block with
    kind needs_input/capability or a human_request, kanban_create with a human_request, and both through the
    deferred `tool_call`. Dependency/time/transient parks ask nobody."""
    a = args if isinstance(args, dict) else {}
    if tool_name == "tool_call":
        for c in a.get("calls") or []:
            if isinstance(c, dict):
                got = human_block(str(c.get("name") or ""), c.get("arguments"), default_card)
                if got:
                    return got
        return None
    hr = a.get("human_request")
    hr_text = json.dumps(hr, ensure_ascii=False) if isinstance(hr, dict) and hr else ""
    if tool_name == "kanban_block":
        if not hr_text and str(a.get("kind") or "") not in ("needs_input", "capability"):
            return None
        # capability + needs=[...] and no human_request opens an agent owner card on the gateway holding the access;
        # it asks nobody (agent-f668487724). A capability block without needs still asks a human.
        if not hr_text and str(a.get("kind") or "") == "capability" and a.get("needs"):
            return None
        text = "\n".join(s for s in (str(a.get("reason") or ""), hr_text) if s)
        return (text, str(a.get("task_id") or default_card or "")) if text.strip() else None
    if tool_name == "kanban_create" and hr_text:
        return ("\n".join(s for s in (str(a.get("title") or ""), hr_text) if s),
                "create:" + str(a.get("title") or "")[:120])
    return None


def block_handoff(text: str, user_ask: str, tool_log: list[str], llm: Callable[[str, str], str],
                  threshold: float = 0.7, thresholds: Optional[dict] = None) -> Optional[dict]:
    """Judge a Kanban ask to the user with the reply judge's handoff rule. {"p", "evidence", "nudge"} on a hit,
    else None (also when the judge fails: fail-open). A permission click paired with an unblock / reply-done step
    (consent_relay) is a hit without the judge."""
    phrase = consent_relay(text)
    if phrase:
        return {"p": 1.0, "evidence": phrase[:200], "src": "consent_relay",
                "nudge": CONSENT_RELAY_NUDGE.format(evidence=phrase[:160])}
    phrase = command_copy_relay(text)
    if phrase:
        return {"p": 1.0, "evidence": phrase[:200], "src": "command_copy_relay",
                "nudge": COMMAND_COPY_NUDGE_WORKER.format(evidence=phrase[:160])}
    msg = ("[The agent is about to block its Kanban card on the user with this ask; nothing else will reach the "
           "user this turn.]\n" + (text or ""))
    try:
        v = parse_verdict(llm(judge_system(), judge_prompt(user_ask, tool_log, msg)))
    except Exception:
        return None
    if not v or v["handoff"] < rule_threshold("handoff", v, thresholds or {}, threshold):
        return None
    ev = (v.get("evidence") or "see the block reason")[:200]
    return {"p": v["handoff"], "evidence": ev, "nudge": BLOCK_HANDOFF_NUDGE.format(evidence=ev)}


# ---- backtest: rerun a deterministic rule over real replies ------------------------------------------------------------
# `python3 reply_check.py backtest credit_ask --days 14` prints every assistant reply in state.db (and every held draft in
# <coach>/drafts.jsonl) that the rule would hold, so a regex change is measured against real replies, not hand-picked
# strings (self-heal agent-ddabb16633: three credit_ask false-positive repairs in 3 days, each fixing one phrase).
BACKTEST_RULES: dict = {"credit_ask": lambda r: credit_ask(r, []), "laptop_job": laptop_job,
                        "skipped_live": lambda r: skipped_live(r, []),
                        "merge_forecast": lambda r: merge_forecast(r, []),
                        "future_clock": lambda r, at=None: future_clock(r, now=at),
                        "recommend_stall": lambda r: recommend_stall(r)}
# Rules that compare the reply with the clock: the backtest passes each reply's own timestamp as `at`.
BACKTEST_AT_RULES = ("future_clock",)


def backtest(rule: str, days: float, db=None, drafts=None) -> list:
    """[(source, session_id, evidence)] for replies newer than `days` that `rule` holds (tool log ignored)."""
    from pathlib import Path
    fn = BACKTEST_RULES[rule]
    since = time.time() - days * 86400
    home = _hermes_home()
    db = Path(db) if db else home / "state.db"
    drafts = Path(drafts) if drafts else home / "coach" / "drafts.jsonl"
    hits = []
    timed = rule in BACKTEST_AT_RULES  # judged at the reply's own time, not now
    if db.exists():
        conn = _ro_connect(db)
        try:
            for mid, sid, content, ts in conn.execute(
                    "select id, session_id, content, timestamp from messages where role='assistant' and timestamp > ? "
                    "and content is not null and content != '' order by id", (since,)):
                ev = fn(content, at=float(ts or 0)) if timed else fn(content)
                if ev:
                    hits.append((f"msg:{mid}", sid, ev))
        finally:
            conn.close()
    if drafts.exists():
        for line in drafts.read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or float(d.get("ts") or 0) < since:
                continue
            text = str(d.get("draft") or "")
            ev = fn(text, at=float(d.get("ts") or 0)) if timed else fn(text)
            if ev:
                hits.append((f"draft:{d.get('draft_id')}", str(d.get("session_id") or ""), ev))
    return hits


# agent-747c4fc95b: `reply_check.py replay-denied-ask --days 30` reruns the denied_ask nudge outcome over the logged
# outcomes in <coach>/log.jsonl: the revision is the session's last assistant message at the outcome time (state.db),
# the asks are the logged card ids read back from the boards (any status, with their choices). It prints every
# outcome whose verdict the current code changes, so a matcher change is measured against real revisions.
def replay_denied_ask(days: float, home=None) -> list:
    """[{ts, session_id, asks, was, now, evidence, reply}] for logged denied_ask outcomes newer than `days`."""
    from pathlib import Path
    home = Path(home) if home else _hermes_home()
    since = time.time() - days * 86400
    log = home / "coach" / "log.jsonl"
    rows = []
    for line in (log.read_text(errors="replace").splitlines() if log.exists() else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (isinstance(r, dict) and r.get("event") == "reply_nudge_outcome" and r.get("rule") == "denied_ask"
                and float(r.get("ts") or 0) >= since):
            rows.append(r)
    boards = [home / "kanban.db"] + sorted((home / "kanban" / "boards").glob("*/kanban.db"))

    def card(cid: str) -> Optional[dict]:
        slug, _, tid = cid.rpartition("/")
        named = [p for p in boards if (p.parent.name if p.parent.parent.name == "boards" else "default") == slug]
        for path in named + [p for p in boards if p not in named]:  # a gateway prefix (hub/t_x) names no board
            if not path.exists():
                continue
            conn = _ro_connect(path, timeout=2)
            try:
                got = conn.execute("select id, title, block_kind, human_request from tasks where id=?",
                                   (tid,)).fetchone()
            finally:
                conn.close()
            if got:  # replay the ask as it was: treat the card as still waiting on the user
                return {"id": cid, "title": got[1], "status": "blocked", "block_kind": got[2] or "needs_input",
                        "human_request": got[3]}
        return None

    out = []
    db = home / "state.db"
    conn = _ro_connect(db) if db.exists() else None
    try:
        for r in rows:
            ids = [str(a) for a in (r.get("asks") or [])]
            if not ids or conn is None:
                continue  # rows before ask-id logging can't be replayed against their cards
            got = conn.execute("select content from messages where session_id=? and role='assistant' and "
                               "timestamp <= ? and content is not null and content != '' order by timestamp desc "
                               "limit 1", (r.get("session_id"), float(r["ts"]) + 1)).fetchone()
            if not got:
                continue
            cards = [c for c in (card(i) for i in ids) if c]
            res = nudge_outcome("denied_ask", got[0], "", [], lambda s, p: "{}",
                                human_asks=lambda: linked_human_asks(cards))
            out.append({"ts": r.get("ts"), "session_id": r.get("session_id"), "asks": ids,
                        "was": r.get("resolved"), "now": res.get("resolved"), "evidence": res.get("evidence"),
                        "reply": got[0]})
    finally:
        if conn is not None:
            conn.close()
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="reply_check.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    bt = sub.add_parser("backtest", help="rerun a deterministic reply rule over state.db replies and held drafts")
    bt.add_argument("rule", choices=sorted(BACKTEST_RULES))
    bt.add_argument("--days", type=float, default=14)
    bt.add_argument("--db", help="state.db (default <HERMES_HOME>/state.db)")
    bt.add_argument("--drafts", help="drafts.jsonl (default <HERMES_HOME>/coach/drafts.jsonl)")
    rp = sub.add_parser("replay-denied-ask", help="rerun logged denied_ask nudge outcomes on their revisions and cards")
    rp.add_argument("--days", type=float, default=30)
    rp.add_argument("--home", help="Hermes home (default $HERMES_HOME)")
    rp.add_argument("--all", action="store_true", help="print every replayed outcome, not only changed ones")
    a = ap.parse_args(argv)
    if a.cmd == "replay-denied-ask":
        rows = replay_denied_ask(a.days, a.home)
        for r in rows:
            if a.all or r["was"] != r["now"]:
                print(f"{r['ts']}\t{r['session_id']}\twas={r['was']} now={r['now']}\t{','.join(r['asks'])}\t"
                      f"{' '.join(str(r['reply']).split())[:160]}")
        changed = sum(1 for r in rows if r["was"] != r["now"])
        print(f"denied_ask outcomes replayed: {len(rows)}, verdict changed: {changed}")
        return 0
    hits = backtest(a.rule, a.days, a.db, a.drafts)
    for src, sid, ev in hits:
        print(f"{src}\t{sid}\t{' '.join(str(ev).split())[:160]}")
    print(f"{a.rule}: {len(hits)} hit(s) in the last {a.days:g} day(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
