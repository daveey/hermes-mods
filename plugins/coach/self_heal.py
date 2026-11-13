"""self_heal: notice an agent/Hermes defect as early as possible: at the end of the turn that caused it, or on the
user's first message that reveals it.

Two entry paths, one schema:
- detect_complaint(prev_reply, user_text, tool_log=...) — pre_llm_call. Every real user message except pure acks
  goes to the aux judge `coach_defect` with the agent's previous reply and that turn's tool log: corrections, failed
  fixes, needless handoffs, frustration, Hermes/product misbehaviour, and nudges after a stalled turn.
- detect_admission(final_reply, tool_log=..., ask=...) — post_llm_call. Machine signals first, no judge (provider
  error reply, interrupted turn, one tool error repeated 3x); then, when the reply admits a mistake, hands the user a
  step, or the tool log shows errors, a turn-end audit by the same judge.
Both judges append the private user overlay (COACH_REPLY_PROFILE or <HERMES_HOME>/coach/reply_profile.md, shared
with reply_check) so they know the user's standing rules.

Both return {p_defect, kind, component, symptom, fingerprint, source} when p_defect >= threshold, else None.
The fingerprint is a stable hash of component + the symptom's content words, so one defect reported several ways
in one day is filed once (`Fingerprints.record`). Eval: eval/selfheal_incident_replay.py.

Mode: COACH_SELFHEAL=off|shadow|live (default shadow). shadow logs `selfheal_detect` and `selfheal_would_file` (the
full card it would file) to coach log.jsonl; live files the investigation card on the hub (see "Lane" below; the
README's "Self-heal lane" section). Fail-open everywhere.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_TL = threading.local()  # detect_complaint -> handle hand-off of an untraced complaint judgment (see handle)

if __package__:
    from .reply_check import CROSS_SESSION_HEADER, CROSS_SESSION_RE, render_tool_log
else:  # Standalone plugin/cron/CLI loader.
    import importlib.util
    _spec = importlib.util.spec_from_file_location("_selfheal_reply_check", Path(__file__).with_name("reply_check.py"))
    assert _spec and _spec.loader
    _reply_check = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_reply_check)
    render_tool_log = _reply_check.render_tool_log
    CROSS_SESSION_HEADER = _reply_check.CROSS_SESSION_HEADER
    CROSS_SESSION_RE = _reply_check.CROSS_SESSION_RE

# The placement helper (fence_evidence / home_pin_note) is shared by every coach card producer.
if __package__:
    from .placement_evidence import EVIDENCE_CLOSE, EVIDENCE_OPEN, HOME_PIN_PREFIX, HomePinUnresolved, fence_evidence, home_machine, home_pin_note, home_runs_on_argv, incident_title
    from . import placement_evidence as _placement_evidence
else:
    _spec = importlib.util.spec_from_file_location("_coach_placement_evidence", Path(__file__).with_name("placement_evidence.py"))
    assert _spec and _spec.loader
    _placement_evidence = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_placement_evidence)
    EVIDENCE_OPEN, EVIDENCE_CLOSE = _placement_evidence.EVIDENCE_OPEN, _placement_evidence.EVIDENCE_CLOSE
    fence_evidence, home_pin_note = _placement_evidence.fence_evidence, _placement_evidence.home_pin_note
    HomePinUnresolved, home_runs_on_argv = _placement_evidence.HomePinUnresolved, _placement_evidence.home_runs_on_argv
    incident_title = _placement_evidence.incident_title
    home_machine = _placement_evidence.home_machine
    HOME_PIN_PREFIX = _placement_evidence.HOME_PIN_PREFIX

COMPONENTS = ("agent", "desktop", "plugin", "cron", "kanban", "bot", "gateway", "provider", "browser", "bb", "other")

OOB_RE = re.compile(r"^\s*\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]", re.S)
# Hermes-written user-role text: "[IMPORTANT: ...", "[System note: ...", kanban completion notices.
# Keep coordination notices out of the user-complaint lane. Their underlying failures
# belong to the machine-signal collector, not a second complaint filed by the recipient.
# Lowercase bracket tags are Hermes-written when the tag is a Hermes tag (agent-b3f86b6bb7: "[ask-gate ...]",
# "[owner-wake]", "[kanban]", "[runtime]" were filed as David's complaints, one hand-listed tag at a time). The set
# is derived from the plugin directories (this repo's plugins/ and <home>/plugins/, read at import), each name's first
# segment too ("[kanban-honest-done]" -> kanban), plus tags written by core or by a plugin under another name.
# tests/test_selfheal_synthetic_tags.py fails CI when a plugin writes a "[<tag>" notice this set does not cover.
EXTRA_HERMES_TAGS = ("kanban", "runtime", "owner-wake", "coach", "chat-closeout", "workflow-state")
_NOT_HERMES_TAGS = ("selfheal-probe", "selfheal")  # probes must reach PROBE_RE (selfheal_skip why=probe)


def _hermes_tags() -> frozenset:
    dirs = [Path(__file__).resolve().parent.parent]
    env = os.environ.get("HERMES_HOME", "").strip()
    dirs.append((Path(env).expanduser() if env else Path.home() / ".hermes") / "plugins")
    tags: set = set(EXTRA_HERMES_TAGS)
    for d in dirs:
        try:
            names = [p.name for p in d.iterdir() if p.is_dir()]
        except OSError:
            continue
        for n in names:
            n = n.lower()
            if re.fullmatch(r"[a-z][a-z0-9_-]{1,40}", n):
                tags.add(n)
                first = re.split(r"[-_]", n, maxsplit=1)[0]
                if len(first) >= 3:
                    tags.add(first)
    return frozenset(tags - set(_NOT_HERMES_TAGS))


HERMES_TAGS = _hermes_tags()
_HERMES_TAG_RE = (r"\[(?:" + "|".join(re.escape(t) for t in sorted(HERMES_TAGS, key=lambda t: (-len(t), t)))
                  + r")(?=[\] :_,]|\s)")
SYNTHETIC_RE = re.compile(
    r"^\s*(?:" + CROSS_SESSION_RE + r"|" + _HERMES_TAG_RE + r"|"
    r"[\u2100-\u2bff\U0001F300-\U0001FAFF\ufe0f]{1,3}\s+(?:\[[^\]\n]+\]\s*)?(?:@\S+\s+)?Kanban\s+t_[0-9a-f]+\b|"
    r"\[[A-Z][A-Za-z -]{2,40}(?::|\]| —)|(?i:\[coach\])|You've reached the maximum|[✔✓✗✖⚠] )")
# Any Kanban watcher notice ("<symbol> [board] @assignee Kanban t_x ...", gateway/kanban_watchers_notifier.py + the
# gateway.kanban.ping locale strings: ⏳ chat-wait/quota/parent-unclaimed, ⏸ blocked, ⏱ timed out, 👀 review, 🛑 triage,
# ⛔ gave up, 🔄 status, ℹ) is gateway-written, not David's words. Listing one symbol at a time (⏸, then ⏳ in e002e0f0)
# let each new notice reach the complaint judge first (kanban-22e8da03db, kanban-3ba089808c); the board tag is optional.
# `[coach]` nudges are stored as user-role turns but are the coach's own text (agent-3ba1979f34): whether the nudge
# fixed the reply is judged by its reply_nudge_outcome (signals.collect_coach), never by the complaint backstop.
# Plugin-appended blocks that are not the user's words.
TRAILER_RE = re.compile(r"\n?\s*(?:\[user-steps\]|--- Attached Context ---|Session link\(s\) @session).*", re.S)
# A deliberate live probe of a detector (agent-771fbd2b85): a session whose FIRST user message starts with
# `[selfheal-probe]` (or the older "Live test of a detector" wording) fails on purpose. Every entry path (handle,
# file_defect, retry_deferred) logs `selfheal_skip why=probe` for it and files nothing; deleting pending rows by hand
# did not hold, because a deferred retry refiled the probe later.
PROBE_RE = re.compile(r"^\s*(?:\[selfheal-probe\]|Live test of a detector\b)", re.I)


def hermes_home(home: Optional[Path] = None) -> Path:
    """An explicit `home` (authoritative), else core get_hermes_home(): task override > HERMES_HOME > platform default."""
    if home:
        return Path(home).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        env = os.environ.get("HERMES_HOME", "").strip()
        return Path(env).expanduser() if env else Path.home() / ".hermes"


def _ro_state(path: Path):
    """Read-only state.db connection: mode=ro, else (a WAL db whose -wal/-shm is absent) mode=rw + query_only."""
    import sqlite3
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return db
    except sqlite3.Error:
        db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
        db.execute("PRAGMA query_only=ON")
        return db


def pause_gap_s(session_id: str, now: float, home: Optional[Path] = None) -> Optional[float]:
    """Seconds an update pause lasted: `now` minus the session's last assistant/tool row at or before `now` in
    <HERMES_HOME>/state.db, i.e. when the paused turn really stopped. None when unknown (no such row, unreadable db).
    The coach file's `updated` is no substitute: it is written at turn start and end only, so a long turn that ended
    by pausing looks like a pause as long as the whole turn (gateway-833d39a686)."""
    import sqlite3
    path = hermes_home(home) / "state.db"
    if not session_id or not path.is_file():
        return None
    try:
        db = _ro_state(path)
        try:
            row = db.execute("SELECT max(timestamp) FROM messages WHERE session_id=? AND role IN ('assistant','tool') "
                             "AND timestamp <= ?", (session_id, now)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    ts = row[0] if row else None
    return max(0.0, now - float(ts)) if isinstance(ts, (int, float)) else None


def is_pause_note(user_text) -> bool:
    """Core's update-pause note (a planned pause), as opposed to its crash-recovery note."""
    return is_resume_note(user_text) and PAUSE_NOTE_MARK in clean_user(user_text)[:400]


def first_user_message(session_id: str, home: Optional[Path] = None) -> Optional[str]:
    """The session's first stored user message (cleaned) from <HERMES_HOME>/state.db; None when the session has no
    user row on this host or the db cannot be read (fail-open: the caller treats that as not a probe)."""
    if not session_id:
        return None
    import sqlite3
    path = hermes_home(home) / "state.db"
    if not path.is_file():
        return None
    try:
        db = _ro_state(path)
        try:
            row = db.execute("SELECT content FROM messages WHERE session_id=? AND role='user' "
                             "ORDER BY timestamp, id LIMIT 1", (session_id,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    content = row[0]
    try:
        parsed = json.loads(content) if isinstance(content, str) and content[:1] == "[" else None
        if isinstance(parsed, list):
            content = parsed
    except ValueError:
        pass
    return clean_user(content)


def probe_session(session_id: str, ask: str = "", home: Optional[Path] = None) -> bool:
    """True when the session is a deliberate detector probe: its first stored user message starts with the marker,
    or (no user row stored yet, e.g. the first turn's hook runs before the write) the turn's own ask does."""
    first = first_user_message(session_id, home)
    if first is None:
        first = clean_user(ask) if ask else ""
    return bool(PROBE_RE.match(first or ""))


def probe_defect(d: dict) -> bool:
    """A defect payload from a probe. The session lookup reads only THIS host's state.db, so a payload about a session
    on another host (the hub's ask-gate reading MacBook digests) carries its own evidence (agent-43f47d4029): an
    explicit `probe` flag, the session's `first_user` turn, or a `quote` that is itself the marked opener (also when
    it survives only as the quoted text in `symptom`). Any of them -> probe; else the local session lookup."""
    if d.get("probe"):
        return True
    sym = str(d.get("symptom") or "")
    quoted = sym.split('"', 1)[1] if '"' in sym else ""
    for text in (d.get("first_user"), d.get("quote"), quoted):
        if isinstance(text, str) and text and PROBE_RE.match(clean_user(text) or ""):
            return True
    return probe_session(str(d.get("session_id") or ""))

ADMIT_RE = re.compile(
    r"\b(my mistake|my (?:bad|fault|error)|that was (?:my|a) mistake|i (?:was wrong|missed|forgot|skipped|"
    r"overlooked|misread|didn'?t (?:check|load|verify|notice|read))|you'?re right[,:.]? (?:and|that|i|it)|i (?:should|"
    r"shouldn'?t) have|(?:a|the) bug in|is a bug|was a bug|bug (?:in|where)|work ?around|regress\w*|i told you "
    r"(?:otherwise|wrong)|i (?:said|reported|claimed) .{0,60} (?:but|when) (?:it|that) (?:wasn'?t|was not|isn'?t)|"
    r"(?:that|this) (?:was|is) (?:wrong|incorrect)|sorry)\b", re.I)

THRESHOLD = 0.6

# Pure acknowledgements: never a defect report, never judged.
ACK_RE = re.compile(
    r"^\s*(?:/\S+.*|go|yes|y|yep|yeah|ok(?:ay)?|k|do it|do both|done|thanks?|thank you|ty|sure|proceed|lgtm|"
    r"ship it|sounds good|perfect|great|nice|cool|approved?|go ahead|yes,? do it|👍)\W*$", re.I)

# Turn-end machine signals, computed from the reply and the coach's tool log (no judge). Same definitions as the
# replay's telemetry (api_err / interrupted / repeat): a provider error reply, an interrupted turn, or the same tool
# error three times in one turn.
API_ERR_RE = re.compile(r"not processed|API call failed|provider error|rate limit(?:ed)? .{0,40}(?:retry|exhaust)", re.I)
# A reply IS a provider error only when it opens with the phrase (first line, a lead-in of at most 40 chars such as
# "Sorry — ", no quote mark before it), or it is a short one-line notice whose last sentence is core's "Your message
# was not processed" (agent/turn_facade_lease.py busy/taken-over notices). A finished reply that quotes the notice
# (the agent reporting the fix for it) is not one: provider-1bc7008942 matched 12 such replies on the hub (state.db
# replay), the same class as the INTERRUPT_RE anchoring below.
API_ERR_LEAD = 40
API_NOTICE_MAX = 300
QUOTE_MARK_RE = re.compile(r"[\"`“”«»]|(?<!\w)['‘’]")  # an apostrophe inside a word ("couldn't") is not a quote
NOT_PROCESSED_TAIL_RE = re.compile(r"(?:^|[.!?]\s+)(Your (?:message|request) was not\s+processed\b.*)\Z", re.S)


def provider_error_span(reply: str) -> Optional[str]:
    """The matched provider-error text when the reply itself is the error, else None (see API_ERR_LEAD)."""
    t = (reply or "").strip()
    first = t.split("\n", 1)[0]
    m = API_ERR_RE.search(first, 0, API_ERR_LEAD + 120)
    if m and m.start() <= API_ERR_LEAD and not QUOTE_MARK_RE.search(first[:m.start()]):
        return first[:m.end() + 100]
    if len(t) <= API_NOTICE_MAX and "\n" not in t and not QUOTE_MARK_RE.search(t):
        n = NOT_PROCESSED_TAIL_RE.search(t)
        if n:
            return n.group(1)[:160]
    return None


# Core's interrupt placeholders, matched at the START of the reply only (core writes them as the whole reply; a
# finished reply that quotes one mid-sentence is not an interruption). The tool log carries every command's arguments
# (a grep for "Turn stopped" is not an interruption) and errors of cancelled tools the agent recovered from, so it is
# read only for a turn that produced no reply and ended on a cancelled tool (gateway-a8b8d01aa4).
INTERRUPT_RE = re.compile(r"^[\W_]{0,8}(?:No reply:\s*)?(?:Operation interrupted|(?:Session )?turn lease lost|"
                          r"Turn stopped by|the turn stopped while a tool result was still pending)", re.I)
CANCEL_LINE_RE = re.compile(r"^\s*<-\s*ERROR\b.*(?:Tool execution cancelled|Operation interrupted|turn lease lost)",
                            re.I)
# Core's crash-recovery note (tui_gateway/session_history.py _AUTO_CONTINUE_NOTE_PREFIX): the next user turn after a
# backend stopped mid-turn. The only trace such a stop leaves, since the cut turn never reaches post_llm_call.
# Core's failed-turn boundary notices (agent/turn_failure_copy.py), matched exactly so a reply quoting one stays a
# reply. agent-c46bf512ae: 15 turns ended on one ("This turn did not complete...") and no turn-end signal fired.
FAILED_TURN_NOTICES = frozenset({
    "Your request was not processed. Send it again if you still want me to carry it out.",
    "This turn did not complete. Some actions may already have run; verify their effects before resending.",
    "Hermes restarted before this turn finished. It continues automatically once the backend is back; "
    "send it again only if nothing happens within a few minutes.",
})
RESUME_NOTE_PREFIX = "[System note: Your previous turn was interrupted mid-run"
# Core's update-pause note (tui_gateway/update_pause.py pause_note) shares that prefix but marks a PLANNED pause: the
# turn stopped at a step boundary so a Hermes update could land, then continued by itself (gateway-9aaf151a15).
PAUSE_NOTE_MARK = "paused at a step boundary for a Hermes update"
# The update's land target: a pause that continued within this is working as designed; a longer one failed.
PAUSE_LAND_S = 30 * 60
ERR_LINE_RE = re.compile(r"(?:<-|->)\s*ERROR\b\s*(.*)")
# agent-1f57a3258a: "remote gateway temporarily unreachable" (a sleeping MacBook: cross_task said `"retryable": true`,
# "the gateway was unavailable ... Retry in a few minutes") is not an agent defect, however often a turn hits it.
# agent_rules.observe marks such an error line with GATEWAY_DOWN_MARK (its 120-char cut drops flag and cause); an
# untruncated line needs both the retryable flag and the cause. Non-retryable errors (an unknown gateway alias, a missing
# task) and other retryable ones (rate limits) still count. Keep in step with agent_rules.py.
GATEWAY_DOWN_MARK = "(retryable: gateway unavailable)"
GATEWAY_DOWN_RE = re.compile(r"RemoteBackendDownError|is down \(connect failed|the gateway was unavailable")
RETRYABLE_RE = re.compile(r'"retryable"\s*:\s*true\b')


def gateway_down_error(text: str) -> bool:
    t = str(text or "")
    return GATEWAY_DOWN_MARK in t or bool(RETRYABLE_RE.search(t) and GATEWAY_DOWN_RE.search(t))

COMPLAINT_SYS = """You watch a power user's chats with his AI agent system "Hermes" (the coding/ops agent, its Desktop
app, plugins, skills, cron jobs, Kanban task cards and workers, chat bots, gateways, model providers) and the products
Hermes builds and runs for him (e.g. a home assistant). You see the agent's previous final message, the tool log of
its previous turn, and the user's new message.

Score p_defect (0-1): the user's message reveals that the agent or Hermes did something it should not have, or failed
to do something it should have known to do, so a self-correcting system should root-cause it. Count:
- corrections of a wrong action, assumption, claim, interpretation, scope or choice (including "use X not Y", "you can
  do X via Y", "I said Z", "don't do that", "that's not what I asked"),
- reports that a claimed fix/build/deploy/change doesn't work, isn't visible, or regressed ("still broken", "nope",
  "I don't see it", "it's back"),
- objections to asks or handoffs the agent could have done itself ("you have ssh", "you buy it", "run it for me", "stop
  asking"), to re-asked questions, or to manual steps it gave him,
- frustration or "why did you / what went wrong / again / I told you",
- reports that Hermes itself (Desktop app, sidebar, panels, plugin, tool, cron, Kanban card state, session status,
  gateway, bot) or a product Hermes built and runs for him (his home assistant, its apps, pods, watch/phone clients,
  hub, feeds, Inbox) misbehaved, lost data, shows wrong state or did the wrong thing,
- requests to fix or root-cause the agent's or Hermes's own behavior,
- a bare nudge ("resume", "what's going on", "are you stuck", "status?") when the previous turn did not end in a real
  answer: it was interrupted, errored, stopped mid-work, went silent after many tool calls, or promised work that never
  came. A nudge after a complete answer is low. A previous message that merely looks cut off may have been clipped
  for length before you saw it; that alone is not a defect. A digest has no tool log: never claim "no tool calls" (or
  that the agent did not act) unless "Tool calls after the agent's last text" says 0; when it is unknown, say so.
Do NOT count (low, <0.3): brand-new requests or feature asks with no failure; design taste or wording preferences the
agent could not have predicted; answers to the agent's legitimate questions; approvals; status questions with no sign
of a problem; problems in third-party services or the outside world that Hermes neither caused nor should have
prevented (kind "external"); bugs in research or demo code that ARE the task he just gave the agent.

kind: agent_fault (how the agent worked: claims, asks, verification, instruction-following, scope), hermes_bug
(Hermes or a product it built/runs misbehaved), external, none.
component: one of agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb (his home-assistant
product), other.
symptom: <=10 words naming what is wrong, neutral words, no names (e.g. "asked user to run terminal command").

Return ONLY JSON: {"p_defect": p, "kind": "...", "component": "...", "symptom": "..."}"""

# Second first-stage opinion: the narrower wording (only Hermes itself, product bugs reported as new issues score low).
# Averaging it with COMPLAINT_SYS cuts false alarms on product bug reports without losing the agent-fault catches.
_STRICT_TEMPLATE = """You watch a power user's chats with his AI agent system "Hermes" (the coding/ops agent, its Desktop
app, plugins, skills, cron jobs, Kanban task cards and workers, chat bots, gateways, model providers) and the products
Hermes builds and runs for him (e.g. a home assistant). You see the agent's previous final message, the tool log of
its previous turn, and the user's new message.

Score p_defect (0-1): the user's message reveals that the agent or Hermes did something it should not have, or failed
to do something it should have known to do, so a self-correcting system should root-cause it. Count:
- corrections of a wrong action, assumption, claim, interpretation, scope or choice (including "use X not Y", "you can
  do X via Y", "I said Z", "don't do that", "that's not what I asked"),
- reports that a claimed fix/build/deploy/change doesn't work, isn't visible, or regressed ("still broken", "nope",
  "I don't see it", "it's back"),
- objections to asks or handoffs the agent could have done itself ("you have ssh", "you buy it", "run it for me", "stop
  asking"), to re-asked questions, or to manual steps it gave him,
- frustration or "why did you / what went wrong / again / I told you",
- reports that Hermes itself (Desktop app, sidebar, panels, plugin, tool, cron, Kanban card state, session status,
  gateway, bot) misbehaved, lost data or shows wrong state,
- requests to fix or root-cause the agent's or Hermes's own behavior,
{strict_extra}- a bare nudge ("resume", "what's going on", "are you stuck", "status?") when the previous turn did not end in a real
  answer: it was interrupted, errored, stopped mid-work, went silent after many tool calls, or promised work that never
  came. A nudge after a complete answer is low. A digest has no tool log: never claim "no tool calls" (or that the
  agent did not act) unless "Tool calls after the agent's last text" says 0; when it is unknown, say so.
Do NOT count (low, <0.3): brand-new requests or feature asks; design taste or wording preferences the agent could not
have predicted; answers to the agent's legitimate questions; approvals; status questions with no sign of a problem;
bugs in his products or the outside world that he reports as a NEW issue with no sign the agent's or Hermes's own work
caused them or should have prevented them (score those p_defect low and set kind "product_bug").{strict_product}

kind: agent_fault (how the agent worked: claims, asks, verification, instruction-following, scope), hermes_bug
(Hermes software/infra misbehaved), product_bug (see above), none.
component: one of agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb (his home-assistant
product), other.
symptom: <=10 words naming what is wrong, neutral words, no names (e.g. "asked user to run terminal command").

Return ONLY JSON: {"p_defect": p, "kind": "...", "component": "...", "symptom": "..."}"""

# Candidate 2 (ask-gate wk 10-12): "root cause it, and fix the pluggin" after a reply that described the account_usage
# plugin bug scored 0 on 3/3 strict runs (read as a new request); the Alpha rename that left the bot named Hermes was
# called product_bug although the agent's own rename caused it.
STRICT_FIX_REQUEST = """- a request to fix or root-cause a Hermes defect that the agent's previous message (or its reply to this
  message) describes, even when worded as a new task ("root cause it, and fix the plugin"),
- a problem in his product that the agent's own earlier work in this session caused or left half-done (e.g. a rename
  it made that missed a place),
"""
# Candidate A (t_eedc20dc): the strict stage labelled red-mark sessions and 'fix the pluggin' product_bug.
STRICT_NEVER_PRODUCT = """
Hermes itself (Desktop app, sidebar, panels, plugins, Kanban, gateway, bots, cron) is never a product_bug: a report
that it misbehaves is hermes_bug."""


def strict_sys(fix_request: bool = True, never_product: bool = True) -> str:
    return (_STRICT_TEMPLATE.replace("{strict_extra}", STRICT_FIX_REQUEST if fix_request else "")
            .replace("{strict_product}", STRICT_NEVER_PRODUCT if never_product else ""))


COMPLAINT_STRICT_SYS = strict_sys()

AUDIT_SYS = """You audit an AI agent's turn right as it ends, for a demanding power user who wants the agent to
self-serve, verify on his real path, and fix its own mistakes. You see his request, the turn's tool log (errors marked
ERROR) and the agent's final message. Judge only from that evidence.

Score p_defect (0-1): this turn shows a defect in the agent or its system that should be root-caused. High (0.8+):
- self-admission: the agent says it was wrong, made a mistake, missed or skipped something, told him something untrue,
  or found a bug in Hermes or its tools while doing something else (even if it fixed the symptom);
- unhandled_failure: the log shows errors, aborts, repeated failures or an empty/partial result that the final message
  hides, glosses over or leaves unresolved, or the message itself is an error / empty after many tool calls;
- needless_handoff: it asks him to do or decide something it could do itself (shell, ssh, sudo via a user-terminal
  tool, browser, credential stores), re-asks something answered, or hands him an infeasible step;
- contradiction: the message contradicts the tool log or itself (claims a check that never ran).
Low (<0.3): honest status reports that state plainly what is unverified or failed and what happens next; a bug that IS
the task he gave (root cause + fix report); apologies with no defect; external outages it surfaces clearly; legitimate
questions about his preferences; a disclosed scope limit that the request itself allows or asks for (a coverage row
marked partial with its reason and a substitute check, a 'not reviewed' / 'what I did not check' list, an unmet
requirement the request puts out of scope) is an honest status report, not a self-admission, unless the agent hides
it or its verdict depends on the part it skipped.
kind scratch_iteration (not a defect): the only slip admitted is in a throwaway helper written this turn only to
gather evidence (an experiment, probe, fake server, verify or mutation script), which its own run exposed and which
was fixed, replaced or set aside before the report. A wrong claim, a missed or skipped step, or a helper that acted on
real state (landed, pushed, stopped, deleted) or watched or judged real runs or jobs (a waiter or poll that reported
done), stood in for a Hermes tool that should exist, was pointed out by him or a reviewer, or that a claim, deliverable
or verdict still rests on, is an admission at full score.

component: agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb, other.
symptom: <=10 words naming what is wrong, neutral words, no names.

Return ONLY JSON: {"p_defect": p,
"kind": "admission|unhandled_failure|needless_handoff|contradiction|scratch_iteration|none",
"component": "...", "symptom": "..."}"""


def clean_user(text) -> str:
    if isinstance(text, list):
        parts = []
        for p in text:
            if isinstance(p, dict):
                if p.get("type") in ("image_url", "image", "input_image"):
                    parts.append("[screenshot]")
                elif p.get("text"):
                    parts.append(str(p["text"]))
        text = " ".join(parts)
    t = str(text or "")
    m = OOB_RE.match(t)
    if m:
        t = m.group(1)
    t = TRAILER_RE.sub("", t)
    return t.strip()


HANDOFF_RE = re.compile(
    r"\b(?:you(?:'ll| will)? (?:need|have) to|run (?:this|these|the following)|paste|in your terminal|can you|could you|"
    r"please (?:run|open|tap|click|log ?in|sign ?in|approve|check|confirm|enable|install)|let me know|once you|"
    r"reply with|do you want|should i|want me to|on your (?:phone|mac|iphone|end)|your call|needs? you|"
    r"for you to|waiting on you|manual step)\b", re.I)


def synthetic_user_row(d: dict) -> bool:
    """A user-text finding (backstop/complaint) whose quote is Hermes-written text, not David's words. Machine-signal
    sources (cron, kanban, ...) quote logs, so a tagged log line there is still a real defect."""
    if (d or {}).get("source") not in ("backstop", "complaint"):
        return False
    q = clean_user(d.get("quote") or "")
    return bool(q) and not PROBE_RE.match(q) and bool(SYNTHETIC_RE.match(q))


def prefilter(user_text) -> bool:
    """Worth judging: any real user message except pure acknowledgements, slash commands and Hermes-written text.
    (The v1 complaint regex missed corrections like "you buy the credits" and nudges like "whats going on"; one cheap
    judge call per real user turn is the price of recall.)"""
    t = clean_user(user_text)[:2000]
    if not t or SYNTHETIC_RE.match(t):
        return False
    return not ACK_RE.match(t[:200])


def _err_lines(tool_log) -> list:
    return [ln for ln in (tool_log or []) if isinstance(ln, str) and ERR_LINE_RE.search(ln)]


def admission_prefilter(reply: str, tool_log=None) -> bool:
    r = (reply or "")[:6000]
    return bool(ADMIT_RE.search(r) or HANDOFF_RE.search(r) or _err_lines(tool_log)
                or (not r.strip() and tool_log))


def machine_signal(final_reply: str, tool_log=None) -> Optional[dict]:
    """Turn-end telemetry, no judge: a provider-error reply, an interrupted turn, or one tool error repeated 3x."""
    r = (final_reply or "")[:400]
    log = [str(x) for x in (tool_log or [])]
    if (final_reply or "").strip() in FAILED_TURN_NOTICES:
        v = {"p_defect": 0.9, "kind": "failed_turn", "component": "gateway",
             "symptom": "turn ended on a failed-turn notice (no reply)"}
    elif (span := provider_error_span(final_reply)):
        v = {"p_defect": 0.9, "kind": "api_error", "component": "provider", "symptom": "turn failed with provider error",
             "evidence": span}
    elif INTERRUPT_RE.search(r) or (not r.strip() and log and CANCEL_LINE_RE.search(log[-1])):
        v = {"p_defect": 0.9, "kind": "interrupted", "component": "gateway",
             "symptom": "turn interrupted before finishing"}
    else:
        rep = repeated_tool_error(log)
        if not rep:
            return None
        # The id is the tool plus the normalized error (agent-6eb3a51e94: one fixed label lumped every repeated error
        # into one defect, and the card only saw the reply tail).
        v = {"p_defect": 0.85, "kind": "repeat_failure", "component": "agent",
             "symptom": f"repeated {rep['tool']} error: {rep['error'][:60]}".rstrip(),
             "tool": rep["tool"], "error": rep["error"], "evidence": rep["evidence"], "details": rep["details"]}
    return {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": "signal"}


# Lifecycle-guard false refusals. Core's terminal guard (`tools/terminal_tool_guards.py`) and cron-create guard
# (`cron/lifecycle_guard.py` check_gateway_lifecycle) fail CLOSED when a script the command executes cannot be scanned
# (live SQLite, oversized, walk budget, cloud path, device...) and say so in a refusal that names the reason. The text
# found no lifecycle command: such a refusal blocks work that is not known to be dangerous. A TRUE lifecycle refusal
# ("cannot restart, stop, or uninstall the gateway" / "cron job contains a gateway lifecycle command") never matches.
# 22 of them in one MacBook chat went unnoticed until a person counted by hand: each agent rewrote its command.
GUARD_TOOLS = ("terminal", "cronjob_manage", "cronjob")  # cronjob_manage = core registry name; cronjob = legacy
GUARD_REFUSAL_HEADS = {
    "Blocked: the lifecycle guard could not scan this command or referenced script: ": "command",  # terminal
    "Blocked: the lifecycle guard could not scan this cron job or referenced script: ": "cron job",  # cron create
}
GUARD_REFUSAL_TAIL_RE = re.compile(r"\.\s+Nothing in the (?:command|job) is known to contain a gateway lifecycle command")
GUARD_REFUSAL_MARK = "the lifecycle guard could not scan this"  # the hook's cheap pre-check only
GUARD_FP_PREFIX = "lifecycleguard-"
GUARD_SCAN_CAP = 1 << 20  # core _MAX_REFERENCED_SCRIPT_BYTES: a file above it is oversized by itself
GUARD_DEDUPE_S = 600  # one (session, command, shape) refused again within this long is one occurrence (#113944 loops)
_GUARD_PATH_RE = re.compile(r"`([^`]+)`")
_GUARD_BUDGET_RE = re.compile(r"scan budget was exhausted \(([a-z ]+?) at depth")


def _guard_error(tool_name: str, result) -> Optional[str]:
    """The refusal text when `result` IS the guard's own refusal, else None. Anchored to the producing envelope so a
    tool that merely quotes the refusal (read_file of a test, kanban_show of a card, pytest output, a log grep) never
    counts: terminal = core `_blocked_json` ({"output": "", "exit_code": 1, "error": <refusal>, ...}); cron create =
    `tool_error(str(e))` ({"error": <refusal>, "success": false})."""
    if tool_name not in GUARD_TOOLS:
        return None
    obj = result
    if isinstance(result, str):
        try:
            obj = json.loads(result)
        except ValueError:
            return None
    if not isinstance(obj, dict) or not isinstance(obj.get("error"), str):
        return None
    if tool_name == "terminal" and (obj.get("exit_code") != 1 or obj.get("output") not in ("", None)):
        return None
    err = obj["error"]
    return err if any(err.startswith(h) for h in GUARD_REFUSAL_HEADS) else None


def _oversized_kind(path: Optional[str]) -> str:
    """`file` when the named file alone exceeds the scan cap, `walk` when it fit the cap but not the walk's remaining
    bytes (one message, two causes, two fixes), `local` when it cannot be stat'ed here."""
    try:
        size = os.stat(os.path.expanduser(path)).st_size if path else None
    except OSError:
        size = None
    return "local" if size is None else "file" if size > GUARD_SCAN_CAP else "walk"


def guard_refusal_shape(reason: str, path_kind: Optional[str] = None) -> str:
    """Reason class + subkind of a fail-closed refusal: sqlite | oversized/<file|walk|local|remote> |
    budget/<text|paths|remote reads> | depth | other/<cloud|nonregular|unreadable|unknown>. The path is evidence, never
    part of the shape (`path_kind` = _oversized_kind of it, read on the host that was refused)."""
    r = reason or ""
    if "is a SQLite database open" in r:
        return "sqlite"
    if "read from the backend exceeds the scan cap" in r:
        return "oversized/remote"
    if "larger than the scan cap" in r or "exceeds the scan cap" in r:
        return "oversized/" + (path_kind or "local")
    m = _GUARD_BUDGET_RE.search(r)
    if m or "scan budget was exhausted" in r:
        return "budget/" + (m.group(1).strip() if m else "unknown")
    if "scan depth limit was reached" in r:  # core names the depth cap once its owner card lands (see the README)
        return "depth"
    if "cloud-synced" in r:
        return "other/cloud"
    if "is not a regular file" in r:
        return "other/nonregular"
    if "could not be read" in r:
        return "other/unreadable"
    return "other/unknown"


def guard_occurrence_new(store, session_id: str, command: str, shape: str, now: Optional[float] = None) -> bool:
    """False when this (session, command, shape) was refused within GUARD_DEDUPE_S: an agent retrying the same refused
    command is one occurrence, not one card comment (and one hub forward) per retry. Fail-open (True)."""
    now = time.time() if now is None else now
    key = hashlib.sha1(f"{session_id}\0{command}\0{shape}".encode()).hexdigest()[:20]
    path = Path(store.root) / "guard_refusals.json"
    try:
        seen = json.loads(path.read_text())
    except Exception:
        seen = {}
    if not isinstance(seen, dict):
        seen = {}
    seen = {k: t for k, t in seen.items() if isinstance(t, (int, float)) and now - t < GUARD_DEDUPE_S}
    fresh = key not in seen
    seen[key] = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen))
        os.replace(tmp, path)
    except OSError:
        pass
    return fresh


def guard_refusal_signal(tool_name: str, args=None, result=None) -> Optional[dict]:
    """A lifecycle-guard fail-closed refusal of a command whose scanned text held no lifecycle command, as a self-heal
    hit (one fingerprint per shape, every occurrence reaches the lane), else None."""
    err = _guard_error(tool_name, result)
    if not err:
        return None
    head = next(h for h in GUARD_REFUSAL_HEADS if err.startswith(h))
    where, rest = GUARD_REFUSAL_HEADS[head], err[len(head):]
    tail = GUARD_REFUSAL_TAIL_RE.search(rest)
    reason = " ".join((rest[:tail.start()] if tail else rest).split())
    path = _GUARD_PATH_RE.search(reason)
    oversized = "scan cap" in reason and "read from the backend" not in reason
    shape = guard_refusal_shape(reason, _oversized_kind(path.group(1)) if path and oversized else None)
    a = args if isinstance(args, dict) else {}
    cmd = str(a.get("command") or a.get("script") or a.get("prompt") or "")
    suffix = Path(path.group(1)).suffix.lower() if path else ""
    refusal = " ".join(err.split())
    details = (f"tool {tool_name or '?'} ({where}) refused: {refusal[:600]}\n"
               f"shape {shape}; path {path.group(1) if path else 'none named'} (suffix {suffix or 'none'})\n"
               f"command: {cmd[:600]}\n"
               "The guard failed closed: it could not scan a file it thinks the command executes, and nothing it did "
               "scan holds a lifecycle command. Source: core cron/lifecycle_guard.py `_unreadable_reason` / "
               "`_budget_exhausted` / `_refuse_unreadable`, caller tools/terminal_tool_guards.py (terminal) or "
               "check_gateway_lifecycle (cron create). Reproduce under the core interpreter with "
               "`from cron.lifecycle_guard import scan_gateway_lifecycle; scan_gateway_lifecycle(<command>, cwd=<cwd>)`. "
               "Decide whether the file is really executed (a false refusal: fix the reference walk) or the cap is "
               "too tight; add a core regression test for this shape.")
    sym = f"lifecycle guard fail-closed refusal ({shape})"
    return {"p_defect": 0.9, "kind": "guard_refusal", "component": "gateway", "symptom": sym,
            "fingerprint": GUARD_FP_PREFIX + hashlib.sha1(shape.encode()).hexdigest()[:12], "source": "signal",
            "shape": shape, "tool": tool_name, "evidence": f"{tool_name or 'tool'}: {reason[:240]}",
            "details": details, "every_occurrence": True, "comment_open_card": True}


_TOOL_CALL_RE = re.compile(r"^\s*->\s*([\w.:-]+)\(")
_ERR_NORM = [
    (re.compile(r"\bt_[0-9a-f]{6,}\b", re.I), " "),  # card ids
    (re.compile(r"\b\d{8}_\d{6}_[0-9a-z]+\b", re.I), " "),  # session ids
    (re.compile(r"(?<![\w/])(?:~|\.{1,2})?/[\w.@+~-]+(?:/[\w.@+~-]*)*"), " "),  # paths (not "I/O")
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b", re.I), " "),  # hex shas/ids
    (re.compile(r"\d+"), " "),
    (re.compile(r"\(\s*\)|\[\s*\]"), " "),
    (re.compile(r"[\\\"'`]+\s*[\\\"'`]*"), " "),  # quotes left around a stripped path
    (re.compile(r"\s+"), " "),
]


_JSON_FIELD_RE = re.compile(r'"(error|message|stderr|output)"\s*:\s*"((?:[^"\\]|\\.)*)')


def unwrap_tool_error(err: str) -> str:
    """The error text inside a tool's JSON result (`{"output": "Error: ...", "exit_code": 1}`, often truncated by the
    turn log so json.loads fails): the first non-empty error/message/stderr/output string, else the text itself."""
    t = str(err or "").strip()
    if not t.startswith("{"):
        return t
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            for k in ("error", "message", "stderr", "output"):
                if isinstance(obj.get(k), str) and obj[k].strip():
                    return obj[k].strip()
    except ValueError:
        pass
    found = {k: v for k, v in reversed(_JSON_FIELD_RE.findall(t)) if v.strip()}
    for k in ("error", "message", "stderr", "output"):
        if k in found:
            return found[k].replace('\\"', '"').replace("\\n", " ").strip()
    return t


def normalize_error(err: str) -> str:
    """Error text with the parts that vary between occurrences of one error (ids, paths, hex, numbers) removed."""
    t = unwrap_tool_error(err)
    for rx, sub in _ERR_NORM:
        t = rx.sub(sub, t)
    return t.strip(" :,;.-")


def repeated_tool_error(log) -> Optional[dict]:
    """The first (tool, normalized error) pair seen 3+ times in one turn's tool log, with the call and error lines.
    -> {tool, error, count, call, line, evidence, details} or None."""
    seen: dict = {}
    tool, call = "tool", ""
    calls: list = []  # [call line, errored?] per tool call, in order
    for ln in log or []:
        ln = str(ln)
        m = _TOOL_CALL_RE.match(ln)
        if m:
            tool, call = m.group(1), ln.strip()
            calls.append([call, False])
            continue
        e = ERR_LINE_RE.search(ln)
        if not e:
            continue
        if calls:
            calls[-1][1] = True
        if gateway_down_error(e.group(1)):  # agent-1f57a3258a: an unreachable remote gateway, not a broken tool
            continue
        norm = normalize_error(e.group(1)[:200])
        if not norm:
            continue
        rec = seen.setdefault((tool, norm), {"tool": tool, "error": norm, "count": 0, "calls": []})
        rec["count"] += 1
        rec["calls"].append((call, e.group(1).strip()))
        rec["call"], rec["line"] = call, e.group(1).strip()
        rec["last_idx"] = len(calls) - 1

    # agent-7f4144a961: a RED/GREEN test cycle (the same test command failed 3x while the agent edited, then passed)
    # is not a stuck loop. Drop a group when the identical call ran again after its last failure and got no error.
    def _recovered(r) -> bool:
        return any(c == r["call"] and not err for c, err in calls[r.get("last_idx", len(calls)) + 1:])

    rep = next((r for r in seen.values() if r["count"] >= 3 and not _recovered(r)), None)
    if not rep:
        return None
    rep.pop("last_idx", None)
    rep["evidence"] = (f"{rep['tool']} failed {rep['count']}x with the same error: {rep['line'][:160]} "
                       f"(last call: {rep['call'][:120]})")
    rep["details"] = "\n".join(f"{c[:200]}\n   <- ERROR {x[:200]}" for c, x in rep.pop("calls")[-4:])
    return rep


# Words too generic to tie a card to a signal hit, and tools too generic to count on their own.
_RELEVANCE_STOP = set("error errors failed failure refused cannot could unable found exit code tool repeated turn "
                      "with before after while finishing stopped open card file line none null true false call "
                      "result".split())
_GENERIC_TOOLS = {"tool", "terminal", "execute_code", "read_file", "write_file", "patch", "search_files", "tool_call",
                  "delegate_task", "browser_exec", "process_manage"}


def signal_terms(d: dict) -> set:
    """Words a card must mention to own this machine-signal defect: the tool (unless generic) and the error's
    content words, else the symptom's content words."""
    text = d.get("error") if d.get("kind") == "repeat_failure" else d.get("symptom")
    terms = {w for w in re.findall(r"[a-z][a-z0-9_]+", str(text or "").lower())
             if len(w) >= 4 and w not in _RELEVANCE_STOP and w not in _STOP}
    if d.get("tool") and str(d["tool"]).lower() not in _GENERIC_TOOLS:
        terms.add(str(d["tool"]).lower())
    return terms


def card_relevant(card: dict, terms: set) -> bool:
    text = " ".join(str(card.get(k) or "") for k in ("title", "body")).lower()
    return bool(text) and any(t in text for t in terms)


# Words that recur in most self-heal and fix-card text, so sharing them says nothing about the subject.
# agent-e5b6dcf428: "agent", "check" and "which" tied a coach report_only signal to a Kanban owner-death fix card.
_SUBJECT_GENERIC = set("agent agents check checks coach reply replies what which that this there here have then "
                       "than just only also into your about would should could every other same some kanban hermes "
                       "session sessions nudge outcome issue problem fix fixed".split())
SUBJECT_SYMPTOM_CHARS = 80  # self-heal card titles carry the symptom truncated near 100 chars
# Sources where the origin session's next card answers a user complaint raised in that same session. For every other
# source (machine signals, the digest backstop) the session's next card is only its next piece of work.
SESSION_RESPONSE_SOURCES = ("complaint", "admission")


def _norm_text(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def _subject_words(text) -> set:
    return {_stem(w) for w in re.findall(r"[a-z][a-z0-9_]+", str(text or "").lower())
            if len(w) >= 4 and w not in _RELEVANCE_STOP and w not in _STOP and w not in _SUBJECT_GENERIC}


def card_names_defect(card: dict, d: dict, fp: Optional[str] = None) -> bool:
    """Subject evidence that `card` is about defect `d`, not just made by the same session: its title, body or
    idempotency key names the fingerprint or the symptom (first SUBJECT_SYMPTOM_CHARS chars), the signal's
    non-generic tool or its normalized error, or at least max(2, a third) of the symptom's distinctive words."""
    fp = fp or d.get("fingerprint")
    text = _norm_text(" ".join(str(card.get(k) or "") for k in ("title", "body", "idempotency_key")))
    if not text:
        return False
    if fp and fp.lower() in text:
        return True
    for phrase in (d.get("symptom"), d.get("error") if d.get("kind") == "repeat_failure" else None):
        needle = _norm_text(phrase)[:SUBJECT_SYMPTOM_CHARS].strip()
        if len(needle) >= 12 and needle in text:
            return True
    tool = str(d.get("tool") or "").lower()
    if tool and tool not in _GENERIC_TOOLS and re.search(r"(?<![a-z0-9_])%s(?![a-z0-9_])" % re.escape(tool), text):
        return True
    want = _subject_words(d.get("error") if d.get("kind") == "repeat_failure" else d.get("symptom"))
    if not want:
        return False
    # Word overlap counts against the title (and key) only: a 4000-char body shares a couple of words with almost
    # any symptom ("reported", "tests"), which once tied 4 unrelated defects to one user-steps card.
    head = _subject_words(" ".join(str(card.get(k) or "") for k in ("title", "idempotency_key")))
    return len(want & head) >= max(2, -(-len(want) // 3))


def is_resume_note(user_text) -> bool:
    """Core's resume note (crash recovery or update pause): a machine-written turn, never a user complaint."""
    return clean_user(user_text).lstrip().startswith(RESUME_NOTE_PREFIX)


def resume_signal(user_text, gap_s: Optional[float] = None, stop: Optional[dict] = None) -> Optional[dict]:
    """A user turn that is core's crash-recovery note: the backend stopped mid-turn and Hermes resumed the chat.
    `gap_s` = seconds the chat stood still (None when unknown); for a pause note, since the paused turn stopped
    (pause_gap_s), never since it started. `stop` = stop_cause() for a crash note (with_stop_cause)."""
    t = clean_user(user_text)
    if not is_resume_note(t):
        return None
    if PAUSE_NOTE_MARK in t[:400]:
        if gap_s is None or gap_s <= PAUSE_LAND_S:
            return None  # a planned update pause that continued in time
        v = {"p_defect": 0.85, "kind": "interrupted", "component": "gateway",
             "symptom": "update pause did not continue in time"}
    else:
        v = {"p_defect": 0.9, "kind": "interrupted", "component": "gateway", "symptom": CRASH_SYMPTOM}
    hit = {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": "signal",
           "gap_s": None if gap_s is None else int(gap_s)}
    return with_stop_cause(hit, stop) if stop is not None and is_crash_hit(hit) else hit


# ── gateway-9aaf151a15 (came back): WHY the backend stopped ──
# Core's crash note names no cause, so every stop shared one fingerprint: once one cause was fixed (fe419c4: update
# pauses), the next stop from any other cause read as "the fix didn't hold". The evidence lives only on the host that
# ran the turn, so it is read there, when the note is detected, over the window between the cut turn's start and the
# resume. First match in STOP_CAUSES order names the cause; "unknown" keeps the old fingerprint.
CRASH_SYMPTOM = "turn interrupted (backend stopped)"
STOP_CAUSES = ("core update", "plugin reload", "gateway shutdown", "machine slept")
STOP_EVIDENCE_MAX = 600
STOP_WINDOW_DEFAULT_S = 6 * 3600  # gap unknown: look back this far
STOP_SLACK_S = 60
_SYNC_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[[^\]]*\] (mods-reload: .*)$")
_PMSET_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})\s+(Sleep|DarkWake|Wake)\s+(.*)$")


def is_crash_hit(hit: Optional[dict]) -> bool:
    return bool(hit) and hit.get("source") == "signal" and str(hit.get("symptom") or "").startswith(CRASH_SYMPTOM[:-1])


def with_stop_cause(hit: dict, stop: Optional[dict]) -> dict:
    """The crash hit with its stop cause: symptom "turn interrupted (backend stopped: <cause>)" and its own
    fingerprint; an unknown cause keeps CRASH_SYMPTOM and gateway-9aaf151a15."""
    stop = stop or {}
    cause = stop.get("cause") if stop.get("cause") in STOP_CAUSES else "unknown"
    out = {**hit, "stop_cause": cause, "stop_evidence": str(stop.get("evidence") or "")[:STOP_EVIDENCE_MAX],
           "stop_host": stop.get("host") or host_name()}
    out["symptom"] = CRASH_SYMPTOM if cause == "unknown" else f"{CRASH_SYMPTOM[:-1]}: {cause})"
    out["fingerprint"] = fingerprint(out["component"], out["symptom"])
    return out


def _iso_ts(s) -> Optional[float]:
    import datetime as _dt
    try:
        d = _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.astimezone()).timestamp()


def _pmset_log() -> list:
    """macOS power log lines (`pmset -g log`); [] elsewhere or on any failure."""
    import subprocess
    import sys
    if sys.platform != "darwin":
        return []
    try:
        out = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    return [ln for ln in out.splitlines() if " Sleep " in ln or "Wake " in ln]


def _tail_lines(path: Path, max_bytes: int = 4 << 20) -> list:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def stop_cause(home: Optional[Path] = None, since: float = 0.0, until: Optional[float] = None) -> dict:
    """Why the backend on THIS host stopped between `since` and `until` (epoch s): {cause, evidence (<= 600 chars
    of the matched lines, cause first), host, causes (every cause that matched)}. Fail-open: unreadable logs are
    no evidence."""
    import datetime as _dt
    h = hermes_home(home)
    until = time.time() if until is None else until
    found: dict = {c: [] for c in STOP_CAUSES}

    def inside(ts):
        return ts is not None and since <= ts <= until

    try:
        for p in sorted((h / "logs" / "update_receipts").glob("*.json")):
            try:
                r = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(r, dict) or r.get("kind") == "sync":
                continue  # `hermes plugins` sync receipts (pm_*, latest.json) restart nothing
            pre = (r.get("pre_update") or {}).get("short_sha") or "?"
            post = (r.get("post_update") or {}).get("short_sha") or "?"
            if pre == post and not (r.get("gateway_restart") or {}).get("restarted_services"):
                continue  # a failed/no-op update moved no code and restarted nothing
            a, b = _iso_ts(r.get("started_at")), _iso_ts(r.get("finished_at"))
            if inside(a) or inside(b) or (a is not None and b is not None and a <= since <= b):
                found["core update"].append(f"update_receipts/{p.name}: {r.get('started_at')} -> "
                                            f"{r.get('finished_at')} {r.get('outcome') or '?'} {pre} -> {post}")
    except OSError:
        pass
    for ln in _tail_lines(h / "logs" / "hermes-sync.log"):
        m = _SYNC_LINE_RE.match(ln)
        if not m:
            continue
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))  # hermes-sync logs local time
        except (ValueError, OverflowError):
            continue
        if inside(ts):
            found["plugin reload"].append(f"{m.group(1)} {m.group(2)}"[:240])
    for ln in _tail_lines(h / "logs" / "gateway-exit-diag.log"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and inside(_iso_ts(r.get("ts"))):
            extra = f" code={r['code']}" if "code" in r else ""
            found["gateway shutdown"].append(f"gateway-exit-diag {r.get('ts')} {r.get('tag')} pid={r.get('pid')}{extra}")
    lines = _tail_lines(h / "logs" / "gateway-shutdown-diag.log")
    for i, ln in enumerate(lines):
        if ln.startswith("=== shutdown diagnostic") and i + 2 < len(lines) and inside(_iso_ts(lines[i + 2].strip())):
            found["gateway shutdown"].append(f"gateway-shutdown-diag {lines[i + 2].strip()} {ln.strip('= ')}")
    slept = False
    for ln in _pmset_log() or []:
        m = _PMSET_RE.match(ln)
        if not m:
            continue
        try:
            ts = _dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            continue
        if inside(ts):
            slept = slept or m.group(2) == "Sleep"
            found["machine slept"].append(f"pmset {m.group(1)} {m.group(2)}: {m.group(3).strip()}"[:200])
    if not slept:
        found["machine slept"] = []  # wake lines alone are not a sleep inside the window
    causes = [c for c in STOP_CAUSES if found[c]]
    cause = causes[0] if causes else "unknown"
    ev = ""
    for c in causes:
        part = f"{c}: " + " | ".join(found[c])
        ev += ("" if not ev else " || also ") + part
    return {"cause": cause, "evidence": ev[:STOP_EVIDENCE_MAX], "host": host_name(), "causes": causes}


def stop_window(hit: dict, session_id: str, now: float, home: Optional[Path] = None) -> tuple:
    """(since, until) for stop_cause: from when the cut turn last wrote a row (state.db, tighter) or started (the
    hit's gap_s), minus a minute of slack, to now."""
    row_gap = pause_gap_s(session_id, now, home) if session_id else None
    turn_gap = hit.get("gap_s") if isinstance(hit.get("gap_s"), (int, float)) else None
    gaps = [g for g in (row_gap, turn_gap) if g is not None]
    gap = min(gaps) if gaps else STOP_WINDOW_DEFAULT_S
    return now - float(gap) - STOP_SLACK_S, now


_STOP = set("a an the to of in on for and or is are was were be been it its this that with not no by at as from "
            "user users his her he she they them i you we me my your our does do did doesnt didnt isnt wasnt "
            "after before when while again still".split())


def _stem(w: str) -> str:
    s = re.sub(r"(?:ing|ed|s)$", "", w) if len(w) > 4 else w
    return s.rstrip("e") or w


def fingerprint(component: str, symptom: str) -> str:
    """Stable id for one defect: component + sorted content words of the symptom (stemmed crudely), so rewordings
    like "sidebar sessions reshuffle" / "session sidebar reshuffles" collide."""
    words = re.findall(r"[a-z0-9]+", (symptom or "").lower())
    stems = sorted({_stem(w) for w in words if w not in _STOP and len(w) > 1})
    comp = component if component in COMPONENTS else "other"
    key = comp + ":" + " ".join(stems)
    return comp + "-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def _json_objects(raw: str):
    """Every top-level JSON object in `raw`, in order (a flash answer can carry two, or wrap one in a fence)."""
    dec, i, s = json.JSONDecoder(), 0, raw or ""
    while True:
        i = s.find("{", i)
        if i < 0:
            return
        try:
            obj, end = dec.raw_decode(s, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            yield obj
        i = end


def parse(raw: str) -> Optional[dict]:
    """The first JSON object in the answer that carries `p_defect`. An answer in another judge's schema (e.g. the
    reply-check keys) is no opinion (None), never a 0 vote: counting it as 0 sank real catches to the cascade mean."""
    d = next((o for o in _json_objects(raw) if "p_defect" in o), None)
    if d is None:
        return None
    try:
        comp = str(d.get("component") or "other").strip().lower()
        return {"p_defect": max(0.0, min(1.0, float(d.get("p_defect") or 0))),
                "component": comp if comp in COMPONENTS else "other",
                "symptom": str(d.get("symptom") or "").strip()[:120],
                "kind": str(d.get("kind") or "").strip().lower()[:30]}
    except Exception:
        return None


def profile_text() -> str:
    """The private user-environment overlay shared with reply_check (COACH_REPLY_PROFILE or
    <HERMES_HOME>/coach/reply_profile.md): his standing rules and what his agents can reach, so the judge can tell a
    lazy handoff or a rule break from a legitimate one. '' when absent."""
    p = os.environ.get("COACH_REPLY_PROFILE")
    if not p:
        try:
            from hermes_constants import get_hermes_home  # type: ignore
            home = Path(get_hermes_home())
        except Exception:
            home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        p = str(home / "coach" / "reply_profile.md")
    try:
        return strip_output_spec(Path(p).read_text())[:6000]
    except Exception:
        return ""


_OUTPUT_SPEC = re.compile(r"(?im)^[ \t]*(?:return|respond|reply|answer|output)\b[^\n]*\bjson\b[^\n]*(?:\n|$)")


def strip_output_spec(text: str) -> str:
    """The overlay holds rules and environment only. An output-format line in it ("Return ONLY JSON {...}" left over
    from another judge) is appended after the judge's own schema and wins: the self-heal judge answered in the
    reply-check keys and its stages parsed as 0."""
    return _OUTPUT_SPEC.sub("", text or "")


def _with_profile(system: str) -> str:
    prof = profile_text()
    return system + (f"\n\nUSER'S STANDING RULES AND ENVIRONMENT (breaking these is a defect):\n{prof}"
                     if prof.strip() else "")


def _log_text(tool_log, n=25, chars=2500) -> str:
    """The newest of the last `n` calls (each cut to 200 chars), oldest first, within `chars`. Slicing the joined
    log kept its oldest calls, so a long turn's final actions (kanban_create/comment) never reached the judge."""
    calls = [str(x)[:200] for x in (tool_log or [])[-n:]]
    return render_tool_log(calls, chars) if calls else "(no tool calls)"


RESTRAINT_RE = re.compile(
    r"\b(?:don'?t|do not|dont|never|must not|mustn'?t|shouldn'?t|should not|no need to|not to|without|only (?:document|read|"
    r"draft|propose|plan|report)|stop short of|leave (?:it|that|them) (?:alone|to me))\b", re.I)
SCOPE_MAX_ENTRIES, SCOPE_MAX_CHARS, SCOPE_SNIP = 6, 1800, 140


def _row_user_text(m) -> str:
    return clean_user(m.get("content")) if isinstance(m, dict) and m.get("role") == "user" else ""


def is_ack_snippet(text, m) -> bool:
    """A bare "don't worry" / "no need to apologise" is not a scope limit."""
    return bool(re.match(r"(?:don'?t|do not|no need to)\s+(?:worry|apologi[sz]e|rush|stress)",
                         text[m.start():m.start() + 24], re.I))


def request_scope(history, current=None) -> str:
    """Earlier user instructions that limit what the agent may do ("document it, don't create it"), oldest first,
    so a complaint judge does not call a wait the user imposed an agent-owned block (agent-d086774bf9).
    A restraint is shown as the snippet around its wording (never the whole message: no transcript dump); user
    messages AFTER the first restraint follow in order as short heads, so a later explicit change of instruction
    is visible and neither overrides the other retroactively. "" when no earlier user message restrains."""
    rows = [t for t in (_row_user_text(m) for m in (history or [])) if t]
    cur = clean_user(current) if current is not None else ""
    if rows and cur and rows[-1] == cur:
        rows = rows[:-1]  # the hook's history may already end with the message being judged
    out, seen = [], False
    for i, t in enumerate(rows, 1):
        hits = [m for m in RESTRAINT_RE.finditer(t) if not is_ack_snippet(t, m)]
        if hits:
            seen = True
            lo, hi = max(0, hits[0].start() - SCOPE_SNIP), min(len(t), hits[-1].end() + SCOPE_SNIP)
            out.append(f"[user message {i}/{len(rows)}, restraint] ...{t[lo:hi].strip()}...")
        elif seen:
            out.append(f"[user message {i}/{len(rows)}] {t[:240].strip()}")
    if not seen:
        return ""
    out = out[:1] + out[1:][-(SCOPE_MAX_ENTRIES - 1):]  # keep the first restraint and the newest entries
    return "\n".join(out)[:SCOPE_MAX_CHARS]


def complaint_prompt(prev_reply: str, user_text, tool_log=None, context: str = "", scope: str = "") -> str:
    """`context` (digest backstop only): the session title and earlier user messages, for turns whose previous agent
    reply is unknown. Without it a context-free report (\"why didnt this work [screenshot]\") reads as a new issue."""
    ctx = f"SESSION CONTEXT (from a digest):\n{context[:2200]}\n\n" if context else ""
    prev = prev_reply or ("(not available)" if context else "(none: first turn)")
    # A digest carries no tool log: "(no tool calls)" there was a fabricated fact the judge then repeated as
    # "made no tool calls" (agent-b6540f8eef). The count, when known, is in the session context.
    log = (_log_text(tool_log) if tool_log or not context
           else "(not available from a digest; see \"Tool calls after the agent's last text\" above)")
    sc = ("EARLIER USER INSTRUCTIONS THAT LIMIT THE AGENT (oldest first; a later message changes an earlier limit "
          "only if it says so, and never makes the earlier restraint a fault):\n"
          f"{scope[:SCOPE_MAX_CHARS]}\n\n") if scope else ""
    return (f"{ctx}{sc}AGENT'S PREVIOUS FINAL MESSAGE:\n{prev[-3000:]}\n\n"
            f"TOOL LOG OF THE AGENT'S PREVIOUS TURN:\n{log}\n\n"
            f"USER'S NEW MESSAGE:\n{clean_user(user_text)[:2000]}")


ASK_HEAD_TAIL = 1500  # a request over 2x this is sent as its first and last ASK_HEAD_TAIL chars


def _ask_text(ask) -> str:
    """The request as the audit sees it: whole up to 2*ASK_HEAD_TAIL chars, else head + "[...]" + tail. Briefs put
    the deliverable and its permissions at the END ("a coverage row may be partial, with why"); a head-only cut showed
    the judge the rule but not the permission, so an honest, allowed partial row filed as a self-admission
    (agent-feed37636b, p=0.99)."""
    t = clean_user(ask)
    if len(t) <= 2 * ASK_HEAD_TAIL:
        return t
    return f"{t[:ASK_HEAD_TAIL]}\n[...]\n{t[-ASK_HEAD_TAIL:]}"


def admission_prompt(final_reply: str, tool_log=None, ask="") -> str:
    return (f"USER'S REQUEST:\n{_ask_text(ask) or '(unknown)'}\n\n"
            f"TOOL LOG THIS TURN:\n{_log_text(tool_log, 30, 3500)}\n\n"
            f"AGENT'S FINAL MESSAGE:\n{(final_reply or '(empty)')[-4000:]}")


def _verdict(v: Optional[dict], source: str, threshold: float) -> Optional[dict]:
    """A hit is an agent/Hermes defect at or above threshold; outside-world problems (kind external) are not."""
    if not v or v["p_defect"] < threshold or v.get("kind") in ("external", "product_bug", "none"):
        return None
    return {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": source}


GATE = 0.6            # first-stage score that escalates to the second opinion and the confirm model
AUDIT_THRESHOLD = 0.9  # turn-end audit admissions file only at this (confirmed) score
# The score a confirm verdict must reach to file, per model the confirm stage resolved to (`confirm_model`).
# class:standard routes coach_defect_confirm to gpt-6.1-sol or claude-sonnet-5-5 by quota and failover, and the two
# are calibrated apart: on the 13-row admission gate set (9 real catches, 4 not_defect), 6 samples per row, Sonnet
# scores every negative 0.0 and its catches 0.80-0.97, so at 0.9 it kept 8/54 catch samples (2/9 rows) and at 0.8
# 26-30/54 (5/9 rows) with 0/24 negatives under either fast judge; sol scores catches 0.94-1.0 but also negatives
# 0.88-0.98, so no sol threshold separates them and it stays at 0.9 (t_01a1d0e2a428608e). A model not listed here
# keeps the caller's threshold.
CONFIRM_THRESHOLDS = {"gpt-6.1-sol": 0.9, "claude-sonnet-5-5": 0.8}


def confirm_threshold(v: Optional[dict], default: float = AUDIT_THRESHOLD) -> float:
    """The filing threshold for an audit verdict: CONFIRM_THRESHOLDS[confirm_model] when the confirm stage ran on a
    calibrated model, else `default`. COACH_SELFHEAL_AUDIT_THRESHOLD, when set, holds for every model."""
    if not v or v.get("stages") != "judge+confirm" or os.environ.get("COACH_SELFHEAL_AUDIT_THRESHOLD"):
        return default
    model = str(v.get("confirm_model") or "").rsplit("/", 1)[-1]
    return CONFIRM_THRESHOLDS.get(model, default)


def _score(v: Optional[dict]) -> float:
    return 0.0 if not v or v.get("kind") in ("external", "product_bug", "none") else v["p_defect"]


RESAMPLE_LO = 0.3     # a stage-1 score in [RESAMPLE_LO, GATE) gets one more sample (candidate 1)
FIX_RESAMPLE = True   # so does any stage-1 miss whose message asks to fix / root-cause something (candidate B)
FIX_RE = re.compile(r"\b(?:fix(?:ed)?|root[- ]?caus\w*|figure (?:it )?(?:ou?t|ut) (?:why|what)|investigate|debug|"
                    r"broken|why (?:is|are|did|didn'?t|does|doesn'?t|isn'?t|aren'?t))\b", re.I)


def resample_stage1(s1: float, user_text) -> bool:
    if RESAMPLE_LO is not None and RESAMPLE_LO <= s1 < GATE:
        return True
    return bool(FIX_RESAMPLE and s1 < GATE and FIX_RE.search(clean_user(user_text)))


def _ask(llm: Callable[[str, str], str], system: str, prompt: str, tries: int = 2,
         trace: Optional[dict] = None, stage: str = "") -> Optional[dict]:
    """One judge stage, retried once when the call fails or the answer does not parse (an empty flash answer is
    common). None = no opinion, which the cascade leaves out; it is never a 'no' vote. With `trace`, a stage that
    exhausts its tries records why in trace["exhausted"] ({stage, error, raised}): without it an outage of the judge
    looked exactly like "no complaints" for a week (plugin-89461de0ed)."""
    last, raised = "", False
    for _ in range(tries):
        try:
            raw = llm(system, prompt)
            v = parse(raw)
            if not v:
                last, raised = ("empty answer" if not str(raw or "").strip()
                                else f"unparsed answer: {str(raw)[:120]}"), False
        except Exception as exc:
            v, last, raised = None, f"{type(exc).__name__}: {exc}"[:300], True
        if v:
            return v
    if trace is not None:
        trace.setdefault("exhausted", []).append({"stage": stage, "error": last, "raised": raised})
    return None


def complaint_verdict(prev_reply: str, user_text, llm: Callable[[str, str], str], tool_log=None,
                      confirm: Optional[Callable[[str, str], str]] = None, context: str = "",
                      trace: Optional[dict] = None, scope: str = "") -> Optional[dict]:
    """The complaint cascade, before the threshold: judge (COMPLAINT_SYS); below GATE stop there. Otherwise a second
    flash opinion (COMPLAINT_STRICT_SYS) and, when configured, the confirm model on COMPLAINT_SYS; p_defect = the mean
    of the stage scores (a non-defect kind scores 0; a stage with no answer after a retry is left out, since counting
    it as 0 halved real catches). Component/symptom come from the strongest stage."""
    prompt = complaint_prompt(prev_reply, user_text, tool_log, context, scope)
    system = _with_profile(COMPLAINT_SYS)
    v1 = _ask(llm, system, prompt, trace=trace, stage="s1")
    if not v1:
        return None
    s1 = _score(v1)
    if s1 < GATE and resample_stage1(s1, user_text):
        # Flash at temp 0 is not deterministic: "why are all the dots red? [screenshot]" scored 0.7, 0.6 and 0.05 on
        # three identical calls. One more sample on a borderline score or an explicit fix request; the max counts.
        v1b = _ask(llm, system, prompt, trace=trace, stage="s1b")
        if v1b and _score(v1b) > s1:
            v1, s1 = v1b, _score(v1b)
    if s1 < GATE:
        return {**v1, "p_defect": s1, "stages": [s1]}
    v2 = _ask(llm, _with_profile(COMPLAINT_STRICT_SYS), prompt, trace=trace, stage="s2")
    stages, best = [s1] + ([_score(v2)] if v2 else []), v1
    if confirm:
        v3 = _ask(confirm, _with_profile(COMPLAINT_SYS), prompt, trace=trace, stage="confirm")
        if v3:
            stages.append(_score(v3))
        best = v3 if v3 and _score(v3) > 0 else best
    p = round(sum(stages) / len(stages), 3)
    return {**best, "kind": best.get("kind") if _score(best) > 0 else v1.get("kind"), "p_defect": p,
            "stages": stages}


def detect_complaint(prev_reply: str, user_text, llm: Optional[Callable[[str, str], str]] = None,
                     threshold: float = THRESHOLD, tool_log=None,
                     confirm: Optional[Callable[[str, str], str]] = None, context: str = "",
                     gap_s: Optional[float] = None, trace: Optional[dict] = None,
                     scope: str = "") -> Optional[dict]:
    """pre_llm_call path: core's resume note (backend stopped mid-turn) is a machine signal; otherwise prefilter,
    then the cascade (complaint_verdict) on (previous reply + its turn's tool log, user message). Returns
    {p_defect, kind, component, symptom, fingerprint, source, stages} or None. Fail-open; with `trace` it records
    the outcome (signal / prefiltered / no_opinion / error / below / hit), score, stages and errors."""
    t = trace if trace is not None else {}
    if trace is None:
        # An older coach __init__ (a long-running backend keeps the one it booted with while this module hot-reloads)
        # passes no trace: park it for handle(), which that __init__ calls next on this thread, to log.
        _TL.pending = t
    try:
        if is_resume_note(user_text):
            t["outcome"] = "signal"
            return resume_signal(user_text, gap_s)  # machine-written: never sent to the complaint judge
        if not prefilter(user_text):
            t["outcome"] = "prefiltered"
            return None
        v = complaint_verdict(prev_reply, user_text, llm or aux_judge, tool_log, confirm, context, trace=t, scope=scope)
        if not v:
            ex = t.get("exhausted") or [{}]
            t["outcome"] = "error" if ex[0].get("raised") else "no_opinion"
            t["error"] = ex[0].get("error") or ""
            return None
        hit = _verdict(v, "complaint", threshold)
        t.update(outcome="hit" if hit else "below", score=_score(v) if not hit else v["p_defect"],
                 stages=v.get("stages") or [], kind=v.get("kind"))
        if hit:
            t["fingerprint"] = hit["fingerprint"]
        return hit
    except Exception as exc:
        t.update(outcome="error", error=f"{type(exc).__name__}: {exc}"[:300])
        return None


def log_judged(store, trace: dict, session_id: str, platform: str = "", started: Optional[float] = None) -> None:
    """One `selfheal_judged` row per complaint-judged turn, plus one `selfheal_judge_error` per stage that exhausted
    its retries. Lives here, not in the hook, so a gateway that hot-reloads this module picks up format changes."""
    rec = {"event": "selfheal_judged", "session_id": session_id, "platform": platform,
           "outcome": trace.get("outcome") or "unknown"}
    for k in ("score", "stages", "kind", "fingerprint", "error", "why"):
        if trace.get(k) not in (None, "", []):
            rec[k] = trace[k]
    if started is not None:
        rec["ms"] = int((time.time() - started) * 1000)
    store.log(rec)
    for ex in trace.get("exhausted") or []:
        store.log({"event": "selfheal_judge_error", "session_id": session_id, "stage": ex.get("stage"),
                   "error": ex.get("error"), "raised": bool(ex.get("raised"))})


def skip_reason(platform: str = "", parent_session_id: str = "", input_origin: str = "") -> Optional[str]:
    """Why user_facing() is False for a turn on a user platform, when that is worth a row: a leaked
    HERMES_KANBAN_TASK in a chat process would mute every chat silently, and an agent-driven turn
    (`input_origin="agent"`) is the proof that the agent_input guard fired. None = log nothing (automated platforms
    and subagents are normal skips)."""
    if (platform or "").lower() in ("cron", "kanban", "webhook", "batch") or parent_session_id:
        return None
    if str(input_origin or "").lower() == "agent":
        return "agent_input"
    if os.environ.get("HERMES_KANBAN_TASK"):
        return "kanban_env"
    return None


AGENT_SESSION_SOURCES = ("scratch",)


def agent_session(session_id: str, home: Optional[Path] = None) -> Optional[str]:
    """'agent_session' when this host's state.db row says an agent drives the chat: `hidden=1` (core creates every
    `/api/ws?client=agent` chat hidden, tui_gateway/methods_session.py `pending_hidden`) or source `scratch`. The
    fallback for a core that passes no `input_origin` to pre_llm_call (agent-d925f95adc: a Kanban worker's throwaway
    Desktop proof chat was judged as David's complaint). None when the row is visible, absent or unreadable
    (fail-open: judged as before)."""
    if not session_id:
        return None
    import sqlite3
    path = hermes_home(home) / "state.db"
    if not path.is_file():
        return None
    try:
        db = _ro_state(path)
        try:
            row = db.execute("SELECT hidden, source FROM sessions WHERE id=?", (session_id,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    hidden, source = row
    if hidden or str(source or "").lower() in AGENT_SESSION_SOURCES:
        return "agent_session"
    return None


def agent_input_defect(d: dict) -> bool:
    """A lane payload from an agent-driven turn: `input_origin` agent, or a digest/session marked hidden. Hosts on
    older coach code still forward these; the lane drops them (selfheal_skip why=agent_input)."""
    return str((d or {}).get("input_origin") or "").lower() == "agent" or (d or {}).get("hidden") is True


_MODEL_TL = threading.local()  # the model the last aux judge call on this thread resolved to (note_model)


def note_model(resp) -> None:
    """Remember the model a judge response came from: `class:` routing picks a provider per session (gpt-6.1-sol or
    claude-sonnet-5-5 for class:standard), and the three not_defect admissions were all confirmed by gpt-6.1-sol while
    their replay ran on claude-sonnet-5-5 (t_01a1d01a9a9bb5db)."""
    _MODEL_TL.model = str(getattr(resp, "model", "") or "")


def _tracked(fn: Callable[[str, str], str]) -> Callable[[str, str], str]:
    def call(system: str, prompt: str) -> str:
        _MODEL_TL.model = ""
        return fn(system, prompt)
    return call


def _resolved(key: str) -> dict:
    """{key: model} when the judge just called reported one (production aux judges do; injected test judges don't)."""
    m = getattr(_MODEL_TL, "model", "")
    return {key: m} if m else {}


def audit_verdict(final_reply: str, llm: Callable[[str, str], str], tool_log=None, ask="",
                  confirm: Optional[Callable[[str, str], str]] = None,
                  judged_input: Optional[tuple] = None) -> Optional[dict]:
    """The turn-end audit, before the threshold: judge; an admission at >= GATE goes to the confirm model (when
    configured), whose verdict replaces it. Only AUDIT_KINDS count; other kinds score 0. The verdict carries `stages`
    (judge, or judge+confirm) and, when confirmed, the judge's own `stage1_p`/`stage1_kind`, so a filed detection
    shows which stage filed it; `judge_model`/`confirm_model` name the model each stage resolved to (class routing
    picks one per session). `judged_input` = (system, prompt) a live detection logged to admission_inputs.jsonl:
    replay that exact input instead of rebuilding it."""
    system, prompt = judged_input or (_with_profile(AUDIT_SYS), admission_prompt(final_reply, tool_log, ask))
    v = parse(_tracked(llm)(system, prompt))
    stages = {"stages": "judge", **_resolved("judge_model")}
    if v and confirm and v.get("kind") in AUDIT_KINDS and v["p_defect"] >= GATE:
        stages = {**stages, "stages": "judge+confirm", "stage1_p": v["p_defect"], "stage1_kind": v.get("kind")}
        v = parse(_tracked(confirm)(system, prompt))
        stages.update(_resolved("confirm_model"))
    if not v:
        return None
    # The exact judged input, so a replay of a filed detection needs no rebuild from state.db: the Sonnet replay of
    # real catches scored 0.82-0.90 where the same model scored them 0.97-0.99 live (t_01a1d01a9a9bb5db). `prompt_sha`
    # is logged; `_audit_input` stays off the log and the card (handle() writes it to admission_inputs.jsonl).
    stages["prompt_sha"] = hashlib.sha1(f"{system}\n{prompt}".encode()).hexdigest()[:16]
    v = {**v, **stages, "_audit_input": {"system": system, "prompt": prompt}}
    return v if v.get("kind") in AUDIT_KINDS else {**v, "p_defect": 0.0}


def detect_admission(final_reply: str, llm: Optional[Callable[[str, str], str]] = None,
                     threshold: float = AUDIT_THRESHOLD, tool_log=None, ask="",
                     confirm: Optional[Callable[[str, str], str]] = None) -> Optional[dict]:
    """post_llm_call path: machine signals first (no judge), then a turn-end audit when the reply admits a mistake or
    hands the user a step, or the turn's tool log shows errors. Only self-admissions count from the audit: the other
    audit kinds (unhandled failure, handoff, contradiction) cost more false alarms than they catch on the replay set.
    Fail-open."""
    try:
        if not isinstance(final_reply, str):
            return None
        sig = machine_signal(final_reply, tool_log)
        if sig:
            return sig
        if not admission_prefilter(final_reply, tool_log):
            return None
        v = audit_verdict(final_reply, llm or aux_judge, tool_log, ask, confirm)
        th = confirm_threshold(v, threshold)
        hit = _verdict(v, "admission", th)
        return {**hit, "threshold": th, "audit_input": audit_input(final_reply, tool_log, ask)} if hit else None
    except Exception:
        return None


def audit_input(final_reply: str, tool_log=None, ask="") -> dict:
    """What the turn-end audit judged, as a `selfheal_replay.py --mode admission` row body ({text, ask, tool_log}).
    A filed admission carries it to the hub (AUDIT_INPUT_FILE), so a not_defect verdict on it becomes a replayable
    negative row. Before, the negatives held only a 300-char reply tail, and every detector card rebuilt each turn by
    hand from the state.db of the host that ran it (plugin-aafb20159a: the MacBook's)."""
    return {"text": (final_reply or "")[-4000:], "ask": _ask_text(ask),
            "tool_log": [str(x)[:400] for x in list(tool_log or [])[-30:]]}


AUDIT_INPUT_FILE = "audit_input.json"  # <coach>/selfheal/<fp>/: the audited turn of an admission finding


def _save_audit_input(store, d: dict) -> None:
    """Keep a filed admission's audited turn next to its proposal (<coach>/selfheal/<fp>/). Never raises."""
    inp = d.get("audit_input")
    if d.get("source") != "admission" or not isinstance(inp, dict) or not inp.get("text"):
        return
    try:
        p = Path(store.root) / "selfheal" / d["fingerprint"] / AUDIT_INPUT_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({**inp, "session_id": d.get("session_id"), "host": d.get("host"),
                                 "p": d.get("p_defect"), "stages": d.get("stages"), "stage1_p": d.get("stage1_p")}))
    except Exception:
        pass


AUDIT_KINDS = ("admission",)


def confirm_judge() -> Optional[Callable[[str, str], str]]:
    """The optional second-stage judge: aux task `coach_defect_confirm` when configured
    (auxiliary.coach_defect_confirm.{provider,model}) or COACH_DEFECT_CONFIRM_MODEL is set; else None."""
    model = os.environ.get("COACH_DEFECT_CONFIRM_MODEL", "")
    provider = os.environ.get("COACH_DEFECT_CONFIRM_PROVIDER", "")
    if not model:
        try:
            from hermes_cli.config import load_config  # type: ignore
            c = (load_config().get("auxiliary") or {}).get("coach_defect_confirm") or {}
            model, provider = c.get("model") or "", c.get("provider") or ""
        except Exception:
            return None
    if not model:
        return None

    def judge(system: str, prompt: str) -> str:
        from agent.auxiliary_client import call_llm  # type: ignore
        resp = call_llm(task="coach_defect_confirm", provider=provider or None, model=model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        max_tokens=400, temperature=0, timeout=60)
        note_model(resp)
        return (getattr(resp.choices[0].message, "content", None) or "").strip()
    return judge


def user_facing(platform: str = "", parent_session_id: str = "", input_origin: str = "") -> bool:
    """Only the user's own chats: not cron runs, subagents (their 'user' is the parent agent), Kanban workers
    (their first message is a card body written by an agent) or turns core marks `input_origin="agent"` (an agent
    script driving a Desktop chat over /api/ws?client=agent, an agent-created or hidden submit). An unknown origin
    ("", older core) is judged; agent_session() covers that case from state.db."""
    if (platform or "").lower() in ("cron", "kanban", "webhook", "batch"):
        return False
    if parent_session_id or os.environ.get("HERMES_KANBAN_TASK"):
        return False
    if str(input_origin or "").lower() == "agent":
        return False
    return True


def mode() -> str:
    """COACH_SELFHEAL env, else `mode` in <coach>/selfheal-config.json (so a cron scheduler or gateway needs no env
    change), else shadow."""
    m = os.environ.get("COACH_SELFHEAL", "").strip().lower()
    if not m:
        try:
            m = str(json.loads((coach_root() / "selfheal-config.json").read_text()).get("mode") or "").lower()
        except Exception:
            m = ""
    return m if m in ("off", "shadow", "live") else "shadow"


def threshold() -> float:
    try:
        return float(os.environ.get("COACH_SELFHEAL_THRESHOLD", THRESHOLD))
    except ValueError:
        return THRESHOLD


def audit_threshold() -> float:
    try:
        return float(os.environ.get("COACH_SELFHEAL_AUDIT_THRESHOLD", AUDIT_THRESHOLD))
    except ValueError:
        return AUDIT_THRESHOLD


def aux_judge(system: str, prompt: str) -> str:
    """Aux task `coach_defect` (auxiliary.coach_defect.{provider,model})."""
    from agent.auxiliary_client import call_llm  # type: ignore
    kw = {}
    if os.environ.get("COACH_DEFECT_MODEL"):
        kw = {"provider": os.environ.get("COACH_DEFECT_PROVIDER") or None, "model": os.environ["COACH_DEFECT_MODEL"]}
    resp = call_llm(task="coach_defect", messages=[{"role": "system", "content": system},
                                                  {"role": "user", "content": prompt}],
                    max_tokens=1500, temperature=0, timeout=30, **kw)
    note_model(resp)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


class Fingerprints:
    """<coach>/selfheal.json: fingerprint -> {first, last, count, sessions, day_count}. `seen` = already recorded
    today (UTC), so one defect becomes one filing per day however many times it is reported."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.path = Path(root) / "selfheal.json"
        self.clock = clock

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def record(self, hit: dict, session_id: str = "") -> bool:
        """Record a hit; True when it is the first time this fingerprint is seen today."""
        d = self._read()
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        fp = hit["fingerprint"]
        e = d.get(fp) or {"first": now, "count": 0, "sessions": [], "component": hit.get("component"),
                          "symptom": hit.get("symptom")}
        new = e.get("day") != day
        e.update(last=now, day=day, count=e["count"] + 1)
        if session_id and session_id not in e["sessions"]:
            e["sessions"] = (e["sessions"] + [session_id])[-20:]
        d[fp] = e
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.path)
        return new


def handle(hit: Optional[dict], session_id: str, store, evidence: str = "", m: Optional[str] = None,
           filer: Optional[Callable] = None, platform: str = "", ask: str = "") -> Optional[dict]:
    """Log a detection and hand a new fingerprint (first time today) to the lane: `file_defect` logs
    `selfheal_would_file` in shadow mode and files the investigation card on the hub in live mode. A deliberate
    detector probe (`probe_session`) is logged and skipped: no fingerprint record, no queue row, no filing."""
    pending = getattr(_TL, "pending", None)
    if pending is not None:  # a complaint judged by an older __init__ that logs no selfheal_judged row itself
        _TL.pending = None
        try:
            log_judged(store, pending, session_id, platform)
        except Exception:
            pass
    if not hit:
        return None
    audit_in = hit.get("_audit_input")
    hit = {k: v for k, v in hit.items() if k != "_audit_input"}
    m = m or mode()
    if is_crash_hit(hit) and "stop_cause" not in hit:
        # Read the stop's cause HERE, on the host that ran the cut turn (its logs exist nowhere else), and in
        # handle() so a long-running backend's older coach __init__ gets it too (gateway-9aaf151a15 came back).
        try:
            clock = getattr(store, "clock", None)
            now = float(clock()) if callable(clock) else time.time()
            since, until = stop_window(hit, session_id, now)
            hit = with_stop_cause(hit, stop_cause(None, since, until))
        except Exception as exc:  # fail-open: an unreadable log only loses the cause, never the alert
            hit = with_stop_cause(hit, {"cause": "unknown", "evidence": f"stop_cause failed: {exc}"[:200]})
    # A machine signal carries what failed; lead with it here, not only in the caller, because a long-running gateway
    # re-execs this module on change but keeps the coach __init__ it booted with (agent-6eb3a51e94 live check).
    hit_ev = str(hit.get("evidence") or "")
    if hit_ev and not str(evidence or "").startswith(hit_ev[:60]):
        evidence = f"{hit_ev[:300]} | reply: {str(evidence or '')[-180:]}"
    stop = {k: hit[k] for k in ("stop_cause", "stop_evidence", "stop_host") if k in hit}
    stop.update({k: hit[k] for k in ("stages", "stage1_p", "stage1_kind", "judge_model", "confirm_model",
                                     "threshold", "prompt_sha") if k in hit})  # which stage filed it, on which model
    if hit.get("source") in ("complaint", "admission") and agent_session(session_id):
        # A backend still on an older coach __init__ judges agent-driven chats; nothing is recorded or filed for them
        # (agent-d925f95adc). Guard refusals and machine signals are defects whoever hit them: not gated here.
        store.log({"event": "selfheal_skip", "mode": m, "fingerprint": hit["fingerprint"], "why": "agent_session",
                   "session_id": session_id})
        return None
    if probe_session(session_id, ask):
        rec = {"event": "selfheal_detect", "mode": m, "session_id": session_id, "source": hit["source"],
               "p": hit["p_defect"], "component": hit["component"], "symptom": hit["symptom"],
               "fingerprint": hit["fingerprint"], "new": False, "probe": True, "evidence": (evidence or "")[:300],
               **stop}
        store.log(rec)
        store.log({"event": "selfheal_skip", "mode": m, "fingerprint": hit["fingerprint"], "why": "probe",
                   "session_id": session_id})
        return rec
    new = Fingerprints(store.root).record(hit, session_id)
    rec = {"event": "selfheal_detect", "mode": m, "session_id": session_id, "source": hit["source"],
           "p": hit["p_defect"], "component": hit["component"], "symptom": hit["symptom"],
           "fingerprint": hit["fingerprint"], "new": new, "evidence": (evidence or "")[:300], **stop}
    store.log(rec)
    if isinstance(audit_in, dict):
        try:  # replay input for selfheal_replay.py; fail-open
            with open(Path(store.root) / "admission_inputs.jsonl", "a") as fh:
                fh.write(json.dumps({"ts": time.time(), "session_id": session_id, "fingerprint": hit["fingerprint"],
                                     "p": hit["p_defect"], "prompt_sha": hit.get("prompt_sha"),
                                     "system": str(audit_in.get("system", ""))[:40000],
                                     "prompt": str(audit_in.get("prompt", ""))[:60000]}) + "\n")
        except Exception:
            pass
    if m == "live" and new:
        q = Path(store.root) / "selfheal_queue.jsonl"
        with open(q, "a") as fh:
            fh.write(json.dumps({**rec, "ts": time.time()}) + "\n")
    # `every_occurrence` (lifecycle-guard refusals): each occurrence reaches the lane, which comments it on the open
    # card or refiles once that card closed; the per-day fingerprint gate would drop every repeat before it got there.
    if (new or hit.get("every_occurrence")) and m in ("shadow", "live"):
        d = {**hit, "session_id": session_id, "quote": (evidence or "")[:500], "host": host_name(),
             "platform": platform, "at": time.time(), "kind": hit.get("kind") or hit["source"]}
        try:
            (filer or file_defect)(d, store=store, m=m)
        except Exception as exc:  # fail-open: a filing problem never breaks a turn
            store.log({"event": "selfheal_file_error", "fingerprint": hit["fingerprint"], "error": str(exc)[:300]})
    return rec


# ════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Lane: defect -> investigation card (hub) -> proposal file (What happened / Why / Plan) -> auto-approve -> fix card
#       -> result stored quietly (selfheal-results/<fp>.md, no Inbox card). auto_approve false restores the Inbox proposal + Do it gate.
#
#   file_defect(d)    any entry path (complaint, admission, machine signal, digest backstop, monitors). On the hub it
#                     decides locally; elsewhere (live) it pipes `d` to `self_heal.py file` on the hub over ssh, so
#                     dedupe, recurrence, suppression and budget are decided in one place. Shadow logs
#                     `selfheal_would_file` with the full card args and creates nothing.
#   investigation     card `selfheal:<fp>` for generation 1, `selfheal:<fp>:r<gen>` after (one monotonic counter per
#                     fingerprint over refiles AND recurrences; kanban idempotency returns done cards too), model
#                     the board's model tier (no pin), 1 h. Investigates LIVE (kanban.db, logs, processes, git log), writes the
#                     five-whys chain and proposal.md, records `event proposed` (posts nothing to the Inbox; the
#                     inbox plugin's post() refuses self-heal posts while auto_approve is on).
#   auto-approve      sync files the fix card for every `proposed` fingerprint (manual mode: the Inbox outcome on key
#                     selfheal:<fp> decides). Old cards still telling the worker to `hermes inbox post` get a one-time
#                     "ignore that step" comment (sweep_inbox_steps).
#   Do it             -> file_fix (hub cron sync, or `self_heal.py fix <fp>`): fix card `selfheal:<fp>[:r<gen>]:fix` (red-then-green regression test, ship,
#                     verify live, `event fixed --result result.md` stores the proof; nothing is posted to the Inbox).
#   sync (cron)       reads Inbox outcomes every 5 min: Do it (a resolve) files the fix card, Not now snoozes the
#                     fingerprint 14 days, Never suppresses it. It also reconciles against the board: a `filed`/`approved`
#                     fingerprint whose card is done/archived/gone with no proposed/fixed event becomes `stale`
#                     (selfheal_stale), so the next detection refiles it under a new generation.
#   backstop (cron)   runs the complaint detector over new user turns in the coach digests (chats where the
#                     in-process hook did not fire).
#   budget            engine_budget lane `selfheal`: $200/UTC day (its own cap, outside the $20 engine total), <= 3
#                     running cards, <= 1 filing per fingerprint per UTC day. Known cap denials never create a card;
#                     investigations persist for admission retry, and fixes keep the Do-it outcome retryable.
# Events (coach log.jsonl, read by coach_weekly's self-heal section): selfheal_would_file, selfheal_filed,
# selfheal_recurrence, selfheal_recurrence_held (an occurrence inside `deploy_grace_hours` after fixed: hosts may
# still run old code; the 3rd held occurrence files anyway), selfheal_not_deployed (a post-fix occurrence from a host
# whose `sync: <label>` commits on the hub's hermes-state clone didn't contain the state-branch fix yet: held, and
# one "host <label> hasn't synced fix" card once that lasts `undeployed_file_hours`), selfheal_skip, selfheal_proposed, selfheal_approved,
# selfheal_fixed, selfheal_reverted, selfheal_not_defect, selfheal_declined.
# ════════════════════════════════════════════════════════════════════════════════════════════════════════════

MODEL = "claude-opus-5-5"  # fix, backport and lane-defect cards (they write and ship code)
PROVIDER = "anthropic"  # pinned with MODEL: a host whose default provider is another (hub: openai-codex) refuses the bare model (model_provider_mismatch)
# Investigation, verify and "host hasn't synced" cards carry no model pin: the board's model tier decides
# (kanban.default_model first; kanban.strong_model after a failed run or a reviewer's change request), so the
# lane's largest card class no longer queues behind one provider's wall. Set both to MODEL/PROVIDER to restore the pin.
TRIAGE_MODEL: Optional[str] = None
TRIAGE_PROVIDER: Optional[str] = None
INVESTIGATION_RUNTIME = 3600
FIX_RUNTIME = 7200
INVESTIGATION_BUDGET = 8
FIX_BUDGET = 15
USD_DAY = 200.0
MAX_RUNNING = 3
INVESTIGATION_SLOTS_WHILE_FIXES_WAIT = 1  # fix-first: the rest of MAX_RUNNING goes to waiting fix cards
FIX_TITLE = "self-heal fix:"
SNOOZE_DAYS = 14
NOT_DEFECT_DAYS = 7
SAME_QUOTE_MIN_CHARS = 120  # a quote this long, shared by other sessions, is one complaint (agent-ea7ff00170)
SKILLS = ("five-whys", "hermes-mods-repo")
BB_SKILLS = ("five-whys", "bb-self-fix")
FIX_SKILLS = ("five-whys", "hermes-mods-repo", "systematic-debugging")
# Every self-heal card declares what its worker needs: core refuses an agent-run create that omits it
# (requires_declaration_missing under kanban.require_capability_declaration), which silently dropped investigations.
SELFHEAL_REQUIRES = ("terminal", "file", "git")  # genuine capabilities; the home pin is `--runs-on machine:<gateway>`
# A default-board fix is split in two (kanban-12ee0e4b65): the FIX card is repo work landed by push, so it declares
# no home pin and fences the proposal as evidence, and the hub's worker pool can run it. Its VERIFY child needs the
# filing gateway's coach store, logs and lane `event`. On the hub that is remote work: the child declares the pool
# attestations (VERIFY_POOL_LABELS) as its `--runs-on` and does every hub step through `hub-admin '<command>'`, so
# a pool worker runs it. Any other gateway (a Mac filing locally) has no such remote path: its VERIFY keeps the
# home machine pin (`--runs-on machine:<gateway>`). Neither declares host_repo (a capability, never a pin).
# Before, every fix card stamped a home pin and quoted the hub-words proposal, so none left the hub's 7 slots.
FIX_REQUIRES = ("terminal", "file", "git")
VERIFY_TITLE = "self-heal verify:"
# Gateways whose lane a pool worker reaches with `hub-admin` (hub-write) and `hub-read`.
# Shared with every coach producer (placement_evidence.REMOTE_GATEWAYS / POOL_LABELS: one route, one label set).
VERIFY_REMOTE_GATEWAYS = _placement_evidence.REMOTE_GATEWAYS
# Board labels a worker publishes only after a real proof of each tool (kanban-hub-pin-audit): hub reads, hub-admin
# writes, and reading the parent fix card's handoff. The hub itself carries none of them, so the card leaves it.
VERIFY_POOL_LABELS = _placement_evidence.POOL_LABELS
VERIFY_REMOTE_DIR = "/tmp/selfheal-verify"  # where a remote VERIFY stages result.md on the gateway for `event`
# Every other home-bound card this lane files (investigation, deploy check, lane repair) follows the VERIFY route on
# a remote-reachable gateway: core's hub admission (hermes_cli/kanban_hub_admission_placement.apply) refuses an
# explicit `machine:hub` pin unless a judge admits an allowlisted hub-only need, and none of these cards has one
# (their gateway steps are hub-read reads and hub-admin writes). From 11:22 UTC 2026-11-12 every investigation was
# refused and the lane filed none (t_01a1cfa3b3805315). Pool labels on a default-board card are not hub-only, so the
# create makes no judge call at all. A named board is not on the pool and keeps the home pin; on the hub route() files
# bb defects on the default board instead (core judges every named-board create there: t_01a1cfb30dc5d3da).
POOL_LABELS = VERIFY_POOL_LABELS
REMOTE_NOTE = ("This card runs on a pool worker. The state it works on (logs, state files, Kanban databases and the "
               "coach store under `~/.hermes`) lives on the gateway that filed it: read it there with "
               "`ssh hermes-hub <cat|tail|head|grep|ls|sqlite ...>` (`ssh hermes-hub help` lists them) or "
               "`hub-admin '<command>'`, and run every command that changes it, including the lane's `event`, as "
               "`hub-admin '<command>'` (single-quoted, so `$HOME` and `~` expand on the gateway). Never run the "
               "lane's `event` command locally: here it would write this worker's own empty coach store.")
# A create core refused on placement (hub admission). The same refusal would hit every later create of this pass.
PLACEMENT_REFUSED_RE = re.compile(r"hub_admission_denied|hub admission denied|\"hub_admission\"\s*:|"
                                  r"admission unavailable|hub_admission_unavailable", re.I)
PLACEMENT_ALERT_STATE = "selfheal_placement_refused.json"
VERIFY_RUNTIME = 3600
VERIFY_SKILLS = ("five-whys", "hermes-mods-repo")
# EVIDENCE_OPEN/CLOSE (same strings as core's kanban_placement) come from placement_evidence.py: the placement scan
# skips a complete fenced block. Reasons a self-heal card stays on the gateway that filed it:
PIN_INVESTIGATION = "it reads this gateway's own logs, state files and coach store, which a worker node does not have"
PIN_FIX = "it records its result in this gateway's coach lane and verifies live on it"
PIN_VERIFY = "its live check and lane event need this gateway's coach store and logs"
PIN_BACKPORT = "it patches a checkout that exists only on the gateway that raised the error"
PIN_DEPLOY = "it checks this gateway's sync log and its connection to the other hosts"
PIN_LANE = "it repairs this gateway's own self-heal lane and its cron"
STUCK_PROPOSAL_S = 2 * 3600  # proposed, budget open, no fix card for this long = the lane is not filing
PULL_FAIL_CARD_AFTER = 3     # consecutive coach-data pull failures before the lane files its own card
CREATED_BY = "self-heal"
INBOX_TYPE = "selfheal"
RESULT_TYPE = "selfheal-result"  # legacy Inbox card type; results are no longer posted (see RESULTS_DIR)
RESULTS_DIR = "selfheal-results"  # <coach>/selfheal-results/<fp>.md: finished-work proof, never an Inbox card
SELF = "~/code/hermes-mods/plugins/coach/self_heal.py"  # how cards and Inbox actions call this file on the hub
# The event commands briefs print pin HERMES_HOME: a card's terminal keeps env between calls, and a repro that ran
# `export HERMES_HOME=$(mktemp -d)` sent `event proposed` to a scratch lane that reported success (plugin-129c2f2518).
SELF_EVENT = f'HERMES_HOME="$HOME/.hermes" python3 {SELF} event'
FP_RE = re.compile(r"^[a-z]+-[0-9a-f]{8,}$")


def host_name() -> str:
    return os.uname().nodename.split(".")[0]


HANDOFF_GRACE_S = 15 * 60  # same as signals.HANDOFF_GRACE_S
NUDGE_OPEN_S = 3 * 3600  # same as signals.NUDGE_OPEN_S
DELIVERY_EVENTS = ("reply_nudge_delivered", "reply_nudge_undelivered", "reply_nudge_abandoned")  # signals.NUDGE_DELIVERY


def _nudge_pairs(n: dict, o: dict) -> bool:
    """Same matching as signals._outcome_matches: a shared nudge_id decides with no time limit (agent-98f77b1ca9);
    id-less records need the same draft and an outcome within the grace window."""
    if o.get("session_id") != n.get("session_id"):
        return False
    if n.get("nudge_id") and o.get("nudge_id"):
        return n["nudge_id"] == o["nudge_id"]
    return (not (n.get("draft_id") and o.get("draft_id") and n["draft_id"] != o["draft_id"])
            and -5 <= float(o.get("ts") or 0) - float(n.get("ts") or 0) <= HANDOFF_GRACE_S)


def _nudge_waiting(n: dict, delivered: dict, now: float) -> bool:
    """Same as `not signals.nudge_due`: an id nudge's clock starts at its delivery record (NUDGE_OPEN_S if none)."""
    t = float(n.get("ts") or 0)
    if not n.get("nudge_id"):
        return now - t <= HANDOFF_GRACE_S
    if n["nudge_id"] in delivered:
        return now - delivered[n["nudge_id"]] <= HANDOFF_GRACE_S
    return now - t <= NUDGE_OPEN_S


def handoff_nudge_counts(root: Optional[Path] = None, now: Optional[float] = None, days: int = 7) -> dict:
    """Handoff reply_nudges in <coach>/log.jsonl over `days`, by what their reply_nudge_outcome said: resolved (the
    revised reply no longer hands off: not filed), still_handed_off and no_outcome (past the grace window: both
    filed by the signals collector), waiting (inside the grace window). For `status`: a judge that never resolves
    anything, or resolves everything, shows here."""
    root = Path(root) if root is not None else coach_root()
    now = time.time() if now is None else now
    since = now - days * 86400
    nudges, outcomes, delivered = [], [], {}  # delivered: nudge_id -> ts of its first delivery record
    try:
        with (root / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if '"reply_nudge' not in ln or '"handoff"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("rule") != "handoff" or float(r.get("ts") or 0) < since:
                    continue
                if r.get("event") in DELIVERY_EVENTS and r.get("nudge_id"):
                    delivered.setdefault(r["nudge_id"], float(r.get("ts") or 0))
                (nudges if r.get("event") == "reply_nudge" else
                 outcomes if r.get("event") == "reply_nudge_outcome" else []).append(r)
    except OSError:
        pass
    c = {"days": days, "nudges": len(nudges), "resolved": 0, "still_handed_off": 0, "no_outcome": 0, "waiting": 0}
    for n in nudges:
        o = next((o for o in outcomes if _nudge_pairs(n, o)), None)
        if o is not None:
            outcomes.remove(o)
            c["resolved" if o.get("resolved") is True else "still_handed_off"] += 1
        else:
            c["waiting" if _nudge_waiting(n, delivered, now) else "no_outcome"] += 1
    c["filed"] = c["still_handed_off"] + c["no_outcome"]
    return c


# reply_check rules whose nudges get a reply_nudge_outcome (same as signals.OUTCOME_RULES).
OUTCOME_RULES = ("handoff", "confirm", "unverified", "unwatched", "report_only", "status_only", "denied_ask",
                 "credit_ask", "laptop_job", "skipped_live", "merge_forecast", "future_clock", "claimed_mutation",
                 "restart_scope_claim")


def _unconfirmed_outcome(o: dict) -> bool:
    """Same as signals.unconfirmed_outcome: a screen-only failing report_only outcome, never filed (agent-e5b6dcf428)."""
    if (o.get("rule") or "") != "report_only" or o.get("resolved") is True:
        return False
    if o.get("unconfirmed"):
        return True
    if o.get("src"):
        return o["src"] not in ("confirm", "closed_owner", "unverified_owner")
    p = o.get("p")
    return o.get("resolved") is False and isinstance(p, (int, float)) and p < 1.0


def nudge_counts(root: Optional[Path] = None, now: Optional[float] = None, days: int = 7) -> dict:
    """reply_nudges per rule over `days`, by their reply_nudge_outcome (agent-3ba1979f34): resolved (not filed),
    still_failing / no_outcome (filed by signals.collect_coach), unconfirmed (a screen-only report_only outcome: not
    filed), unjudged (resolved=None, no verdict: filed as one coach defect, not in `filed`; agent-614ed52ae7), waiting (inside the grace window), untracked (a
    non-handoff nudge from a backend on the old plugin, no `outcome` marker: never filed). For `status`: a rule whose
    nudges never get outcomes, or never resolve, shows here."""
    root = Path(root) if root is not None else coach_root()
    now = time.time() if now is None else now
    since = now - days * 86400
    nudges, outcomes, dropped, delivered = [], [], [], {}  # delivered: nudge_id -> ts of first delivery
    try:
        with (root / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if '"reply_nudge' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("rule") not in OUTCOME_RULES or float(r.get("ts") or 0) < since:
                    continue
                if r.get("event") in DELIVERY_EVENTS and r.get("nudge_id"):
                    delivered.setdefault(r["nudge_id"], float(r.get("ts") or 0))
                (nudges if r.get("event") == "reply_nudge" else
                 outcomes if r.get("event") == "reply_nudge_outcome" else
                 dropped if r.get("event") == "reply_nudge_answer_dropped" else []).append(r)
    except OSError:
        pass
    out: dict = {}
    for d in dropped:  # agent-e868df5c84: the revision lost the draft's direct answer (shown only when it happened)
        c = out.setdefault(d["rule"], {"nudges": 0, "resolved": 0, "still_failing": 0, "no_outcome": 0, "waiting": 0,
                                       "untracked": 0, "unconfirmed": 0, "filed": 0})
        c["answer_dropped"] = c.get("answer_dropped", 0) + 1
    for n in nudges:
        rule = n["rule"]
        c = out.setdefault(rule, {"nudges": 0, "resolved": 0, "still_failing": 0, "no_outcome": 0, "waiting": 0,
                                  "untracked": 0, "unconfirmed": 0, "filed": 0})
        c["nudges"] += 1
        o = next((o for o in outcomes if o.get("rule") == rule and _nudge_pairs(n, o)), None)
        if o is not None:
            outcomes.remove(o)
            k = ("resolved" if o.get("resolved") is True else
                 "unconfirmed" if _unconfirmed_outcome(o) else
                 # agent-614ed52ae7: nobody scored it; it files as one coach defect, outside `filed`
                 "unjudged" if o.get("resolved") is None else "still_failing")
        elif rule != "handoff" and not n.get("outcome"):
            k = "untracked"
        else:
            k = "waiting" if _nudge_waiting(n, delivered, now) else "no_outcome"
        c[k] = c.get(k, 0) + 1  # `unjudged` appears only when it happened
        c["filed"] = c["still_failing"] + c["no_outcome"]
    return out


def coach_root() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home()) / "coach"
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "coach"


def lane_config() -> dict:
    """<coach>/selfheal-config.json (private, per host): {"hub_host": "<hostname of the hub>", "hub_ssh":
    "<user@addr>", "board": "<hub board>", "bb_board": "bb", "deploy_grace_hours": 6}. Env overrides:
    COACH_SELFHEAL_HUB (ssh target), COACH_SELFHEAL_IS_HUB=1. With no hub configured every host files locally.
    `deploy_grace_hours` (hub side): occurrences this soon after `fixed` are held, not filed as came-back."""
    try:
        cfg = json.loads((coach_root() / "selfheal-config.json").read_text())
    except Exception:
        cfg = {}
    if os.environ.get("COACH_SELFHEAL_HUB"):
        cfg["hub_ssh"] = os.environ["COACH_SELFHEAL_HUB"]
    return cfg


def hub_ssh_argv(cfg: dict) -> list:
    """ssh argv prefix to the hub. Pins the key (`hub_ssh_key` in the config, default ~/.ssh/id_ed25519 when it
    exists) with IdentitiesOnly: a launchd job's default identities differ from the shell's (no agent, other keys
    offered first), and the hub then refuses it preauth while the same host's interactive ssh works."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    kp = Path(os.path.expanduser(cfg.get("hub_ssh_key") or "~/.ssh/id_ed25519"))
    if cfg.get("hub_ssh_key") or kp.exists():
        argv += ["-i", str(kp), "-o", "IdentitiesOnly=yes"]
    return argv


def is_hub(cfg: Optional[dict] = None) -> bool:
    cfg = lane_config() if cfg is None else cfg
    if os.environ.get("COACH_SELFHEAL_IS_HUB") in ("1", "true"):
        return True
    if not cfg.get("hub_ssh"):
        return True  # no hub configured: file locally
    return bool(cfg.get("hub_host")) and host_name() == cfg["hub_host"]


def route(d: dict, cfg: Optional[dict] = None) -> dict:
    """Board + skills. Baby Bear product defects go to the hub bb board (cfg ``bb_board``, default ``bb``) with
    bb-self-fix and assignee ``default``; everything else to the hub's default board (Hermes / harness). A missing
    named board is not created here: live filing defers the payload instead of dropping it."""
    cfg = lane_config() if cfg is None else cfg
    if d.get("component") == "bb":
        board = cfg.get("bb_board") or "bb"
        if board != "default" and remote_verify():
            # On the hub a named board is structural for core's hub admission (structural_reason -> explicit), so
            # every bb-board create is judged and denied: a bb defect needs no hub-only context (hub-read/hub-admin
            # and the babybear checkout are on the pool). The hub files it on the default board, bb skills, pool.
            board = None
        return {"board": board, "skills": list(BB_SKILLS), "bb": True}
    return {"board": cfg.get("board") or None, "skills": list(SKILLS), "bb": False}


_BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")
_MISSING_BOARD_RE = re.compile(r"board\s+['\"]?[\w.-]+['\"]?\s+does not exist", re.I)
MISSING_BOARD_RETRY_S = 900  # cron is every 5 min; don't hammer a board that is not there yet


def _kanban_root() -> Path:
    """Same root Hermes uses for boards: ``HERMES_KANBAN_HOME``, else the default Hermes root."""
    override = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from hermes_constants import get_default_hermes_root  # type: ignore
        return Path(get_default_hermes_root())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def board_present(board: Optional[str], home: Optional[Path] = None) -> bool:
    """Whether ``hermes kanban --board <slug> create`` has a board to land on.

    None and ``default`` always exist (DB at ``<root>/kanban.db``). A named board exists only when
    ``<root>/kanban/boards/<slug>/board.json`` is present and not archived — Hermes's identity marker.
    ``home`` overrides the root (tests)."""
    if not board or board == "default":
        return True
    if not _BOARD_SLUG_RE.match(board):
        return False
    root = Path(home) if home is not None else _kanban_root()
    try:
        data = json.loads((root / "kanban" / "boards" / board / "board.json").read_text())
    except Exception:
        return False
    return not data.get("archived")


def _board_missing_output(err: str, out: str) -> bool:
    return bool(_MISSING_BOARD_RE.search(f"{err or ''}\n{out or ''}"))


def _pt(ts: float) -> str:
    import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.fromtimestamp(ts or 0, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts or 0))


# Where to look, per component. The replay of 202 incidents showed a transcript-only investigator almost never fixes
# surface provenance (C10), Kanban state/routing (C11) or process lifecycle (C12): those need the live systems.
CHECKLIST = {
    "kanban": ("C11 Kanban state machine / routing",
               "- `sqlite3 -readonly ~/.hermes/kanban.db` (and `~/.hermes/kanban/boards/*/kanban.db`): the card's "
               "`tasks` row (status, assignee, board, idempotency_key, created_by), `task_events` in order "
               "(kind + payload: claimed/blocked/scheduled/unblocked/reclaimed/timed_out and their reasons), "
               "`task_runs` (outcome, error, worker pid, session_id).\n"
               "- Compare what the user was told (quote) with the event log at that minute: which writer moved the "
               "state, and which session/board it routed to.\n"
               "- Cross-session leaks: is the card/notice the chat talked about OWNED by this chat (tasks.session_id, "
               "created_by) or did it arrive via a subscription (kanban_notify_subs) or a board-wide query? "
               "Duplicate cards (same title, no idempotency_key)? A worker that parked David-facing steps in a "
               "comment or a timer instead of a needs_input block?\n"
               "- `git log -S'<event kind or state>' -- hermes_cli/kanban*` in ~/.hermes/hermes-agent for the code "
               "that writes it; the dispatcher log `~/.hermes/logs/kanban*.log` / `agent.log` at that time."),
    "desktop": ("C10 surface provenance / duplicated state",
                "- Which surface showed it (sidebar row, Inbox card, toast, panel, status chip) and which writer "
                "produced it: grep the exact string in ~/.hermes/hermes-agent/apps/desktop and "
                "~/code/hermes-mods/desktop-plugins; `git log --since` on that file.\n"
                "- The backing state: the REST endpoint or db row the surface renders (state.db sessions row, inbox "
                "items, kanban task) at the reported time; two writers or a stale cache = provenance gap.\n"
                "- Do not stop at the renderer: the wrong row/badge/card is usually right for the data it got. Follow "
                "the field it keys on (source, owner, status, origin/test flag) back to the code path that WROTE it "
                "(e.g. a create path that hardcodes `source`, test traffic with no origin tag) and cite that file:line.\n"
                "- On a Mac, the Desktop log (`~/Library/Logs/Hermes/` / `~/.hermes/logs/desktop.log`) via the "
                "mac diag command if configured; else the coach digest of the session."),
    "plugin": ("C10/C12 plugin behaviour",
               "- `git -C ~/code/hermes-mods log --since=7.days -- plugins/<name> desktop-plugins/<name>`: what "
               "changed just before the report.\n"
               "- `grep -n '<plugin>' ~/.hermes/logs/errors.log` around the time; hook exceptions; the plugin's "
               "own log/state under ~/.hermes/<plugin>/ or ~/.hermes/cache/<plugin>/.\n"
               "- Reproduce by calling the plugin function directly under the Hermes interpreter."),
    "gateway": ("C12 process lifecycle / environment isolation",
                "- `ps -eo pid,lstart,command | grep -i hermes`, `systemctl --user status` / `launchctl print "
                "gui/$UID/<label>`: what was running, who started it, restarts (`journalctl --user -u ...`).\n"
                "- `~/.hermes/logs/{gateway,agent,errors}.log` and `hermes-sync.log` (a `hermes update` at that "
                "minute stops servers) in a +-10 min window.\n"
                "- Environment: HERMES_HOME, profile, interpreter/venv of each process (`/proc/<pid>/environ` or "
                "`ps -E`). Guards keyed on inherited env vars (HERMES_DELEGATED_CHILD_CONTEXT, HERMES_SESSION_*, "
                "HERMES_KANBAN_*) misfire when a launcher (update/relaunch/sync, execute_code's env scrub) passes or "
                "drops the parent env: grep the guard, then every spawn path that sets or strips the var.\n"
                "- Who ended or orphaned it: session reapers / LRU eviction and agent close tearing down owned "
                "background processes; checkpoints (processes.json) written before the notify/watch flags, so a "
                "restart re-adopts the job without them. A wrong status chip is the symptom; the lifecycle writer is "
                "the root."),
    "cron": ("C12/C15 scheduled jobs",
             "- `hermes cron list` and `~/.hermes/cron/output/<job>/` for the failing run; its script under "
             "~/.hermes/scripts; the delivery target; `errors.log` at run time."),
    "agent": ("agent behaviour (C03-C09)",
              "- The session transcript: `sqlite3 -readonly ~/.hermes/state.db \"select role, substr(content,1,800) "
              "from messages where session_id='<sid>' order by id\"` on the host that ran it, else the coach digest "
              "`~/code/hermes-coach-data/<host>/<profile>/<sid>.json`.\n"
              "- What instruction pointed the agent there: the skill(s) loaded, ~/AGENTS.md rules, tool descriptions; "
              "`git log` of the skill. The fix is usually a skill/tool/AGENTS.md change plus a check that fires at "
              "the failure point (a coach rule or a tool-result hint), not more prose."),
}
CHECKLIST["bot"] = CHECKLIST["gateway"]
CHECKLIST["provider"] = ("provider / model calls",
                         "- `~/.hermes/logs/agent.log` + `errors.log` at the time: provider, status code, retries, "
                         "fallback chain; `hermes config get model` and the fallback plugin's state.")
CHECKLIST["browser"] = CHECKLIST["plugin"]


def investigation_brief(d: dict, previous: Optional[dict] = None, cfg: Optional[dict] = None,
                        remote: bool = False) -> str:
    """The investigation card. ``remote`` (the hub's lane, see :func:`remote_lane`): a pool worker runs it, reads
    the gateway over hub-read, stages the proposal and records every lane event there through hub-admin."""
    text = _investigation_brief(d, previous, cfg)
    if not remote:
        return text
    fp = d["fingerprint"]
    stage = f".hermes/coach/selfheal/{fp}/proposal.md"  # relative to the gateway's $HOME, where hub-admin starts
    text = text.replace(f"write the proposal to ~/.hermes/coach/selfheal/{fp}/proposal.md",
                        "write the proposal to `proposal.md` here")
    text = text.replace(
        "   Do NOT post it to the Inbox. Record it:",
        f"   Stage it on the gateway: `hub-admin \"mkdir -p {os.path.dirname(stage)} && echo $(base64 -w0 proposal.md) | "
        f"base64 -d > {stage}\"`\n   (double quotes: the `$(...)` expands here) and read it back with "
        f"`hub-admin 'head -n 3 {stage}'`.\n   Do NOT post it to the Inbox. Record it:")
    return remote_body(text)


def _investigation_brief(d: dict, previous: Optional[dict] = None, cfg: Optional[dict] = None) -> str:
    fp = d["fingerprint"]
    comp = d.get("component") or "other"
    when = _pt(d.get("at") or time.time())
    sid = d.get("session_id") or ""
    title, checks = CHECKLIST.get(comp, CHECKLIST["agent"])
    extra = "" if comp == "agent" else "\n" + CHECKLIST["agent"][1]
    rec = ""
    if previous:
        rec = (f"\n## Recurrence\nThis fingerprint was fixed before by card {previous.get('fix_card') or '?'} "
               f"({_pt(previous.get('fixed_at') or 0)}). The previous fix {previous.get('fix_card') or ''} didn't "
               "hold. Say so in the proposal's first line, find why the earlier fix and its test didn't catch the "
               "return (five-whys step 5), and the Plan MUST add a detection-level guard (test, assertion, alert or "
               "gate) that fails if it comes back.\n")
        held = [h for h in (previous.get("held") or []) if h.get("at")]
        if held:
            rec += ("\nHeld inside the rollout window (seen soon after the fix was pushed, so possibly a host still "
                    "on old code; filed now because it kept happening or came after the window):\n" +
                    "".join(f"- {_pt(h['at'])} on {h.get('host') or '?'} ({h.get('source') or '?'})\n" for h in held))
    proposal = f"~/.hermes/coach/selfheal/{fp}/proposal.md"
    evidence = fence_evidence(f"""- Symptom: {d.get('symptom') or '?'}
- Component: {comp} · detector: {d.get('source') or '?'} ({d.get('kind') or '?'}, p={d.get('p_defect', '?')})
- When: {when} · host: {d.get('host') or '?'} · session: {('@session:' + (d.get('profile') or 'default') + '/' + sid) if sid else 'none (no chat, e.g. a cron run)'}
- Quote: "{(d.get('quote') or '').strip()[:500]}"
{('- Extra: ' + str(d.get('details'))[:1500]) if d.get('details') else ''}
{('- Stop evidence (captured on ' + str(d.get('stop_host') or d.get('host') or '?') + '): cause ' +
  str(d.get('stop_cause')) + (' — ' + str(d.get('stop_evidence'))[:600] if d.get('stop_evidence') else
  ' (no update receipt, mods-reload, gateway exit or sleep line in the window)'))
 if d.get('stop_cause') else ''}""")
    return f"""Self-heal investigation for defect `{fp}`. Nothing here needs a human; David's standing approval of this
lane covers the investigation AND the fix. You do NOT ship the fix yourself: you write the proposal, and the lane's
next pass auto-approves it and starts the fix card. Post NOTHING to the Inbox (David only wants finished fixes there).
Budget for this card <= ${INVESTIGATION_BUDGET}.
{home_pin_note(PIN_INVESTIGATION)}

## Evidence (from the detector)
{evidence}
{rec}
## Do this
1. Reproduce or confirm it LIVE, not from the transcript alone. Scope it from the user's own words and the session's
   open work (its cards, background processes, earlier turns), not only the detector's symptom: the trigger can point
   at a different event than the one that hurt him. Checklist for {title}:
{checks}{extra}
   Other hosts: the coach digests in ~/code/hermes-coach-data/<host>/ (git pull first) carry every session's user
   turns, tool counts and last reply. A host's state.db / kanban.db / logs are only readable ON that host: if the
   incident ran elsewhere, use what reaches it (ssh diag, digests, git) and list in the proposal what you could not
   read.
2. Before writing the chain, name 2-3 candidate roots and run the one check that tells them apart. The root is the
   writer of the wrong state (code path, env, config, skill rule), not the surface that displayed it or the turn the
   detector fired on. Then write the five-whys chain from evidence (five-whys skill): one causal step per why, each
   with a file:line, log line, db row or quote; label guesses `(hypothesis)`; branch "why it happened" and "why
   nothing caught it"; stop at a process/guard/design gap Hermes controls. Post the chain as a comment on this card.
3. A real defect already owned by another card is a duplicate, NOT not_defect. Record
   `{SELF_EVENT} duplicate {fp} --owner <owner-card> --note "<evidence>"`,
   then complete this card as a duplicate. Post nothing to the Inbox; the owner retains the real defect.
   A countermeasure you recommend (the owner could/should..., a follow-up) is a `kanban_create` card whose id is in
   --note; the event refuses a note that recommends one without naming its card (it would be dropped).
   Not a real defect (expected behaviour, user's own setting, outside world with nothing to guard)? Only after you
   checked the user's complaint itself, not just the detector's trigger. Run
   `{SELF_EVENT} not_defect {fp} --note "<why>"` and complete this card with a
   result that starts with "not a defect:". Post nothing to the Inbox.
4. Otherwise write the proposal to {proposal} (markdown, <= 3500 chars, plain words, no jargon) with exactly these
   sections:
   **What happened**: the symptom, when (PT), the `@session:<profile>/<id>` link, his quote.
   **Why**: the five-whys chain, one line per why, each with its evidence.
   **Plan**: the exact changes (files/skills/config), the regression test (fails before, passes after), the
   detection guard, and what gets verified live after shipping.
   **Cost / risk**, then **Undo**: how to revert. The first line is a one-line title: what is broken.
   Do NOT post it to the Inbox. Record it: `{SELF_EVENT} proposed {fp}`
   (the lane auto-approves it within 5 minutes and files the fix card from this proposal).
5. Complete this card with the proposal's first line as the summary. Do not start the fix.
"""


def _proposal_text(fp: str, st: dict) -> str:
    proposal = coach_root() / "selfheal" / fp / "proposal.md"
    try:
        return proposal.read_text()[:6000]
    except Exception:
        return f"(proposal file {proposal} missing: read the investigation card {st.get('card') or '?'} comments)"


fenced_evidence = fence_evidence  # the old name; the shared helper lives in placement_evidence.py


def split_fix(st: dict, board: Optional[str]) -> bool:
    """Default-board, non-bb fixes run as a portable FIX card plus a VERIFY child here; bb keeps one card."""
    return st.get("component") != "bb" and (not board or board == "default")


def portable_fix_brief(fp: str, st: dict) -> str:
    """The FIX card: repo work only (reproduce, red test, fix, push). No host paths or lane commands in the work
    text, and the proposal (incident evidence) is fenced, so the hub's worker pool may run it. Its handoff is read
    by the VERIFY child, which runs here."""
    incident = f"Defect: {fp}\nSymptom: {st.get('symptom') or fp}"
    # Old proposals can still prescribe core shipping. Annotate only this rendered
    # copy: preserve the source proposal verbatim for VERIFY and investigation.
    proposal = _proposal_text(fp, st).replace("hermes-core-wt", "[core shipping delegated to VERIFY]")
    return f"""Self-heal FIX for the defect identified in the evidence below.
The proposal is approved (David's standing self-heal approval;
the lane auto-approves every proposal). Post nothing to the Inbox. Investigation card: {st.get('card') or '?'}.
Budget for this card <= ${FIX_BUDGET}.

This card may run on a worker node. It does the repo part: reproduce, red test, fix, push. A child VERIFY card on
the gateway that filed it does the live check and records the result, from your completion handoff. So never run
the lane's `self_heal.py event` command here, and don't read the gateway's own state files: the proposal below is
the evidence you get (it describes where it happened, not where you work).

## Approved proposal (evidence)
{fenced_evidence(incident)}
{fenced_evidence(proposal)}

## Acceptance (all of it, in order)
1. Re-check the defect still reproduces on the current code of the repo(s) it names (fresh clone or worktree on
   origin). Already fixed? Complete the card with a summary starting `not a defect: already fixed by <sha>` and
   metadata `{{"not_defect": "already fixed by <sha>"}}`; the verify card records it.
2. Write the regression test FIRST and show it FAILING on the current code (paste the failing output in a card
   comment).
3. Make the fix from the plan (root level, not just the symptom) and the detection guard. Show the same test
   PASSING, plus the module's existing tests.
4. Ship only hermes-mods and state changes under David's standing merge/deploy approval: hermes-mods commit +
   push (pre-commit hook, never --no-verify), settings through shared-config.json (hermes-mods-repo skill).
   Do not ship core from this card. Put the decided core change (files, behaviour, red test) in metadata
   `core_change`; the VERIFY child files it where core ships.
5. If part of the plan can only be done on the gateway host itself (its local settings, scheduled jobs or
   databases), do the repo part and list the rest, exact commands included, in metadata `host_steps`; the verify
   card runs them. Don't block for it.
6. Complete the card with metadata `{{"fix_shas": ["<hermes-mods and state commit sha>"],
   "core_change": "<decided core spec, or empty>", "red": "<failing
   test output>", "green": "<passing output>", "live_check": "<what the verify card must check, and where>",
   "host_steps": [...], "undo": "<revert command>"}}`. If something irreversible or external blocks the fix,
   kanban_block with the exact ask instead.
"""


def verify_brief(fp: str, st: dict, fix_card: str, remote: bool = False) -> str:
    """The VERIFY child: host steps, live check, and the lane event, from the fix's handoff. ``remote`` (the hub's
    lane): a pool worker does every gateway step through `hub-admin '<command>'`; else it is machine-pinned here."""
    if remote:
        return remote_verify_brief(fp, st, fix_card)
    return f"""Self-heal VERIFY for defect `{fp}`: owner/fix card {fix_card} (its parent here).
{home_pin_note(PIN_VERIFY)}
{fence_evidence('Symptom to verify: ' + str(st.get('symptom') or fp))}
A completed card or a commit in its metadata is not proof that this symptom was fixed. The owner may be an
adopted investigation or unrelated session card, not a self-heal fix card. First confirm the handoff addresses
this exact defect. If investigation led to another fix card, follow that handoff and wait for its delivery.
If no fix exists, repair the actual symptom here, or create/link a scoped repair owner and wait on it before
recording fixed (do not call a real unresolved defect not_defect). Clear any mistaken adoption in your report.
A false detection requires evidence before `event not_defect`.
You run
on the host that filed it, because the live check and the lane record need this host's coach store and logs.
Post nothing to the Inbox. Budget for this card <= ${INVESTIGATION_BUDGET}.

1. Read the fix card's handoff: `kanban_show(task_id="{fix_card}")` (summary, metadata `fix_shas`, `core_change`,
   `red`, `green`, `live_check`, `host_steps`, `undo`, or `not_defect`).
2. `core_change` set? File one core card with `kanban_create`, assignee="default",
   idempotency_key="selfheal-core:{fp}", body = that decided spec plus "land with `hermes-core-wt ship` after a
   green 'All required checks pass (v2)' and an independent exact-head review". Use `kanban_link` to make that
   core card this VERIFY card's parent, then `kanban_schedule` until it lands. On resume, read its handoff and
   include its merge sha in the deploy check below and the recorded fix shas. Reuse the idempotent card, never
   file another core owner for this defect.
3. `not_defect` set? Confirm the cited sha is on the deployed checkout, run
   `{SELF_EVENT} not_defect {fp} --card {fix_card} --note "<its not_defect text>"` and complete. Stop.
4. Confirm each fix sha is deployed here (`git -C <checkout> merge-base --is-ancestor <sha> HEAD` for the live
   checkout that runs it; a hermes-mods change deploys within 10 min, core through hermes-sync). Not yet deployed
   after 30 min? kanban_schedule a re-check; don't record fixed on undeployed code.
5. Run every `host_steps` entry. Verify live on the real path (the surface/command/session type where it
   happened) as `live_check` says, and capture the proof. A live probe that makes a detector fire on purpose starts
   its first prompt with `[selfheal-probe]` (e.g. `hermes chat -q '[selfheal-probe] ...'`): the lane then logs
   `selfheal_skip why=probe` and files nothing. Check that log line; never delete selfheal_pending rows by hand.
   The live check fails? Comment the evidence, then kanban_block naming what still fails; do not record fixed.
6. Record the result QUIETLY. The Inbox is for decisions only: do NOT run `hermes inbox post`. Write `result.md`
   (what changed with commits, the red/green test output from the handoff, your live proof, the undo command) and
   run `{SELF_EVENT} fixed {fp} --card {fix_card} --note "<every fix sha, space-separated>" --result result.md`.
   That stores it under <coach>/selfheal-results/{fp}.md and logs selfheal_fixed.
7. Complete the card with the live proof in the summary.
"""


def remote_verify_brief(fp: str, st: dict, fix_card: str) -> str:
    """The pool VERIFY child for the hub's lane (see :data:`VERIFY_POOL_LABELS`). Every step that reads or writes
    the gateway (deploy check, host steps, live check, the lane `event`) runs through `hub-admin '<command>'`, so the
    work text names no local state path or host word: the runs_on attestations are the declared need."""
    stage = f"{VERIFY_REMOTE_DIR}/{fp}.md"
    coach = '"$HOME/.hermes/coach"'  # expanded by the gateway's shell inside hub-admin's single quotes
    return f"""Self-heal VERIFY for defect `{fp}`: owner/fix card {fix_card} (its parent here).
This card runs on a pool worker. The lane it records into lives on the gateway that filed it, so EVERY step that
reads or changes that gateway runs there through `hub-admin '<command>'` (single-quoted, so `$HOME` and `~` expand
on the gateway, not here). Never run the lane's `event` command, a live check or a host step locally: here it would
write this worker's own empty coach store. Repo reads (git log, a fresh clone) are fine locally.
{fence_evidence('Symptom to verify: ' + str(st.get('symptom') or fp))}
A completed card or a commit in its metadata is not proof that this symptom was fixed. The owner may be an
adopted investigation or unrelated session card, not a self-heal fix card. First confirm the handoff addresses
this exact defect. If investigation led to another fix card, follow that handoff and wait for its delivery.
If no fix exists, repair the actual symptom, or create/link a scoped repair owner and wait on it before
recording fixed (do not call a real unresolved defect not_defect). Clear any mistaken adoption in your report.
A false detection requires evidence before `event not_defect`.
Post nothing to the Inbox. Budget for this card <= ${INVESTIGATION_BUDGET}.

1. Read the fix card's handoff: `kanban_show(task_id="{fix_card}")` (summary, metadata `fix_shas`, `core_change`,
   `red`, `green`, `live_check`, `host_steps`, `undo`, or `not_defect`).
2. `core_change` set? File one core card with `kanban_create`, assignee="default",
   idempotency_key="selfheal-core:{fp}", body = that decided spec plus "land with `hermes-core-wt ship` after a
   green 'All required checks pass (v2)' and an independent exact-head review". Use `kanban_link` to make that
   core card this VERIFY card's parent, then `kanban_schedule` until it lands. On resume, read its handoff and
   include its merge sha in the deploy check below and the recorded fix shas. Reuse the idempotent card, never
   file another core owner for this defect.
3. `not_defect` set? Confirm the cited sha is deployed (step 4's command), run
   `hub-admin '{SELF_EVENT} not_defect {fp} --card {fix_card} --note "<its not_defect text>"'` and complete. Stop.
4. Confirm each fix sha is deployed on the gateway: `hub-admin 'git -C <checkout> merge-base --is-ancestor <sha>
   HEAD'` (exit 0) for the live checkout that runs it (`~/code/hermes-mods` for a hermes-mods change, which deploys
   within 10 min; the core checkout for core). Not yet deployed after 30 min? kanban_schedule a re-check; don't
   record fixed on undeployed code.
5. Run every `host_steps` entry through `hub-admin '<command>'`. Verify live on the real path (the
   surface/command/session type where it happened) as `live_check` says, through `hub-admin`, and capture the
   proof. A live probe that makes a detector fire on purpose starts its first prompt with `[selfheal-probe]`
   (e.g. `hub-admin 'hermes chat -q "[selfheal-probe] ..."'`): the lane then logs `selfheal_skip why=probe` and
   files nothing. Check that line with `hub-admin 'grep selfheal_skip {coach}/log.jsonl | tail -n 3'`; never
   delete selfheal_pending rows by hand. The live check fails? Comment the evidence, then kanban_block naming what
   still fails; do not record fixed.
6. Record the result QUIETLY. The Inbox is for decisions only: do NOT run `hermes inbox post`. Write `result.md`
   here (what changed with commits, the red/green test output from the handoff, your live proof, the undo
   command), stage it on the gateway and record it there:
   `hub-admin "mkdir -p {VERIFY_REMOTE_DIR} && echo $(base64 -w0 result.md) | base64 -d > {stage}"` (double quotes:
   the `$(...)` expands here), then
   `hub-admin '{SELF_EVENT} fixed {fp} --card {fix_card} --note "<every fix sha, space-separated>" --result {stage}'`.
   That stores it under <coach>/selfheal-results/{fp}.md and logs selfheal_fixed. Read both back:
   `hub-admin 'cmp {stage} {coach}/selfheal-results/{fp}.md && grep selfheal_fixed {coach}/log.jsonl | tail -n 1'`.
7. Complete the card with the live proof and that readback in the summary.
"""


def fix_brief(fp: str, st: dict) -> str:
    text = _proposal_text(fp, st)
    bb = st.get("component") == "bb"
    ship = ("Ship it through the bb-self-fix procedure (PR, merge, deploy) under David's standing approval."
            if bb else
            "Ship it under David's standing merge/deploy approval: hermes-mods commit + push (pre-commit hook, never "
            "--no-verify), core changes through a hermes-core-wt worktree + ship, settings through shared-config.json "
            "(hermes-mods-repo skill).")
    return f"""Self-heal FIX for defect `{fp}`. The proposal below is approved (David's standing self-heal approval;
the lane auto-approves every proposal). Post nothing to the Inbox: the result is recorded quietly (step 6).
Investigation card: {st.get('card') or '?'}. Budget for this card <= ${FIX_BUDGET}.
{home_pin_note(PIN_FIX)}

## Approved proposal (evidence)
{fence_evidence(st.get('symptom') or '')}
{fence_evidence(text)}

## Acceptance (all of it, in order)
1. Re-check the defect still reproduces on the current code (it may have been fixed meanwhile: then say so, run
   `{SELF_EVENT} not_defect {fp} --note "already fixed by <sha>"` and stop).
2. Write the regression test FIRST and show it FAILING on the current code (paste the failing output in a card
   comment).
3. Make the fix from the plan (root level, not just the symptom) and the detection guard. Show the same test
   PASSING, plus the module's existing tests.
4. {ship}
5. Verify live on the real path (the surface/command/session type where it happened), and capture the proof.
   A live probe that makes a detector fire on purpose starts its first prompt with `[selfheal-probe]` (e.g.
   `hermes chat -q '[selfheal-probe] ...'`): the lane then logs `selfheal_skip why=probe` and files nothing.
   Check that log line; never delete selfheal_pending rows by hand (a deferred retry refiles them).
6. Record the result QUIETLY. The Inbox is for decisions only: do NOT run `hermes inbox post` for a finished fix
   (David archived those unread and the inbox muted the poster). Write `result.md` (what changed with commits,
   the red/green test output, the live proof, and the undo command) and run
   `{SELF_EVENT} fixed {fp} --note "<commit sha>" --result result.md`.
   That stores the result under <coach>/selfheal-results/{fp}.md and logs selfheal_fixed; the card's
   kanban_complete summary carries it too. Nothing reaches the Inbox. Put EVERY commit sha of the fix in the
   note (core and hermes-mods, space-separated): a later occurrence checks whether the checkout that raised it
   contains one, and files a backport card for a stale pinned runtime instead of "the fix didn't hold".
7. Complete the card. If something irreversible or external blocks the fix, kanban_block with the exact ask instead.
"""


class LaneState:
    """<coach>/selfheal_lane.json: fingerprint -> {status, card, fix_card, component, symptom, filed_day, snooze_until,
    suppressed, fixed_at, recurrences, refiles, gen, cards, history, held}. `held` = occurrences seen inside the
    rollout window after fixed_at ([{at, host, source, seen}]), cleared by the next filing or fix.
    Statuses: filed, proposed, approved, fixed,
    not_defect, declined, suppressed, reverted, stale."""

    def __init__(self, root: Path):
        self.path = Path(root) / "selfheal_lane.json"

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def get(self, fp: str) -> dict:
        return dict(self.read().get(fp) or {})

    @contextlib.contextmanager
    def _locked(self):
        """Serialize read-modify-write of the lane file across processes (lane cron, signals cron, every card's
        `event ...` CLI): an unlocked rewrite of the whole file drops a concurrent writer's change."""
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _apply(self, fp: str, kw: dict) -> dict:
        d = self.read()
        e = d.get(fp) or {}
        hist = e.get("history") or []
        if "status" in kw and kw["status"] != e.get("status"):
            hist = (hist + [[round(time.time()), kw["status"]]])[-30:]
        e.update(kw, history=hist)
        d[fp] = e
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.path)
        return e

    def update(self, fp: str, **kw) -> dict:
        with self._locked():
            return self._apply(fp, kw)

    def transition(self, fp: str, expect: dict, **kw) -> Optional[dict]:
        """update() only while the entry still holds every `expect` field; None (nothing written) otherwise.
        For a writer that decided from an earlier read: reconcile walks a snapshot for minutes, and a receipt
        (`event fixed` / `not_defect`) recorded meanwhile must not be overwritten by its `stale`."""
        with self._locked():
            cur = self.read().get(fp) or {}
            if any(cur.get(k) != v for k, v in expect.items()):
                return None
            return self._apply(fp, kw)


def _utc_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _next_utc_midnight(ts: float) -> str:
    return time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime((int(ts) // 86400 + 1) * 86400))


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _budget_mod():
    """engine_budget from the hermes-coach skill (same repo), or None."""
    import importlib.util
    import sys
    p = Path(__file__).resolve().parents[2] / "skills" / "productivity" / "hermes-coach" / "scripts" / "engine_budget.py"
    if not p.exists():
        return None
    name = "_selfheal_engine_budget"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(name, mod)
    return mod


@contextlib.contextmanager
def _admission_lock(store, live: bool = True, timeout: float = 180.0):
    """One lock across every admission path (lane cron, signals cron, Do-it sync, CLI): budget read + card
    create is a single transaction, so two concurrent admissions cannot both see the same headroom. A lock
    that cannot be taken in `timeout` seconds fails closed (RuntimeError) instead of admitting unserialized."""
    if not live:
        yield
        return
    import fcntl
    Path(store.root).mkdir(parents=True, exist_ok=True)
    with (Path(store.root) / "selfheal-admission.lock").open("a") as lock:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise RuntimeError("self-heal admission lock busy")
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def budget(board: Optional[str] = None, now: Optional[float] = None,
           new_card_usd: float = INVESTIGATION_BUDGET) -> dict:
    """{"usd", "cap", "running", "ok_usd", "ok_running", "why"} for the selfheal lane today (UTC).
    ok_usd reserves in-flight work: every running/ready/todo selfheal card's unspent card budget plus
    `new_card_usd` for the card being admitted must still fit under the cap (spend alone is a lagging
    measure; the Oct 28 overshoot was $268 against $200)."""
    now = now or time.time()
    out = {"usd": None, "cap": USD_DAY, "running": None, "ok_usd": False,
           "ok_running": False, "available": False, "why": "", "reserved": None}
    home = hermes_home()
    b = board or os.environ.get("COACH_KANBAN_BOARD") or "default"
    try:
        eb = _budget_mod()
        if eb is None:
            raise RuntimeError("accounting module unavailable")
        snap = eb.snapshot(home=home, board=b, now=now)
        usd = snap["lanes"]["selfheal"]
        cap = snap["caps"]["selfheal"]
        import math
        if (not isinstance(usd, (int, float)) or not isinstance(cap, (int, float))
                or not math.isfinite(usd) or not math.isfinite(cap) or usd < 0 or cap <= 0):
            raise ValueError("invalid self-heal spend or cap")
        inflight = [c for c in eb.engine_cards(home, b, ("running", "ready", "todo")) if c["lane"] == "selfheal"]
        running = sum(1 for c in inflight if c.get("status", "running") == "running")
        # A just-created card is `ready` until the dispatcher claims it: counting only `running` let one signals pass
        # admit 5 investigations in 10 s against a cap of 3 (t_01a1c00ca6bc0945). Admitted = running + ready.
        busy = [c for c in inflight if c.get("status", "running") in ("running", "ready")]
        occupied_fix = sum(1 for c in busy if str(c.get("title") or "").startswith(FIX_TITLE))
        reserved = float(new_card_usd)
        holders = []
        for c in inflight:
            # inv_key is selfheal:<fingerprint>[:rN]; all other kinds default to FIX.
            key = str(c.get("idempotency_key") or "")
            default = (INVESTIGATION_BUDGET if re.fullmatch(r"selfheal:[^:]+(?::r[1-9][0-9]*)?", key)
                       else FIX_BUDGET)
            # Only admission substitutes this fallback. The watchdog's original snapshot
            # and card_budget()/decide() semantics remain untouched.
            admission_snap = {**snap, "caps": {**snap["caps"], "selfheal": default}, "total_cap": default}
            cb = eb.card_budget(c, admission_snap) if hasattr(eb, "card_budget") else float(default)
            spent = eb.card_cost(home, b, c["id"]) if hasattr(eb, "card_cost") else 0.0
            if not all(math.isfinite(float(v)) and float(v) >= 0 for v in (cb, spent)):
                raise ValueError("invalid card budget or spend")
            remaining = max(0.0, float(cb) - float(spent))
            holders.append({"card": c["id"], "reserved": remaining})
            reserved += remaining
        ok, why = eb.check(snap, "selfheal", reserved)
        if not ok and usd < cap:
            why = f"{why} (spend ${usd:.2f} + ${reserved:.2f} reserved for in-flight and the new card)"
        # Commit the observation only after ALL reads succeed. Unknown is not zero spend.
        out.update(usd=usd, cap=cap, running=running, ok_usd=ok, reserved=round(reserved, 2),
                   occupied=len(busy), occupied_fix=occupied_fix, holders=holders, new_card_usd=new_card_usd,
                   ok_running=len(busy) < MAX_RUNNING, available=True, why=why)
    except Exception as exc:
        out["why"] = f"budget read failed: {exc}"
    return out


def _budget_unavailable(b: dict, fp: str, store) -> Optional[dict]:
    """Refuse creation, not create-then-schedule, when accounting cannot be read."""
    if b.get("available", True):  # legacy injected budget functions retain their contract
        return None
    why = b.get("why") or "budget unavailable"
    store.log({"event": "selfheal_budget_unavailable", "fingerprint": fp, "why": why})
    return {"action": "error", "why": why}


def note_reservation_starved(store, b: dict, now: float, runner=None) -> None:
    """Caller holds the admission lock. One daily event and one open incident owner, never Inbox."""
    if (not b.get("available", False) or b.get("ok_usd") or b.get("usd") != 0
            or b.get("reserved", 0) < b.get("cap", float("inf"))):
        return
    path = Path(store.root) / "selfheal-reservation-starved.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    owner = state.get("card")
    # A closed owner ends an incident, including a recurrence on the same UTC day.
    # An unreadable status is not evidence of closure: retain the owner on read failure.
    if owner and card_status(owner, None, runner) in ENDED:
        state = {"incident": int(state.get("incident", 1)) + 1}
        owner = None
    state.setdefault("incident", 1)
    now = store.clock()
    day = _utc_day(now)
    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, path)
    if state.get("day") != day:
        state["day"] = day
        save()
        store.log({"event": "selfheal_reservation_starved", "utc_day": day, "ts": now,
                   "holders": b.get("holders", []), "reserved": b["reserved"], "cap": b["cap"],
                   "new_card_usd": b.get("new_card_usd")})
    if state.get("delivered_day") == day:
        return
    body = (f"Self-heal admission starved at $0 spend on {day}: ${b['reserved']:.2f} reserved "
            f"against ${b['cap']:g} cap. Holding cards (unspent reservations):\n" +
            "\n".join(f"- {h['card']}: ${h['reserved']:.2f}" for h in b.get("holders", [])) +
            f"\nNew admission: ${b.get('new_card_usd', 0):g}.\n"
            "Inspect the holding cards and admission accounting. Fix the root cause without raising the cap "
            "or cancelling legitimate work. Verify admission resumes. Post nothing to Inbox.")
    if owner:
        rc, _, _ = _run(["hermes", "kanban", "comment", owner, body], runner)
        delivered = rc == 0
    else:
        result = _lane_card("self-heal lane: reservations starve admission at zero spend", body,
                            f"selfheal-reservation-starved:{host_name()}:{day}:{state['incident']}", runner)
        delivered = bool(result.get("card"))
        if delivered:
            state["card"] = result["card"]
    if delivered:
        state["delivered_day"] = day
        save()


def _admission_denied(b: dict, now: float, store=None, runner=None) -> Optional[dict]:
    """A known denial must never create a dispatchable card, even briefly."""
    if not b["ok_usd"]:
        if store is not None:
            try:
                note_reservation_starved(store, b, now, runner)
            except Exception as exc:
                store.log({"event": "selfheal_reservation_alert_error", "why": str(exc)[:300]})
        return {"action": "deferred", "retry_at": _next_utc_midnight(now),
                "why": b.get("why") or "self-heal daily cap reached"}
    if not b["ok_running"]:
        return {"action": "deferred", "retry_at": _iso(now + 3600),
                "why": "self-heal concurrency limit reached"}
    return None


def _log_deferral_once(store, fp: str, denied: dict, b: dict, now: float, stage: str = "fix") -> None:
    """Caller holds _admission_lock. Persist before logging: at most once even across process restarts.

    Budget amounts/retry_at change on each pass; the reason is the admission gate, not those metrics.
    Keep only today's keys, independently of lane generations and state transitions.
    """
    path = Path(store.root) / "selfheal-deferrals.json"
    stamp = store.clock()  # sync may have started before midnight; key and logged ts must agree
    day = _utc_day(stamp)
    reason = "daily_cap" if not b["ok_usd"] else "concurrency"
    key = f"{stage}:{fp}:{reason}"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("day") != day:
        state = {"day": day, "keys": []}
    if key in state["keys"]:
        return
    state["keys"].append(key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)
    store.log({"event": f"selfheal_{stage}_deferred", "fingerprint": fp, **denied,
               "reason": reason, "utc_day": day, "ts": stamp})


def fixes_waiting(store, auto: Optional[bool] = None) -> int:
    """Proposals the lane will turn into fix cards (status proposed, auto-approve on). 0 when approval is manual:
    those wait on David, not on a slot."""
    if not (auto_approve() if auto is None else auto):
        return 0
    try:
        return sum(1 for st in LaneState(store.root).read().values() if st.get("status") == "proposed")
    except Exception:
        return 0


def _fix_first_denied(b: dict, store, now: float) -> Optional[dict]:
    """Finish before starting: while proposals wait for fix cards, a new investigation may take only the one
    investigation slot (INVESTIGATION_SLOTS_WHILE_FIXES_WAIT) and must leave FIX_BUDGET of dollar headroom. Without
    this every freed slot went to the next investigation and 40 approved fixes waited up to 112 h
    (t_01a1c00ca6bc0945). Budget dicts without `occupied` (injected legacy budget functions) keep the old gate."""
    if "occupied" not in b:
        return None
    waiting = fixes_waiting(store)
    if not waiting:
        return None
    investigations = int(b.get("occupied") or 0) - int(b.get("occupied_fix") or 0)
    if investigations >= INVESTIGATION_SLOTS_WHILE_FIXES_WAIT:
        return {"action": "deferred", "retry_at": _iso(now + 3600),
                "why": f"fix-first: {waiting} proposal(s) wait for fix cards and {investigations} investigation(s) "
                       f"already hold the investigation slot"}
    try:
        need = float(b["usd"]) + float(b["reserved"]) + FIX_BUDGET
        cap = float(b["cap"])
    except (KeyError, TypeError, ValueError):
        return None
    if need >= cap:
        return {"action": "deferred", "retry_at": _next_utc_midnight(now),
                "why": f"fix-first: {waiting} proposal(s) wait for fix cards; this investigation would leave less "
                       f"than ${FIX_BUDGET} for one (${need:.2f} >= ${cap:g} cap)"}
    return None


def _pending_db(store):
    import sqlite3
    store.root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(store.root) / "selfheal_pending.sqlite", timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS pending (fp TEXT PRIMARY KEY, payload TEXT NOT NULL, "
               "retry REAL NOT NULL, version TEXT NOT NULL)")
    return db


def _defer_investigation(d: dict, result: dict, store):
    """Persist before acknowledging a denial; all producers may advance their cursors afterward."""
    import datetime
    import uuid
    retry = datetime.datetime.fromisoformat(result["retry_at"].replace("Z", "+00:00")).timestamp()
    db = _pending_db(store)
    try:
        with db:
            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?) ON CONFLICT(fp) DO UPDATE SET "
                       "payload=excluded.payload, retry=excluded.retry, version=excluded.version",
                       (d["fingerprint"], json.dumps(d), retry, uuid.uuid4().hex))
    finally:
        db.close()
    store.log({"event": "selfheal_deferred", "fingerprint": d["fingerprint"], **result})
    return result


def _defer_missing_board(d: dict, board: str, store, now: float) -> dict:
    """Hold the defect for retry. A missing board must not be a silent drop: the payload stays in
    ``selfheal_pending.sqlite`` and the cron refiles it. ``stop_pass`` is false so this does not halt
    other pending admissions (it is not a cap denial)."""
    result = {"action": "deferred", "why": f"kanban board {board!r} does not exist",
              "retry_at": _iso(now + MISSING_BOARD_RETRY_S), "board": board, "stop_pass": False}
    return _defer_investigation(d, result, store)


PENDING_MAX_AGE_S = 7 * 86400  # detections waiting longer than this are dropped, not filed late
_SOURCE_RANK = {"complaint": 0, "admission": 0, "signal": 1}  # in-chat judges, then machine signals (9/10 real in
# the Oct 2026 shadow sample), then the digest backstop (3/6) and anything else


def _detected_at(d: dict) -> Optional[float]:
    at = d.get("at")
    if isinstance(at, (int, float)):
        return float(at)
    if isinstance(at, str) and at:
        import datetime
        try:
            return datetime.datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _pending_priority(d: dict) -> tuple:
    """Drain order: complaints/admissions, then machine signals, then the digest backstop; within a class the
    likelier defect, then the newest. Input (~75 new fingerprints a day in the Oct 2026 shadow log) far exceeds the
    $200/day lane, so this order decides which defects get investigated at all."""
    try:
        p = float(d.get("p_defect") or 0)
    except (TypeError, ValueError):
        p = 0.0
    return (_SOURCE_RANK.get(d.get("source") or "", 2), -p, -(_detected_at(d) or 0))


CLOSED_CARD_STATUSES = ("archived", "done")
_KANBAN_DETAILS_RE = re.compile(r"\bboard (\S+) cards? ((?:t_[0-9a-zA-Z]+(?:,\s*)?)+)")
_BOARD_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def kanban_trigger(d: dict) -> Optional[tuple]:
    """(board, [card ids]) a kanban signal is about: the structured ``kanban`` key (signals.to_defect), else the
    ``board <b> card <id>`` details line that older pending payloads carry. None for other defects."""
    if d.get("kind") != "kanban" and d.get("component") != "kanban":
        return None
    k = d.get("kanban") or {}
    if k.get("board") and k.get("cards"):
        return str(k["board"]), [str(c) for c in k["cards"]]
    m = _KANBAN_DETAILS_RE.search(d.get("details") or "")
    if not m:
        return None
    return m.group(1), [c.strip() for c in m.group(2).split(",") if c.strip()]


def _kanban_board_path(board: str, home: Path) -> Optional[Path]:
    """signals.board_dbs labels: ``<board>`` on the default home, ``<profile>/<board>`` on a profile."""
    prof, _, name = board.rpartition("/")
    if not _BOARD_PART_RE.match(name) or (prof and not _BOARD_PART_RE.match(prof)):
        return None
    h = home / "profiles" / prof if prof else home
    return h / "kanban.db" if name == "default" else h / "kanban" / "boards" / name / "kanban.db"


HOLD_STALE_S = 6 * 3600  # a systemic hold filed later than this after it fired, with every held card closed, is history


def _cards_closed(board: str, cards: list, home: Optional[Path]) -> Optional[list]:
    """["<id> now <status>", ...] when every card is archived/done on the local board; None when any is open or the
    board/card cannot be read."""
    path = _kanban_board_path(board, Path(home) if home else _kanban_home())
    if not path or not path.is_file():
        return None
    import sqlite3
    try:
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.Error:
            db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
            db.execute("PRAGMA query_only=ON")
        try:
            got = []
            for c in cards:
                row = db.execute("SELECT status FROM tasks WHERE id=?", (c,)).fetchone()
                if not row or row[0] not in CLOSED_CARD_STATUSES:
                    return None
                got.append(f"{c} now {row[0]}")
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return got


def kanban_trigger_closed(d: dict, home: Optional[Path] = None, now: Optional[float] = None) -> Optional[str]:
    """Why to drop a kanban signal: every card it names is now archived/done on its (local) board, e.g. the owner
    closed it while the detection waited in the pending queue. A systemic hold (``kanban_hold``, signals.py
    _systemic_holds) is dropped only when it is also older than HOLD_STALE_S: a fresh hold is filed even when its
    held cards rerun and finish, since the crash cause may still be live (kanban-754a3e3cf1 filed a 2-day-old hold
    whose cards were archived). None (= file as before) when the signal is not a kanban one, came from another
    host's board, or the board/card cannot be read."""
    if d.get("host") and d["host"] != host_name():
        return None
    hold = d.get("kanban_hold")
    if isinstance(hold, dict) and hold.get("board") and hold.get("cards"):
        at = _detected_at(d)
        now = time.time() if now is None else now
        if at is None or now - at <= HOLD_STALE_S:
            return None
        got = _cards_closed(str(hold["board"]), [str(c) for c in hold["cards"]], home)
        if not got:
            return None
        return (f"stale systemic hold: cards {', '.join(got)} closed (hold {_iso(at)}, "
                f"> {HOLD_STALE_S // 3600}h before filing)")
    trig = kanban_trigger(d)
    if not trig:
        return None
    board, cards = trig
    got = _cards_closed(board, cards, home)
    return "trigger card " + ", ".join(got) if got else None


def _monitor_withdrawn(d: dict, store=None) -> Optional[str]:
    """Why the monitor that queued this finding no longer stands by it, else None. A detector fix must retract the
    findings it already queued (agent-ea7ff00170: ask-gate rows queued 32 min before the classifier learned that
    injected Kanban prompts are not David, then refiled as 39 Opus cards). Only positive evidence drops a row: the
    stored quote is truncated, so "no pattern matched" proves nothing; "the quote is a generated prompt" does.
    Any import failure files the row as before. The row is re-checked with the monitor's full current verdict
    (coach_askgate.still_flags: generated quote, detector already saw the turn, lane already filed the session), not
    only generated(): plugin-656a006c84 was queued 11 minutes before detection_for existed and filed anyway."""
    if d.get("monitor") != "ask-gate":
        return None
    try:
        import importlib.util
        p = (Path(__file__).resolve().parents[2] / "skills" / "productivity" / "hermes-coach" / "scripts"
             / "coach_askgate.py")
        spec = importlib.util.spec_from_file_location("_selfheal_askgate", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        homes = [Path(store.root).parent] if store is not None else None  # <home>/coach -> <home>
        return mod.still_flags(d, homes=homes)
    except Exception:
        return None
    return None


def _sqlite_incident_over(d: dict, store, now: float) -> Optional[str]:
    """Expire a quiet incident, not a later-hour recurrence on the same source DB."""
    inc = d.get("incident")
    at = _detected_at(d)
    if (not isinstance(inc, dict) or not inc.get("db") or not inc.get("host")
            or not isinstance(inc.get("hour"), int) or at is None or now - at <= 86400):
        return None
    for fp, state in LaneState(store.root).read().items():
        later = state.get("incident") if isinstance(state, dict) else None
        if (fp.startswith("sqlitecorrupt-") and isinstance(later, dict)
                and later.get("db") == inc["db"] and later.get("host") == inc["host"]
                and isinstance(later.get("hour"), int) and later["hour"] > inc["hour"]):
            return None
    return f"sqlite incident over (no occurrence since {_iso(at)})"


def _crash_evidence(text: str, host: str) -> Optional[dict]:
    """Conservative legacy evidence reader: explicit crash ranges, never detection/event timestamps.

    Require an exception class and a machine reason (e.g. storage_unavailable). General prose is not
    enough to suppress an investigation. Partial header ranges describe only the observed crashes.
    """
    import datetime
    text = str(text or "")
    error = re.search(r"\b([A-Z][A-Za-z0-9]*Error)\b[^\n]*?\b([a-z]+(?:_[a-z]+)+)\b", text)
    if not error or not host:
        return None
    # Stack function names are not machine error reasons. Prefer the final token after a colon.
    reasons = re.findall(r":\s*([a-z]+(?:_[a-z]+)+)\b", error.group(0) + text[error.end():].split("\n", 1)[0])
    if not reasons:
        return None
    stamp = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
    window = re.search(r"Crash times[^\n]*?earliest " + stamp + r", latest " + stamp, text)
    if not window:
        # Investigation comments also use a single explicit range with the end date elided.
        # Multiple ranges are ambiguous: do not combine an outage/deploy window with a crash window.
        # Legacy comments use "YYYY-MM-DD HH:MM:SS–HH:MM:SSZ": the final Z
        # explicitly scopes the range to UTC. Never infer UTC for an unzoned range.
        range_start = r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}Z?)"
        ranges = list(re.finditer(range_start + r"`?\s*(?:\.\.|–|—| - |to)\s*`?"
                                 r"((?:\d{4}-\d{2}-\d{2}[T ])?\d{2}:\d{2}:\d{2}Z)", text))
        window = ranges[0] if len(ranges) == 1 else None
    if not window:
        return None
    first, last = (t.replace(" ", "T") for t in window.groups())
    if not first.endswith("Z"):
        first += "Z"
    if "T" not in last:
        last = first.split("T")[0] + "T" + last
    try:
        start, end = (datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
                      for t in (first, last))
    except ValueError:
        return None
    if end < start:
        return None
    return {"host": host, "error": error.group(1), "reason": reasons[-1], "start": start, "end": end}


def _crash_card_evidence(st: dict, host: str) -> list:
    """Read legacy investigation evidence, not the owner card's unrelated incidents. No board writes."""
    import sqlite3
    if not isinstance(st, dict) or not isinstance(st.get("board", "default"), (str, type(None))):
        return []
    path = _kanban_board_path(st.get("board") or "default", _kanban_home())
    if not path or not path.is_file() or not st.get("card"):
        return []
    try:
        with contextlib.closing(sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)) as db:
            db.execute("PRAGMA query_only=ON")
            body = db.execute("SELECT body FROM tasks WHERE id=?", (st["card"],)).fetchone()
            texts = ([body[0]] if body else []) + [r[0] for r in db.execute(
                "SELECT body FROM task_comments WHERE task_id=?", (st["card"],))]
    except sqlite3.Error:
        return []
    # Host, exception and range must be in the SAME evidence record; never borrow the host from another comment.
    return [e for text in texts if isinstance(text, str)
            and re.search(r"(?<![\w.-])" + re.escape(host) + r"(?![\w.-])", text)
            if (e := _crash_evidence(text, host))]


def _resolved_crash_signal(d: dict, store) -> Optional[str]:
    try:
        return _resolved_crash_signal_checked(d, store)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None  # corrupt/old evidence must never prevent the ordinary admission pass


def _resolved_crash_signal_checked(d: dict, store) -> Optional[str]:
    """A sibling disposition is proof only with matching incident evidence and a locally deployed core fix.

    Duplicate maps to approved then stale in the lane, so consult lifecycle events, not status alone.
    Missing/rotated logs, unreadable cards, remote hosts and unknown commits all fail open to investigation.
    """
    if (d.get("source") != "signal" or d.get("host") != host_name()
            or "crash storm" not in str(d.get("symptom") or "")):
        return None
    evidence = _crash_evidence(str(d.get("symptom") or "") + "\n" + str(d.get("details") or ""), d["host"])
    if not evidence:
        return None
    lane = LaneState(store.root).read()
    dispositions = {}
    try:
        with (Path(store.root) / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                fp, event = row.get("fingerprint"), row.get("event")
                if fp not in lane or fp == d.get("fingerprint"):
                    continue
                if event in ("selfheal_duplicate", "selfheal_fixed"):
                    dispositions[fp] = row
                elif event in ("selfheal_reverted", "selfheal_filed", "selfheal_not_defect", "selfheal_declined"):
                    dispositions.pop(fp, None)
    except OSError:
        return None
    for fp, row in dispositions.items():
        if evidence["end"] > float(row.get("ts") or 0):
            continue  # a post-disposition crash is a recurrence, not a deferred old observation
        # A note often names old HEADs too: only accept commits explicitly attributed as the fix.
        shas = re.findall(r"(?i:\bfix(?:ed)?(?:\s+(?:by|in))?\s+)`?([0-9a-f]{7,40})\b", str(row.get("note") or ""))
        if not shas:
            continue
        for sibling in _crash_card_evidence(lane[fp], evidence["host"]):
            if (any(sibling[k] != evidence[k] for k in ("host", "error", "reason"))
                    or evidence["start"] > sibling["end"] + 7200
                    or sibling["start"] > evidence["end"] + 7200):
                continue
            if all(_git_rc(_fork_repo(), "merge-base", "--is-ancestor", sha, "HEAD")[0] == 0 for sha in shas):
                return f"resolved under {fp}"
    return None


def retry_deferred(store=None, now: Optional[float] = None, filer=None) -> list:
    """Recheck due investigations through normal admission, never automatically release a card.

    Highest priority first (`_pending_priority`); the pass stops at the first renewed denial, since every later row
    would hit the same cap. Rows whose detection is older than PENDING_MAX_AGE_S are dropped (`selfheal_skip`,
    why `stale pending`), which bounds the queue when input outruns the budget. Resolved sibling crash
    incidents are withdrawn before the cap-ordered filing pass, with `resolved under <fp>` evidence."""
    store = _store(store)
    now = now or time.time()
    if not (Path(store.root) / "selfheal_pending.sqlite").exists():
        return []
    db = _pending_db(store)
    out = []
    try:
        rows = db.execute("SELECT fp, payload, version FROM pending WHERE retry <= ?", (now,)).fetchall()
        due = []
        for fp, payload, version in rows:
            d = json.loads(payload)
            at = _detected_at(d)
            if at is not None and now - at > PENDING_MAX_AGE_S:
                with db:
                    db.execute("DELETE FROM pending WHERE fp=? AND version=?", (fp, version))
                store.log({"event": "selfheal_skip", "mode": "live", "fingerprint": fp,
                           "why": f"stale pending (detected {_iso(at)}, > {PENDING_MAX_AGE_S // 86400}d)"})
                out.append({"action": "skip", "why": "stale pending"})
                continue
            if synthetic_user_row(d):  # before the cap-ordered pass: a denial stops that pass before late rows
                with db:
                    db.execute("DELETE FROM pending WHERE fp=? AND version=?", (fp, version))
                closed_lane = _close_synthetic_lane(store, fp, now)  # an owner adopted earlier must get no VERIFY
                store.log({"event": "selfheal_withdrawn", "mode": "live", "fingerprint": fp, "why": "synthetic_tag",
                           "source": d.get("source"), "session_id": d.get("session_id"),
                           "quote": str(d.get("quote") or "")[:200],
                           **({"lane_closed": closed_lane} if closed_lane else {})})
                out.append({"action": "skip", "why": "synthetic_tag"})
                continue
            closed = (_sqlite_incident_over(d, store, now)
                      or kanban_trigger_closed(d, now=now) or _monitor_withdrawn(d, store)
                      or _resolved_crash_signal(d, store))
            if closed:  # the owner closed the card, or the monitor withdrew its verdict, while this waited
                with db:
                    db.execute("DELETE FROM pending WHERE fp=? AND version=?", (fp, version))
                store.log({"event": "selfheal_skip", "mode": "live", "fingerprint": fp, "why": closed})
                out.append({"action": "skip", "why": closed})
                continue
            due.append((_pending_priority(d), fp, d, version))
        due.sort(key=lambda r: r[0])
        for i, (_, fp, d, version) in enumerate(due):
            try:
                result = (filer or file_defect)(d, store=store, m="live", now=now)
            except Exception as exc:
                result = {"action": "error", "why": str(exc)[:300]}
            out.append(result)
            if result.get("action") in ("filed", "skip"):
                with db:
                    # Do not erase a newer detection written while this retry was executing.
                    db.execute("DELETE FROM pending WHERE fp=? AND version=?", (fp, version))
            elif result.get("action") == "deferred" and result.get("stop_pass", True):
                break  # a cap denial denies the rest; a missing board (stop_pass false) does not
            elif result.get("placement_refused"):
                # Core refused the create's placement (hub admission). Every later row would pay the same judge call
                # and get the same refusal (106 in 40 min on 2026-11-12): stop here, the rows stay pending.
                store.log({"event": "selfheal_pass_stopped", "why": "placement_refused", "fingerprint": fp,
                           "left": len(due) - i - 1})
                break
    finally:
        db.close()
    return out

def generation(st: dict) -> int:
    """Generations used so far for a fingerprint (0 = never filed). Lane files written before `gen` existed derive it
    from the old counters; a collision with an earlier card is caught in file_defect anyway."""
    if st.get("gen") is not None:
        return int(st["gen"])
    if not st.get("card"):
        return 0
    return 1 + int(st.get("recurrences") or 0) + int(st.get("refiles") or 0)


def inv_key(fp: str, gen: int) -> str:
    return f"selfheal:{fp}" if gen <= 1 else f"selfheal:{fp}:r{gen}"


def fix_key(fp: str, gen: int) -> str:
    return f"selfheal:{fp}:fix" if gen <= 1 else f"selfheal:{fp}:r{gen}:fix"


def verify_key(fp: str, gen: int) -> str:
    return f"selfheal:{fp}:verify" if gen <= 1 else f"selfheal:{fp}:r{gen}:verify"


def known_cards(st: dict) -> set:
    return {c for c in (st.get("cards") or []) + [st.get("card"), st.get("fix_card")] if c}


DEPLOY_GRACE_HOURS = 6.0  # "fixed" is stamped at push; hosts run old code until they update/restart (core updates
# defer up to 3 h while cards run). An occurrence inside this window after fixed_at is held, not a recurrence.
HELD_FILE_AFTER = 3  # ...unless the fingerprint is held this many times: a fix that really failed surfaces fast


def deploy_grace_s(cfg: Optional[dict] = None) -> float:
    """`deploy_grace_hours` in selfheal-config.json (0 = every occurrence after fixed is a recurrence)."""
    cfg = lane_config() if cfg is None else cfg
    try:
        return max(0.0, float(cfg.get("deploy_grace_hours", DEPLOY_GRACE_HOURS))) * 3600
    except (TypeError, ValueError):
        return DEPLOY_GRACE_HOURS * 3600


_FILE_FRAME = re.compile(r'File "([^"]+)"')


def fix_shas(note: str) -> list:
    """Commit shas named in a `fixed` event note (hex with at least one letter, so timestamps don't count)."""
    out = []
    for s in re.findall(r"\b[0-9a-f]{7,40}\b", str(note or "")):
        if re.search(r"[a-f]", s) and s not in out:
            out.append(s)
    return out


def _git_rc(repo, *args: str, stdin: Optional[str] = None) -> tuple:
    import subprocess
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, input=stdin, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return p.returncode, p.stdout


def _has_fix_content(top: str, sha: str, tb_rel: set) -> bool:
    """The fix's diff is already in this checkout's working tree (a cherry-pick or an uncommitted backport on a
    pinned HEAD): its reverse applies cleanly. Checked on the fix's files the traceback names, else its non-test
    files; a fix that touched neither can't be judged by content."""
    rc, out = _git_rc(top, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    if rc != 0:
        return False
    files = [f for f in out.splitlines() if f]
    pick = [f for f in files if f in tb_rel] or [f for f in files if not re.search(r"(^|/)tests?/|(^|/)test_", f)]
    if not pick:
        return False
    rc, patch = _git_rc(top, "diff", "--no-ext-diff", "--no-textconv", "--binary", f"{sha}^", sha, "--", *pick)
    if rc != 0 or not patch.strip():
        return False
    rc, _ = _git_rc(top, "apply", "-R", "--check", "-", stdin=patch)
    return rc == 0


def stale_checkout(d: dict, shas: list) -> Optional[dict]:
    """A post-fix occurrence raised by code that never got the fix: a traceback frame on THIS host lives in a git
    checkout (a pinned runtime worktree, e.g. ~/.hermes/askeve-runtime/core-<sha>) whose HEAD lacks a fix sha
    and whose files don't carry its diff either. -> {checkout, sha, head} or None when it can't tell (no sha,
    another host's paths, no readable checkout, a sha that checkout doesn't know) or every checkout has the fix."""
    if not shas or not d.get("host") or d.get("host") != host_name():
        return None
    tops: dict = {}
    for frame in reversed(_FILE_FRAME.findall(str(d.get("details") or ""))):
        path = os.path.expanduser(frame)
        if not os.path.isfile(path):
            continue
        rc, out = _git_rc(os.path.dirname(path), "rev-parse", "--show-toplevel")
        top = out.strip() if rc == 0 else ""
        if top:
            real = os.path.realpath(path)
            rel = os.path.relpath(real, os.path.realpath(top))
            tops.setdefault(top, set()).add(rel)
    for top, rel in tops.items():
        for sha in shas:
            rc, full = _git_rc(top, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}")
            full = full.strip()
            if rc != 0 or not full:
                continue  # not this repository's commit (e.g. the hermes-mods half of a fix)
            anc, _ = _git_rc(top, "merge-base", "--is-ancestor", full, "HEAD")
            if anc == 0 or anc is None or anc > 1:
                continue  # contains it (or git can't tell): not stale
            if _has_fix_content(top, full, rel):
                continue
            _, head = _git_rc(top, "rev-parse", "HEAD")
            return {"checkout": top, "sha": full, "head": head.strip()}
    return None


def backport_key(fp: str, checkout: str, sha: str) -> str:
    return f"selfheal:{fp}:backport:{sha[:12]}:{hashlib.sha1(checkout.encode()).hexdigest()[:8]}"


# ── sync-delivered fixes: has the host that raised it synced the fix yet? (cron-b2c33e9921) ──
# A fix on the private state branch reaches a host only when that host's hermes-sync merges and imports it; a laptop
# asleep for days runs the old script long after the 6 h rollout window. Each host's sync commits ("sync: <label>
# <date>") on origin/state show when its tree first contained the fix.
STATE_REPO = "~/.hermes/repos/hermes-state"
STATE_REF = "origin/state"
UNDEPLOYED_FILE_HOURS = 72.0  # a host still without the fix this long after it files one card instead of holding
_ON_LABEL = re.compile(r"\bon ([A-Za-z0-9][A-Za-z0-9._-]{0,62}[A-Za-z0-9])\b")


def _sync_grep(label: str) -> str:
    return "^sync: " + re.sub(r"([.\\^$*+?()\[\]{}|])", r"\\\1", label) + "( |$)"


def host_sync_labels(d: dict, cfg: Optional[dict]) -> list:
    """Candidate sync labels for the host that raised `d`: `on <label>` in its details/symptom (scripts name their
    host that way), then `host_sync_labels[host]` from selfheal-config.json, then the host name itself."""
    out: list = []
    for text in (d.get("details"), d.get("symptom")):
        out += _ON_LABEL.findall(str(text or ""))
    m = (cfg or {}).get("host_sync_labels") or {}
    if isinstance(m, dict) and d.get("host") and m.get(d["host"]):
        out.append(str(m[d["host"]]))
    if d.get("host"):
        out.append(str(d["host"]))
    seen: list = []
    for x in out:
        if x not in seen:
            seen.append(x)
    return seen


def sync_deploy(d: dict, shas: list, cfg: Optional[dict] = None) -> Optional[dict]:
    """When the host that raised `d` first synced fix `shas` (the ones that are commits on the state ref). ->
    {label, sha, deployed_at (committer time of its first `sync: <label>` commit containing every fix sha, or
    None), last_sync (its newest sync commit), ref_at (the ref's tip time)}; None when git can't tell (no state
    clone, no sha on the ref, no label with sync commits)."""
    cfg = cfg or {}
    repo = os.path.expanduser(str(cfg.get("state_repo") or STATE_REPO))
    ref = str(cfg.get("state_ref") or STATE_REF)
    if not shas or not os.path.isdir(repo):
        return None
    rc, tip = _git_rc(repo, "log", "-1", "--format=%ct", ref)
    if rc != 0 or not tip.strip():
        return None
    ref_at = float(tip.strip())
    fulls = []
    for sha in shas:
        rc, full = _git_rc(repo, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}")
        full = full.strip()
        if rc != 0 or not full:
            continue  # not a state commit (e.g. the hermes-mods half of the fix)
        anc, _ = _git_rc(repo, "merge-base", "--is-ancestor", full, ref)
        if anc == 0:
            fulls.append(full)
    if not fulls:
        return None
    for label in host_sync_labels(d, cfg):
        pat = _sync_grep(label)
        rc, last = _git_rc(repo, "log", "-1", "--format=%ct", "-E", f"--grep={pat}", ref)
        if rc != 0 or not last.strip():
            continue  # not a hermes-sync label
        deployed: Optional[float] = 0.0
        for full in fulls:
            rc, out = _git_rc(repo, "log", "--ancestry-path", "--format=%ct", "-E", f"--grep={pat}", f"{full}..{ref}")
            if rc != 0:
                return None
            ts = [float(x) for x in out.split() if x.strip()]
            if not ts:
                deployed = None
                break
            deployed = max(deployed or 0.0, min(ts))
        return {"label": label, "sha": fulls[0], "deployed_at": deployed, "last_sync": float(last.strip()),
                "ref_at": ref_at}
    return None


def undeployed_key(fp: str, label: str, sha: str) -> str:
    return f"selfheal:{fp}:undeployed:{re.sub(r'[^A-Za-z0-9._-]', '_', label)}:{sha[:12]}"


def _not_deployed(d: dict, st: dict, at: float, now: float, cfg: Optional[dict]) -> Optional[dict]:
    """A post-fix occurrence from a host whose sync had not delivered the fix when it happened -> a not_deployed
    decision (overdue once the host still lacks it UNDEPLOYED_FILE_HOURS after the fix), else None."""
    dep = sync_deploy(d, list(st.get("fix_shas") or []), cfg)
    if not dep:
        return None
    fixed_at = float(st.get("fixed_at") or 0)
    label, sha = dep["label"], dep["sha"]
    if dep["deployed_at"] is None:
        if dep["ref_at"] < at:
            return None  # the clone hasn't fetched past the occurrence: no post-fix sync proves nothing
        try:
            hours = float((cfg or {}).get("undeployed_file_hours", UNDEPLOYED_FILE_HOURS))
        except (TypeError, ValueError):
            hours = UNDEPLOYED_FILE_HOURS
        overdue = bool(fixed_at) and now - fixed_at >= hours * 3600
        why = (f"host {label} hasn't synced fix {sha[:11]} (last sync {_iso(dep['last_sync'])}"
               f"{', over %g h after the fix' % hours if overdue else ''})")
        return {"action": "not_deployed", **dep, "overdue": overdue, "why": why,
                "key": undeployed_key(d["fingerprint"], label, sha) if overdue else None}
    if at < dep["deployed_at"]:
        return {"action": "not_deployed", **dep, "overdue": False, "key": None,
                "why": f"happened {_iso(at)}, before host {label} synced fix {sha[:11]} ({_iso(dep['deployed_at'])})"}
    return None


def _cron_error(d: dict) -> str:
    """First error, excluding collect_cron's job/script envelope; legacy state kept only symptom."""
    if d.get("cron_error"):
        return str(d["cron_error"])
    lines = str(d.get("details") or "").strip().splitlines()
    if len(lines) >= 3 and lines[0].startswith("job ") and lines[1].startswith("script="):
        error = lines[2]
    else:
        symptom = str(d.get("symptom") or "")
        error = re.sub(r"^cron job .*? failed on [^:]+: ", "", symptom).split("\n", 1)[0]
    error = re.sub(r"(?:~?/|[A-Za-z]:\\)[^\s'\"]+", "<path>", error)
    return re.sub(r"\d+", "#", error).strip()


def decide(d: dict, st: dict, now: float, cfg: Optional[dict] = None) -> dict:
    """Dedupe / suppression / recurrence decision for one defect against its lane state. Pure except one probe:
    a post-fix occurrence checks whether the checkout that raised it contains the fix (stale_checkout).
    a post-fix occurrence on another host checks whether its hermes-sync had delivered a state-branch fix yet
    (sync_deploy, read-only git on the hub's hermes-state clone).
    -> {"action": "file"|"skip"|"hold"|"stale_runtime"|"not_deployed", "why", "key", "previous"}"""
    fp = d["fingerprint"]
    status = st.get("status")
    if st.get("suppressed"):
        return {"action": "skip", "why": "suppressed (Never)"}
    if (st.get("snooze_until") or 0) > now:
        return {"action": "skip", "why": f"snoozed until {_iso(st['snooze_until'])} (Not now)"}
    # Its card ended without a fix (stale) or the fix was declined/reverted: a new card, the same UTC day too.
    refile_closed = bool(d.get("every_occurrence")) and status in ("stale", "declined", "reverted")
    if st.get("filed_day") == _utc_day(now) and status != "fixed" and not refile_closed:  # post-fix: not a duplicate
        return {"action": "skip", "why": "already filed today (<= 1 per fingerprint per UTC day)"}
    if status in ("filed", "proposed", "approved"):
        return {"action": "skip", "why": f"open: {status} {st.get('fix_card') or st.get('card') or ''}".strip()}
    if status == "not_defect" and now - (st.get("closed_at") or 0) < NOT_DEFECT_DAYS * 86400:
        return {"action": "skip", "why": f"closed as not a defect within {NOT_DEFECT_DAYS} days"}
    g = generation(st) + 1  # every filing gets a fresh generation, so its keys never name an earlier card
    if status == "fixed":
        if fp.startswith("cron-"):
            old = _cron_error(st)
            # Legacy lane state kept only the producer's 120-character symptom error.
            # Compare that same representation, not its missing suffix against new full details.
            new = _cron_error(d if st.get("cron_error") else {"symptom": d.get("symptom")})
            if old and new and old != new:
                why = f"same job, different error than the fixed one ({old} vs {new})"
                return {"action": "file", "why": why, "key": inv_key(fp, g), "gen": g,
                        "previous": None, "different_error": why}
        # The fix can only have failed where it runs. A pinned runtime cut before the fix still raises the old
        # error; that is a missing backport, not "the fix didn't hold" (desktop-e05d9218fa: Ask Eve's pinned core).
        try:
            stale = stale_checkout(d, list(st.get("fix_shas") or []))
        except Exception:  # health: allow BLE001 -- the probe is advisory; fall back to the time-based decision
            stale = None
        if stale:
            return {"action": "stale_runtime", **stale, "key": backport_key(fp, stale["checkout"], stale["sha"]),
                    "why": f"{stale['checkout']} (HEAD {stale['head'][:11]}) lacks fix {stale['sha'][:11]}"}
        # Judge by when it happened, not when it was processed: inside the rollout window it is most likely a
        # host still running pre-fix code. Held occurrences are deduped by time so a replay never counts twice.
        at = _detected_at(d)
        at = now if at is None else at
        # A sync-delivered fix (state branch) holds wherever the host's own sync commits show it wasn't there yet,
        # however long the host was away (cron-b2c33e9921); git that can't tell keeps the time rule below.
        try:
            nd = _not_deployed(d, st, at, now, cfg)
        except Exception:  # health: allow BLE001 -- the probe is advisory; fall back to the time-based decision
            nd = None
        if nd:
            return {**nd, "at": at}
        grace = deploy_grace_s(cfg)
        held = [h for h in (st.get("held") or []) if h.get("at") != at]
        if grace and st.get("fixed_at") and at - float(st["fixed_at"]) < grace and len(held) + 1 < HELD_FILE_AFTER:
            return {"action": "hold", "at": at, "held": held, "until": float(st["fixed_at"]) + grace,
                    "why": f"inside the {grace / 3600:g} h rollout window after fixed ({_iso(st['fixed_at'])}); "
                           f"held {len(held) + 1}/{HELD_FILE_AFTER - 1}"}
        why = "recurrence" if not held else f"recurrence ({len(held)} held inside the rollout window)"
        return {"action": "file", "why": why, "key": inv_key(fp, g), "gen": g, "previous": st}
    if st.get("card"):  # an earlier card closed without a fix (declined/reverted/not_defect/stale): new key
        return {"action": "file", "why": "refile", "key": inv_key(fp, g), "gen": g, "previous": None}
    return {"action": "file", "why": "new", "key": inv_key(fp, g), "gen": g, "previous": None}


def card_args(d: dict, key: str, previous: Optional[dict] = None, cfg: Optional[dict] = None,
              different_error: str = "") -> dict:
    r = route(d, cfg)
    sym = incident_title(d.get("symptom"))
    remote = remote_lane(r["board"])  # the hub's lane: a pool worker investigates, never a refused machine:hub pin
    return {"title": f"self-heal: {sym}" + (" (came back)" if previous else ""),
            "body": investigation_brief(d, previous, cfg, remote=remote) + (f"\n\n{different_error}\n" if different_error else ""),
            "idempotency_key": key, "model": TRIAGE_MODEL, "provider": TRIAGE_PROVIDER,
            "max_runtime": INVESTIGATION_RUNTIME, "skills": r["skills"], "board": r["board"],
            "assignee": "default", "created_by": CREATED_BY, "requires": list(SELFHEAL_REQUIRES),
            "home_pin": not remote, "runs_on": list(POOL_LABELS) if remote else []}


def kanban_argv(args: dict, hermes: str = "hermes") -> list:
    argv = [hermes, "kanban"] + (["--board", args["board"]] if args.get("board") else []) + [
        "create", args["title"], "--body", args["body"], "--assignee", args["assignee"],
        "--idempotency-key", args["idempotency_key"],
        "--max-runtime", str(args["max_runtime"]), "--created-by", args["created_by"], "--json"]
    if args.get("model"):
        argv += ["--model", args["model"]]
    if args.get("model") and args.get("provider"):
        argv += ["--provider", args["provider"]]
    for s in args.get("skills") or []:
        argv += ["--skill", s]
    req = args.get("requires", SELFHEAL_REQUIRES)
    argv += [x for c in req for x in ("--requires", c)] if req else ["--no-requires"]
    if args.get("home_pin"):  # the explicit machine pin; raises HomePinUnresolved on a synced board it cannot read
        argv += home_runs_on_argv()
    elif args.get("runs_on"):  # declared pool attestations (labels every eligible worker carries)
        argv += ["--runs-on", ",".join(args["runs_on"])]
    for parent in args.get("parents") or []:
        argv += ["--parent", parent]
    return argv


def _run_card(args: dict, runner=None):
    """``_run(kanban_argv(args))``; an unresolvable home pin is a failed create (rc 1), never a portable card."""
    try:
        argv = kanban_argv(args)
    except HomePinUnresolved as exc:
        return 1, "", f"home pin unresolved: {exc}"
    return _run(argv, runner)


def _run(argv, runner=None, stdin=None):
    import subprocess
    if runner is None and os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":  # tests: never touch the real board/hub
        raise RuntimeError(f"COACH_SELFHEAL_NO_EXEC: refused {argv[:3]}")
    r = (runner or subprocess.run)(argv, capture_output=True, text=True, input=stdin, timeout=120)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _task_id(out: str) -> Optional[str]:
    m = re.search(r'"id"\s*:\s*"(t_[0-9a-f]+)"', out) or re.search(r"\b(t_[0-9a-f]{6,})\b", out)
    return m.group(1) if m else None


def _store(store=None):
    if store is not None:
        return store

    class _S:
        root = coach_root()

        def clock(self):
            return time.time()

        def log(self, rec):
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "log.jsonl").open("a") as f:
                f.write(json.dumps({"ts": self.clock(), **rec}) + "\n")
    return _S()


ADOPT_STATUSES = ("ready", "running", "blocked", "todo", "review", "done")  # finished fixes count: the work exists
ADOPT_LOOKBACK_S = 2 * 3600  # no `complaint_at` on the defect: the complaint turn is at most this long before detection
# An admission's quote is the agent reporting a defect it usually ALREADY fixed, so its fix commit predates
# complaint_at by construction (plugin-aafb20159a: hermes-mods 2c0772cc landed 7 min before the report that became the
# admission). session_commit_fix searches this far before complaint_at for those sources; defect binding is still
# required, so the window only selects candidates. A user complaint raised after a commit says that commit did not fix
# it, so complaints keep the strict "after complaint_at" window.
ADMISSION_FIX_LOOKBACK_S = 2 * 3600
POST_FIX_REPORT_SOURCES = ("admission",)


def session_owned_cards(session_id: Optional[str], since: float, home: Optional[Path] = None) -> list:
    """Kanban cards (ADOPT_STATUSES: active, review or done) the origin session created (tasks.session_id) or subscribed
    to (kanban_notify_subs.chat_id), created at/after `since`, newest first, across the default and named boards.
    -> [{id, title, status, created_at, board}]. Unreadable boards are skipped."""
    if not session_id:
        return []
    import sqlite3
    home = hermes_home(home)
    dbs = [(None, home / "kanban.db")] + [(p.parent.name, p) for p in sorted((home / "kanban" / "boards").glob("*/kanban.db"))]
    marks = ",".join("?" * len(ADOPT_STATUSES))
    rows: list = []
    for board, path in dbs:
        if not path.exists():
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.Error:
            try:
                db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
                db.execute("PRAGMA query_only=ON")
            except sqlite3.Error:
                continue
        try:
            cols = {c[1] for c in db.execute("PRAGMA table_info(tasks)")}
            body = "substr(body, 1, 4000)" if "body" in cols else "NULL"
            ikey = "idempotency_key" if "idempotency_key" in cols else "NULL"
            for r in db.execute(
                    f"SELECT id, title, status, created_at, {body}, {ikey} FROM tasks WHERE status IN ({marks}) "
                    "AND created_at >= ? "
                    "AND (session_id = ? OR id IN (SELECT task_id FROM kanban_notify_subs WHERE chat_id = ?))",
                    (*ADOPT_STATUSES, int(since), session_id, session_id)):
                rows.append({"id": r[0], "title": r[1], "status": r[2], "created_at": r[3], "board": board,
                             "body": r[4], "idempotency_key": r[5]})
        except sqlite3.Error:
            pass
        finally:
            db.close()
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


_CARD_ID = re.compile(r"\bt_[0-9a-f]{6,}\b")
_COUNTERMEASURE_WORDS = re.compile(r"\b(?:could|should|follow[- ]?ups?)\b", re.IGNORECASE)


def digest_owner_cards(d: dict, since: float, data: Optional[Path] = None) -> list:
    """Cards the origin session itself names after the complaint, read from its coach digest (it may be a session on
    another host, whose board the hub cannot open): t_<hex> ids in user turns newer than `since` (their `prev` agent
    reply and the text, e.g. a chat-closeout notice) and in `last_reply` when the session moved on after `since`.
    -> [{id, title, status, created_at, board: '<host>:default'}], newest mention first. Unreadable digest -> []."""
    sid, host = d.get("session_id"), d.get("host")
    if not sid:
        return []
    root = Path(data) if data else Path(os.environ.get("COACH_DATA") or Path.home() / "code" / "hermes-coach-data")
    paths = sorted(root.glob(f"{host}/*/{sid}.json")) if host else sorted(root.glob(f"*/*/{sid}.json"))
    for path in paths:
        try:
            dg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        origin = dg.get("host") or path.parts[-3]
        mentions = []
        for t in dg.get("user_turns") or []:
            ts = t.get("t") or 0
            if ts <= since:
                continue
            mentions += [(ts, x) for k in ("prev", "text") for x in _CARD_ID.findall(str(t.get(k) or ""))]
        if (dg.get("last_activity_at") or 0) > since:
            mentions += [(dg["last_activity_at"], x) for x in _CARD_ID.findall(str(dg.get("last_reply") or ""))]
        found: dict = {}
        for ts, cid in sorted(mentions, key=lambda m: m[0], reverse=True):
            found.setdefault(cid, {"id": cid, "title": None, "status": None, "created_at": ts,
                                   "board": f"{origin}:default"})
        if found:
            return list(found.values())
    return []


_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
SESSION_COMMIT_SLACK_S = 1800  # a fix may land a little after the session's last recorded activity


def _fork_repo() -> Path:
    return Path(os.environ.get("COACH_FORK_REPO") or Path.home() / ".hermes" / "hermes-agent")


def _mods_repo() -> Path:
    """hermes-mods: plugin, desktop-plugin and skill fixes land here, not in the fork (desktop-9077b82c2d)."""
    return Path(os.environ.get("COACH_MODS_REPO") or Path.home() / "code" / "hermes-mods")


def _fix_repos(repo: Optional[Path] = None) -> list:
    """Repos a session's fix can land in: an explicit `repo` alone, else the fork and hermes-mods."""
    if repo:
        return [Path(repo)]
    out = []
    for r in (_fork_repo(), _mods_repo()):
        if r not in out:
            out.append(r)
    return out


# A commit is a fix for a component only if its diff touches that component's code. A subject word is no proof:
# `fix(desktop): an inline update report...` (gateway code) matched, `fix(session-panel)` (desktop-plugins/) didn't.
COMPONENT_PATHS = {"desktop": ("apps/desktop/", "desktop-plugins/")}


def _commit_files(repo: Path, sha: str) -> list:
    return _git_lines(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)


def _commit_in_component(repo: Path, sha: str, subject: str, comp: str) -> bool:
    """Window candidate selection: the diff touches COMPONENT_PATHS[comp]; a component with no path map falls back
    to its name as a word in the subject."""
    paths = COMPONENT_PATHS.get(comp)
    if paths:
        return any(f.startswith(p) for f in _commit_files(repo, sha) for p in paths)
    return bool(comp) and bool(re.search(r"\b%s\b" % re.escape(comp), subject.lower()))


# A bare "go on" the user sends after a turn that produced no reply: the next turn still answers the complaint.
NUDGE_RE = re.compile(r"^\s*(?:resume|continue|go on|keep going|carry on|retry|try again|again|go|proceed)\W*$", re.I)


def _machine_user_row(content: str) -> bool:
    """A user row core or Desktop wrote, not the person: "[Turn stopped by Desktop…]", a resume/pause system note."""
    t = (content or "").strip()
    return bool(INTERRUPT_RE.match(t)) or t.startswith(RESUME_NOTE_PREFIX) or t.startswith("[System note:")


def _stock_reply(content: str) -> bool:
    """An assistant row that is core's failed-turn notice or a provider error, not an answer."""
    t = (content or "").strip()
    return t in FAILED_TURN_NOTICES or provider_error_span(t) is not None or bool(INTERRUPT_RE.match(t))


def _complaint_reply_text(sid: str, since: float) -> Optional[str]:
    """The assistant's full reply to the complaint (messages after `since` up to the next user message) from this
    host's state.db; None when the session is not there (another host's session: use its digest). The coach digest's
    last_reply is clipped and is the session's LAST reply, so a fix sha named while answering the complaint was lost
    in it (desktop-9077b82c2d: "…[clipped" cut before f986de8c).
    Machine user rows (_machine_user_row) never end the reply, and while the complaint has no real answer yet (only
    stock notices: _stock_reply, which are left out of the text) a bare nudge ("resume", NUDGE_RE) doesn't either:
    desktop-0e7cc2f6c8's turn was stopped with "Your request was not processed", the user sent "resume" four hours
    later, and the fix sha came in that turn. '' = the session is here but nothing answered the complaint."""
    import sqlite3
    db = hermes_home() / "state.db"
    if not sid or not db.is_file():
        return None
    try:
        con = _ro_state(db)
        try:
            if not con.execute("SELECT 1 FROM messages WHERE session_id=? LIMIT 1", (sid,)).fetchone():
                return None
            rows = con.execute("SELECT role, timestamp, content FROM messages WHERE session_id=? AND timestamp>? "
                               "AND role IN ('user','assistant') ORDER BY timestamp, id", (sid, since - 2)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    out = []
    for role, ts, content in rows:
        if role == "user" and (ts or 0) > since + 2:  # the next user turn ends the complaint's reply
            if _machine_user_row(str(content or "")) or (not out and NUDGE_RE.match(str(content or ""))):
                continue
            break
        if role == "assistant" and content and not _stock_reply(str(content)):
            out.append(str(content))
    return "\n".join(out)


def _git_lines(repo: Path, *args: str) -> list:
    import subprocess
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return p.stdout.splitlines() if p.returncode == 0 else []


def _traceback_commit_fix(d: dict, since: float, repo: Path, now: float) -> Optional[dict]:
    """Without a digest, require an edit to the last frame's literal raise or its direct if guard.

    A same-file edit elsewhere (even in the same diff hunk) is not evidence. Use parent-source AST spans and
    zero-context removed lines, not message context in `git show` (which also includes unrelated nearby edits).
    """
    import ast
    frames = re.findall(r'File "([^"]+)"', str(d.get("details") or ""))
    sig = error_signature(d)
    msg = (sig or {}).get("exc_msg")
    if not frames or not msg or len(msg) < SIGNATURE_MIN_MSG:
        return None
    frame_func = re.findall(r'File "[^"]+", line \d+, in ([^\n]+)', str(d.get("details") or ""))
    func = frame_func[-1].strip() if frame_func else None
    lower = (d.get("detected_at") or _detected_at(d) or since) - 86400
    for ln in reversed(_git_lines(repo, "log", "origin/main", f"--since={int(lower)}",
                                  f"--until={int(now)}", "--format=%H\t%ct\t%s")):
        sha, _, rest = ln.partition("\t")
        ts, _, subject = rest.partition("\t")
        if not ts.isdigit() or not lower <= int(ts) <= now or not subject.lower().startswith("fix"):
            continue
        files = _git_lines(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
        for fname in files:
            # Match the full repository-relative suffix, never only a shared basename.
            if not (frames[-1] == fname or frames[-1].endswith("/" + fname)):
                continue
            source = "\n".join(_git_lines(repo, "show", f"{sha}^:{fname}"))
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            spans = set()
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for parent in ast.walk(tree):
                for node in ast.iter_child_nodes(parent):
                    if not isinstance(node, ast.Raise) or not node.exc:
                        continue
                    scope = parents.get(node)
                    while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        scope = parents.get(scope)
                    if func and (scope.name if scope else "<module>") != func:
                        continue
                    if not any(isinstance(n, ast.Constant) and n.value == msg for n in ast.walk(node.exc)):
                        continue
                    spans.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
                    if isinstance(parent, ast.If) and node in parent.body:
                        spans.update(range(parent.test.lineno, (parent.test.end_lineno or parent.test.lineno) + 1))
            if not spans:
                continue
            diff = _git_lines(repo, "diff", "--no-ext-diff", "--no-textconv", "--unified=0", sha + "^", sha,
                              "--", fname)
            for line in diff:
                hunk = re.match(r"@@ -(\d+)(?:,(\d+))? \+", line)
                if hunk:
                    start, count = int(hunk[1]), int(hunk[2] or 1)
                    if spans.intersection(range(start, start + count)):
                        return {"sha": sha, "at": int(ts), "subject": subject, "via": "commit_no_session"}
    return None


def _commit_binds_defect(repo: Path, sha: str, subject: str, d: dict) -> bool:
    """Defect-bound proof that commit `sha` fixes defect `d`, not merely landed nearby: the defect's traceback files
    are in the diff, or the commit's own subject/body names the fingerprint, the symptom or enough of its
    distinctive words (card_names_defect rules). Time and component overlap never count (79477b7501f4, a turn-lease
    fix, was credited to three unrelated needs-you/credential complaints), and neither does a sha the session
    merely mentions. One extra rule (_commit_names_origin): a message that names the defect's origin session id
    is bound when its subject and body together share enough distinctive symptom words; the session id is provenance,
    the shared words are the subject (an unrelated fix from the same session shares at most one)."""
    tb_files = {os.path.basename(p) for p in re.findall(r'File "([^"]+)"', str(d.get("details") or ""))}
    if tb_files:
        return bool(tb_files & {os.path.basename(f) for f in _git_lines(repo, "show", "--name-only", "--format=", sha)})
    body = "\n".join(_git_lines(repo, "show", "-s", "--format=%b", sha))
    return card_names_defect({"title": subject, "body": body}, d) or _commit_names_origin(subject, body, d)


def _commit_names_origin(subject: str, body: str, d: dict) -> bool:
    """The commit message names the origin session id and shares max(2, a third) of the symptom's distinctive words
    (card_names_defect's threshold and stemming), counted over subject AND body. card_names_defect counts the title
    only, because a long body shares a couple of words with almost any symptom; the session id is what makes the body
    count here. plugin-aafb20159a: 2c0772cc's body named session 20261026_235722_bef6c6, its subject shared
    "subscription" and its body "completion". One shared word is not enough even then: an unrelated same-session
    fix shares a word like "task"."""
    sid = str(d.get("session_id") or "")
    if len(sid) < 12 or sid not in f"{subject}\n{body}":
        return False
    want = _subject_words(d.get("error") if d.get("kind") == "repeat_failure" else d.get("symptom"))
    return bool(want) and len(want & _subject_words(f"{subject}\n{body}")) >= max(2, -(-len(want) // 3))


def session_commit_fix(d: dict, since: float, data: Optional[Path] = None, repo: Optional[Path] = None,
                       now: Optional[float] = None) -> Optional[dict]:
    """A fix the origin session shipped as a bare commit (no kanban card): a commit on origin/main of the fork or
    hermes-mods (_fix_repos; an explicit `repo` alone) made after `since` and either
    (a) named by sha in the session's reply to the complaint (full transcript in this host's state.db) and bound to
        the defect by _commit_binds_defect or by touching the component's code (COMPONENT_PATHS): answering the
        complaint with that commit is the defect binding;
    (b) named in the session's digest (other hosts' sessions; a clipped, whole-session summary) and bound by
        _commit_binds_defect; or
    (c) landed inside the session's active window (since .. last_activity_at + slack) as a `fix` whose diff touches
        the component (_commit_in_component) and is bound by _commit_binds_defect; or
    (d) for a post-fix report (POST_FIX_REPORT_SOURCES: an admission), a commit within ADMISSION_FIX_LOOKBACK_S of
        `since` (either side) whose message names the origin session id, bound by _commit_binds_defect.
    For a post-fix report every path searches from ADMISSION_FIX_LOOKBACK_S before `since`: the agent reports a
    defect it already fixed, so the fix predates the report (plugin-aafb20159a). Other sources search after `since`.
    Window and component only select candidates; without defect-bound proof the defect stays unresolved (a session
    can report several defects, and a nearby fix for another one proves nothing). -> {sha, at, subject, via}|None."""
    sid, host, comp = d.get("session_id"), d.get("host"), (d.get("component") or "").lower()
    root = Path(data) if data else Path(os.environ.get("COACH_DATA") or Path.home() / "code" / "hermes-coach-data")
    paths = (sorted(root.glob(f"{host}/*/{sid}.json")) if host else sorted(root.glob(f"*/*/{sid}.json"))) if sid else []
    repos = _fix_repos(repo)
    reply = _complaint_reply_text(sid, since) if sid else None
    post_fix = d.get("source") in POST_FIX_REPORT_SOURCES
    lo = since - ADMISSION_FIX_LOOKBACK_S if post_fix else since

    cache: dict = {}

    def history(r: Path) -> list:  # (sha, committer ts, subject) on origin/main since `lo`; lazy, cached
        if r not in cache:
            out = []
            for ln in _git_lines(r, "log", "origin/main", f"--since={int(lo)}", "--format=%H\t%ct\t%s"):
                sha, _, rest = ln.partition("\t")
                ts, _, subj = rest.partition("\t")
                if ts.isdigit() and int(ts) > lo:
                    out.append((sha, int(ts), subj))
            cache[r] = out
        return cache[r]

    if reply is not None:
        named = set(_SHA.findall(reply))
        for r in (repos if named else []):
            for sha, ts, subj in history(r):
                if any(sha.startswith(n) for n in named) and (
                        _commit_binds_defect(r, sha, subj, d) or
                        (comp in COMPONENT_PATHS and _commit_in_component(r, sha, subj, comp))):
                    return {"sha": sha, "at": ts, "subject": subj, "via": "named", "repo": str(r)}
    has_digest = False
    for path in paths:
        try:
            dg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(dg, dict):
            continue
        has_digest = True
        until = (dg.get("last_activity_at") or since) + SESSION_COMMIT_SLACK_S
        if not (reply or "").strip():  # no transcript here, or nothing answered the complaint: the digest names it
            text = " ".join([str(dg.get("last_reply") or "")] + [str(t.get(k) or "") for t in dg.get("user_turns") or []
                                                                if (t.get("t") or 0) > since for k in ("prev", "text")])
            named = set(_SHA.findall(text))
            for r in repos:
                for sha, ts, subj in history(r):
                    if any(sha.startswith(n) for n in named) and _commit_binds_defect(r, sha, subj, d):
                        return {"sha": sha, "at": ts, "subject": subj, "via": "named", "repo": str(r)}
        for r in repos:
            for sha, ts, subj in sorted(history(r), key=lambda c: c[1]):
                if ts <= until and subj.lower().startswith("fix") and _commit_in_component(r, sha, subj, comp):
                    if not _commit_binds_defect(r, sha, subj, d):
                        continue
                    return {"sha": sha, "at": ts, "subject": subj, "via": "window", "repo": str(r)}
    if post_fix and sid and len(str(sid)) >= 12:
        hi = since + ADMISSION_FIX_LOOKBACK_S  # a session-named commit long after the report is later, other work
        for r in repos:
            for ln in _git_lines(r, "log", "origin/main", f"--since={int(lo)}", "--fixed-strings", f"--grep={sid}",
                                 "--format=%H\t%ct\t%s"):
                sha, _, rest = ln.partition("\t")
                ts, _, subj = rest.partition("\t")
                if ts.isdigit() and lo < int(ts) <= hi and _commit_binds_defect(r, sha, subj, d):
                    return {"sha": sha, "at": int(ts), "subject": subj, "via": "session_named", "repo": str(r)}
    if not has_digest:
        tnow = time.time() if now is None else now
        for r in repos:
            fix = _traceback_commit_fix(d, since, r, tnow)
            if fix:
                return fix
    return None


def _replica_card_text(cards: list, hub_db: Optional[Path]) -> list:
    """Fill title/body/key/status of cards another host's digest named (digest_owner_cards knows only their ids)
    from that gateway's records in the hermes-hub replica, so the subject check can judge them. A card the replica
    cannot describe stays textless and is not adopted: a mention in the session is not evidence it owns the defect."""
    import sqlite3
    if not cards or hub_db is None or not Path(hub_db).exists():
        return cards
    try:
        con = _ro_state(Path(hub_db))
    except sqlite3.Error:
        return cards
    try:
        for c in cards:
            gw = str(c.get("board") or "").partition(":")[0]
            try:
                rows = con.execute("SELECT data FROM replica WHERE gw = ? AND coll = 'tasks' AND key LIKE ?",
                                   (gw, "%/" + c["id"])).fetchall()
            except sqlite3.Error:
                break
            for (raw,) in rows:
                try:
                    t = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(t, dict) and t.get("id") == c["id"] and t.get("gateway") == gw:
                    c.update(title=t.get("title"), body=str(t.get("body") or "")[:4000],
                             idempotency_key=t.get("idempotency_key"), status=t.get("status"), described=True)
                    break
    finally:
        con.close()
    # session_owned_cards only offers ADOPT_STATUSES; a card the replica shows archived is no owner either
    return [c for c in cards if not c.pop("described", False) or c.get("status") in ADOPT_STATUSES]


def _defect_subject_cards(d: dict) -> set:
    """Card ids the defect's own evidence names (quote, evidence, symptom, details): the complaint is ABOUT them."""
    return set(_CARD_ID.findall(" ".join(str(d.get(k) or "") for k in ("quote", "evidence", "symptom", "details"))))


def _lane_process_cards(store, fp: str) -> set:
    """Investigation and VERIFY cards of every OTHER fingerprint's lane: self-heal process cards, not repairs."""
    out: set = set()
    for other, st in LaneState(store.root).read().items():
        if other != fp and isinstance(st, dict):
            out |= {c for c in (st.get("card"), st.get("verify_card")) if c}
    return out


def _card_names_fingerprint(card: dict, fp: str) -> bool:
    text = _norm_text(" ".join(str(card.get(k) or "") for k in ("title", "body", "idempotency_key")))
    return bool(fp) and fp.lower() in text


def _adopt_session_card(d: dict, fp: str, store, now: float, owner_fn: Optional[Callable] = None) -> Optional[dict]:
    """If the defect's origin session created/subscribed an active card after the complaint that names the defect
    (card_names_defect; never the card the complaint is about, never another lane's investigation/VERIFY card),
    record the newest such card as the lane's fix_card (approved) and log `selfheal_adopted_owner`.
    -> result dict, or None to go on and file."""
    sid = d.get("session_id")
    if owner_fn is None and os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":
        return None
    since = d.get("complaint_at") or ((d.get("at") or now) - ADOPT_LOOKBACK_S)
    try:
        cards = (owner_fn or session_owned_cards)(sid, since) if sid else []
        if not cards and owner_fn is None and sid:  # not on this hub's boards: the origin host's cards, named in its digest
            cards = _replica_card_text(digest_owner_cards(d, d.get("complaint_at") or d.get("at") or since),
                                       _lookup_sources()[1])
    except Exception as exc:
        store.log({"event": "selfheal_adopt_error", "fingerprint": fp, "error": str(exc)[:200]})
        return None
    if not cards and owner_fn is None:  # no card: the session may have fixed it with a bare commit
        try:
            fix = session_commit_fix(d, d.get("complaint_at") or d.get("at") or since, now=now)
        except Exception as exc:
            store.log({"event": "selfheal_adopt_error", "fingerprint": fp, "error": str(exc)[:200]})
            fix = None
        if fix:
            origin = "matching traceback" if fix["via"] == "commit_no_session" else f"origin session {sid}"
            record_event("fixed", fp, note=f"fixed by {origin} in commit {fix['sha'][:12]}: {fix['subject']}"[:300],
                         store=store, now=now)
            store.log({"event": "selfheal_adopted_owner",
                       "via": "commit_no_session" if fix["via"] == "commit_no_session" else "session_commit",
                       "fingerprint": fp, "commit": fix["sha"], "match": fix["via"], "session_id": sid})
            return {"action": "skip", "why": f"{origin} already fixed it in commit {fix['sha'][:12]}",
                    "commit": fix["sha"], "adopted": True}
    if not cards:
        return None
    # Sharing the session is not owning the defect, whatever the source: its next cards are just its next work (an
    # Ask Eve test once owned a repeated kanban error; agent-e5b6dcf428's report_only signal was closed against a
    # Kanban owner-death fix). A user complaint used to be the exception and adopted the session's newest card with
    # no subject evidence at all (desktop-d39268b86c: "the response dissapeard" -> a delivery-disposition card;
    # agent-2e9530b2b5: a landing timeout -> a SQLite read-helper fix); the lane then waited on, and was closed
    # against, work that never touched the symptom. Two kinds of card are never the owner even when their words
    # match: one the complaint itself names (it existed before the complaint, so it is the subject, e.g. the stalled
    # card of a wait notice: kanban-04bdba70ac), and another fingerprint's investigation/VERIFY card (it judges
    # that lane's fix and implements nothing: desktop-727bbca8b8).
    subject = _defect_subject_cards(d) | _filer_cards(d)
    process = _lane_process_cards(store, fp)
    named = [x for x in cards
             if x["id"] not in subject and card_names_defect(x, d, fp)
             and (x["id"] not in process or _card_names_fingerprint(x, fp))]
    if not named:
        store.log({"event": "selfheal_adopt_skipped", "why": "no session card names the defect",
                   "fingerprint": fp, "session_id": sid, "source": d.get("source"),
                   "terms": sorted(_subject_words(d.get("symptom")))[:12],
                   "candidates": [x["id"] for x in cards],
                   "subject_cards": [x["id"] for x in cards if x["id"] in subject],
                   "process_cards": [x["id"] for x in cards if x["id"] in process]})
        return None
    cards = named
    c = cards[0]
    lane = LaneState(store.root)
    st = lane.get(fp)
    lane.update(fp, status="approved", fix_card=c["id"], approved_at=now, component=d.get("component"),
                symptom=d.get("symptom"), session_id=sid, board=c.get("board"), filed_day=_utc_day(now),
                cards=sorted(known_cards(st) | {c["id"]}), **_complaint_identity(d))
    store.log({"event": "selfheal_adopted_owner", "fingerprint": fp, "card": c["id"], "status": c.get("status"),
               "board": c.get("board"), "session_id": sid, "candidates": [x["id"] for x in cards]})
    return {"action": "skip", "why": f"adopted owner card {c['id']} from session {sid}", "card": c["id"],
            "adopted": True}


_EXC_LINE = re.compile(r"^\s*((?:[A-Za-z_][\w]*\.)*([A-Z]\w*(?:Error|Exception|Exit|Interrupt))):\s*(\S.*?)\s*$", re.M)
_SYMPTOM_LOC = re.compile(r"\b([A-Z]\w*(?:Error|Exception))\s+in\s+([A-Za-z_]\w*)(?:\s*\(([\w./-]+\.\w+)\))?")
SIGNATURE_MIN_MSG = 6  # a shorter exception message ("1", "None") is too generic to match card text on


def error_signature(d: dict) -> Optional[dict]:
    """The defect's error signature: {exc_class, exc_msg, func, file, symptom}. Exception from the last
    `Class: message` line of `details` (a traceback tail), else from `symptom` ("OperationalError in _read_counts
    (kanban_session_activity.py): ..."); function/file from the symptom. None when there is no exception at all."""
    sym = str(d.get("symptom") or "")
    det = str(d.get("details") or "")
    exc_class = exc_msg = func = fname = None
    hits = _EXC_LINE.findall(det)
    if hits:
        _, exc_class, exc_msg = hits[-1]
    m = _SYMPTOM_LOC.search(sym)
    if m:
        exc_class = exc_class or m.group(1)
        func, fname = m.group(2), m.group(3)
    if not exc_class:
        return None
    return {"exc_class": exc_class, "exc_msg": exc_msg, "func": func, "file": fname, "symptom": sym.strip()}


def signature_matches(sig: dict, text: str) -> bool:
    """Specific enough to adopt: the identical symptom string, or the exception (class, plus its message when the
    defect has one) AND the function or file it was raised in. Same exception elsewhere is not a match."""
    t = (text or "").lower()
    if sig.get("corruption_db"):
        if "corrupt" not in t:
            return False
        db = sig["corruption_db"].lower()
        # A bare kanban.db means only the default DB; never a named board's path.
        tokens = re.findall(r"(?:[~\w./-]+\.(?:db|sqlite3?))", t)
        tokens = [re.sub(r"^(?:/home/[^/]+|/users/[^/]+)(?=/\.hermes/)", "~", x) for x in tokens]
        return db in tokens or (db == "~/.hermes/kanban.db" and "kanban.db" in tokens)
    if len(sig.get("symptom") or "") >= 20 and sig["symptom"].lower() in t:
        return True
    msg = sig.get("exc_msg")
    if msg and len(msg) >= SIGNATURE_MIN_MSG:
        exc_ok = msg.lower() in t
    else:
        exc_ok = sig["exc_class"].lower() in t
    loc_ok = any(x and x.lower() in t for x in (sig.get("func"), sig.get("file")))
    return exc_ok and loc_ok


# A card that tells its worker to run `self_heal.py file` is filing defects, not investigating them: its body quotes
# the payload (symptom verbatim), so signature matching alone would make it the owner (t_01a1cfc408527b07).
_FILES_DEFECTS = re.compile(r"self_heal\.py[`'\"]?\s+file\b")


def _filer_cards(d: dict) -> set:
    """Cards filing this payload, never its owner: the payload's `filed_by` (a hand re-file run over hub-admin/ssh,
    where the worker's env does not reach), plus this process's own Kanban task."""
    ids = {d.get("filed_by"), os.environ.get("HERMES_KANBAN_TASK"), os.environ.get("HERMES_KANBAN_WORKER_TASK")}
    return {str(i) for i in ids if i}


def signature_owned_cards(sig: dict, home: Optional[Path] = None) -> list:
    """Open (not done/archived) cards on this host's boards whose title+body match `sig` (signature_matches).
    A card whose body instructs `self_heal.py file` (a re-file, review or audit quoting the payload) never matches.
    -> [{id, title, status, created_at, board}], newest first. Opened read-only like session_owned_cards."""
    import sqlite3
    home = Path(home) if home else _kanban_home()
    dbs = _board_dbs(home)
    rows: list = []
    for board, path in dbs:
        if not path.exists():
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.Error:
            try:
                db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
                db.execute("PRAGMA query_only=ON")
            except sqlite3.Error:
                continue
        try:
            for r in db.execute("SELECT id, title, body, status, created_at FROM tasks "
                                "WHERE status NOT IN ('done', 'archived')"):
                if sig.get("corruption_db"):
                    # Local incident ownership only: remote hosts' equally named DBs are unrelated.
                    matches = (r[3] == "running" and r[0] != os.environ.get("HERMES_KANBAN_TASK")
                               and sig.get("host") == host_name()
                               and signature_matches(sig, r[1] or ""))
                else:
                    matches = signature_matches(sig, f"{r[1] or ''}\n{r[2] or ''}")
                if matches and _FILES_DEFECTS.search(r[2] or ""):
                    matches = False
                if matches:
                    rows.append({"id": r[0], "title": r[1], "status": r[3], "created_at": r[4], "board": board})
        except sqlite3.Error:
            pass
        finally:
            db.close()
    # Prefer the original incident owner over later running dedupe/follow-up cards.
    rows.sort(key=lambda r: r["created_at"] or 0, reverse=not bool(sig.get("corruption_db")))
    if sig.get("corruption_db") and sig.get("host") == host_name():
        holder = recovery_holder_card(sig["corruption_db"], home, dbs)
        if holder:
            rows = [holder] + [r for r in rows if r["id"] != holder["id"]]
    return rows


def _kanban_home() -> Path:
    """Base of signals.board_dbs labels (signals scans its --home, default HERMES_HOME), not core's kanban root."""
    return hermes_home()


def _board_dbs(home: Path) -> list:
    return [(None, home / "kanban.db")] + [(p.parent.name, p) for p in
                                           sorted((home / "kanban" / "boards").glob("*/kanban.db"))]


def _proc_start(pid: int) -> Optional[str]:
    """Linux process start time in clock ticks (/proc/<pid>/stat field 22): tells a reused pid apart."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def incident_holder_card(d: dict) -> Optional[dict]:
    """The live recovery-lock holder's card for this SQLite incident's DB, on this host only (the record and
    the pid are local facts). None for remote incidents, non-incidents, or under COACH_SELFHEAL_NO_EXEC."""
    inc = d.get("incident") or {}
    if not inc.get("db") or inc.get("host") != host_name() or os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":
        return None
    home = _kanban_home()
    return recovery_holder_card(inc["db"], home, _board_dbs(home))


def _adopt_recovery_holder(d: dict, fp: str, st: dict, store, now: float, runner=None) -> Optional[dict]:
    """A live `hermes kanban recover` owns its DB's incident: point the lane at its card and comment there."""
    holder = incident_holder_card(d)
    if not holder:
        return None
    lane = LaneState(store.root)
    if st.get("fix_card") != holder["id"] or st.get("status") != "approved":
        lane.update(fp, status="approved", fix_card=holder["id"], approved_at=now, component=d.get("component"),
                    symptom=d.get("symptom"), board=holder.get("board"), filed_day=_utc_day(now),
                    cards=sorted(known_cards(st) | {holder["id"]}))
        store.log({"event": "selfheal_adopted_owner", "via": "recovery_lock", "fingerprint": fp,
                   "card": holder["id"], "status": holder.get("status"), "board": holder.get("board"),
                   "previous_card": st.get("fix_card") or st.get("card")})
    commented = _corruption_comment(d, lane.get(fp), store, runner)
    if commented["action"] == "error":
        return commented
    return {"action": "skip", "why": f"adopted recovery-lock holder card {holder['id']}", "card": holder["id"],
            "adopted": True, "via": "recovery_lock"}


def recovery_holder_card(db: str, home: Path, dbs: list) -> Optional[dict]:
    """The card of the live `hermes kanban recover` (or swap_db.py) holding this DB's recovery lock, from the
    `<db>.recover.pid` record it writes ({pid, session_id, task_id}); it owns the incident while it runs. None
    when there is no record, its process is gone, it names no card, or that card is already done/archived.
    `db` is the incident's home-relative spelling (`~/.hermes/...`), resolved against this host's home."""
    import sqlite3
    rel = re.sub(r"^~/\.hermes/?", "", db)
    if rel == db:
        return None
    path = Path(home) / rel
    try:
        info = json.loads(path.with_name(path.name + ".recover.pid").read_text())
        pid, card = int(info["pid"]), str(info.get("task_id") or "")
        os.kill(pid, 0)
    except PermissionError:
        pass
    except (OSError, ValueError, KeyError, TypeError, OverflowError, AttributeError):
        return None
    if info.get("proc_start") and _proc_start(pid) not in (None, str(info["proc_start"])):
        return None  # the recorded process died and its pid was reused
    if not _CARD_ID.fullmatch(card):
        return None
    for board, kdb in dbs:  # the card's status and board, from whichever readable board holds it
        try:
            con = _ro_state(kdb)
            try:
                r = con.execute("SELECT id, title, status, created_at FROM tasks WHERE id = ?", (card,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if r:
            if r[2] in ("done", "archived"):
                return None
            return {"id": r[0], "title": r[1], "status": r[2], "created_at": r[3], "board": board,
                    "via": "recovery_lock"}
    # Not readable anywhere: usually the card lives on the board being rebuilt. Its live holder still owns it.
    board = path.parent.name if path.parent.parent.name == "boards" else None
    return {"id": card, "title": "", "status": "running", "created_at": None, "board": board, "via": "recovery_lock"}


def _adopt_signature_card(d: dict, fp: str, store, now: float,
                          sig_owner_fn: Optional[Callable] = None) -> Optional[dict]:
    """No session owner (e.g. a background signal with no session): adopt an open card that already names the same
    error signature (exception + function/file, or the identical symptom). Logs `selfheal_adopted_owner` via=signature."""
    if sig_owner_fn is None and os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":
        return None
    sig = ({"corruption_db": d["incident"]["db"], "host": d["incident"]["host"]}
           if d.get("incident") else error_signature(d))
    if not sig:
        return None
    lane = LaneState(store.root)
    st = lane.get(fp)
    try:
        cards = [c for c in (sig_owner_fn or signature_owned_cards)(sig) if c["id"] not in known_cards(st)]
    except Exception as exc:
        store.log({"event": "selfheal_adopt_error", "fingerprint": fp, "via": "signature", "error": str(exc)[:200]})
        return None
    filers = _filer_cards(d)
    if any(c["id"] in filers for c in cards):
        store.log({"event": "selfheal_adopt_skipped", "via": "signature", "why": "candidate is the filing card",
                   "fingerprint": fp, "filer_cards": sorted(c["id"] for c in cards if c["id"] in filers)})
        cards = [c for c in cards if c["id"] not in filers]
    if not cards:
        return None
    c = cards[0]
    lane.update(fp, status="approved", fix_card=c["id"], approved_at=now, component=d.get("component"),
                symptom=d.get("symptom"), session_id=d.get("session_id"), board=c.get("board"),
                filed_day=_utc_day(now), cards=sorted(known_cards(st) | {c["id"]}), **_complaint_identity(d))
    store.log({"event": "selfheal_adopted_owner", "via": "signature", "fingerprint": fp, "card": c["id"],
               "status": c.get("status"), "board": c.get("board"), "session_id": d.get("session_id"),
               "signature": {k: v for k, v in sig.items() if k != "symptom"},
               "candidates": [x["id"] for x in cards]})
    return {"action": "skip", "why": f"adopted owner card {c['id']} by error signature", "card": c["id"],
            "adopted": True, "via": "signature"}


# Root families: different exceptions that are faces of ONE root, so one open lane owns them all. A Kanban board
# read through a stale per-process view (deleted -wal/-shm, dropped locks) shows up as "disk I/O error", as a false
# KanbanDbCorruptError from the health guard, or (after the quarantine fix) as KanbanDbUnreliableViewError.
_KANBAN_DB_FACE = re.compile(r"KanbanDbCorruptError|KanbanDbUnreliableViewError|disk I/O error|"
                             r"database disk image is malformed", re.I)
_KANBAN_DB_WHERE = re.compile(r"kanban_db_connect|kanban_session_activity|kanban\.db|kanban DB", re.I)


def root_family(d: dict) -> Optional[str]:
    """Family name for a defect whose exception is one face of a known shared root, else None."""
    text = "\n".join(str(d.get(k) or "") for k in ("symptom", "details"))
    if not _KANBAN_DB_FACE.search(text):
        return None
    if re.search(r"KanbanDb(?:Corrupt|UnreliableView)Error", text) or _KANBAN_DB_WHERE.search(text):
        return "kanban-db-view"
    return None


_DISK_FULL_FACE = re.compile(r"disk I/O error|database or disk is full|Errno 28\b|No space left on device", re.I)
_DISK_PRESSURE_TITLE = re.compile(r"ENOSPC|critical|%\s*used|free", re.I)


def disk_pressure_owner(d: dict) -> Optional[dict]:
    """Same-host disk incident, open or created within two hours of the event.

    Require a host token in the title, not an incidental host mention in a traceback.
    No generic family-lane alias: that would merge unrelated hosts and incidents.
    """
    import sqlite3
    host = str(d.get("host") or "")
    if not host or os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":
        return None
    try:
        at = float(d["at"])
    except (KeyError, TypeError, ValueError):
        return None
    text = "\n".join(str(d.get(k) or "") for k in ("symptom", "details"))
    has_face = bool(_DISK_FULL_FACE.search(text))
    if not has_face and (d.get("source") != "signal" or host != host_name()):
        return None
    host_re = re.compile(r"(?<![\w.-])" + re.escape(host) + r"(?![\w.-])", re.I)
    candidates = []
    for board, path in _board_dbs(_kanban_home()):
        db = None
        try:
            db = _ro_state(path)
            for cid, title, status, created in db.execute("SELECT id, title, status, created_at FROM tasks"):
                title = title or ""
                if not (host_re.search(title) and re.search(r"\bdisk\b", title, re.I)
                        and _DISK_PRESSURE_TITLE.search(title)):
                    continue
                if status in ("done", "archived") and abs(float(created or 0) - at) > 7200:
                    continue
                candidates.append({"id": cid, "board": board, "created_at": created,
                                   "status": status})
        except (sqlite3.Error, ValueError, TypeError):
            continue
        finally:
            if db is not None:
                db.close()
    if candidates and not has_face:
        # Query only this host, only a bounded event window, and only after finding an owner.
        import subprocess
        try:
            journal = subprocess.run(
                ["journalctl", "--since", f"@{at - 120:.0f}", "--until", f"@{at + 120:.0f}",
                 "--no-pager", "-o", "cat", "--grep", r"No space left on device|Errno 28\b|ENOSPC", "-n", "1"],
                capture_output=True, text=True, timeout=5)
            if journal.returncode != 0 or not re.search(r"No space left on device|Errno 28\b|ENOSPC", journal.stdout):
                return None
        except (OSError, subprocess.TimeoutExpired):
            return None
    return min(candidates, key=lambda c: (abs(float(c["created_at"] or 0) - at), c["id"]), default=None)


def _adopt_disk_pressure(d: dict, store, m: str, runner=None) -> Optional[dict]:
    owner = disk_pressure_owner(d)
    if not owner:
        return None
    result = {"action": "skip", "adopted": True, "via": "host-disk-full",
              "card": owner["id"], "why": "adopted disk-pressure owner", "fingerprint": d["fingerprint"]}
    if m == "live":
        commented = _open_card_comment(d, {"fix_card": owner["id"], "board": owner["board"]}, store, runner)
        if commented["action"] == "error":
            return commented
        store.log({"event": "selfheal_adopted_owner", "family": "host-disk-full", **result})
    return result


def _lane_family(e: dict) -> Optional[str]:
    return e.get("family") or root_family({"symptom": e.get("symptom")})


def _complaint_identity(d: dict) -> dict:
    """Stable evidence shared by hook and digest detectors; never hash generated symptom text.

    Hook `at` is detection time; backstop `at` is the user-turn time. The quote bridges
    delayed detection. Use 300 normalized characters (the queue's evidence limit).
    Missing fields remain unknown: do not backfill legacy lanes from their filing time.
    A hand re-file (`filed_by`) carries the filer's words, not the user's turn: no quote hash and no detection-time
    fallback, so one re-file text shared by several fingerprints never merges their lanes (t_01a1cfc408527b07).
    An explicit `complaint_at` copied from the original filing still counts.
    """
    if d.get("every_occurrence"):
        # A machine signal counted per occurrence (lifecycle-guard refusals): one session can hit several shapes, each
        # its own defect. It names no user turn, so it neither adopts nor lends a complaint-turn owner.
        return {"complaint_at": None, "complaint_quote_hash": None}
    if d.get("filed_by"):
        quote, at = "", d.get("complaint_at")
    else:
        quote = re.sub(r"\s+", " ", clean_user(str(d.get("quote") or ""))).strip().casefold()[:300]
        at = d.get("complaint_at", d.get("at"))
    try:
        at = float(at) if at is not None else None
    except (TypeError, ValueError):
        at = None
    return {"complaint_at": at,
            "complaint_quote_hash": hashlib.sha256(quote.encode()).hexdigest()[:20] if quote else None}


# Exact transcript boundaries from agent/turn_failure_copy.py. Keep a standalone
# fallback: the digest consumer can run without the source host's core installed.
_FAILED_TURN_NOTICES = frozenset({
    "Your request was not processed. Send it again if you still want me to carry it out.",
    "This turn did not complete. Some actions may already have run; verify their effects before resending.",
    "Hermes restarted before this turn finished. It continues automatically once the backend is back; "
    "send it again only if nothing happens within a few minutes.",
    "An automated delivery was interrupted and was not run again. Some actions may already have run; verify their effects.",
})


def _failed_turn_boundary(turn: dict) -> bool:
    text = str(turn.get("prev") or "").strip()
    if turn.get("prev_display_kind") == "failed_turn" or text in _FAILED_TURN_NOTICES:
        return True
    # The fifth boundary is parameterized by a validated refusal category.
    return bool(re.fullmatch(
        r"The model provider's content filter refused this turn(?: \(category: [A-Za-z0-9_-]{1,80}\))?\. "
        r"Try to rephrase your question, or start a new chat or fork from before the problematic context; "
        r"a long conversation can contribute to a filter trigger\."
        r"(?: Some actions may already have run; verify their effects\.)?", text))


def _failed_turn_nudge_since(turns: list, turn: dict) -> Optional[float]:
    """Conservative digest interval: never bridge a real reply or missing history.

    A digest stores the previous reply on the *next user* row, without its own
    timestamp. That user's timestamp is a safe upper bound for the real reply.
    With no retained reply, start at the first retained user row, not session birth.
    """
    if not _failed_turn_boundary(turn):
        return None
    earlier = turns[:next(i for i, t in enumerate(turns) if t is turn)]
    since = turns[0]["t"]
    for t in earlier:
        if not _failed_turn_boundary(t):
            since = t["t"]
    return since


def _adopt_complaint_lane(d: dict, fp: str, store, now: float) -> Optional[dict]:
    """Different fingerprints for the same session/user turn share the original owner.

    A resolved complaint is not a new recurrence merely because a second detector saw
    it late. Declined owners and expired not-defect decisions may be investigated anew.
    """
    sid = d.get("session_id")
    identity = _complaint_identity(d)
    if not sid or not any(identity.values()):
        return None
    # A long quote shared by other sessions is one piece of evidence, not N complaints (agent-ea7ff00170: one
    # injected Kanban card body, one wake per session, became 39 cards). Only while its owner card is open; a short
    # quote ("so, can you fix it please") may be two real complaints and still files per session.
    long_quote = len(re.sub(r"\s+", " ", clean_user(str(d.get("quote") or ""))).strip()) >= SAME_QUOTE_MIN_CHARS
    lane = LaneState(store.root)
    owners = []
    entries = lane.read()
    for ofp, e in entries.items():
        # An alias retains the old turn even if its canonical fingerprint later recurs.
        # Consult the canonical lifecycle rather than trusting a stale copied verdict.
        canonical = entries.get(e["complaint_owner"], {}) if e.get("complaint_owner") else e
        if (ofp == fp and not e.get("complaint_owner")) or not canonical or canonical.get("status") == "declined":
            continue
        if e.get("session_id") != sid:
            card = e.get("fix_card") or e.get("card")
            if (long_quote and card and identity["complaint_quote_hash"]
                    and identity["complaint_quote_hash"] == e.get("complaint_quote_hash")
                    and canonical.get("status") in ("filed", "proposed", "approved")):
                owners.append((e.get("filed_at") or now, ofp, card, e, "complaint_quote"))
            continue
        if (canonical.get("status") == "not_defect" and
                now - (canonical.get("closed_at") or 0) >= NOT_DEFECT_DAYS * 86400):
            continue
        card = e.get("fix_card") or e.get("card")
        same_quote = (identity["complaint_quote_hash"] and
                      identity["complaint_quote_hash"] == e.get("complaint_quote_hash"))
        try:
            same_time = (identity["complaint_at"] is not None and e.get("complaint_at") is not None and
                         abs(identity["complaint_at"] - float(e["complaint_at"])) <= 120)
        except (TypeError, ValueError):
            same_time = False
        if card and (same_quote or same_time):
            owners.append((e.get("filed_at") or now, ofp, card, e, "complaint_turn"))
        elif card and d.get("source") == "backstop" and d.get("failed_turn_since") is not None:
            owner_at = e.get("complaint_at")
            if owner_at is None:
                owner_at = e.get("filed_at")
            nudge_at = identity["complaint_at"]
            try:
                if nudge_at is not None and float(d["failed_turn_since"]) <= float(owner_at) <= nudge_at:
                    owners.append((e.get("filed_at") or now, ofp, card, e, "failed_turn_nudge"))
            except (TypeError, ValueError):
                pass
    if not owners:
        return None
    _, ofp, card, e, via = sorted(owners, key=lambda o: (o[0], o[1]))[0]
    st = lane.get(fp)
    # Keep only an alias, not a copied lifecycle: copying `proposed` would launch a
    # second fix from sync(), and copying `not_defect` would suppress this fingerprint
    # globally. Keep its own earlier lifecycle for a genuinely new recurrence.
    previous = st.get("complaint_previous") if st.get("status") == "adopted" else (st or None)
    lane.update(fp, status="adopted", card=None, fix_card=card,
                complaint_previous=previous,
                fixed_at=None, closed_at=None, filed_day=None,
                component=d.get("component"), symptom=d.get("symptom"), session_id=sid,
                board=e.get("board"), complaint_owner=e.get("complaint_owner") or ofp,
                complaint_at=e.get("complaint_at"), complaint_quote_hash=e.get("complaint_quote_hash"),
                cards=sorted(known_cards(st) | {card}))
    store.log({"event": "selfheal_adopted_owner", "via": via, "fingerprint": fp,
               "owner_fingerprint": ofp, "card": card, "session_id": sid, "board": e.get("board"),
               "owner_session_id": e.get("session_id")})
    why = f"same quote as {card} ({ofp})" if via == "complaint_quote" else f"same complaint turn as {ofp}"
    if via == "failed_turn_nudge":
        why = f"nudge after failed turn owned by {ofp}"
    if via == "complaint_quote":
        store.log({"event": "selfheal_skip", "mode": "live", "fingerprint": fp, "why": why, "card": card,
                   "session_id": sid})
    return {"action": "skip", "why": why, "card": card,
            "adopted": True, "via": via, "fingerprint": fp}


def _adopt_family_lane(d: dict, fp: str, store, now: float) -> Optional[dict]:
    """Another open lane (filed/proposed/approved) already owns this defect's root family: join its card instead of
    filing a second investigation of the same root. The oldest open lane wins. Logs selfheal_adopted_owner via=family."""
    fam = root_family(d)
    if not fam:
        return None
    lane = LaneState(store.root)
    owners = []
    for ofp, e in lane.read().items():
        if ofp == fp or e.get("suppressed") or e.get("status") not in ("filed", "proposed", "approved"):
            continue
        card = e.get("fix_card") or e.get("card")
        if card and _lane_family(e) == fam:
            first = min([h[0] for h in (e.get("history") or []) if h] or [e.get("filed_at") or now])
            owners.append((first, ofp, card, e.get("board")))
    if not owners:
        return None
    _, ofp, card, board = sorted(owners)[0]
    st = lane.get(fp)
    lane.update(fp, status="approved", fix_card=card, approved_at=now, component=d.get("component"),
                symptom=d.get("symptom"), session_id=d.get("session_id"), board=board, family=fam,
                family_owner=ofp, filed_day=_utc_day(now), cards=sorted(known_cards(st) | {card}), **_complaint_identity(d))
    store.log({"event": "selfheal_adopted_owner", "via": "family", "family": fam, "fingerprint": fp, "card": card,
               "owner_fingerprint": ofp, "board": board, "session_id": d.get("session_id"),
               "candidates": [o[2] for o in sorted(owners)]})
    return {"action": "skip", "why": f"adopted {ofp}'s card {card}: same root family {fam}", "card": card,
            "adopted": True, "via": "family", "fingerprint": fp}


def corruption_incident(d: dict, now: float) -> Optional[dict]:
    """One SQLite incident per source host, DB identity and UTC event-hour, not per callsite.
    Pathless kanban tracebacks refer to the source's default board, never the hub's env override.
    Other pathless SQLite errors are left alone rather than merging unrelated databases.
    """
    text = "\n".join(str(d.get(k) or "") for k in ("symptom", "details", "quote"))
    if not (re.search(r"database disk image is malformed|KanbanDbCorruptError|file is not a database", text, re.I)
            # only the log-record collector's own signal; text another defect quotes never joins an incident
            or (d.get("kind") == "corruption" and "(unconfirmed corruption)" in str(d.get("symptom") or ""))):
        return None
    paths = re.findall(r"(?:~?/|[A-Za-z]:[/\\])[^\s\"'<>():,;]*\.(?:db|sqlite3?|sqlite)", text)
    path = d.get("db_path") or (paths[0] if paths else None)
    if not path and d.get("component") == "kanban":
        path = "~/.hermes/kanban.db"
    if not path:
        return None
    # The source home may not be the collector's home. Do not expand ~ on this host.
    path = re.sub(r"^(?:/home/[^/]+|/Users/[^/]+)(?=/\.hermes/)", "~", str(path))
    return {"db": os.path.normpath(path), "host": d.get("host") or host_name(),
            "hour": int(d.get("at") or now) // 3600}


def _corruption_comment(d: dict, st: dict, store, runner=None) -> dict:
    card = (st.get("card") if st.get("status") in ("filed", "proposed") else st.get("fix_card"))
    card = card or st.get("fix_card") or st.get("card")
    if not card:
        return {"action": "skip", "why": "incident suppressed"}
    board = st.get("board")
    argv = ["hermes", "kanban"] + (["--board", board] if board else [])
    body = (f"SQLite corruption occurrence ({d['incident']['db']}, host {d['incident']['host']}): "
            f"{d.get('symptom') or ''}\n{str(d.get('details') or '')[-1500:]}")
    rc, out, err = _run(argv + ["comment", card, body], runner)
    store.log({"event": "selfheal_incident_comment", "fingerprint": d["fingerprint"], "card": card,
               "ok": rc == 0, "error": (err or out)[-200:] if rc else None})
    return {"action": "skip" if rc == 0 else "error", "card": card,
            "why": "existing SQLite incident" if rc == 0 else "incident comment failed"}


def _open_card_comment(d: dict, st: dict, store, runner=None) -> dict:
    """A signal flagged ``comment_open_card`` (one occurrence of a defect whose card is open): add it there.
    `proposed` (the investigation finished, its fix card not filed yet; <= one 5-minute sync) still targets the done
    investigation card, which links the fix card once sync files it: an accepted gap, the occurrence stays on record."""
    card = (st.get("card") if st.get("status") in ("filed", "proposed") else st.get("fix_card")) or st.get("card")
    if not card:
        return {"action": "skip", "why": "no open card"}
    argv = ["hermes", "kanban"] + (["--board", st["board"]] if st.get("board") else [])
    body = (f"Occurrence on {d.get('host') or '?'}: {d.get('symptom') or ''}\n"
            f"{str(d.get('details') or '')[-1500:]}")
    rc, out, err = _run(argv + ["comment", card, body], runner)
    store.log({"event": "selfheal_occurrence_comment", "fingerprint": d["fingerprint"], "card": card,
               "ok": rc == 0, "error": (err or out)[-200:] if rc else None})
    return {"action": "skip" if rc == 0 else "error", "card": card,
            "why": "occurrence added to the open card" if rc == 0 else "occurrence comment failed"}


def file_defect(d: dict, store=None, m: Optional[str] = None, runner=None, now: Optional[float] = None,
                cfg: Optional[dict] = None, budget_fn: Optional[Callable] = None,
                owner_fn: Optional[Callable] = None, sig_owner_fn: Optional[Callable] = None) -> dict:
    store = _store(store)
    now = now or time.time()
    incident = corruption_incident(d, now)
    if incident:
        key = json.dumps(incident, sort_keys=True)
        d = {**d, "incident": incident, "host": incident["host"],
             "fingerprint": "sqlitecorrupt-" + hashlib.sha1(key.encode()).hexdigest()[:16]}
    def run():
        with _admission_lock(store, live=(m or mode()) == "live" and is_hub(cfg)):
            return _file_defect(d, store, m, runner, now, cfg, budget_fn, owner_fn, sig_owner_fn)
    if not incident or (m or mode()) != "live" or not is_hub(cfg):
        return run()
    # Lane JSON is a read/check/create/write transaction. One lock also protects different
    # incident fingerprints from losing each other's updates in the shared lane file.
    import fcntl
    Path(store.root).mkdir(parents=True, exist_ok=True)
    with (Path(store.root) / "sqlite-incidents.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return run()


def _file_defect(d: dict, store=None, m: Optional[str] = None, runner=None, now: Optional[float] = None,
                cfg: Optional[dict] = None, budget_fn: Optional[Callable] = None,
                owner_fn: Optional[Callable] = None, sig_owner_fn: Optional[Callable] = None) -> dict:
    """File one defect: {fingerprint, component, symptom, source, kind, p_defect, session_id, quote, host, at,
    details}. Returns {"action": would_file|filed|skip|forwarded|error, ...}.
    Optional `filed_by`: the card doing a hand (re-)filing; never its owner, and its quote is no complaint turn."""
    store = _store(store)
    m = m or mode()
    now = now or time.time()
    cfg = lane_config() if cfg is None else cfg
    if not d.get("fingerprint"):
        d = {**d, "fingerprint": fingerprint(d.get("component") or "other", d.get("symptom") or "")}
    fp = d["fingerprint"]
    if m == "off":
        return {"action": "skip", "why": "COACH_SELFHEAL=off"}
    if probe_defect(d):  # every path lands here, incl. retry_deferred and payloads forwarded from other hosts
        store.log({"event": "selfheal_skip", "mode": m, "fingerprint": fp, "why": "probe",
                   "session_id": d.get("session_id")})
        return {"action": "skip", "why": "probe"}
    if agent_input_defect(d):  # an agent-driven turn forwarded by a host on older code (agent-d925f95adc)
        store.log({"event": "selfheal_skip", "mode": m, "fingerprint": fp, "why": "agent_input",
                   "session_id": d.get("session_id"), "host": d.get("host")})
        return {"action": "skip", "why": "agent_input"}
    if synthetic_user_row(d):  # queued before the classifier knew this tag: withdraw, never file (agent-b3f86b6bb7)
        closed_lane = _close_synthetic_lane(store, fp, now) if m == "live" else None
        store.log({"event": "selfheal_withdrawn", "mode": m, "fingerprint": fp, "why": "synthetic_tag",
                   "source": d.get("source"), "session_id": d.get("session_id"),
                   "quote": str(d.get("quote") or "")[:200],
                   **({"lane_closed": closed_lane} if closed_lane else {})})
        return {"action": "skip", "why": "synthetic_tag"}
    hub = is_hub(cfg)
    if m == "live" and not hub:
        rc, out, err = _run(hub_ssh_argv(cfg) + [cfg["hub_ssh"],
                             f"python3 {SELF} file --mode live"], runner, stdin=json.dumps(d))
        res = {"action": "forwarded" if rc == 0 else "error", "hub": cfg["hub_ssh"],
               "out": (out or err).strip()[-300:]}
        try:  # the hub prints its own file_defect result as the last line: callers (monitors) need its action
            res["hub_result"] = json.loads((out or "").strip().splitlines()[-1])
        except Exception:
            pass
        store.log({"event": "selfheal_forward", "fingerprint": fp, "ok": rc == 0, "detail": res["out"]})
        return res
    _save_audit_input(store, d)
    if m == "live" and hub:
        closed = kanban_trigger_closed(d, now=now)
        if closed:
            store.log({"event": "selfheal_skip", "mode": m, "fingerprint": fp, "why": closed})
            return {"action": "skip", "why": closed}
    if hub:
        disk_owner = _adopt_disk_pressure(d, store, m, runner)
        if disk_owner:
            return disk_owner
    lane = LaneState(store.root)
    if d.get("incident") and m == "live" and hub:
        # Retain recurrence identity even when admission is capped; fingerprints alone are hashes.
        lane.update(fp, incident=d["incident"])
    st = lane.get(fp)
    if st.get("status") == "adopted" and m == "live" and hub:
        owner = _adopt_complaint_lane(d, fp, store, now)
        if owner:
            if d.get("incident"):
                commented = _corruption_comment(d, lane.get(fp), store, runner)
                if commented["action"] == "error":
                    return commented
            return owner
        if st.get("complaint_previous"):
            st = lane.update(fp, **{**st["complaint_previous"], "complaint_previous": None,
                                   "complaint_owner": None})
    if (d.get("incident") and m == "live" and hub and not st.get("suppressed")
            and (st.get("snooze_until") or 0) <= now):
        adopted = _adopt_recovery_holder(d, fp, st, store, now, runner)
        if adopted:
            return adopted
    if st.get("status") == "fixed" and "fix_shas" not in st:  # fixed before fix_shas was stored: its note has them
        st = lane.update(fp, fix_shas=_logged_fix_shas(store, fp))
    if d.get("every_occurrence") and m == "live" and hub and st.get("status") == "filed":
        # Each occurrence comments on the open card; once that card is done/archived it opens a fresh one. Read the
        # investigation card now instead of waiting for the 5-minute sync, so an occurrence never comments on a closed
        # card. `filed` only: an approved owner's receipt recovery takes the admission lock this pass already holds.
        try:
            if _reconcile_fp(lane, store, fp, st, runner, now, via="occurrence"):
                st = lane.get(fp)
        except Exception as exc:  # health: allow BLE001 -- advisory; sync's reconcile still runs every 5 minutes
            store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": f"occurrence reconcile: {exc}"[:300]})
    dec = decide(d, st, now, cfg)
    if dec["action"] == "stale_runtime":
        return _file_backport(d, fp, st, dec, store, m, runner, now, cfg, budget_fn)
    if dec["action"] == "not_deployed":
        return _file_undeployed(d, fp, st, dec, store, m, runner, now, cfg, budget_fn)
    if dec["action"] == "hold":
        held = dec["held"] + [{"at": dec["at"], "host": d.get("host"), "source": d.get("source"), "seen": now}]
        lane.update(fp, held=held)
        store.log({"event": "selfheal_recurrence_held", "mode": m, "fingerprint": fp, "at": dec["at"],
                   "held": len(held), "until": dec["until"], "host": d.get("host"), "source": d.get("source"),
                   "component": d.get("component"), "previous": st.get("fix_card"), "why": dec["why"]})
        return {"action": "skip", "why": dec["why"], "held": len(held)}
    if dec["action"] == "skip":
        if (d.get("incident") and m == "live" and hub and not st.get("suppressed")
                and (st.get("snooze_until") or 0) <= now and st.get("status") in ("filed", "approved", "proposed")):
            return _corruption_comment(d, st, store, runner)
        if (d.get("comment_open_card") and m == "live" and hub and not st.get("suppressed")
                and (st.get("snooze_until") or 0) <= now and st.get("status") in ("filed", "approved", "proposed")):
            return _open_card_comment(d, st, store, runner)
        store.log({"event": "selfheal_skip", "mode": m, "fingerprint": fp, "why": dec["why"]})
        return {"action": "skip", "why": dec["why"]}
    if m == "live" and hub:
        owner = _adopt_complaint_lane(d, fp, store, now)
        if owner:
            if d.get("incident"):
                commented = _corruption_comment(d, lane.get(fp), store, runner)
                if commented["action"] == "error":
                    return commented
            return owner
    if m == "live" and hub:  # the origin session already queued its own fix card: adopt it, file nothing
        owner = ((_adopt_family_lane(d, fp, store, now) or _adopt_signature_card(d, fp, store, now, sig_owner_fn))
                 if d.get("incident") else
                 (_adopt_session_card(d, fp, store, now, owner_fn) or _adopt_family_lane(d, fp, store, now) or
                  _adopt_signature_card(d, fp, store, now, sig_owner_fn)))
        if owner:
            if d.get("incident"):
                commented = _corruption_comment(d, lane.get(fp), store, runner)
                if commented["action"] == "error":
                    return commented
            return owner
    args = card_args(d, dec["key"], dec.get("previous"), cfg, dec.get("different_error", ""))
    if m == "live" and hub and args.get("board") and not board_present(args["board"]):
        return _defer_missing_board(d, args["board"], store, now)
    b = (budget_fn or budget)(args.get("board"), now)
    sched = None
    if not b["ok_usd"]:
        sched = (_next_utc_midnight(now), f"self-heal lane budget: {b['why'] or 'cap reached'}")
    elif not b["ok_running"]:
        sched = (_iso(now + 3600), f"self-heal lane: {b['running']} cards running (max {MAX_RUNNING})")
    if m != "live" or not hub:
        rec = {"event": "selfheal_would_file", "mode": m, "fingerprint": fp, "component": d.get("component"),
               "source": d.get("source"), "why": dec["why"], "recurrence": bool(dec.get("previous")),
               "schedule": sched, "args": args, "host": d.get("host"),
               "budget_available": b.get("available", True)}
        store.log(rec)
        return {"action": "would_file", **rec}
    unavailable = _budget_unavailable(b, fp, store)
    if unavailable:
        return unavailable
    denied = _admission_denied(b, now, store, runner) or _fix_first_denied(b, store, now)
    if denied:
        return _defer_investigation(d, denied, store)
    known, gen = known_cards(st), dec["gen"]
    tid, out, err = None, "", ""
    for _ in range(5):  # an idempotency key that hands back an earlier card (any non-archived status) -> next gen
        rc, out, err = _run_card(args, runner)
        if rc != 0 and _board_missing_output(err, out):
            return _defer_missing_board(d, args.get("board") or "?", store, now)
        tid = _task_id(out) if rc == 0 else None
        if not tid or tid not in known:
            break
        store.log({"event": "selfheal_key_collision", "fingerprint": fp, "key": args["idempotency_key"], "card": tid})
        gen += 1
        args = card_args(d, inv_key(fp, gen), dec.get("previous"), cfg, dec.get("different_error", ""))
    if not tid or tid in known:
        why = (err or out).strip()[-300:] if not tid else f"idempotency keys keep returning earlier card {tid}"
        refused = not tid and placement_refused(err, out)
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why,
                   **({"code": "placement_refused"} if refused else {})})
        if refused:  # every later create of this pass would be refused the same way: the caller stops the pass
            note_placement_refused(store, fp, err or out, now, runner)
            return {"action": "error", "why": why, "placement_refused": True}
        return {"action": "error", "why": why}
    prev = dec.get("previous")
    kw: dict = dict(status="filed", card=tid, component=d.get("component"), symptom=d.get("symptom"),
              cron_error=_cron_error(d) if fp.startswith("cron-") else None,
              filed_day=_utc_day(now), filed_at=now, board=args.get("board"), session_id=d.get("session_id"),
              fix_card=None if not prev else st.get("fix_card"), gen=gen,
              cards=sorted(known | {tid}), proposed_at=None, family=root_family(d), held=[],
              complaint_owner=None, complaint_previous=None, source=d.get("source"),
              **_complaint_identity(d))
    if prev:
        kw["recurrences"] = int(st.get("recurrences") or 0) + 1
    elif dec["why"] == "refile":
        kw["refiles"] = int(st.get("refiles") or 0) + 1
    lane.update(fp, **kw)
    store.log({"event": "selfheal_filed", "fingerprint": fp, "card": tid, "component": d.get("component"),
               "board": args.get("board"), "recurrence": bool(prev), "scheduled": sched[0] if sched else None,
               "source": d.get("source")})
    if prev:
        store.log({"event": "selfheal_recurrence", "fingerprint": fp, "card": tid,
                   "previous": prev.get("fix_card"), "component": d.get("component")})
    return {"action": "filed", "card": tid, "key": args["idempotency_key"], "scheduled": sched}


def _logged_fix_shas(store, fp: str) -> list:
    """Shas from the latest `selfheal_fixed` log line of `fp` (lane entries written before `fix_shas` existed)."""
    try:
        lines = (Path(store.root) / "log.jsonl").read_text(errors="replace").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        if '"selfheal_fixed"' not in line or fp not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "selfheal_fixed" and r.get("fingerprint") == fp:
            return fix_shas(r.get("note") or "")
    return []


def backport_brief(d: dict, fp: str, st: dict, dec: dict) -> str:
    seen = fence_evidence(f"""Host: {d.get('host')}
Checkout: {dec['checkout']}
HEAD:     {dec['head']}  (does not contain {dec['sha']}, and the fix's diff is not in its working tree)
Symptom:  {d.get('symptom')}

Evidence (traceback, home shown as ~):
```
{str(d.get('details') or '')[-1500:]}
```""")
    return f"""Self-heal BACKPORT for defect `{fp}`: a runtime still raises an error that fix
{dec['sha'][:12]} (card {st.get('fix_card') or '?'}) already removed on main. That fix held; this checkout never
got it, so this is not a "came back" investigation.
{home_pin_note(PIN_BACKPORT)}

{seen}

Do this:
1. Find who owns that checkout before touching it: a RUNBOOK.md next to it, the systemd unit or process running
   from it (`systemctl --user status`, `ps -eo pid,args | grep <checkout>`), a `git worktree list` lock reason.
   Follow that owner's upgrade/restart rules; a pinned runtime usually keeps local edits on a detached HEAD.
2. Backport the fix's diff without losing local edits: `git -C <repo> show {dec['sha'][:12]} -- <paths> | git -C
   {dec['checkout']} apply --check`, then apply. Keep the change uncommitted unless the owner's rules say to
   commit; the self-heal check treats a reverse-applicable diff as "has the fix".
3. Prove it in that checkout's own interpreter (repro the error before, gone after); restart the runtime only when
   its owner's rules allow it (idle), else record that it applies at the next restart.
4. Note the backport where the owner tracks the runtime (its RUNBOOK "CURRENT" section or owner card), then
   complete this card with the patch path and the undo (`git -C {dec['checkout']} apply -R <patch>`).
Do not post to the Inbox. If the owner forbids the change, kanban_block with the exact ask.
"""


def _file_backport(d: dict, fp: str, st: dict, dec: dict, store, m: Optional[str], runner, now: float,
                   cfg: Optional[dict], budget_fn: Optional[Callable]) -> dict:
    """ONE card per checkout+fix sha ("backport <sha> to <checkout>"); the fingerprint stays fixed."""
    lane = LaneState(store.root)
    key = dec["key"]
    rec = {"event": "selfheal_stale_runtime", "mode": m, "fingerprint": fp, "checkout": dec["checkout"],
           "sha": dec["sha"], "head": dec["head"], "key": key, "host": d.get("host"), "source": d.get("source"),
           "component": d.get("component"), "previous": st.get("fix_card")}
    backports = dict(st.get("backports") or {})
    if backports.get(key):
        why = f"backport already filed: {backports[key]} ({dec['why']})"
        store.log({"event": "selfheal_skip", "mode": m, "fingerprint": fp, "why": why})
        return {"action": "skip", "why": why}
    r = route(d, cfg)
    args = {"title": f"backport {dec['sha'][:11]} to {dec['checkout']}"[:200],
            "body": backport_brief(d, fp, st, dec), "idempotency_key": key, "model": MODEL, "provider": PROVIDER,
            "max_runtime": FIX_RUNTIME, "skills": list(FIX_SKILLS), "board": r["board"], "assignee": "default",
            "created_by": CREATED_BY}
    if m != "live" or not is_hub(cfg):
        store.log({**rec, "would_file": True, "args": args})
        return {"action": "stale_runtime", "card": None, "key": key, "why": dec["why"]}
    b = (budget_fn or budget)(args.get("board"), now)
    if not b.get("ok_usd", True) or not b.get("ok_running", True):
        why = f"backport deferred: {b.get('why') or 'self-heal budget/concurrency'}; the next occurrence retries"
        store.log({**rec, "deferred": why})
        return {"action": "skip", "why": why}
    rc, out, err = _run_card(args, runner)
    tid = _task_id(out) if rc == 0 else None
    if not tid:
        why = (err or out).strip()[-300:]
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why, "key": key})
        return {"action": "error", "why": why}
    backports[key] = tid
    lane.update(fp, backports=backports, cards=sorted(known_cards(st) | {tid}))
    store.log({**rec, "card": tid})
    return {"action": "stale_runtime", "card": tid, "key": key, "why": dec["why"]}


def undeployed_brief(d: dict, fp: str, st: dict, dec: dict) -> str:
    seen = fence_evidence(f"Host: {d.get('host')}\nLatest occurrence: {_iso(float(dec.get('at') or 0))}: {d.get('symptom')}")
    return f"""Self-heal DEPLOY check for defect `{fp}`: fix {dec['sha'][:12]} (card {st.get('fix_card') or '?'}) landed
on the state branch at about {_iso(float(st.get('fixed_at') or 0))}, and host `{dec['label']}` still
has no `sync: {dec['label']}` commit containing it. Its newest sync commit is {_iso(dec['last_sync'])}. Its post-fix
occurrences are held as "not deployed", not filed as "the fix didn't hold"; this card exists so that hold doesn't
go on silently.
{home_pin_note(PIN_DEPLOY)}

{seen}

Do this:
1. Find out why the host isn't syncing: is it reachable (`tailscale status`, the hub's hosts view), and when did
   its hermes-sync last run (its `sync: {dec['label']}` commits on origin/state; on the host,
   `~/.hermes/logs/hermes-sync.log`)? A sleeping laptop needs nothing: complete with "away; holds until it syncs".
2. A host that syncs but never merges origin (diverged, a failing import) is the defect: fix the sync there, per
   the hermes-mods-repo skill.
3. A retired host or renamed label: correct `host_sync_labels` in the hub's <coach>/selfheal-config.json.
Do not post to the Inbox. If only David can act (power the host on, sign in), kanban_block with the exact ask.
"""


def _file_undeployed(d: dict, fp: str, st: dict, dec: dict, store, m: Optional[str], runner, now: float,
                     cfg: Optional[dict], budget_fn: Optional[Callable]) -> dict:
    """Hold a not-yet-deployed occurrence (selfheal_not_deployed, nothing filed); once overdue, file ONE card per
    host label + fix sha ("host <label> hasn't synced fix <sha>"). The fingerprint stays fixed either way."""
    lane = LaneState(store.root)
    rec = {"event": "selfheal_not_deployed", "mode": m, "fingerprint": fp, "host": d.get("host"),
           "label": dec["label"], "sha": dec["sha"], "at": dec.get("at"), "last_sync": dec["last_sync"],
           "deployed_at": dec["deployed_at"], "overdue": dec["overdue"], "source": d.get("source"),
           "component": d.get("component"), "previous": st.get("fix_card"), "why": dec["why"]}
    if not dec["overdue"]:
        store.log(rec)
        return {"action": "skip", "why": dec["why"], "not_deployed": True}
    key = dec["key"]
    filed = dict(st.get("undeployed") or {})
    if filed.get(key):
        why = f"undeployed card already filed: {filed[key]} ({dec['why']})"
        store.log({**rec, "card": filed[key]})
        return {"action": "skip", "why": why, "not_deployed": True}
    r = route(d, cfg)
    args = pool_args({"title": f"host {dec['label']} hasn't synced fix {dec['sha'][:11]}"[:200],
            "body": undeployed_brief(d, fp, st, dec), "idempotency_key": key, "model": TRIAGE_MODEL, "provider": TRIAGE_PROVIDER,
            "max_runtime": INVESTIGATION_RUNTIME, "skills": ["hermes-mods-repo"], "board": r["board"],
            "assignee": "default", "created_by": CREATED_BY, "requires": list(SELFHEAL_REQUIRES), "home_pin": True})
    if m != "live" or not is_hub(cfg):
        store.log({**rec, "would_file": True, "args": args})
        return {"action": "not_deployed", "card": None, "key": key, "why": dec["why"]}
    b = (budget_fn or budget)(args.get("board"), now)
    if not b.get("ok_usd", True) or not b.get("ok_running", True):
        why = f"undeployed card deferred: {b.get('why') or 'self-heal budget/concurrency'}; the next occurrence retries"
        store.log({**rec, "deferred": why})
        return {"action": "skip", "why": why, "not_deployed": True}
    rc, out, err = _run_card(args, runner)
    tid = _task_id(out) if rc == 0 else None
    if not tid:
        why = (err or out).strip()[-300:]
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why, "key": key})
        return {"action": "error", "why": why}
    filed[key] = tid
    lane.update(fp, undeployed=filed, cards=sorted(known_cards(st) | {tid}))
    store.log({**rec, "card": tid})
    return {"action": "not_deployed", "card": tid, "key": key, "why": dec["why"]}


def file_fix(fp: str, store=None, runner=None, now: Optional[float] = None, budget_fn=None,
             trigger: str = "do_it") -> dict:
    """Admission is serialized with investigations (see _admission_lock) and reserves FIX_BUDGET."""
    store = _store(store)
    bf = budget_fn or (lambda bd, n: budget(bd, n, new_card_usd=FIX_BUDGET))
    with _admission_lock(store, live=True):
        return _file_fix(fp, store, runner, now, bf, trigger)


def _file_fix(fp: str, store=None, runner=None, now: Optional[float] = None, budget_fn=None,
              trigger: str = "do_it") -> dict:
    """Do it: the fix card `selfheal:<fp>[:r<gen>]:fix` on the hub, keyed to the current generation (idempotent
    within it; a key that returns an earlier card of this fingerprint is an error, never an approval)."""
    store = _store(store)
    now = now or time.time()
    lane = LaneState(store.root)
    st = lane.get(fp)
    if not FP_RE.match(fp or ""):
        return {"action": "error", "why": f"not a fingerprint: {fp!r}"}
    if st.get("fix_card") and st.get("status") in ("approved", "fixed"):
        r = route({"component": st.get("component")})
        if split_fix(st, r["board"]) and st.get("status") == "approved" and not st.get("verify_card"):
            # The verify child failed to file with its fix card: retry it (idempotent key), never a second fix.
            _file_verify(fp, st, st["fix_card"], r, store, runner, now)
        return {"action": "skip", "why": f"fix card exists: {st['fix_card']}", "card": st["fix_card"]}
    if _synthetic_fix_row(store, fp):
        # Filed before the classifier knew the quote's tag (agent-71a1e1fdca: a "[runtime] ... NOT shown" row filed
        # 11-09 06:21, f8d40bd5 at 13:48, auto-approved 17 h later): the pending/file passes withdraw such rows, but
        # one already proposed reached here. Withdraw it now; a fix card would only re-diagnose Hermes's own text.
        rec = _detect_record(store, fp)
        lane.update(fp, status="not_defect", closed_at=now)
        store.log({"event": "selfheal_withdrawn", "mode": "live", "fingerprint": fp, "why": "synthetic_tag",
                   "stage": "fix", "source": rec.get("source"), "session_id": rec.get("session_id"),
                   "quote": str(rec.get("evidence") or rec.get("quote") or "")[:200]})
        return {"action": "skip", "why": "synthetic_tag"}
    r = route({"component": st.get("component")})
    split = split_fix(st, r["board"])
    key = fix_key(fp, generation(st) or 1)
    # Incident text (even the "cron" component) is evidence, not an execution need; titles never carry it raw.
    args = {"title": f"{FIX_TITLE} {'defect ' + fp.rsplit('-', 1)[-1] if split else incident_title(st.get('symptom') or fp)}",
            "body": portable_fix_brief(fp, st) if split else fix_brief(fp, st),
            "idempotency_key": key, "model": MODEL, "provider": PROVIDER, "max_runtime": FIX_RUNTIME,
            "skills": (list(BB_SKILLS) if r["bb"] else list(FIX_SKILLS)), "board": r["board"],
            "assignee": "default", "created_by": CREATED_BY,
            "requires": list(FIX_REQUIRES if split else SELFHEAL_REQUIRES), "home_pin": not split}
    if not split:  # a one-card (bb) fix on the hub goes to the pool like the investigation: no machine:hub pin
        args = pool_args(args)
    if args.get("board") and not board_present(args["board"]):
        why = f"kanban board {args['board']!r} does not exist"
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why, "board": args["board"]})
        return {"action": "error", "why": why, "board": args["board"]}
    b = (budget_fn or budget)(args.get("board"), now)
    unavailable = _budget_unavailable(b, fp, store)
    if unavailable:
        return unavailable
    denied = _admission_denied(b, now, store, runner)
    if denied:
        # Keep status=proposed: sync rereads the same Do-it outcome and retries admission.
        _log_deferral_once(store, fp, denied, b, now)
        return denied
    rc, out, err = _run_card(args, runner)
    tid = _task_id(out) if rc == 0 else None
    if not tid and _board_missing_output(err, out):
        why = (err or out).strip()[-300:] or f"kanban board {args.get('board')!r} does not exist"
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why, "board": args.get("board")})
        return {"action": "error", "why": why, "board": args.get("board")}
    if not tid:
        # Log every refused create: this bare return hid refusals from log.jsonl (only the cron's 50-run stdout saw
        # them). status stays proposed, so the next pass retries.
        why = (err or out).strip()[-300:] or f"kanban create exited {rc} with no task id"
        refused = placement_refused(err, out)
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why, "rc": rc, "key": key,
                   "board": args.get("board"), "stage": "fix", **({"code": "placement_refused"} if refused else {})})
        return {"action": "error", "why": why, **({"placement_refused": True} if refused else {})}
    if tid in known_cards(st):
        why = f"fix key {key} returned earlier card {tid}"
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why})
        return {"action": "error", "why": why}
    sched = None
    lane.update(fp, status="approved", fix_card=tid, approved_at=now, cards=sorted(known_cards(st) | {tid}),
                verify_card=None)
    store.log({"event": "selfheal_approved", "fingerprint": fp, "card": tid, "component": st.get("component"),
               "trigger": trigger, "scheduled": sched, "split": split})
    verify = _file_verify(fp, lane.get(fp), tid, r, store, runner, now) if split else None
    return {"action": "filed", "card": tid, "key": key, "scheduled": sched, "verify_card": verify}


def remote_verify() -> bool:
    """True when this gateway's VERIFY children run on the pool (its lane is reachable with hub-read/hub-admin).
    An unresolvable identity is False: the home-pin path then fails the create closed (HomePinUnresolved)."""
    return _placement_evidence.remote_home()


def remote_lane(board: Optional[str] = None) -> bool:
    """True when a home-bound card for ``board`` goes to the pool instead of a machine pin: this gateway's lane is
    remote-reachable (:func:`remote_verify`) and the board is the pool's (default). A named board stays pinned."""
    return (not board or board == "default") and remote_verify()


_LOCAL_EVENT_RE = re.compile(r"`(" + re.escape(SELF_EVENT) + r"[^`']*)`")


def remote_body(body: str) -> str:
    """A home-pinned brief rewritten for a pool worker: the pin sentence becomes :data:`REMOTE_NOTE` and every
    backticked lane `event` command runs on the gateway through `hub-admin '<command>'`."""
    body = _placement_evidence.remote_body(body, REMOTE_NOTE)  # the shared pin-sentence rewrite, the lane's note
    return _LOCAL_EVENT_RE.sub(lambda m: f"`hub-admin '{m.group(1)}'`", body)


def pool_args(args: dict) -> dict:
    """``args`` of a home-pinned card, sent to the pool on a remote-reachable gateway (:func:`remote_lane`):
    ``runs_on`` the pool attestations and a body whose gateway steps go through hub-read / hub-admin. Anything else
    (another gateway, a named board, an unresolvable identity) is returned unchanged and keeps the home pin."""
    if not args.get("home_pin") or not remote_lane(args.get("board")):
        return args
    return {**args, "home_pin": False, "runs_on": list(POOL_LABELS), "body": remote_body(args.get("body") or "")}


def _file_verify(fp: str, st: dict, fix_card: str, r: dict, store, runner, now: float) -> Optional[str]:
    """File the VERIFY child of ``fix_card``: on the pool through hub-admin/hub-read when this gateway is remote-
    reachable (:func:`remote_verify`), else machine-pinned here. -> its id, or None (logged)."""
    lane = LaneState(store.root)
    remote = remote_verify()
    args = {"title": f"{VERIFY_TITLE} {incident_title(st.get('symptom') or fp)}",
            "body": verify_brief(fp, st, fix_card, remote=remote),
            "idempotency_key": verify_key(fp, generation(st) or 1), "model": TRIAGE_MODEL, "provider": TRIAGE_PROVIDER,
            "max_runtime": VERIFY_RUNTIME, "skills": list(VERIFY_SKILLS), "board": r["board"],
            "assignee": "default", "created_by": CREATED_BY, "requires": list(SELFHEAL_REQUIRES),
            "home_pin": not remote, "runs_on": list(VERIFY_POOL_LABELS) if remote else [],
            "parents": [fix_card]}
    located = _owner_location(st, fix_card)
    if located and located[0]:
        # The owner lives on another gateway's board: this board cannot gate on it, and its verifier must read it
        # there (cross_task), not conclude "no such task" from this board.
        args["parents"] = []
        args["body"] += (f"\n\nOwner location: `{fix_card}` is on gateway `{located[0]}`, board `{located[1]}`, "
                         "not on this board. Read it with `cross_task` "
                         f"(or `hermes kanban wait-check task {fix_card} --gateway {located[0]}`); "
                         "\"no such task\" from this board says nothing about it.")
    elif located and located[1] != (r.get("board") or "default"):
        # Same host, another board: a parent edge cannot cross boards, and the default-board `show` misses it.
        args["parents"] = []
        args["body"] += (f"\n\nOwner location: `{fix_card}` is on this host's board `{located[1]}` "
                         f"(`hermes kanban --board {located[1]} show {fix_card}`), not on this card's board.")
    rc, out, err = _run_card(args, runner)
    vid = _task_id(out) if rc == 0 else None
    if not vid or vid == fix_card:
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "stage": "verify", "card": fix_card,
                   "error": (err or out).strip()[-300:] or f"kanban create exited {rc} with no task id"})
        return None
    e = lane.get(fp)
    lane.update(fp, verify_card=vid, cards=sorted(known_cards(e) | {vid}))
    store.log({"event": "selfheal_verify_filed", "fingerprint": fp, "card": vid, "fix_card": fix_card})
    return vid


_CRON_SYMPTOM = re.compile(r"^cron job '(?P<name>.+?)' failed on (?P<host>[^:\s]+):")


def cron_job_state(fp: str, st: dict, home: Optional[Path] = None) -> Optional[dict]:
    """Live failure_streak of the cron job behind a `cron-*` fingerprint, or None when it can't be judged here
    (job on another host, jobs.json unreadable, no job matches the fingerprint)."""
    m = _CRON_SYMPTOM.match(str(st.get("symptom") or ""))
    if not fp.startswith("cron-") or not m or m.group("host") != host_name():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_coach_signals_selfheal", Path(__file__).with_name("signals.py"))
        sig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sig)
    except Exception:
        return None
    home = hermes_home(home)
    for prof, h in sig.homes(home):
        try:
            j = json.loads((h / "cron" / "jobs.json").read_text())
        except Exception:
            continue
        for job in (j.get("jobs") if isinstance(j, dict) else j) or []:
            name = job.get("name") or job.get("id")
            if name == m.group("name") and sig.fp("cron", m.group("host"), prof, name) == fp:
                return {"job": job.get("id"), "name": name, "profile": prof,
                        "failure_streak": int(job.get("failure_streak") or 0), "threshold": sig._cron_threshold(h),
                        "last_status": job.get("last_status")}
    return None


class FixPending(Exception):
    """`event fixed` refused: the cron job behind the fingerprint is still failing."""

    def __init__(self, state: dict, entry: dict):
        super().__init__(f"{state['name']} failure_streak={state['failure_streak']} >= {state['threshold']}")
        self.state, self.entry = state, entry


class UnknownFingerprint(ValueError):
    """`event` refused: this lane has no entry for the fingerprint (wrong HERMES_HOME, typo)."""


EVENT_STATUS = {"proposed": "proposed", "fixed": "fixed", "not_defect": "not_defect", "reverted": "reverted",
                "declined": "declined", "duplicate": "approved"}


def resolve_event_card(card: str, st: dict) -> str:
    """The card an `event` reports for, when `--card` is omitted.

    Briefs print the command without `--card`: Hermes scrubs ``HERMES_KANBAN_TASK`` from a worker's tool children
    (terminal, execute_code: ``scrub_kanban_env``) and exports ``HERMES_KANBAN_WORKER_TASK`` there instead, so a
    `--card $HERMES_KANBAN_TASK` line expanded to a bare `--card` and argparse exited 2. Order: explicit `--card`,
    the worker process's own id, the tool-child marker, then the fingerprint's recorded card (the fix card once
    approved, else the investigation card)."""
    for c in (card, os.environ.get("HERMES_KANBAN_TASK"), os.environ.get("HERMES_KANBAN_WORKER_TASK")):
        c = (c or "").strip()
        if c:
            return c
    if st.get("status") == "approved" and st.get("fix_card"):
        return st["fix_card"]
    return st.get("card") or st.get("fix_card") or ""


def _log_late_adopt(store, fp: str, st: dict, note: str, now: float) -> Optional[dict]:
    """Detection for adoption misses: a filed card closed as fixed/not_defect citing a commit that landed BEFORE the
    card was filed means the lane's "already fixed?" check (session_commit_fix) missed an existing fix and the lane
    paid for a card to rediscover it (desktop-9077b82c2d: f986de8c, then a filed investigation 47 h later). Logs
    `selfheal_late_adopt`; `status` counts them (late_adopt_counts)."""
    filed_at = st.get("filed_at")
    if not filed_at or not st.get("card"):
        return None
    try:
        for sha in fix_shas(note):
            for r in _fix_repos():
                ct = _git_lines(r, "show", "-s", "--format=%H\t%ct", sha)
                full, _, ts = (ct[0] if ct else "").partition("\t")
                if ts.isdigit() and int(ts) < float(filed_at):
                    rec = {"event": "selfheal_late_adopt", "fingerprint": fp, "card": st.get("card"),
                           "commit": full, "commit_at": int(ts), "filed_at": float(filed_at), "repo": str(r),
                           "late_s": round(float(filed_at) - int(ts))}
                    store.log(rec)
                    return rec
    except Exception as exc:  # detection only: never block recording the event
        store.log({"event": "selfheal_late_adopt_error", "fingerprint": fp, "error": str(exc)[:200]})
    return None


def late_adopt_counts(root: Optional[Path] = None, now: Optional[float] = None, days: int = 7) -> dict:
    """selfheal_late_adopt events over `days` (status): filed cards whose fix predated them."""
    root = Path(root) if root is not None else coach_root()
    since = (time.time() if now is None else now) - days * 86400
    fps = []
    try:
        with (root / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if '"selfheal_late_adopt"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("event") == "selfheal_late_adopt" and float(r.get("ts") or 0) >= since:
                    fps.append(r.get("fingerprint"))
    except OSError:
        pass
    return {"late_adopt": len(fps), "days": days, "fingerprints": sorted(set(f for f in fps if f))}


def record_event(kind: str, fp: str, card: str = "", note: str = "", store=None, now: Optional[float] = None,
                 owner: str = "", result: str = "", require_known: bool = False, runner=None) -> dict:
    """`self_heal.py event <kind> <fp>`: cards report their lifecycle; logged as selfheal_<kind>.
    `result` (a file path, `fixed` only) is copied to <coach>/selfheal-results/<fp>.md: the quiet home for
    finished work (the Inbox is for decisions only). `require_known` (the CLI) refuses a fingerprint this lane never
    filed: an event sent to the wrong lane (a scratch HERMES_HOME left exported in the card's terminal) used to
    create an orphan entry there, print success, and leave the real entry to go stale (plugin-129c2f2518)."""
    store = _store(store)
    now = now or time.time()
    if kind not in EVENT_STATUS:
        raise ValueError(f"unknown event {kind}; one of {sorted(EVENT_STATUS)}")
    lane = LaneState(store.root)
    if require_known and fp not in lane.read():
        raise UnknownFingerprint(f"unknown fingerprint {fp} in {lane.path}: not recorded. Is HERMES_HOME pointing at a "
                                 f"scratch dir? Rerun with HERMES_HOME=\"$HOME/.hermes\".")
    st = lane.get(fp)
    card = resolve_event_card(card, st)
    if kind == "duplicate" and (not _CARD_ID.fullmatch(owner) or owner == card):
        raise ValueError("duplicate requires a distinct --owner t_<hex> card")
    if kind == "duplicate" and _COUNTERMEASURE_WORDS.search(note) and not set(_CARD_ID.findall(note)) - {owner, card}:
        # A fix recommended only in a duplicate note has no owner and is dropped: the quota-wall registry fix
        # was proposed as "the owner should ..." on t_01a1946b62dfd5b3 and filed twice more (agent-ffb3d1ba3e).
        raise ValueError("duplicate note recommends a countermeasure (could/should/follow-up) but names no card for "
                         "it: kanban_create the fix card and put its t_<hex> id in --note, or drop the recommendation")
    kw: dict = {"status": EVENT_STATUS[kind]}
    cron_check = None
    if result and kind != "fixed":
        raise ValueError("--result applies to event fixed only")
    result_path = ""
    if result:
        src = Path(result).expanduser()
        if not src.is_file():
            raise ValueError(f"--result file not found: {src}")
        dest = Path(store.root) / RESULTS_DIR / f"{fp}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text())
        result_path = str(dest)
    if kind == "fixed" and fp.startswith("cron-"):
        # A cron fix is only fixed once the job runs green: refuse while it is still at core's nudge streak
        # (cron-5aa82ca809 was marked fixed at streak 7 and climbing, and came back hours later).
        cj = cron_job_state(fp, st)
        cron_check = "unchecked" if cj is None else "ok"
        if cj is not None and cj["failure_streak"] >= cj["threshold"]:
            e = lane.update(fp, fix_pending_at=now, fix_pending_card=card or st.get("fix_card"))
            store.log({"event": "selfheal_fix_pending", "fingerprint": fp, "card": card or st.get("fix_card"),
                       "job": cj["job"], "failure_streak": cj["failure_streak"], "threshold": cj["threshold"],
                       "note": note[:300]})
            raise FixPending(cj, e)
    if kind == "duplicate":
        kw.update(fix_card=owner, approved_at=now, closed_at=None,
                  cards=sorted(known_cards(st) | {c for c in (card, owner) if c}))
        store.log({"event": "selfheal_adopted_owner", "via": "duplicate", "fingerprint": fp,
                   "card": owner, "duplicate_card": card, "note": note[:300]})
    if kind == "proposed":
        kw["proposed_at"] = now
    if kind == "fixed":
        kw.update(fixed_at=now, fix_card=card or st.get("fix_card"), held=[], fix_shas=fix_shas(note))
    if kind in ("not_defect", "declined"):
        kw["closed_at"] = now
    e = lane.update(fp, **kw)
    late = None
    if kind in ("fixed", "not_defect"):
        late = _log_late_adopt(store, fp, st, note, now)
    store.log({"event": f"selfheal_{kind}", "fingerprint": fp, "card": card or e.get("card"),
               "component": e.get("component"), "note": note[:300], "recurrence": bool(e.get("recurrences")),
               **({"cron_check": cron_check} if cron_check else {}),
               **({"result_file": result_path} if result_path else {})})
    if kind == "not_defect":
        admission_negative(fp, e, note, store=store, now=now, runner=runner, late_adopt=late)
    return e


ADMISSION_NEG_FILE = "eval/admission-negatives.jsonl"  # under the private coach data repo (COACH_DATA)
ADMISSION_NEG_CARD_AT = 3     # this many audit false alarms inside ADMISSION_NEG_DAYS open one detector card
ADMISSION_NEG_DAYS = 7
ADMISSION_NEG_STATE = "selfheal_admission_negatives.json"  # <coach>/: the open detector card


def _coach_data() -> Path:
    return Path(os.environ.get("COACH_DATA") or Path.home() / "code" / "hermes-coach-data")


def _detect_record(store, fp: str) -> dict:
    """The newest selfheal_detect / selfheal_filed row for `fp` (source, session, evidence), {} when none."""
    best: dict = {}
    try:
        with (Path(store.root) / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if fp not in ln or ('"selfheal_detect"' not in ln and '"selfheal_filed"' not in ln):
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("fingerprint") == fp and r.get("source"):
                    best = {**best, **{k: v for k, v in r.items() if v not in (None, "")}}
    except OSError:
        pass
    return best


def _synthetic_fix_row(store, fp: str) -> bool:
    """The defect's detection quote (newest selfheal_detect/filed row) is Hermes-written by today's classifier."""
    rec = _detect_record(store, fp)
    return synthetic_user_row({"source": rec.get("source"), "quote": rec.get("evidence") or rec.get("quote") or ""})


# Lane statuses a synthetic withdrawal closes: an investigation, a proposal, or an (adopted) owner still awaiting its
# receipt. fixed/not_defect/declined/stale/reverted/suppressed are already settled and keep their verdict.
SYNTHETIC_CLOSE_STATUSES = ("filed", "proposed", "approved", "adopted")


def _close_synthetic_lane(store, fp: str, now: float) -> Optional[str]:
    """Close the open lane entry of a defect just withdrawn as synthetic (status not_defect, closed_at), so reconcile
    never files a VERIFY for it (kanban-0c3cff01cc: an owner adopted before the classifier knew the quote's tag kept
    the lane approved after its pending row was withdrawn). -> the status it closed, or None.
    The lane closes only when its own detection record is synthetic too: a fingerprint is component+symptom, so a
    synthetic row may share it with a real complaint's open lane, which must stay open."""
    lane = LaneState(store.root)
    was = lane.get(fp).get("status")
    if was not in SYNTHETIC_CLOSE_STATUSES or not _synthetic_fix_row(store, fp):
        return None
    lane.update(fp, status="not_defect", closed_at=now)
    return was


def admission_negative(fp: str, st: dict, note: str = "", store=None, now: Optional[float] = None,
                       runner=None, late_adopt: Optional[dict] = None) -> Optional[dict]:
    """Detection for turn-end audit false alarms (agent-feed37636b): a `not_defect` verdict on an audit (`admission`)
    finding is appended to <coach data>/eval/admission-negatives.jsonl as a label-0 replay row, and the
    ADMISSION_NEG_CARD_AT-th inside ADMISSION_NEG_DAYS opens ONE detector card (later cases comment on it while it is
    open; a done/archived/unreadable card means the next repeat files a fresh one). Three honest, allowed scope notes
    closed not_defect in one day before anything counted them. Never raises.
    `late_adopt` (record_event's selfheal_late_adopt row): the not_defect cites a fix that landed before the card was
    filed, so the audit found a REAL defect and the lane's "already fixed?" check missed its fix. That is an adoption
    miss (late_adopt_counts), not an audit false alarm: no row, no count (plugin-aafb20159a was counted and opened a
    detector card whose audit had been right)."""
    store = _store(store)
    now = now or time.time()
    try:
        rec = _detect_record(store, fp)
        source = st.get("source") or rec.get("source")
        if source != "admission":
            return None
        if late_adopt:
            out = {"event": "selfheal_admission_negative", "fingerprint": fp, "action": "skip",
                   "why": "already_fixed", "commit": late_adopt.get("commit")}
            store.log(out)
            return out
        row = {"id": fp, "label": 0, "source": "admission", "component": st.get("component") or rec.get("component"),
               "symptom": st.get("symptom") or rec.get("symptom"),
               "session_id": st.get("session_id") or rec.get("session_id"), "p": rec.get("p"),
               "evidence": str(rec.get("evidence") or "")[-1500:], "note": note[:500], "card": st.get("card"),
               "at": now, "host": host_name()}
        try:  # the audited turn, saved at filing: makes the row replayable (selfheal_replay.py --mode admission)
            inp = json.loads((Path(store.root) / "selfheal" / fp / AUDIT_INPUT_FILE).read_text())
            row.update({k: inp[k] for k in ("text", "ask", "tool_log", "stages", "stage1_p") if inp.get(k)})
        except (OSError, ValueError, TypeError):
            pass
        path = _coach_data() / ADMISSION_NEG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        if path.exists():
            for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    pass
        if not any(r.get("id") == fp and r.get("card") == row["card"] for r in rows):
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            rows.append(row)
        since = now - ADMISSION_NEG_DAYS * 86400
        recent = [r for r in rows if float(r.get("at") or 0) >= since]
        out: dict = {"event": "selfheal_admission_negative", "fingerprint": fp, "recent": len(recent),
                     "file": str(path)}
        if len(recent) >= ADMISSION_NEG_CARD_AT:
            spath = Path(store.root) / ADMISSION_NEG_STATE
            card = _open_card(spath, runner)
            if card:
                argv = ["hermes", "kanban", "comment", card,
                        f"Another audit false alarm closed not_defect: {fp} ({row['symptom']}), session "
                        f"{row['session_id']}. Note: {note[:400]}\nEvidence: {row['evidence'][-600:]}"]
                rc, o, err = _run(argv, runner)
                out.update(action="comment" if rc == 0 else "error", card=card)
            else:
                lines = "\n".join(f"- `{r.get('id')}` ({r.get('symptom')}), session {r.get('session_id')}: "
                                  f"{str(r.get('note') or '')[:200]}" for r in recent[-10:])
                seen = fence_evidence(lines + "\n\n" + f"Rows: {path} (label 0).")
                body = (f"The self-heal turn-end audit (AUDIT_SYS + admission_prompt in "
                        f"plugins/coach/self_heal.py) filed {len(recent)} findings in {ADMISSION_NEG_DAYS} days that "
                        f"investigation closed as not_defect.\n{home_pin_note(PIN_LANE)}\n\n"
                        f"{seen}\n\nFind what these "
                        f"turns share that the audit misreads (five-whys), change the prompt or rule, and gate it with "
                        f"`plugins/coach/eval/selfheal_replay.py --mode admission` over these rows plus known real "
                        f"catches (fixed admission fingerprints): false alarms under {AUDIT_THRESHOLD} on 3 of 3 "
                        f"samples, real catches still at or above it. Ship it in hermes-mods (hermes-mods-repo). "
                        f"Post nothing to the Inbox.")
                res = _lane_card(f"self-heal audit: {len(recent)} admission findings closed not_defect in "
                                 f"{ADMISSION_NEG_DAYS} days", body,
                                 f"selfheal-admission-negatives:{_utc_day(now)}:{fp}", runner)
                if res.get("card"):
                    spath.parent.mkdir(parents=True, exist_ok=True)
                    spath.write_text(json.dumps({"card": res["card"], "at": now}))
                out.update(action=res.get("action"), card=res.get("card"), why=res.get("why"))
        store.log(out)
        return out
    except Exception as exc:  # detection only: never block recording the event
        store.log({"event": "selfheal_admission_negative_error", "fingerprint": fp, "error": str(exc)[:200]})
        return None


def inbox_outcome(fp: str, runner=None) -> Optional[dict]:
    rc, out, _ = _run(["hermes", "inbox", "outcome", f"selfheal:{fp}"], runner)
    if rc != 0:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return None


# ── card lookup authority ──
# `hermes kanban show` answers for ONE board of THIS host. "no such task" there closed lanes whose owner lived on
# another board (agent-04039a9129, runners) or another gateway (kanban-f3b72e7010 and bb-448ec69637: MacBook cards
# the hub board never held), and twice for local cards that existed (agent-69f58b8c13, agent-4027d5f47f; cause never
# established). "missing" is a closure, so it now needs every source this host can read to agree: the named board's
# CLI, a read-only scan of every local board DB, and the hermes-hub replica of the other gateways' boards. A source
# that cannot be read, a board mid-recovery, a stale replica scope or a gateway that has not synced since the lane
# learned of the card is "unknown" (None): the lane stays open and nothing is inferred from it.
LOOKUP_SCOPE_FLOOR_S = 600.0    # core kanban_wait_replica.MAX_AGE_SECONDS
LOOKUP_SCOPE_MAX_S = 1800.0     # core kanban_scope_heartbeat.SCOPE_HEARTBEAT_MAX_S
LOOKUP_SCOPE_FACTOR = 2.5       # core kanban_scope_heartbeat.SCOPE_HEARTBEAT_PERIOD_FACTOR
LOOKUP_RETIRED_S = 7 * 86400    # a gateway silent this long is retired: it no longer vouches or vetoes


def _hub_replica_path() -> Path:
    """This host's hermes-hub replica of every gateway's boards (core kanban_capability_registry.hub_db_path)."""
    return Path(os.environ.get("HERMES_HUB_STATE") or Path.home() / ".local/state/hermes-hub") / "hub.db"


def _lookup_sources(runner=None) -> tuple:
    """(home, hub_db) that card_lookup reads beside the CLI. A caller that injects its runner (tests, dry runs)
    owns the board it shows, so the live replica is left out; so it is under COACH_SELFHEAL_NO_EXEC."""
    live = runner is None and os.environ.get("COACH_SELFHEAL_NO_EXEC") != "1"
    return _kanban_home(), (_hub_replica_path() if live else None)


def _board_in_recovery(path: Path) -> bool:
    """A live `hermes kanban recover` holds this DB (its `<db>.recover.pid` record): its rows prove nothing yet."""
    try:
        pid = int(json.loads(path.with_name(path.name + ".recover.pid").read_text())["pid"])
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return False
    return True


def _local_board_lookup(card: str, home: Path) -> tuple:
    """Read-only scan of every board DB under `home`. -> ({status, board} | None, why-unreadable | None)."""
    import sqlite3
    why = None
    for board, path in _board_dbs(Path(home)):
        if not path.exists():
            continue
        if _board_in_recovery(path):
            why = f"board {board or 'default'} is in recovery"
            continue
        try:
            con = _ro_state(path)
            try:
                r = con.execute("SELECT status FROM tasks WHERE id = ?", (card,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error as exc:
            why = f"board {board or 'default'} unreadable: {str(exc)[:80]}"
            continue
        if r:
            return {"status": r[0], "board": board}, None
    return None, why


def _scope_window(info: dict) -> float:
    period = info.get("scope_heartbeat_period_s") if isinstance(info, dict) else None
    if isinstance(period, bool) or not isinstance(period, (int, float)) or period <= 0:
        return LOOKUP_SCOPE_FLOOR_S
    return max(LOOKUP_SCOPE_FLOOR_S, min(LOOKUP_SCOPE_MAX_S, LOOKUP_SCOPE_FACTOR * float(period)))


def _replica_lookup(card: str, hub_db: Optional[Path], now: float, gateway: Optional[str] = None,
                    board: Optional[str] = None, since: Optional[float] = None) -> tuple:
    """The card in the hermes-hub replica (read-only). -> (found | None, why-unknown | None, configured).
    found = {status, gateway, board} only from an owner-qualified record whose gateway read that board cleanly
    inside its heartbeat window; a stale or unknown scope gives (None, why) with `last_seen` carried in why's dict
    form by card_lookup. Absence counts only when every gateway still in service synced after `since`."""
    import sqlite3
    if hub_db is None or not Path(hub_db).exists():
        return None, None, False
    try:
        con = _ro_state(Path(hub_db))
        try:
            rows = con.execute("SELECT gw, key, data FROM replica WHERE coll = 'tasks' AND key LIKE ?",
                               ("%/" + card,)).fetchall()
            infos = con.execute("SELECT gw, data FROM replica WHERE coll = 'gateway'").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return None, {"why": f"replica unreadable: {str(exc)[:80]}"}, True
    scopes: dict = {}   # (gw, profile, board) -> (state, observed_at, window)
    last_sync: dict = {}  # gw -> newest clean observation
    last_seen: dict = {}  # gw -> newest observation of any state (is the gateway still in service?)
    for gw, raw in infos:
        try:
            info = json.loads(raw)
        except ValueError:
            continue
        window = _scope_window(info)
        for sc in (info.get("task_scopes") or []) if isinstance(info, dict) else []:
            at = sc.get("observed_at")
            at = float(at) if type(at) in (int, float) else None
            scopes[(gw, sc.get("profile"), sc.get("board"))] = (sc.get("state"), at, window)
            if at is not None:
                last_seen[gw] = max(last_seen.get(gw, 0.0), at)
            if sc.get("state") == "ok" and at is not None:
                last_sync[gw] = max(last_sync.get(gw, 0.0), at)
    owned = []
    for gw, key, raw in rows:
        try:
            t = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(t, dict) or t.get("id") != card or t.get("gateway") != gw or not t.get("profile"):
            continue  # legacy two-part keys carry no owner and can lag their owner-qualified twin
        if gateway and gw != gateway:
            continue
        if gateway and board and t.get("board") != board:
            continue
        owned.append((gw, t))
    if owned:
        fresh = []
        for gw, t in owned:
            state, at, window = scopes.get((gw, t.get("profile"), t.get("board")), (None, None, LOOKUP_SCOPE_FLOOR_S))
            if state == "ok" and at is not None and 0 <= now - at <= window:
                fresh.append((gw, t))
        if fresh and len({t.get("status") for _, t in fresh}) == 1:
            gw, t = fresh[0]
            return {"status": t.get("status"), "gateway": gw, "board": t.get("board")}, None, True
        gw, t = (fresh or owned)[0]
        why = ("replica records disagree" if fresh else
               f"stale replica scope for {gw}/{t.get('board')}: its gateway has not vouched for it recently")
        return None, {"why": why, "gateway": gw, "board": t.get("board"), "last_seen": t.get("status")}, True
    if gateway and gateway not in last_sync:
        return None, {"why": f"no replica of gateway {gateway} to read", "gateway": gateway}, True
    # Absent from the replica. That is evidence only from scopes read cleanly inside their window: a board its
    # gateway could not read (state != ok) or has not vouched for lately may hold the card. A gateway silent past
    # LOOKUP_RETIRED_S is out of service and neither vouches nor vetoes, unless it is the one asked for.
    dirty = sorted({f"{gw}/{b}" for (gw, _p, b), (state, at, window) in scopes.items()
                    if (gw == gateway if gateway else now - last_seen.get(gw, 0.0) <= LOOKUP_RETIRED_S)
                    and (not (gateway and board) or b == board)
                    and (state != "ok" or at is None or not 0 <= now - at <= window)})
    if dirty:
        return None, {"why": f"stale replica scope: {', '.join(dirty[:4])} cannot vouch that the card is absent",
                      "gateway": gateway}, True
    if since is not None:
        behind = sorted(gw for gw, at in last_sync.items()
                        if at < since and now - at <= LOOKUP_RETIRED_S and (not gateway or gw == gateway))
        if behind:
            return None, {"why": f"gateway {', '.join(behind)} has not synced since the card was recorded"}, True
    return None, None, True


def card_lookup(card: str, board: Optional[str] = None, runner=None, home: Optional[Path] = None,
                hub_db: Optional[Path] = None, now: Optional[float] = None, since: Optional[float] = None,
                gateway: Optional[str] = None) -> dict:
    """Where a card is and what state it is in, with the authority behind the answer.
    -> {status, authority, board?, gateway?, why?, last_seen?}. status is the Kanban status, "missing" (every
    readable source agrees it is not there) or None (unknown: `why` says which source could not vouch).
    `board` = the lane's board ('<gateway>:<board>' when a digest named another host's card); `gateway` = a known
    remote owner; `since` = when the lane learned of the card (absence must be vouched for after it).
    Reads only: one `hermes kanban show`, mode=ro SQLite opens."""
    now = time.time() if now is None else now
    if board and ":" in str(board):  # digest_owner_cards' '<host>:<board>': not a slug this host's CLI accepts
        gw, _, rboard = str(board).partition(":")
        gateway, board = gateway or gw, rboard or None
        remote_board = board
    else:
        remote_board = None
    if not gateway:
        argv = ["hermes", "kanban"] + (["--board", board] if board else []) + ["show", card, "--json"]
        try:
            rc, out, err = _run(argv, runner)
        except Exception as exc:
            return {"status": None, "authority": None, "why": f"board read failed: {str(exc)[:80]}"}
        if rc == 0:
            try:
                return {"status": json.loads(out)["task"]["status"], "authority": "board", "board": board}
            except Exception:
                return {"status": None, "authority": None, "why": "board read returned no task status"}
        if "no such task" not in (err + out).lower():
            return {"status": None, "authority": None, "why": "board read failed"}
        found, local_why = _local_board_lookup(card, hermes_home(home) if home else _kanban_home())
        if found:
            return {**found, "authority": "local_board"}
    else:
        local_why = None
    found, unknown, configured = _replica_lookup(card, hub_db, now, gateway, remote_board, since)
    if found:
        return {**found, "authority": "replica"}
    if unknown:
        return {"status": None, "authority": None, **unknown}
    if gateway and not configured:
        return {"status": None, "authority": None, "gateway": gateway,
                "why": f"no replica on this host to read gateway {gateway}"}
    if local_why:
        return {"status": None, "authority": None, "why": local_why}
    return {"status": "missing", "authority": "all_sources"}


def card_status(card: str, board: Optional[str] = None, runner=None, home: Optional[Path] = None,
                hub_db: Optional[Path] = None) -> Optional[str]:
    """Kanban status of a card, "missing" when every readable source agrees there is no such card, None when
    unknown (a read failed or a source could not vouch): see card_lookup."""
    d_home, d_hub = _lookup_sources(runner)
    return card_lookup(card, board, runner, home=home or d_home, hub_db=hub_db or d_hub).get("status")


def _owner_location(st: dict, card: str) -> Optional[tuple]:
    """(gateway | None, board) where reconcile found `card` off the lane's board, or None. The record names the
    card it was read for (`owner_card`): a lane re-approved onto another fix card must not look that one up on the
    old owner's gateway (it would read a live local card as missing)."""
    if not card or st.get("owner_card") != card or not (st.get("owner_gateway") or st.get("owner_board")):
        return None
    return st.get("owner_gateway"), st.get("owner_board") or "default"


def _lane_owner_lookup(lane, store, fp: str, st: dict, card: str, runner, now: float) -> Optional[str]:
    """reconcile's status read for a lane card. Records where an owner was found off the lane's board
    (`owner_gateway`/`owner_board`, logged once as selfheal_owner_located) and an unknown read
    (selfheal_lookup_unknown, once per card and reason): unknown never ends a lane."""
    home, hub_db = _lookup_sources(runner)
    owner = card == st.get("fix_card")
    located = _owner_location(st, card) if owner else None
    look = card_lookup(card, st.get("board"), runner, home=home, hub_db=hub_db, now=now,
                       since=st.get("approved_at") or st.get("filed_at"),
                       gateway=located[0] if located else None)
    status = look.get("status")
    if status is None:
        mark = {"card": card, "why": look.get("why")}
        if st.get("lookup_unknown") != mark:
            lane.update(fp, lookup_unknown=mark)
            store.log({"event": "selfheal_lookup_unknown", "fingerprint": fp, "card": card, "was": st.get("status"),
                       "why": look.get("why"), "gateway": look.get("gateway"), "board": look.get("board"),
                       "last_seen": look.get("last_seen")})
        return None
    kw: dict = {}
    if st.get("lookup_unknown"):
        kw["lookup_unknown"] = None
    where = (look.get("gateway"), look.get("board") or "default")
    if owner and look.get("authority") in ("replica", "local_board") and status != "missing" and \
            where != located and (look.get("gateway") or where[1] != (st.get("board") or "default")):
        kw.update(owner_card=card, owner_gateway=look.get("gateway"), owner_board=where[1])
        store.log({"event": "selfheal_owner_located", "fingerprint": fp, "card": card, "status": status,
                   "gateway": look.get("gateway"), "board": look.get("board"), "lane_board": st.get("board"),
                   "authority": look.get("authority")})
    if kw:
        lane.update(fp, **kw)
    return status


ENDED = ("done", "archived", "missing")


def _recover_completion_receipt(fp: str, store, runner, now: float) -> Optional[str]:
    """One budgeted VERIFY handoff for a completed owner without a lane receipt; never infer a fix from prose."""
    with _admission_lock(store):
        st = LaneState(store.root).get(fp)  # another sync/event may have settled it while we waited
        if st.get("status") != "approved" or st.get("verify_card"):
            return st.get("verify_card")
        if _synthetic_fix_row(store, fp):  # a Hermes-written detection never earns a verify run
            rec = _detect_record(store, fp)
            LaneState(store.root).update(fp, status="not_defect", closed_at=now)
            store.log({"event": "selfheal_withdrawn", "mode": "live", "fingerprint": fp, "why": "synthetic_tag",
                       "stage": "receipt", "lane_closed": "approved", "fix_card": st.get("fix_card"),
                       "source": rec.get("source"), "session_id": rec.get("session_id"),
                       "quote": str(rec.get("evidence") or rec.get("quote") or "")[:200]})
            return None
        r = route({"component": st.get("component")})
        r["board"] = st.get("board") or r["board"]
        b = budget(r["board"], now)
        if _budget_unavailable(b, fp, store):
            return None
        denied = _admission_denied(b, now, store, runner)
        if denied:
            _log_deferral_once(store, fp, denied, b, now, stage="receipt")
            return None
        return _file_verify(fp, st, st["fix_card"], r, store, runner, now)


def reconcile(store=None, runner=None, now: Optional[float] = None) -> list:
    """Recover a completed approved owner's missing receipt with a bounded VERIFY handoff.
    Unreported investigations, abandoned owners, and ended verifiers become stale so later detection can refile.
    Returns [(fp, what)]."""
    store = _store(store)
    now = now or time.time()
    lane = LaneState(store.root)
    done = []
    for fp, st in lane.read().items():
        what = _reconcile_fp(lane, store, fp, st, runner, now)
        if what:
            done.append((fp, what))
    return done


def _reconcile_fp(lane, store, fp: str, st: dict, runner, now: float, via: str = "") -> Optional[str]:
    """reconcile() for one fingerprint; -> what changed, or None. ``via`` tags the stale event when an occurrence
    (an ``every_occurrence`` signal in _file_defect) reconciled it ahead of the 5-minute sync."""
    status = st.get("status")
    card = st.get("card") if status == "filed" else st.get("fix_card") if status == "approved" else None
    if not card:
        return None
    ks = _lane_owner_lookup(lane, store, fp, st, card, runner, now)
    if ks == "done" and status == "approved" and not st.get("verify_card"):
        # Legacy/non-split and adopted owners can finish without ever knowing this fingerprint.
        # Preserve the generation while a budgeted verifier obtains the receipt (including create failure).
        verify = _recover_completion_receipt(fp, store, runner, now)
        if not verify and lane.get(fp).get("status") == "not_defect":
            return "withdrawn (synthetic_tag): no receipt verification"
        return f"receipt verification -> {verify or 'pending admission'}"
    if ks == "done" and status == "approved" and st.get("verify_card"):
        # A split fix ends at its VERIFY child (it records fixed): judge that card, not the finished fix card.
        card = st["verify_card"]
        ks = card_status(card, st.get("board"), runner)
    if ks not in ENDED:
        return None
    # What this decision was read from: the write below applies only while the lane still holds it.
    held = {"status": status, "card": st.get("card"), "fix_card": st.get("fix_card"),
            "verify_card": st.get("verify_card"), "gen": st.get("gen")}
    proposal = Path(store.root) / "selfheal" / fp / "proposal.md"
    try:
        p_mtime = proposal.stat().st_mtime if status == "filed" and ks == "done" else 0.0
    except OSError:
        p_mtime = 0.0
    if p_mtime and p_mtime >= (st.get("filed_at") or 0):
        # The investigation finished and wrote this generation's proposal, but its `event proposed` never reached
        # this lane (plugin-129c2f2518: an exported scratch HERMES_HOME sent it to a temp lane that reported
        # success). The proposal is the evidence: record it as proposed so sync() files the fix card.
        if not lane.transition(fp, held, status="proposed", proposed_at=p_mtime):
            return None
        store.log({"event": "selfheal_proposed", "fingerprint": fp, "card": card, "via": "reconcile",
                   "note": f"card {ks} without reporting; proposal.md found", "component": st.get("component")})
        return f"proposed (card {card} {ks}, proposal.md found)"
    if not lane.transition(fp, held, status="stale", closed_at=now):
        # The lane moved while this pass looked the card up (a receipt, a re-approval): that newer state stands.
        store.log({"event": "selfheal_stale_skipped", "fingerprint": fp, "card": card, "was": status, "kanban": ks,
                   "now": lane.get(fp).get("status")})
        return None
    store.log({"event": "selfheal_stale", "fingerprint": fp, "card": card, "was": status, "kanban": ks,
               "component": st.get("component"), **({"via": via} if via else {})})
    return f"stale ({status}, card {card} {ks})"


# The retired step in cards filed before 921830b (proposals) / 4f1c2a6 (results): both templates wrote
# `env HERMES_HOME="$HOME/.hermes" hermes inbox post --...`. Current briefs only mention "`hermes inbox post`" in a
# "do NOT run" sentence, and proposals may quote a bare `hermes inbox post --poster ...` live check: neither matches.
OLD_INBOX_STEP = re.compile(r'env HERMES_HOME="\$HOME/\.hermes" hermes inbox post\s+--')
INBOX_STEP_NOTE = ("self-heal: IGNORE the `hermes inbox post` step in this card's body. Self-heal now auto-approves "
                   "every proposal and stores results quietly: record a proposal with `self_heal.py event proposed`, "
                   "a finished fix with `self_heal.py event fixed --result result.md` (saved to "
                   "selfheal-results/<fp>.md). The Inbox refuses self-heal posts (AgentFixablePost).")


def sweep_inbox_steps(store=None, runner=None) -> list:
    """Cards keep the instructions they were filed with: comment once on each open self-heal card whose body still
    carries the retired `hermes inbox post --...` step. Returns [(fp, card)]. Lane field `inbox_step_noted` = card."""
    store = _store(store)
    lane = LaneState(store.root)
    done = []
    for fp, st in lane.read().items():
        status = st.get("status")
        card = st.get("card") if status == "filed" else st.get("fix_card") if status == "approved" else None
        if not card or st.get("inbox_step_noted") == card:
            continue
        argv = ["hermes", "kanban"] + (["--board", st["board"]] if st.get("board") else [])
        try:
            rc, out, _ = _run(argv + ["show", card, "--json"], runner)
            task = json.loads(out)["task"] if rc == 0 else None
        except Exception:
            task = None
        if not task or task.get("status") in ENDED or not OLD_INBOX_STEP.search(task.get("body") or ""):
            continue
        try:
            rc, out, err = _run(argv + ["comment", card, INBOX_STEP_NOTE], runner)
        except Exception as exc:
            rc, out, err = 1, "", str(exc)
        if rc == 0:
            lane.update(fp, inbox_step_noted=card)
            done.append((fp, card))
        store.log({"event": "selfheal_inbox_step_noted", "fingerprint": fp, "card": card, "ok": rc == 0,
                   "error": (err or out)[-200:] if rc else None})
    return done


def auto_approve(cfg: Optional[dict] = None) -> bool:
    """Every proposal is approved without a human (David's standing approval): `auto_approve` in
    selfheal-config.json, env COACH_SELFHEAL_AUTO_APPROVE=0/1 overrides; default on."""
    env = os.environ.get("COACH_SELFHEAL_AUTO_APPROVE", "").strip().lower()
    if env:
        return env not in ("0", "false", "off", "no")
    cfg = lane_config() if cfg is None else cfg
    return bool(cfg.get("auto_approve", True))


def sync(store=None, runner=None, now: Optional[float] = None, fixer: Optional[Callable] = None,
         auto: Optional[bool] = None) -> list:
    """Act once on each proposal. Auto-approve (default): every `proposed` fingerprint gets its fix card unless an
    Inbox decision of this generation said Not now / Never; an open legacy proposal card is resolved as Do it so it
    leaves the Inbox. Manual (auto_approve false): the Inbox outcome decides: do_it, not_now (snooze), never
    (suppress). Returns [(fp, what)]."""
    store = _store(store)
    now = now or time.time()
    auto = auto_approve() if auto is None else auto
    lane = LaneState(store.root)
    done = reconcile(store, runner, now)
    try:
        done += [(fp, f"inbox step noted on {card}") for fp, card in sweep_inbox_steps(store, runner)]
    except Exception as exc:
        store.log({"event": "selfheal_inbox_step_error", "error": str(exc)[:200]})
    for fp, st in lane.read().items():
        if st.get("status") != "proposed":
            continue
        o = inbox_outcome(fp, runner)
        stale = bool(o) and (o.get("at") or now) < (st.get("filed_at") or 0)  # an earlier generation's decision
        undecided = not o or o.get("state") == "open" or stale
        if auto and (undecided or o.get("outcome") == "do_it"):
            if o and o.get("state") == "open" and not stale and o.get("session_id"):
                try:  # take the legacy proposal card out of the Inbox
                    _run(["hermes", "inbox", "act", str(o["session_id"]), "do_it"], runner)
                except Exception as exc:
                    store.log({"event": "selfheal_inbox_resolve_error", "fingerprint": fp, "error": str(exc)[:200]})
            how = "do_it" if (o and not stale and o.get("outcome") == "do_it") else "auto"
            r = (fixer or file_fix)(fp, store=store, runner=runner, now=now, trigger=how)
            done.append((fp, f"{how} -> {r.get('card') or r.get('why')}"))
            continue
        if undecided:
            continue
        what = o.get("outcome")
        if what == "do_it":
            r = (fixer or file_fix)(fp, store=store, runner=runner, now=now, trigger="sync")
            done.append((fp, f"do_it -> {r.get('card') or r.get('why')}"))
        elif what in ("not_now", "never"):
            kw = {"status": "suppressed" if what == "never" else "declined", "closed_at": now}
            if what == "never":
                kw["suppressed"] = True
            else:
                kw["snooze_until"] = now + SNOOZE_DAYS * 86400
            lane.update(fp, **kw)
            store.log({"event": "selfheal_declined", "fingerprint": fp, "card": st.get("card"), "how": what,
                       "component": st.get("component")})
            done.append((fp, what))
        elif o.get("state") == "archived":
            lane.update(fp, status="declined", closed_at=now)
            store.log({"event": "selfheal_declined", "fingerprint": fp, "card": st.get("card"), "how": "archived",
                       "component": st.get("component")})
            done.append((fp, "archived"))
    return done


# ── digest backstop ──
INTERACTIVE = ("desktop", "tui", "cli", "slack", "telegram", "discord", "whatsapp", "signal")


def backstop_context(dg: dict, turns: list, t: dict, n: int = 3, chars: int = 300, reply_chars: int = 600) -> str:
    """Session title, the `n` user turns before `t` (each clipped) and the agent's reply to `t`, for the backstop judge."""
    i = next((k for k, x in enumerate(turns) if x is t), len(turns))
    earlier = [str(x.get("text") or "")[:chars].replace("\n", " ") for x in turns[max(0, i - n):i]]
    lines = [f"Session title: {dg.get('title') or '(untitled)'}"]
    lines += ([f"Earlier user messages, oldest first:"] + [f"- {e}" for e in earlier] if earlier
              else ["This is the first user message of the session."])
    # The agent's answer to this message (the next turn's `prev`, or the digest's last reply for the newest turn): the
    # judge cannot see a screenshot, and "why are all the dots red? [screenshot]" only reads as a plugin bug once the
    # reply says what the dots are. A digest written mid-turn has last_reply == this turn's prev: left out.
    reply = (turns[i + 1].get("prev") if i + 1 < len(turns) else dg.get("last_reply")) or ""
    if reply.strip() and reply != (t.get("prev") or ""):
        lines.append("The agent's reply to this message (written after it; use it only to learn what the message and "
                     "any screenshot were about, not as proof of a defect): " + reply[:reply_chars].replace("\n", " "))
    # The digest keeps text only, so a reply ending "Reading the card first:" reads the same whether tools ran after it
    # or the agent stalled (agent-b6540f8eef). Digest v8 counts them; older digests say nothing, so say unknown.
    if (t.get("prev") or "").strip():
        n_tools = t.get("prev_tools")
        lines.append("Tool calls after the agent's last text: "
                     + (str(n_tools) if isinstance(n_tools, int) else "unknown"))
    return "\n".join(lines)


# A judge symptom claiming the agent did not act. With digest `prev_tools` > 0 that claim is false by measurement.
NO_TOOL_CLAIM_RE = re.compile(r"\b(?:no|zero|without|never made any|made no|didn'?t make any)\s+tool[- ]?calls?\b"
                              r"|\bno tools? (?:ran|run|used)\b", re.I)


_UNSET = object()

# ── interrupted model waits (digest `prev_interrupt`, core display_metadata.interrupt) ──
# A human Stop answered by a bare nudge is a slow first token, not an agent fault; a backend exit the user had to
# nudge is a lost turn (auto-continue did not resume it). Other producers (ws_orphan, lease_takeover, ...) still go
# to the judge. Rows without a reason (older cores) are judged exactly as before.
HUMAN_STOPS = ("user_stop", "new_message", "user_interrupt")
SLOW_MODEL_S = 60.0
BARE_NUDGE_RE = re.compile(
    r"^\s*(?:resume|continue|go on|keep going|carry on|status\??|still there\??|are you (?:still )?(?:there|stuck|"
    r"working)\??|what'?s (?:going on|happening|up)\??|\?+|ping|hello\??|and\??|so\??|well\??)\W*$", re.I)


def interrupt_triage(t: dict) -> Optional[dict]:
    """Verdict for a user turn that follows an interrupted model wait, or None to judge it normally:
    ``{"action": "slow_model"|"user_stop"}`` (skip: not a defect) or ``{"action": "lost_turn", "hit": {...}}``."""
    info = t.get("prev_interrupt")
    if not isinstance(info, dict):
        return None
    reason = str(info.get("reason") or "")
    if reason in HUMAN_STOPS:
        if not BARE_NUDGE_RE.match(clean_user(t.get("text") or "")[:200]):
            return None  # a real complaint after a Stop is still judged
        try:
            waited = float(info.get("waited_s") or 0)
        except (TypeError, ValueError):
            waited = 0.0
        return {"action": "slow_model" if waited >= SLOW_MODEL_S else "user_stop"}
    if reason == "backend_exit":
        v = {"p_defect": 0.95, "kind": "hermes_bug", "component": "gateway",
             "symptom": "turn lost after backend exit, not auto-continued"}
        return {"action": "lost_turn", "hit": {**v, "fingerprint": fingerprint(v["component"], v["symptom"]),
                                               "source": "signal", "stages": []}}
    return None


def backstop(data: Path, store=None, judge: Optional[Callable] = None, now: Optional[float] = None,
             lookback: float = 3 * 3600, filer: Optional[Callable] = None, m: Optional[str] = None,
             limit: int = 200, confirm=_UNSET) -> list:
    """Run the complaint detector over user turns newer than the per-session cursor in the coach digests
    (<data>/<host>/<profile>/<sid>.json). A session seen for the first time is scanned only `lookback` back. The judge
    gets the agent reply before each turn (digest v6 `prev`) and always the session context (title + the earlier user
    turns, `backstop_context`): with neither, the strict stage read \"why didnt this work\" or \"the camera stream is
    broken\" as a new issue and scored 0. Returns the filings."""
    store = _store(store)
    now = now or time.time()
    m = m or mode()
    cur_p = Path(store.root) / "selfheal_backstop.json"
    try:
        cursor = json.loads(cur_p.read_text())
    except Exception:
        cursor = {}
    judge = judge or aux_judge
    # The confirm model (auxiliary.coach_defect_confirm) joins the cascade here as on the hook path; None when unset.
    confirm = confirm_judge() if confirm is _UNSET else confirm
    out, n = [], 0
    for f in sorted(Path(data).glob("*/*/*.json")):
        if f.parts[-3] in ("reports", "findings", "research", "eval", "curriculum"):
            continue
        rel = "/".join(f.parts[-3:])
        try:
            if f.stat().st_mtime < now - 14 * 86400:
                continue
            dg = json.loads(f.read_text())
        except Exception:
            continue
        if dg.get("source") not in INTERACTIVE or dg.get("parent_session_id"):
            continue
        turns = [t for t in dg.get("user_turns") or [] if isinstance(t, dict) and t.get("t")]
        # Cursor: a bare timestamp (legacy) or [timestamp, k] meaning k turns AT that timestamp were already scanned.
        cur = cursor.get(rel, now - lookback)
        since, skip = (cur[0], int(cur[1])) if isinstance(cur, list) and len(cur) == 2 else (cur, None)
        turns.sort(key=lambda t: t["t"])
        at_since = [t for t in turns if t["t"] == since]
        skip = len(at_since) if skip is None else skip  # legacy bare timestamp: everything at it was scanned
        new = at_since[skip:] + [t for t in turns if t["t"] > since]
        if not new:
            continue
        if dg.get("hidden") is True:
            # A hidden chat is agent-driven (core creates /api/ws?client=agent chats hidden): its user turns are an
            # agent's steering, not David's complaints (agent-d925f95adc). Advance the cursor; one row per batch.
            store.log({"event": "selfheal_skip", "mode": m, "why": "agent_input", "session_id": dg.get("id") or f.stem,
                       "host": dg.get("host") or f.parts[-3], "turns": len(new)})
            last = new[-1]["t"]
            cursor[rel] = [last, sum(1 for t in new if t["t"] == last) + (skip if last == since else 0)]
            continue
        done = []  # turns actually scanned; the cursor only advances over these, so a spent limit loses nothing
        for t in new:
            if n >= limit:
                break
            done.append(t)
            text = t.get("text") or ""
            if not prefilter(text):
                continue
            tri = interrupt_triage(t)
            if tri and tri["action"] in ("slow_model", "user_stop"):
                info = t.get("prev_interrupt") or {}
                store.log({"event": "selfheal_slow_model" if tri["action"] == "slow_model" else "selfheal_user_stop",
                           "session_id": dg.get("id") or f.stem, "at": t["t"], "waited_s": info.get("waited_s"),
                           "prompt_tokens": info.get("prompt_tokens"), "reason": info.get("reason")})
                continue
            if tri:  # lost_turn: a machine signal, no judge
                hit = tri["hit"]
            else:
                n += 1
                hit = detect_complaint(t.get("prev") or "", text, llm=judge, threshold=threshold(),
                                       context=backstop_context(dg, turns, t), confirm=confirm)
                if (hit and isinstance(t.get("prev_tools"), int) and t["prev_tools"] > 0
                        and NO_TOOL_CLAIM_RE.search(f"{hit.get('symptom') or ''} {hit.get('component') or ''}")):
                    # The judge's "no tool calls" is contradicted by the digest's own count (agent-b6540f8eef).
                    store.log({"event": "selfheal_skip", "why": "tools_ran", "session_id": dg.get("id") or f.stem,
                               "at": t["t"], "prev_tools": t["prev_tools"], "symptom": hit.get("symptom"),
                               "p": hit.get("p_defect")})
                    continue
            if not hit:
                continue
            sid = dg.get("id") or f.stem
            new_fp = Fingerprints(store.root).record(hit, sid)
            d = {**hit, "session_id": sid, "quote": clean_user(text)[:500], "host": dg.get("host") or f.parts[-3],
                 "profile": dg.get("profile") or f.parts[-2], "at": t["t"], "source": "backstop",
                 "kind": hit.get("kind") or "complaint"}
            since_failure = _failed_turn_nudge_since(turns, t)
            if since_failure is not None:
                d["failed_turn_since"] = since_failure
            store.log({"event": "selfheal_detect", "mode": m, "session_id": sid, "source": "backstop",
                       "p": hit["p_defect"], "component": hit["component"], "symptom": hit["symptom"],
                       "fingerprint": hit["fingerprint"], "new": new_fp, "evidence": d["quote"][:300]})
            if new_fp:
                out.append((filer or file_defect)(d, store=store, m=m))
        if done:
            last = done[-1]["t"]
            scanned = sum(1 for t in done if t["t"] == last) + (skip if last == since else 0)
            cursor[rel] = [last, scanned]
    cur_p.parent.mkdir(parents=True, exist_ok=True)
    cur_p.write_text(json.dumps(cursor))
    return out


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="self_heal.py", description="self-heal lane (see the Lane comment)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("file", help="file one defect JSON from stdin (hub side of an ssh forward)")
    f.add_argument("--mode", choices=("shadow", "live"))
    f.add_argument("--filed-by", default="", help="card doing this (re-)filing; never adopted as the owner "
                                                   "(default: the payload's filed_by, else $HERMES_KANBAN_TASK)")
    x = sub.add_parser("fix", help="Do it: create the fix card for a fingerprint")
    x.add_argument("fp")
    e = sub.add_parser("event", help="record a lifecycle event from a card")
    e.add_argument("kind", choices=sorted(EVENT_STATUS))
    e.add_argument("fp")
    e.add_argument("--card", default="")
    e.add_argument("--note", default="")
    e.add_argument("--owner", default="", help="existing owner card for event duplicate")
    e.add_argument("--result", default="", help="event fixed: result.md to store quietly (no Inbox post)")
    sub.add_parser("sync", help="act on Inbox outcomes (Do it / Not now / Never)")
    b = sub.add_parser("backstop", help="detector over new user turns in the coach digests")
    b.add_argument("--data", default=os.environ.get("COACH_DATA") or str(Path.home() / "code/hermes-coach-data"))
    b.add_argument("--mode", choices=("shadow", "live"))
    c = sub.add_parser("cron", help="hub pass every 30 min: drain queue, backstop, sync")
    c.add_argument("--data", default=os.environ.get("COACH_DATA") or str(Path.home() / "code/hermes-coach-data"))
    sub.add_parser("status", help="lane state + budget")
    a = ap.parse_args(argv)
    if a.cmd == "file":
        d = json.loads(sys.stdin.read())
        filer = (d.get("filed_by") or a.filed_by or os.environ.get("HERMES_KANBAN_TASK")
                 or os.environ.get("HERMES_KANBAN_WORKER_TASK"))
        if filer:
            d["filed_by"] = filer
        print(json.dumps(file_defect(d, m=a.mode)))
    elif a.cmd == "fix":
        print(json.dumps(file_fix(a.fp)))
    elif a.cmd == "event":
        try:
            e = record_event(a.kind, a.fp, a.card, a.note, owner=a.owner, result=a.result, require_known=True)
        except UnknownFingerprint as exc:
            print(json.dumps({"fingerprint": a.fp, "error": str(exc)}))
            print(f"self_heal event: {exc}", file=sys.stderr)
            sys.exit(4)
        except FixPending as fx:
            s = fx.state
            print(json.dumps({"fingerprint": a.fp, "status": fx.entry.get("status"), "fix_pending": True,
                              "job": s["name"], "failure_streak": s["failure_streak"], "threshold": s["threshold"],
                              "why": "cron job still failing: not recorded fixed; keep the card open, verify a green "
                                     "run (failure_streak below threshold), then record fixed again"}))
            sys.exit(3)
        print(json.dumps({"fingerprint": a.fp, "status": e.get("status")}))
    elif a.cmd == "sync":
        for fp, what in sync():
            print(f"{fp}: {what}")
    elif a.cmd == "backstop":
        for r in backstop(Path(a.data), m=a.mode):
            print(json.dumps(r)[:400])
    elif a.cmd == "cron":
        for line in cron(Path(a.data)):
            print(line)
    elif a.cmd == "status":
        st = LaneState(coach_root()).read()
        print(json.dumps({"mode": mode(), "hub": is_hub(), "budget": budget(),
                          "fingerprints": {k: {**{kk: v.get(kk) for kk in ("status", "card", "fix_card", "filed_day")},
                                               "held": len(v.get("held") or [])}
                                           for k, v in st.items()},
                          "held_total": sum(len(v.get("held") or []) for v in st.values()),
                          "handoff_nudges": handoff_nudge_counts(),
                          "nudges": nudge_counts(),
                          "late_adopt": late_adopt_counts(),
                          "deploy_grace_hours": deploy_grace_s() / 3600}, indent=1))
    return 0


def drain(store=None, m: Optional[str] = None, filer: Optional[Callable] = None) -> list:
    """File queued live detections that were not filed in-process (e.g. the hub was unreachable)."""
    store = _store(store)
    q = Path(store.root) / "selfheal_queue.jsonl"
    done_p = Path(store.root) / "selfheal_queue.offset"
    if not q.exists():
        return []
    lines = q.read_text().splitlines()
    try:
        off = int(done_p.read_text().strip() or 0)
    except Exception:
        off = 0
    out = []
    for line in lines[off:]:
        try:
            r = json.loads(line)
        except Exception:
            continue
        d = {k: r.get(k) for k in ("fingerprint", "component", "symptom", "source", "session_id")}
        d.update(quote=r.get("evidence") or "", at=r.get("ts"), p_defect=r.get("p"), host=host_name())
        out.append((filer or file_defect)(d, store=store, m=m))
    done_p.write_text(str(len(lines)))
    return out


# ── coach-data refresh and lane self-checks (cron-7b7df72ab4: a strict pull killed the lane for 4 days) ──
def _git(data: Path, *args, runner=None):
    import subprocess
    r = (runner or subprocess.run)(["git", "-C", str(data), *args], capture_output=True, text=True, timeout=180)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _heal_unmerged(data: Path, runner=None) -> list:
    """Resolve a conflicted index a failed autostash pop left: append-only *.jsonl become the deduped union of
    base/ours/theirs (same rule as coach_feed.resolve_unmerged). Returns the paths it could NOT resolve."""
    _, out, _ = _git(data, "diff", "--name-only", "--diff-filter=U", runner=runner)
    bad, healed = [], []
    for path in sorted(set(out.split())):
        if not path.endswith(".jsonl"):
            bad.append(path)
            continue
        seen, lines = set(), []
        for stage in "123":
            rc, txt, _ = _git(data, "show", f":{stage}:{path}", runner=runner)
            for ln in (txt.splitlines() if rc == 0 else []):
                if ln.strip() and ln not in seen:
                    seen.add(ln)
                    lines.append(ln)
        (Path(data) / path).write_text("\n".join(lines) + "\n")
        _git(data, "add", "--", path, runner=runner)
        healed.append(path)
    if healed and not bad:
        _git(data, "reset", "-q", runner=runner)  # leave the merged rows as ordinary local edits, nothing staged
        _, top, _ = _git(data, "stash", "list", "-1", "--format=%gs", runner=runner)
        if top.strip().endswith("autostash"):
            _git(data, "stash", "drop", "-q", runner=runner)  # its content is merged in above
    return bad


def pull_data(data: Path, runner=None) -> Optional[str]:
    """Refresh the coach digests: `git pull --rebase --autostash` (local writers such as coach_eval leave tracked
    jsonl rows uncommitted in this clone; --ff-only refused every pull once upstream touched the same file).
    *.jsonl merge with the union driver. Never raises: None = pulled (or `data` is not a git checkout), else git's
    error text. The caller carries on with the digests it already has."""
    data = Path(data)
    if not (data / ".git").exists():
        return None
    try:
        rc, gp, _ = _git(data, "rev-parse", "--git-path", "info/attributes", runner=runner)
        if rc == 0 and gp.strip():
            f = data / gp.strip()
            cur = f.read_text() if f.exists() else ""
            if "*.jsonl merge=union" not in cur.splitlines():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(cur + ("" if not cur or cur.endswith("\n") else "\n") + "*.jsonl merge=union\n")
        bad = _heal_unmerged(data, runner)
        if bad:
            return f"unmerged paths from an earlier pull: {', '.join(bad)[:300]}"
        rc, out, err = _git(data, "pull", "-q", "--rebase", "--autostash", runner=runner)
        bad = _heal_unmerged(data, runner)
        if rc:
            for d in ("rebase-merge", "rebase-apply"):
                r2, p, _ = _git(data, "rev-parse", "--git-path", d, runner=runner)
                if r2 == 0 and (data / p.strip()).exists():
                    _git(data, "rebase", "--abort", runner=runner)
                    break
            return ((err or out).strip() or f"git pull exited {rc}")[-600:]
        if bad:
            return f"autostash left unmerged paths: {', '.join(bad)[:300]}"
        return None
    except Exception as exc:  # a refresh failure never stops the lane
        return f"{type(exc).__name__}: {exc}"[:600]


def placement_refused(*texts) -> bool:
    """True when a failed `hermes kanban create` was refused on placement (core hub admission), not by the card."""
    return any(PLACEMENT_REFUSED_RE.search(t or "") for t in texts)


def note_placement_refused(store, fp: str, error: str, now: float, runner=None) -> Optional[dict]:
    """Detection for a lane whose creates core refuses on placement: ONE pool card per incident (an open card is
    left alone; a done one gets a fresh card, its key carries the UTC day). Before this, every investigation was
    refused for 40 min and only a sibling card noticed (t_01a1cfa3b3805315). Never raises."""
    try:
        path = Path(store.root) / PLACEMENT_ALERT_STATE
        if _open_card(path, runner):
            return None
        host = host_name()
        seen = fence_evidence(f"Seen on {host} at {_iso(now)} filing {fp}:\n```\n{(error or '')[-1500:]}\n```")
        body = ("Core refused the placement of a self-heal card (hub admission), so the lane files no investigations "
                "and its pending rows wait. Each pass now stops at the first refusal.\n"
                f"{home_pin_note(PIN_LANE)}\n\n{seen}\n\n"
                "Find which self-heal producer still asks for a hub slot (plugins/coach/self_heal.py `card_args`, "
                "`pool_args`, `kanban_argv`) or what core's admission changed, fix it in hermes-mods (hermes-mods-repo) "
                "with a regression test that runs the producer's argv through core's admission, and confirm the next "
                "pass logs `selfheal_filed`. Post nothing to the Inbox.")
        res = _lane_card("self-heal lane: card creates refused by hub admission", body,
                         f"selfheal-placement:{host}:{_utc_day(now)}", runner)
        if res.get("card"):
            path.write_text(json.dumps({"card": res["card"], "at": now}))
        store.log({"event": "selfheal_placement_card", "fingerprint": fp, **res})
        return res
    except Exception as exc:  # health: allow BLE001 -- detection only: never break the filing path
        store.log({"event": "selfheal_placement_card", "action": "error", "why": str(exc)[:300]})
        return None


def _lane_card(title: str, body: str, key: str, runner=None) -> dict:
    """A plain Kanban card for a lane defect (not an investigation: those need this lane to become fix cards)."""
    args = pool_args({"title": title, "body": body, "idempotency_key": key, "model": MODEL, "provider": PROVIDER,
            "max_runtime": FIX_RUNTIME, "skills": list(FIX_SKILLS), "board": None, "assignee": "default",
            "created_by": CREATED_BY, "requires": list(SELFHEAL_REQUIRES), "home_pin": True})
    try:
        rc, out, err = _run_card(args, runner)
    except Exception as exc:
        return {"action": "error", "why": f"{type(exc).__name__}: {exc}"[:300]}
    tid = _task_id(out) if rc == 0 else None
    return {"action": "filed", "card": tid} if tid else {"action": "error", "why": (err or out).strip()[-300:]}


def _open_card(path: Path, runner=None) -> Optional[str]:
    """The card recorded in a lane alert state file while it is still open (one card per incident)."""
    try:
        card = json.loads(path.read_text()).get("card")
    except Exception:
        return None
    if not card:
        return None
    # done/archived/missing/unreadable = no open owner: file a fresh card (its key carries the UTC day, so an
    # unreadable board files at most one a day)
    return None if card_status(card, None, runner) in ENDED + (None,) else card


def note_pull(store, err: Optional[str], now: float, runner=None) -> Optional[dict]:
    """Track consecutive pull failures in <coach>/selfheal_pull.json; at PULL_FAIL_CARD_AFTER file ONE card for the
    streak (a later streak, or the same one after its card ended, files a fresh card)."""
    path = Path(store.root) / "selfheal_pull.json"
    try:
        st = json.loads(path.read_text())
    except Exception:
        st = {}
    if err is None:
        if st.get("fails"):
            store.log({"event": "selfheal_data_pull_recovered", "fails": st.get("fails"), "since": st.get("since")})
        if st:
            path.write_text(json.dumps({"card": st.get("card")}))
        return None
    st["fails"] = int(st.get("fails") or 0) + 1
    st.setdefault("since", now)
    st["error"] = err[-600:]
    store.log({"event": "selfheal_data_pull_failed", "fails": st["fails"], "since": st["since"], "error": err[-300:]})
    res = None
    if st["fails"] >= PULL_FAIL_CARD_AFTER and not _open_card(path, runner):
        host = host_name()
        body = (f"The self-heal lane could not refresh the coach digests. It keeps running on the digests it has "
                f"(Inbox outcomes, queue drain and fix filing still work), but the digest backstop judges stale "
                f"turns.\n{home_pin_note(PIN_LANE)}\n\n"
                + fence_evidence(f"Seen on {host}: {st['fails']} runs in a row (since {_iso(st['since'])}).\n"
                                 f"git error:\n```\n{st['error']}\n```") + "\n\n"
                "Find what leaves the coach data clone unpullable (uncommitted tracked edits from a local writer, a "
                "conflicted index, auth), fix it at the writer, and preserve its rows (commit + push, never discard). "
                "Done when `selfheal_data_pull_recovered` appears in <coach>/log.jsonl.")
        res = _lane_card("self-heal lane: coach-data pull failing", body,
                         f"selfheal-data-pull:{host}:{time.strftime('%Y-%m-%d', time.gmtime(now))}", runner)
        if res.get("card"):
            st["card"] = res["card"]
        store.log({"event": "selfheal_data_pull_card", **res})
    path.write_text(json.dumps(st))
    return res


def stuck_proposals(store=None, now: Optional[float] = None, budget_fn=None, min_age: float = STUCK_PROPOSAL_S,
                    auto: Optional[bool] = None) -> list:
    """[(fp, age_s)] proposed for more than `min_age` with no fix card while the budget would admit one. Empty when
    auto-approve is off (a proposal then legitimately waits on David) or the budget is closed/unknown (deferral)."""
    store = _store(store)
    now = now or time.time()
    if not (auto_approve() if auto is None else auto):
        return []
    old = [(fp, now - float(st.get("proposed_at") or 0)) for fp, st in LaneState(store.root).read().items()
           if st.get("status") == "proposed" and not st.get("fix_card") and st.get("proposed_at")
           and now - float(st["proposed_at"]) > min_age]
    if not old:
        return []
    b = (budget_fn or (lambda bd, n: budget(bd, n, new_card_usd=FIX_BUDGET)))(None, now)
    if not (b.get("available", True) and b.get("ok_usd") and b.get("ok_running")):
        return []
    return sorted(old, key=lambda x: -x[1])


def check_stuck(store=None, now: Optional[float] = None, budget_fn=None, runner=None, auto: Optional[bool] = None,
                lane_status: str = "") -> dict:
    """Detection for the lane itself (run from a pass that does not depend on it, e.g. the signals sweep): files ONE
    Kanban card per incident while proposals sit unfiled (cron-7b7df72ab4's own proposal waited 4 days on the dead
    lane it described). Logs `selfheal_stuck`."""
    store = _store(store)
    now = now or time.time()
    stuck = stuck_proposals(store, now, budget_fn, auto=auto)
    path = Path(store.root) / "selfheal_stuck.json"
    seen_p = Path(store.root) / "selfheal_stuck_seen.json"
    if not stuck:
        seen_p.unlink(missing_ok=True)
        return {"action": "ok"}
    # Open capacity at one instant is not a dead lane: a slot can free minutes before the next lane pass fills it
    # (t_01a1c00ca6bc0945 was filed that way, and that pass then approved 3 fixes). Alarm only when capacity was
    # open at an earlier sweep too and the lane approved nothing since.
    try:
        first = float(json.loads(seen_p.read_text()).get("at") or 0)
    except Exception:
        first = 0.0
    if not first or first >= now or _recent_events(store.root, "selfheal_approved", first):
        seen_p.write_text(json.dumps({"at": now}))
        return {"action": "watch", "n": len(stuck), "since": now}
    open_card = _open_card(path, runner)
    rec = {"event": "selfheal_stuck", "n": len(stuck), "oldest": stuck[0][0], "oldest_h": round(stuck[0][1] / 3600, 1),
           "card": open_card}
    if open_card:
        store.log(rec)
        return {"action": "skip", "why": f"open: {open_card}", **rec}
    errs = [r.get("error") for r in _recent_events(store.root, "selfheal_file_error", now - 86400)][-3:]
    host = host_name()
    lines = "\n".join(f"- {fp}: proposed {a / 3600:.1f} h ago" for fp, a in stuck[:20])
    seen = (f"Seen on {host}: {len(stuck)} self-heal fingerprint(s) are `proposed` with no fix card although the "
            f"budget admits one (oldest {stuck[0][1] / 3600:.1f} h; threshold {STUCK_PROPOSAL_S // 3600} h).\n\n{lines}\n\n"
            + (f"Lane cron: {lane_status}\n\n" if lane_status else "")
            + (f"Recent selfheal_file_error: {json.dumps(errs)[:900]}" if errs else ""))
    body = (f"Self-heal fingerprints are `proposed` with no fix card although the budget admits one. The lane cron "
            f"(`self_heal.cron` -> `sync`) files these every 5 min, so it is failing or not running.\n"
            f"{home_pin_note(PIN_LANE)}\n\n{fence_evidence(seen)}\n\n"
            + "Fix the lane (read the cron's last output, `self_heal.py status`), then confirm the next pass logs "
              "`selfheal_approved` for these. Do not file fix cards by hand for fingerprints another card owns.")
    res = _lane_card(f"self-heal lane: {len(stuck)} proposals stuck without fix cards", body,
                     f"selfheal-stuck:{host}:{time.strftime('%Y-%m-%d', time.gmtime(now))}", runner)
    if res.get("card"):
        path.write_text(json.dumps({"card": res["card"], "at": now}))
    store.log({**rec, "filed": res})
    return {**res, **rec}


def cron(data: Path, store=None, now: Optional[float] = None, backstop_every: float = 25 * 60,
         backstop_fn: Optional[Callable] = None, liveness_every: float = 3600,
         liveness_fn: Optional[Callable] = None, pull_fn: Optional[Callable] = None) -> list:
    """The hub's pass (no agent, hermes cron every 5 min): Inbox outcomes (Do it -> fix card within 5 min), queue
    drain, and the digest backstop at most every `backstop_every` seconds. Prints only what it did.

    Do it is a plain resolve on the Inbox card and THIS pass files the fix card: a Kanban worker (the Inbox's task
    action would run one) is fenced from creating cards through the CLI, the cron scheduler is not."""
    lines = []
    if mode() == "off":
        return lines
    store = _store(store)
    now = now or time.time()
    perr = (pull_fn or pull_data)(data)
    if perr is not None:
        # Fix filing, queue drain and Inbox outcomes need no digests: only the digest backstop is skipped (stale
        # intake, d499a5a), and the streak files its own card (note_pull).
        lines.append(f"pull: failed (digest backstop skipped): {perr[-300:]}")
    try:
        pc = note_pull(store, perr, now)
        if pc:
            lines.append(f"pull: card {pc.get('card') or pc.get('why')}")
    except Exception as exc:
        lines.append(f"pull: streak tracking failed: {exc}")
    if mode() == "live":
        for r in retry_deferred(store, now=now):
            lines.append(f"deferred: {json.dumps(r)[:300]}")
    for fp, what in sync(store, now=now):
        lines.append(f"inbox: {fp} {what}")
    for r in drain(store):
        if r.get("action") not in ("skip",):
            lines.append(f"queue: {json.dumps(r)[:300]}")
    stamp = Path(store.root) / "selfheal_backstop.last"
    try:
        last = float(stamp.read_text().strip() or 0)
    except Exception:
        last = 0.0
    if perr is None and now - last >= backstop_every:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now))
        try:
            for r in (backstop_fn or backstop)(data, store):
                if r.get("action") not in ("skip",):
                    lines.append(f"backstop: {r.get('action')} {r.get('fingerprint') or r.get('card') or ''}")
        except Exception as exc:
            lines.append(f"backstop failed: {exc}")
    lstamp = Path(store.root) / "selfheal_liveness.last"
    try:
        llast = float(lstamp.read_text().strip() or 0)
    except Exception:
        llast = 0.0
    if now - llast >= liveness_every:
        lstamp.parent.mkdir(parents=True, exist_ok=True)
        lstamp.write_text(str(now))
        try:
            r = (liveness_fn or judge_liveness)(store, now=now)
            if r.get("action") not in ("ok", "unknown", "skip"):
                lines.append(f"liveness: {r.get('action')} {r.get('fingerprint') or r.get('card') or ''}")
        except Exception as exc:
            lines.append(f"liveness failed: {exc}")
    return lines


# ── liveness: a complaint judge that has gone quiet while users chat is a defect (plugin-89461de0ed) ──
USER_SOURCES = ("desktop", "cli", "tui", "telegram", "discord", "slack", "signal", "whatsapp", "sms", "imessage",
                "matrix", "email")
LIVENESS_MIN_TURNS = 20
LIVENESS_MIN_JUDGED = 3   # this many judge-reaching rows, all error/no_opinion, = the judge is failing
JUDGE_REACHED = ("hit", "below", "no_opinion", "error")


def user_turns_since(since: float, home: Optional[Path] = None) -> Optional[int]:
    """User messages after `since` in this host's state.db, in top-level sessions on user platforms (no cron,
    Kanban, subagent or scripted-dispatch sessions). None when the db cannot be read."""
    import sqlite3
    path = hermes_home(home) / "state.db"
    if not path.is_file():
        return None
    marks = ",".join("?" * len(USER_SOURCES))
    try:
        db = _ro_state(path)
        try:
            row = db.execute(
                f"SELECT sum((SELECT count(*) FROM messages m WHERE m.session_id=s.id AND m.role='user' "
                f"AND m.timestamp > ?)) FROM sessions s WHERE COALESCE(s.last_activity_at, s.started_at) > ? "
                f"AND s.parent_session_id IS NULL AND s.source IN ({marks})",
                (since, since, *USER_SOURCES)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return int(row[0] or 0) if row else 0


def _recent_events(root: Path, name: str, since: float, tail_bytes: int = 32 << 20) -> list:
    """`name` rows after `since` from the last `tail_bytes` of <coach>/log.jsonl (bounded read of a growing log)."""
    p = Path(root) / "log.jsonl"
    if not p.exists():
        return []
    with p.open("rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - tail_bytes))
        data = fh.read().decode("utf-8", "replace")
    out, needle = [], f'"event": "{name}"'
    for line in data.splitlines():
        if needle not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == name and (r.get("ts") or 0) > since:
            out.append(r)
    return out


def judge_liveness(store=None, now: Optional[float] = None, turns_fn: Optional[Callable] = None,
                   filer: Optional[Callable] = None, window: float = 86400,
                   min_turns: int = LIVENESS_MIN_TURNS) -> dict:
    """Hub cron check: more than `min_turns` user turns in `window` on this host with zero `selfheal_judged`
    rows (the hook or its thread is not running), or >= LIVENESS_MIN_JUDGED judge-reaching rows that are all
    error/no_opinion (the judge is failing), files one defect through the lane. Its fingerprint names the host and
    alarm, so an ongoing incident is one card (the lane dedupes) and a later one refiles. Logs `selfheal_liveness`."""
    store = _store(store)
    now = now or time.time()
    since = now - window
    turns = (turns_fn or user_turns_since)(since)
    rows = _recent_events(store.root, "selfheal_judged", since)
    reached = [r for r in rows if r.get("outcome") in JUDGE_REACHED]
    failed = [r for r in reached if r.get("outcome") in ("error", "no_opinion")]
    alarm = None
    if turns is not None and turns > min_turns:
        if not rows:
            alarm = "no_judgments"
        elif len(reached) >= LIVENESS_MIN_JUDGED and len(failed) == len(reached):
            alarm = "judge_failing"
    rec = {"event": "selfheal_liveness", "turns": turns, "judged": len(rows), "reached": len(reached),
           "failed": len(failed), "alarm": alarm}
    store.log(rec)
    if turns is None:
        return {"action": "unknown", **rec}
    if not alarm:
        return {"action": "ok", **rec}
    host = host_name()
    symptom = ("self-heal complaint judge logged no verdicts while users chatted" if alarm == "no_judgments"
               else "self-heal complaint judge calls all failed") + f" on {host}"
    errs = sorted({str(r.get("error") or "")[:120] for r in failed if r.get("error")})[:3]
    quote = (f"{turns} user turns in the last {int(window // 3600)} h on {host}; {len(rows)} selfheal_judged rows, "
             f"{len(reached)} reached the judge, {len(failed)} error/no_opinion."
             + (f" Errors: {'; '.join(errs)}" if errs else ""))
    d = {"component": "plugin", "symptom": symptom, "source": "signal", "kind": "signal", "p_defect": 0.9,
         "session_id": "", "quote": quote, "host": host, "platform": "cron", "at": now,
         "fingerprint": fingerprint("plugin", symptom)}
    res = (filer or file_defect)(d, store=store)
    return {**(res or {}), "fingerprint": d["fingerprint"], "alarm": alarm}


if __name__ == "__main__":
    raise SystemExit(main())
