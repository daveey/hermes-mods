#!/usr/bin/env python3
"""Ask-gate metric (stdlib only): two weekly KPIs from the cross-host coach digests, both target 0.
  ask_me          David's corrections of the form "why are you asking me / I already did it / do it yourself".
  had_to_ask_fix  David asking for a Hermes defect to be fixed or root-caused ("make a card to fix", "root cause",
                  "figure out why", "fixed this once / broken again", "why did you not…", a screenshot + "fix"):
                  the work the self-heal lane should have started unasked. Baby Bear product fix requests count
                  separately as `had_to_ask_fix_bb` and go to bb-tuner's handoff file (--bb-handoff), not the lane.

    coach_askgate.py --data ~/code/hermes-coach-data --weeks 6 --baseline OUT.md     # baseline, 6 weeks
    coach_askgate.py --data ... --last-week --post --selfheal                         # Monday: previous Mon-Sun, to the Inbox
    coach_askgate.py --data ... --weeks 6 --json                                      # machine-readable

The Monday post is one card (key ask-gate-weekly:<monday>) with both classes and, with --selfheal, the self-heal lane
section from coach_weekly.py. It writes reports/askgate/<monday>.json in the data repo; another host that finds that
marker (posted by someone else) skips its post, so moving the cron between hosts never doubles the card.

A hit is a real user turn (digests already drop Hermes-written user-role messages; Slack-engagement batches, probe
prompts and Kanban notices are skipped here too) matching a correction pattern: the agent asked David for something
it should have checked or done itself. Regex precision is imperfect by design (see references/labeling.md): hits are
listed with session + quote so a human can eyeball them, and a miss on the sampled turns (digests keep the first 10 and
last 50 user turns per session) is possible. Weeks run Monday 00:00 PT to Monday 00:00 PT. Hits seen on several hosts
(forked or mirrored sessions) count once.
Target after the ask-gate ships: 0.

With --last-week the report also carries a "Skill outcomes" section (plugins/skill-outcomes): per skill uses, Done-means
tick-offs, first-pass rate, mean fix rounds and correction rate for that week, read from every host's
<data>/<host>/skill-outcomes.jsonl (coach_feed copies them) plus the local --home logs. --post also writes it to
reports/askgate/<monday>-skill-outcomes.md and puts it in the review card body.

A hit whose turn the self-heal detector already saw (shadow or live) is listed as "Detected, not a miss" instead of
filed. Detections come from the local <home>/coach/selfheal.json plus every host's <data>/<host>/selfheal-seen.json
(coach_feed copies them), since ask-gate runs on the hub and detection state is per host (plugin-03d4509c8a).
"""
import argparse, functools, glob, json, os, re, sqlite3, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")

# Turns that are not David typing a correction.
SKIP = re.compile(
    r"^\s*(\[(Slack engagement|IMPORTANT:|OUT-OF-BAND|PEER MESSAGE from|Message from another Hermes session)|"
    r"(?:Go|Chose|✓ Done):|\{\s*\"(?:person|kind)\"|Without any other tools:|[✔✖✘⏸⏱🔄]\ufe0f?\s+\[)", re.I)
# Defense in depth for old digests: generated user-role rows are not human corrections.
GENERATED = re.compile(
    r'^\s*(?:# Kanban task t_|\[(?:kanban|owner-wake|coach|selfheal-probe)\]|'
    r'\{\s*"item"\s*:|New notes? on your kanban task from another agent session\b|'
    r'Diagnosis only\. Inspect this runner/host|⚠\ufe0f?\s+\[[^\]\n]+\]\s+@\S+\s+Kanban\s+t_)'
    r'|\(Roleplay test of our reply checker', re.I)
# Same marker as plugins/coach/self_heal.py PROBE_RE: a session opener that marks a deliberate detector probe.
PROBE = re.compile(r"^\s*(?:\[selfheal-probe\]|Live test of a detector\b)", re.I)

# A stamped line of a live card note: "- <author> (<tags>) at HH:MM TZ: <body…>" (core _comment_stamp).
NOTE_HEADER = re.compile(r"^\s*New notes? on your kanban task from ", re.I)
NOTE_LINE = re.compile(r"(?m)^- [^\n]*? at \d{1,2}:\d{2}(?: [A-Za-z0-9+\-]+)?: ")
AGENT_STAMP = re.compile(r"^- (?:Subagent:|[^\n:(]*\([^)\n]*\bagent chat\b)")
REVIEW_BRIEF = re.compile(
    r"^\s*(?:You are (?:an?|the) independent\b[^\n]*\breviewer\b|Review the COMPLETE exact PR\d+\b)", re.I)


NOTE_PREFIX = 120  # chars of a note line's body matched against agents' kanban_comment tool calls
TOOLCALL_DROPPED = set()  # note-line prefixes withdrawn by a tool-call match this run (weekly-post count)
TOOLCALL_DROPS = {"lines": 0}


def _norm_note(s):
    """Note text for comparison across the JSON/shell escaping a tool call adds: no quotes/backslashes, one space."""
    return _ws(re.sub(r"[\"'\\`]", "", s or ""))


def _call_texts(tool_calls):
    """The comment bodies an assistant row's tool calls wrote: kanban_comment `body`, or the command of a terminal
    call that shells out to `hermes kanban comment` (core tags those notes "human")."""
    try:
        calls = json.loads(tool_calls) if isinstance(tool_calls, str) else tool_calls
    except Exception:
        return
    for c in calls if isinstance(calls, list) else ():
        if not isinstance(c, dict):
            continue
        fn = c.get("function") if isinstance(c.get("function"), dict) else c
        name, args = fn.get("name") or "", fn.get("arguments") or fn.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                continue
        if not isinstance(args, dict):
            continue
        if name.endswith("kanban_comment") and isinstance(args.get("body"), str):
            yield args["body"]
        elif name in ("terminal", "shell") and "kanban comment" in str(args.get("command") or ""):
            yield str(args["command"])


def _ro_connect(path, timeout=2):
    """Read-only open. mode=ro fails on a WAL db while its -wal/-shm is absent (writers delete them on last close),
    so fall back to mode=rw + query_only, which reads the same data and never writes rows or creates the db."""
    uri = Path(path).resolve().as_uri()
    try:
        c = sqlite3.connect(uri + "?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(uri + "?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


NOTE_STAMP = re.compile(r"^- [^\n]*? at (\d{1,2}):(\d{2})(?: ([A-Za-z0-9+\-]+))?: ")  # NOTE_LINE with groups
STAMP_OFFSETS = {"UTC": 0, "GMT": 0, "Z": 0, "PST": -8, "PDT": -7, "MST": -7, "MDT": -6, "CST": -6, "CDT": -5,
                 "EST": -5, "EDT": -4}
NOTE_SLACK = 1800  # seconds either side of a note's stamp searched for the tool call that wrote it
NOTE_LOOKBACK = 86400  # no usable stamp: the day before the turn


def note_window(t, at=None):
    """The (lo, hi) epoch window in which the tool call that wrote a note delivered at turn time `t` must sit.
    `at` is the line stamp (HH, MM, zone): the note was written at the latest such wall time at or before t+120 s,
    +/- NOTE_SLACK. No stamp or an unknown zone -> [t - 1 day, t + 120 s]. No t -> None (nothing is searched)."""
    if not t:
        return None
    t = float(t)
    zone = (at[2] or "UTC").upper() if at else None
    off = STAMP_OFFSETS.get(zone) if zone else None
    if off is None and zone and re.fullmatch(r"[+-]\d{4}", zone):
        off = (1 if zone[0] == "+" else -1) * (int(zone[1:3]) + int(zone[3:]) / 60)
    if off is None:
        return int(t - NOTE_LOOKBACK), int(t + 120)
    tz = timezone(timedelta(hours=off))
    end = datetime.fromtimestamp(t + 120, tz)
    try:
        at_dt = end.replace(hour=int(at[0]), minute=int(at[1]), second=0, microsecond=0)
    except ValueError:
        return int(t - NOTE_LOOKBACK), int(t + 120)
    if at_dt > end:
        at_dt -= timedelta(days=1)
    s = at_dt.timestamp()
    return int(s - NOTE_SLACK), int(s + NOTE_SLACK)


def _first_id_at(con, ts, lo, hi):
    """Smallest message id in [lo, hi + 1] whose row (the first at or after it) has timestamp >= ts: a binary search
    on the rowid (messages has no timestamp-only index). hi + 1 -> every row is older."""
    hi += 1
    while lo < hi:
        mid = (lo + hi) // 2
        r = con.execute("SELECT id, timestamp FROM messages WHERE id >= ? ORDER BY id LIMIT 1", (mid,)).fetchone()
        if r is None or (r[1] is not None and r[1] >= ts):
            hi = mid
        else:
            lo = r[0] + 1
    return lo


@functools.lru_cache(maxsize=512)
def _tool_call_hit(prefix, db, lo_ts, hi_ts):
    words = sorted(set(re.findall(r"[A-Za-z0-9_]{5,}", prefix)), key=len, reverse=True)[:3]
    if len(words) < 2 or not os.path.exists(db):
        return False
    try:
        con = _ro_connect(db, timeout=2)
    except Exception:
        return False
    try:
        deadline = time.monotonic() + 15  # backstop only: the rowid window keeps the scan small
        con.set_progress_handler(lambda: time.monotonic() > deadline, 100000)
        # Two statements, never "SELECT MIN(id), MAX(id)": SQLite's min/max shortcut takes one aggregate only, and
        # the pair scans a whole index (93 MB, 13 s cold on the hub's 11 GB state.db, t_01a1cf40f3fd3701).
        first = con.execute("SELECT MIN(id) FROM messages").fetchone()[0]
        last = con.execute("SELECT MAX(id) FROM messages").fetchone()[0]
        if first is None:
            return False
        lo = _first_id_at(con, lo_ts, first, last)
        hi = _first_id_at(con, hi_ts, lo, last) - 1  # last id older than hi_ts
        if hi < lo:
            return False
        sql = ("SELECT tool_calls FROM messages WHERE id BETWEEN ? AND ? AND role = 'assistant'"
               " AND tool_calls LIKE '%kanban%comment%'" + " AND tool_calls LIKE ?" * len(words) + " LIMIT 20")
        for (tc,) in con.execute(sql, [lo, hi] + [f"%{w}%" for w in words]):
            if any(prefix in _norm_note(t) for t in _call_texts(tc)):
                return True
    except Exception:
        return False
    finally:
        con.close()
    return False


def _note_prefix(body):
    """The comparable head of a note body: the text before a digest clip marker (session_digest cuts long bodies,
    and the marker is never in the tool call), trailing ellipsis dropped, first NOTE_PREFIX chars."""
    body = (body or "").split(CLIP, 1)[0]
    return _norm_note(body).rstrip("…").strip()[:NOTE_PREFIX].strip()


def note_line_by_agent(body, home=None, t=None, at=None):
    """True when an assistant tool call in the local state.db (read-only) wrote this card-note body: positive
    provenance for an untagged note from before core recorded writer_kind (agent-b14b35066b). Only the rows in
    note_window(t, at) are searched (a rowid range, so an 11 GB state.db is not scanned); no t, a missing or
    unreadable state.db, or no match -> False (the line stays David's for review)."""
    win = note_window(t, at)
    prefix = _note_prefix(body)
    if win is None or len(prefix) < 20:
        return False
    home = Path(home or os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
    return _tool_call_hit(prefix, str(home / "state.db"), *win)


def _untagged_by_agent(part, t=None):
    """An unstamped note line (no agent tag) whose body an agent's tool call wrote. Counted for the weekly post."""
    m = NOTE_STAMP.match(part)
    body = part[m.end():] if m else ""
    if not note_line_by_agent(body, t=t, at=m.groups() if m else None):
        return False
    TOOLCALL_DROPPED.add(_note_prefix(body))
    TOOLCALL_DROPS["lines"] = len(TOOLCALL_DROPPED)
    return True


def toolcall_drop_lines():
    """Weekly-post line: legacy notes withdrawn only because a tool call proved an agent wrote them."""
    n = TOOLCALL_DROPS["lines"]
    if not n:
        return []
    return [f"Legacy card notes withdrawn by a tool-call match: {n} untagged note line{'s' if n != 1 else ''} "
            "matched an agent's kanban_comment tool call in the local state.db, so they were not counted as David's."]


def drop_agent_notes(text, t=None):
    """A live card note minus the lines an agent wrote (core stamps them "(agent chat",
    agent-261aa31f49): their words are not David's. Each line runs to the next stamp, so a
    multi-line body is dropped whole. An unstamped (older) line is dropped only when the local
    state.db holds the agent tool call that wrote it (agent-b14b35066b); otherwise it is kept for review."""
    if not NOTE_HEADER.match(text):
        return text
    starts = [m.start() for m in NOTE_LINE.finditer(text)]
    if not starts:
        return text
    parts = [text[:starts[0]]] + [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    kept = [parts[0]] + [p for p in parts[1:] if not (AGENT_STAMP.match(p) or _untagged_by_agent(p, t))]
    if len(kept) == 1:  # every line was an agent's: none of it is David's
        return ""
    return "".join(kept)


def generated(text):
    # Operator notes can carry a human's words even with an "another session"
    # stamp. Without writer provenance, retain them for individual review.
    return bool(GENERATED.search(text))


OOB = re.compile(r"\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]", re.S)

_V = r"(?:do|did|doing|check|checked|merge|nudge|investigate|investigating|figure|fix|run|handle|try|test|verify|find|look|ssh|read|open|search|review|update|click|sign|log)"
PATTERNS = [
    ("why-asking-me", r"\bwhy\b.{0,40}\b(?:ask|asking|asked)\w*\s+me\b"),
    ("stop-asking", r"\b(?:stop|quit)\s+asking\b|(?<!\bi )\bkeep\s+asking\b|\bdon.?t\s+(?:ask|keep asking)\s+me\b|\bto stop asking\b"),
    ("do-it-yourself", r"\b" + _V + r"\b(?:\s+\S+){0,5}?\s+yourself\b"),
    ("why-not-yourself", r"\bwhy\s+(?:did|didn.?t|don.?t|can.?t|couldn.?t|would|wouldn.?t)\w*\s+(?:you|the agent|it)\b.{0,40}\byourself\b"),
    ("why-didnt-check", r"\bwhy\s+(?:did|didn.?t)\w*\s+you\s+(?:just\s+)?(?:figure|check|look|find|verify|list|search)"),
    ("instead-of-doing", r"\binstead of (?:doing|checking|running|trying)\b.{0,30}\b(?:yourself|it)\b"),
    ("already-done", r"\bi(?:.?ve|\s+have)?\s+(?:already|just)\s+(?:did|done|told|said|answered|completed|handled)\b"
                     r"|\bi\s+keep\s+(?:completing|doing|telling|answering|saying|handling)\b"
                     r"|\bhas been (?:revoked|canceled|cancelled|done|handled)\s+for\b"),
    ("you-just-said", r"\byou\s+(?:just|already)\s+(?:said|told|know)\b|\byou should (?:already )?know\b"),
    ("waiting-on-me", r"\bare you waiting (?:for|on) me\b|\bi (?:dont|don.?t) see any blocked\b"),
    # The ask itself was unreadable ("this request makes no sense, what do you need me to do? why is it so cryptic").
    ("unclear-ask", r"\b(?:request|ask|card|prompt|question)\b.{0,20}\bmakes? no sense\b"
                    r"|(?:^|[.,;!?]\s*|\bso\s+)what (?:do|did) you\s*need (?:me to do|from me)\b"
                    r"|\bwhy is (?:it|this|the)\b.{0,25}\bso crypt\w*"),
]
RX = [(n, re.compile(p, re.I)) for n, p in PATTERNS]
# Not corrections even though a pattern fires (the user is granting access, giving a style rule, or describing a deliverable).
NEGATIVE = re.compile(r"\brights? to\b|\b(?:intro|introduce|rename|describe|identify)\s+yourself\b|\bsetting (?:this|it) up yourself\b", re.I)


# Class `had_to_ask_fix`: David asking for a Hermes defect to be fixed or root-caused ("make a card to fix", "root
# cause", "figure out why", "fixed this once / broken again", "why did you not…", a screenshot + "fix"). Counted only
# in interactive chats (a cron/Kanban prompt that says "root cause" is a Hermes-written instruction, not David), and
# only when the turn is not already an ask-me correction (that class wins, so no turn counts twice). Like the ask-me
# class, a regex: it cannot tell a Hermes defect from a bug in the project David is building, so the weekly post lists
# every hit with its quote. The self-heal lane exists to drive this to 0.
ASK_CLASS, FIX_CLASS, BB_CLASS = "ask_me", "had_to_ask_fix", "had_to_ask_fix_bb"
CLASS_LABEL = {ASK_CLASS: "Ask-me corrections (\"why are you asking me / I already did it / do it yourself\")",
               FIX_CLASS: "Had to ask for a Hermes/agent fix (\"make a card to fix / root cause / figure out why / "
                          "broken again\")",
               BB_CLASS: "Had to ask for a Baby Bear fix (handed to bb-tuner: why didn't BB's own monitoring catch it?)"}

# Baby Bear product fix requests count separately (`had_to_ask_fix_bb`): the self-heal complaint cascade scopes product
# bugs reported as new issues out by design, and catching them before David notices is BB's own monitoring (bb-tuner:
# logs, digest flags, burn-down), so each one goes to bb-tuner as a "why didn't we catch this first" finding instead of
# to the self-heal lane. Decided 2026-10-22 (ask-gate review, week of 2026-10-12). The turn's own words decide; the
# session title only breaks a tie when the turn names neither BB nor Hermes.
BB_WORDS = re.compile(r"\b(?:bb(?!-tuner)|baby ?bear|pods?|echo[- ]?(?:dot|ear)|echoear|nanit|cameras?|camera stream|"
                      r"home assistant|triptych|mishear\w*|(?:my|the|apple) watch|watch app|room listening|wake word|"
                      r"volume|main room|soundscapes?)\b", re.I)
BB_HA = re.compile(r"\bHA\b")
HERMES_WORDS = re.compile(r"\b(?:hermes|desktop|sidebar|sessions?|kanban|cards?|plugins?|pluggin|gateway|skills?|cron|"
                          r"subagents?|bottom-bar|status bar|left pane|panel)\b", re.I)


def _bb_hermes_counts(text):
    return len(BB_WORDS.findall(text)) + len(BB_HA.findall(text)), len(HERMES_WORDS.findall(text))


def session_text(d):
    """A digest's user turns, the agent replies before them and its last reply, joined: the last-resort tie-break."""
    turns = [u for u in d.get("user_turns") or [] if isinstance(u, dict)]
    return " ".join([f"{u.get('text') or ''} {u.get('prev') or ''}" for u in turns] + [d.get("last_reply") or ""])


# "[Image attached at: ~/Library/Application Support/Hermes/composer-images/…]": the path names Hermes on every
# Desktop screenshot, which made every screenshot fix request a Hermes one.
ATTACH_PATH = re.compile(r"\[Image attached at:[^\]\n]*\]?", re.I)


def is_bb(text, title="", profile="", session=""):
    """True when a fix request is about the Baby Bear product rather than Hermes or the agent. The turn's own words
    decide, then the title; when neither names BB or Hermes, the rest of the session does (BB words outnumber Hermes
    words). Week of 2026-10-12: "why didnt this work [screenshot]" and "can we figure out why this happen" were BB
    music/watch sessions whose later turns and replies said so, and counted as Hermes misses."""
    if (profile or "").startswith("bb-") and profile != "bb-tuner":
        return True
    text = ATTACH_PATH.sub("[screenshot]", text or "")
    if BB_WORDS.search(text) or BB_HA.search(text):
        return True
    if HERMES_WORDS.search(text):
        return False
    if BB_WORDS.search(title or "") or BB_HA.search(title or ""):
        return True
    if HERMES_WORDS.search(title or ""):
        return False
    bb, hermes = _bb_hermes_counts(session or "")
    return bb > hermes
_FIXV = r"(?:fix|investigat\w*|root.?caus\w*|figure out|debug|look into|find out)"
FIX_PATTERNS = [
    ("card-to-fix", r"\b(?:make|create|file|open|start|spin up|add)\s+(?:a\s+|an\s+|another\s+)?(?:new\s+)?(?:kanban\s+)?"
                    r"(?:card|task|ticket|issue|bug)s?\b.{0,60}?\b" + _FIXV),
    ("root-cause", r"\broot.?caus(?:e|ed|es|ing)\b|\b5 whys\b|\bfive.?whys\b"),
    ("figure-out-why", r"\b(?:figure out|find out|investigate|look into|dig into|understand)\b.{0,30}?"
                       r"\b(?:why|what went wrong|what(?:.s| is) (?:wrong|broken|going on))\b"),
    ("analyze-and-fix", r"\b(?:investigate|analy[sz]e|analysis|debug|diagnose)\b.{0,40}?\band (?:then )?fix\b"),
    ("broken-again", r"\bfixed (?:this|it|that) (?:once|before|already)\b|\b(?:broken|broke|happening|failing|back) again\b"
                     r"|\bthought (?:we|you|i) (?:had |mostly |already |just )*fixed\b|\bstill (?:broken|happening|failing|"
                     r"not (?:working|fixed))\b|\bregress(?:ed|ion)\b"),
    ("why-did-you-not", r"\bwhy (?:did you not|didn.?t you|did(?:n.?t)? (?:it|that|the \w+) not|did you (?:skip|ignore|lose|"
                        r"forget|stop|break|delete|drop))\b|\bwhy (?:is|are|was|were|isn.?t|aren.?t|wasn.?t|does|doesn.?t)"
                        r"\b(?:\s+\S+){0,4}?\s+(?:not (?:working|linked|showing|loading|running)|broken|missing|red|failing|"
                        r"empty|gone|stuck)\b"),
    ("is-broken", r"\b(?:is|are|seems|looks|got|was|it.?s|that.?s) (?:still |now |totally |completely |all )?broken\b"
                  r"|\bkeeps? (?:crashing|failing|breaking|launching|stealing|popping|losing|reshuffling|reordering)\b"),
]
FIX_RX = [(n, re.compile(p, re.I)) for n, p in FIX_PATTERNS]
SCREENSHOT = re.compile(r"\[(?:screenshot|Image attached)[^\]]*\]", re.I)
SCREENSHOT_FIX = re.compile(r"\b(?:fix|broken|wtf|why)\b", re.I)
# Instructions Hermes wrote (Inbox Do-it prompts, hand-off briefs, card bodies pasted back: David does not type
# Markdown bold), and a quoted button label.
# Also not a fix request (week of 2026-10-12 review): a status question about past fixes ("do we expect that we fixed
# the root causes"), a conditional plan ("make babybear do it, and if it fails figure out why"), and a spec for a
# mechanism that files cards itself ("a process that ... should create a kanban task to fix", "modify bb-tuner to
# measure X and then file issues to investigate").
FIX_NEGATIVE = re.compile(r"^\s*(?:work kanban task|Investigate this [\w-]+ cron output|Review (?:these|this week)|"
                          r"Self-heal|Context carried over|\{)|\*\*\w|\bno need to (?:root|fix)\b|[\"'“]still broken[\"'”]"
                          r"|\bfixed the root.?causes?\b"
                          r"|\bif (?:it|that|this|they) (?:fails?|breaks?|doesn.?t work|don.?t work)\b"
                          r"|\b(?:it|that|which|then)\s+(?:should\s+|would\s+|will\s+|can\s+)?(?:automatically\s+)?"
                          r"(?:file|create|open|make)s?\s+(?:a\s+|an\s+)?(?:new\s+)?(?:kanban\s+)?(?:card|task|ticket|issue)s?\b"
                          # approving numbered items from the agent's own list ("make cards to fix 3, 4, 5"): the
                          # agent proposed them, so it is a go, not a defect report (week of 2026-10-12)
                          r"|\b(?:make|create|open|file)\s+(?:kanban\s+)?(?:cards?|tasks?|tickets?|issues?)\s+(?:to fix|for)\s+"
                          r"(?:items?\s+|#)?\d+(?:\s*(?:,|and|&|-)\s*#?\d+)+\b",
                          re.I)
INTERACTIVE_SOURCES = {"desktop", "tui", "cli", "slack", "telegram", "discord", "whatsapp", "signal"}


def classify_fix(text):
    """The had_to_ask_fix sub-pattern of a cleaned user turn, or None. Ask-me corrections take precedence."""
    if generated(text) or SKIP.match(text) or FIX_NEGATIVE.search(text) or classify(text):
        return None
    for name, rx in FIX_RX:
        if rx.search(text):
            return name
    if SCREENSHOT.search(text) and SCREENSHOT_FIX.search(SCREENSHOT.sub("", text)):
        return "screenshot-fix"
    return None


def clean(text, t=None):
    # Only a top-level steering envelope is a user message. Never unwrap one
    # quoted inside an agent-to-agent message.
    if re.match(r"^\s*\[OUT-OF-BAND USER MESSAGE", text):
        m = OOB.search(text)
        if m:
            text = m.group(1)
    # Desktop appends these machine-owned sections to real user text. Their
    # quoted button labels are not new complaints from the user.
    text = re.split(r"(?m)^\s*\[(?:(?:user-steps|chat-closeout)\]|System:)", text, maxsplit=1)[0]
    return drop_agent_notes(text.strip(), t).strip()


# An Inbox notice (plugins/inbox/store.py _post_text/_update_text) ends in "**Proposed:** <accept_prompt>" and the
# footer "Press **Do it** to run the proposal"; pressing Do it sends <accept_prompt> as the user's message. That turn
# is the poster's text, not David's (plugin-9ab6e8e868: plain-text proposals got past the bold-markdown filter).
DOIT_FOOTER = re.compile(r"Press \*\*Do it\*\* to run the proposal", re.I)
DOIT_UPDATE = re.compile(r"^\s*\*\*Update \d{4}-\d{2}-\d{2} \d{2}:\d{2}\*\*")
PROPOSED = re.compile(r"\*\*Proposed:\*\*\s*(.*?)\s*(?:\n\s*\n\s*_(?:Inbox message|From )|\Z)", re.S)
CLIP = "…[clipped]…"  # session_digest clip_ends / clip_user_turn marker


def _ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _ends(s):
    """(head, tail) of a possibly clipped digest string: the text before the first clip marker and after the last."""
    if CLIP not in s:
        return s, s
    return s.split(CLIP, 1)[0].strip(), s.rsplit(CLIP, 1)[1].strip()


def doit_remainder(text, prev):
    """When ``text`` is an Inbox Do-it press (``prev`` is the Inbox notice and the turn starts with its proposal),
    the rest of the turn after the proposal ("" when the turn is exactly the proposal), else None. Only the
    remainder is David's: an edited or own-words reply no longer starts with the proposal and counts as before."""
    prev = prev or ""
    if not (DOIT_FOOTER.search(prev) or DOIT_UPDATE.match(prev)):
        return None
    ms = list(PROPOSED.finditer(prev))
    if not ms:
        return None
    p, t = _ws(ms[-1].group(1)), _ws(text)
    if not p:
        return None
    if CLIP not in p and CLIP not in t:
        return t[len(p):].strip() if t.startswith(p) else None
    # Either side was clipped by the digest: match the kept ends, treat a match as the whole turn.
    (ph, pt), (th, tt) = _ends(p), _ends(t)
    heads = ph.startswith(th) or th.startswith(ph)
    tails = pt.endswith(tt) or tt.endswith(pt)
    return "" if ph and th and heads and tails else None


def inbox_doit(text, prev):
    """True when the whole turn is an Inbox proposal pasted by the Do it button (not David's words)."""
    return doit_remainder(text, prev) == ""


def classify(text):
    if generated(text) or SKIP.match(text) or NEGATIVE.search(text):
        return None
    # A complaint about the user's repeated requests is not the agent repeatedly asking.
    text = re.sub(r"\b(?:i|we)\s+(?:had|have)\s+to\s+keep asking\b", "user repeated requests", text, flags=re.I)
    for name, rx in RX:
        if rx.search(text):
            return name
    return None


def week_start(ts):
    d = datetime.fromtimestamp(ts, PT)
    mon = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return mon


def collect(data, since_ts, until_ts, doit=None):
    """-> (hits, scanned). Turns that are an Inbox Do-it press (the poster's proposal pasted as the user's message)
    are not David's words: they are neither hits nor scanned turns, and are appended to ``doit`` when given."""
    hits, seen, first_ts = [], set(), None
    scanned = {}  # week start ts -> user turns scanned (0 = no digest coverage, the count is unknown, not zero)
    for f in glob.glob(str(Path(data).expanduser() / "*" / "*" / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("id"):  # reports/askgate markers, eval files: not digests
            continue
        st = d.get("started_at")
        if st and (first_ts is None or st < first_ts):
            first_ts = st
        turns = d.get("user_turns") or []
        # A deliberate detector probe opens with `[selfheal-probe]`; its later turns are scripted too. The lane that
        # files the hit may run on another host than the session (no state.db row there), so the hit carries the
        # opener and a probe flag (agent-43f47d4029). Digests keep the first user turns, so turns[0] is the opener.
        first = clean((turns[0].get("text") if turns and isinstance(turns[0], dict) else "") or "",
                      turns[0].get("t") if turns and isinstance(turns[0], dict) else None)
        probe = bool(PROBE.match(first))
        for i, u in enumerate(turns):
            t, text = u.get("t") or 0, clean(u.get("text") or "", u.get("t"))  # "…[N turns omitted]" rows carry t: None
            if not (since_ts <= t < until_ts) or not text or generated(text) or SKIP.match(text):
                continue
            if u.get("generated"):  # the digest's writer flag: core recorded a plugin wrote this turn (plugin-4731e7cdc3)
                continue
            # Launcher-authored review instructions are not human corrections. Do not
            # suppress ordinary one-shot requests or interactive review requests.
            if d.get("source") == "oneshot" and i == 0 and REVIEW_BRIEF.match(text):
                continue
            # Provenance: the digest's `inbox_doit` flag (the Inbox store says Do it sent this prompt; the only proof
            # for a labelled card, whose prompt is not in prev), else the Inbox notice in prev.
            rest = "" if u.get("inbox_doit") is True else doit_remainder(text, u.get("prev"))
            if rest is not None:  # Inbox Do-it press: only words David added after the proposal are his
                if not rest:
                    dk = ("doit", d.get("id"), int(t))  # mirrored sessions on several hosts count once
                    if doit is not None and dk not in seen:
                        seen.add(dk)
                        doit.append({"t": t, "session": d.get("id"), "host": d.get("host"), "quote": text})
                    continue
                text = rest
            ws = week_start(t)
            scanned[ws] = scanned.get(ws, 0) + 1
            # A session that opens with a screenshot and "stop asking me" is about an OS/app popup, not an agent ask
            # (nothing preceded it). Text-only openers still count: they often answer an ask carried over from another
            # session (e.g. a CLI chat replying to a bb-tuner question).
            opener_popup = i == 0 and re.search(r"\[(?:screenshot|Image attached)", text, re.I)
            kind, cls = classify(text), ASK_CLASS
            if opener_popup and kind != "unclear-ask":  # a screenshot of an unreadable Needs-you ask is about the agent
                kind = None
            if not kind and d.get("source") in INTERACTIVE_SOURCES:
                # a fix request often opens a session ("fix [screenshot]"), so the opener rule does not apply here
                kind, cls = classify_fix(text), FIX_CLASS
                if kind and is_bb(text, d.get("title") or "", d.get("profile") or Path(f).parent.name,
                                  session_text(d)):
                    cls = BB_CLASS
            if not kind:
                continue
            key = (d.get("id"), int(t), text[:80])
            if key in seen:
                continue
            seen.add(key)
            hits.append({"t": t, "session": d.get("id"), "host": d.get("host"), "source": d.get("source"),
                         "class": cls, "pattern": kind, "quote": text,
                         **({"probe": True, "first_user": first[:300]} if probe else {})})
    hits.sort(key=lambda h: h["t"])
    return hits, scanned


def fmt_ts(t):
    return datetime.fromtimestamp(t, PT).strftime("%Y-%m-%d %H:%M %Z")


def quote(h, n=240):
    q = re.sub(r"\s+", " ", re.sub(r"\[Image attached[^\]]*\]", "", h["quote"])).strip()
    return q if len(q) <= n else q[:n] + "…"


def by_class(hits, cls):
    return [h for h in hits if h.get("class", ASK_CLASS) == cls]


def week_counts(hits, weeks):
    out = {}
    for w in weeks:
        a = [h for h in by_class(hits, ASK_CLASS) if week_start(h["t"]) == w]
        f = [h for h in by_class(hits, FIX_CLASS) if week_start(h["t"]) == w]
        b = [h for h in by_class(hits, BB_CLASS) if week_start(h["t"]) == w]
        out[w] = {ASK_CLASS: len(a), FIX_CLASS: len(f), "fix_sessions": len({h["session"] for h in f}), BB_CLASS: len(b)}
    return out


def doit_counts(doit, weeks):
    return {w: sum(1 for x in doit or () if week_start(x["t"]) == w) for w in weeks}


def doit_lines(doit, weeks=None):
    """Report lines for Inbox Do-it presses: kept visible, never counted as David's words."""
    doit = [x for x in doit or () if weeks is None or week_start(x["t"]) in weeks]
    lines = [f"Inbox Do-it presses (not counted): {len(doit)} turn{'s' if len(doit) != 1 else ''} that were an Inbox "
             "proposal pasted by the Do it button, not David's words."]
    lines += [f"- {fmt_ts(x['t'])} · session `{x['session']}`: \"{quote(x, 100)}\"" for x in doit]
    return lines


def render(hits, weeks, scanned, title, doit=None):
    counts = week_counts(hits, weeks)
    out = [f"# {title}", "",
           f"Generated {fmt_ts(time.time())}. Detector: skills/productivity/hermes-coach/scripts/coach_askgate.py "
           f"(regex over cross-host coach digests). Two classes, target 0 each: `{ASK_CLASS}` once the ask-gate ships, "
           f"`{FIX_CLASS}` once the self-heal lane is live. A turn counts in one class only (ask-me wins); fix requests "
           "are counted in interactive chats only.", "",
           "| Week (Mon, PT) | Ask-me | Had to ask for a Hermes/agent fix (sessions) | Baby Bear fix requests | "
           "User turns scanned |", "|---|---|---|---|---|"]
    now = datetime.now(PT)
    for w in weeks:
        tag = " (partial)" if w + timedelta(days=7) > now else ""
        n, c = scanned.get(w, 0), counts[w]
        fix_cell = f"{c[FIX_CLASS]} ({c['fix_sessions']})" if n else "n/a"
        out.append(f"| {w:%Y-%m-%d}{tag} | {c[ASK_CLASS] if n else 'n/a'} | {fix_cell} | {c[BB_CLASS] if n else 'n/a'} | "
                   f"{n} |")
    fx = by_class(hits, FIX_CLASS)
    out.append(f"| **Total** | **{len(by_class(hits, ASK_CLASS))}** | **{len(fx)}** ({len({h['session'] for h in fx})}) | "
               f"**{len(by_class(hits, BB_CLASS))}** | {sum(scanned.values())} |")
    out.append("")
    if doit is not None:
        out += doit_lines(doit, weeks) + [""]
    for cls in (FIX_CLASS, BB_CLASS, ASK_CLASS):
        out += [f"## {CLASS_LABEL[cls]}", ""]
        for w in weeks:
            hs = [h for h in by_class(hits, cls) if week_start(h["t"]) == w]
            out.append(f"### Week of {w:%Y-%m-%d}: {len(hs) if scanned.get(w) else 'n/a (no digest coverage)'}")
            out.append("")
            for h in hs:
                out.append(f"- {fmt_ts(h['t'])} · {h['host']} · session {h['session']} · {h['pattern']}: \"{quote(h)}\"")
            if not hs:
                out.append("- none" if scanned.get(w) else "- no data: no host kept sessions from this week")
            out.append("")
    out.append("Digests keep the first 10 and last 50 user turns per session, so a hit in the middle of a very long session "
               "can be missed; 'n/a' weeks predate every host's retained sessions.")
    return "\n".join(out)


def marker_path(data, week):
    return Path(data) / "reports" / "askgate" / f"{week:%Y-%m-%d}.json"


def posted_elsewhere(data, week, me):
    """The host that already posted this week's card, if it is not this one (one card per week across hosts)."""
    p = marker_path(data, week)
    try:
        m = json.loads(p.read_text())
    except Exception:
        return None
    who = m.get("posted_by") or m.get("host")  # "host" = markers written before the rename
    return who if who and who != me else None


def post_body(hits, week, extra=None):
    ask, fix, bb = by_class(hits, ASK_CLASS), by_class(hits, FIX_CLASS), by_class(hits, BB_CLASS)
    fs = len({h["session"] for h in fix})
    lines = [f"**{len(fix)}** time{'s' if len(fix) != 1 else ''} David had to ask for a Hermes/agent fix (\"make a card to "
             f"fix / root cause / figure out why / broken again\") in {fs} session{'s' if fs != 1 else ''}, "
             f"**{len(bb)}** Baby Bear fix request{'s' if len(bb) != 1 else ''} (handed to bb-tuner), and "
             f"**{len(ask)}** correction{'s' if len(ask) != 1 else ''} where he had to say \"why are you asking me / I "
             f"already did it / do it yourself\", in the week of {week:%Y-%m-%d}. Target: 0 for all three.", ""]
    for cls, hs in ((FIX_CLASS, fix), (BB_CLASS, bb), (ASK_CLASS, ask)):
        lines += [f"### {CLASS_LABEL[cls]}: {len(hs)}", ""]
        for h in hs[:15]:
            lines.append(f"- {fmt_ts(h['t'])} · session `{h['session']}` · {h['pattern']}: \"{quote(h, 160)}\"")
        if len(hs) > 15:
            lines.append(f"- … and {len(hs) - 15} more")
        if not hs:
            lines.append("- none")
        lines.append("")
    if extra:
        lines += extra
    return "\n".join(lines)


def selfheal_lines(data, homes):
    """The weekly self-heal lane section (coach_weekly), or a one-line note if it cannot be built here."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import coach_weekly
        return coach_weekly.section_selfheal(Path(data), time.time(), 7, homes=homes)
    except Exception as e:  # the KPI post never fails on the lane section
        return [f"Self-heal lane section unavailable: {type(e).__name__}: {e}"]


def _coach_mod(name):
    import importlib.util
    p = Path(__file__).resolve().parents[4] / "plugins" / "coach" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_askgate_{name}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def skill_outcome_lines(data, homes, lo, hi):
    """The weekly Skill outcomes section (plugins/skill-outcomes/outcomes.py rollup over [lo, hi)), or a one-line
    note when it cannot be built. Sources: every <data>/*/skill-outcomes.jsonl plus each home's own logs."""
    try:
        import importlib.util
        p = Path(__file__).resolve().parents[4] / "plugins" / "skill-outcomes" / "outcomes.py"
        spec = importlib.util.spec_from_file_location("_askgate_skill_outcomes", p)
        so = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(so)
        homes = homes or [Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()]
        paths = sorted(Path(data).glob("*/skill-outcomes.jsonl"))
        for h in homes:
            paths += so.home_logs(h)
        return so.rollup_lines(so.read_lines(paths), lo, hi)
    except Exception as e:  # the KPI post never fails on this section
        return [f"Skill outcomes section unavailable: {type(e).__name__}: {e}"]


def replan_lines(homes, lo, hi):
    """Weekly count of ask-gate replan holds (self-heal agent-866a12f54f): asks that handed David "end the approved
    goal unmet, or expand scope" with no changed limit named. Source: each home's logs/ask-gate.jsonl."""
    homes = homes or [Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()]
    held, released, tasks = 0, 0, set()
    for h in homes:
        try:
            lines = (Path(h) / "logs" / "ask-gate.jsonl").read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"replan_' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not lo <= float(r.get("t") or 0) < hi:
                continue
            if r.get("kind") == "replan_hold":
                held += 1
                if r.get("task"):
                    tasks.add(r["task"])
            elif r.get("kind") == "replan_released":
                released += 1
    return [f"Replan holds (ask-gate `replan_hold`): {held} held, {released} released after the hold budget"
            + (f", cards: {', '.join(sorted(tasks))}" if tasks else "") + ". A release means an unchanged "
            "'end it or expand scope' ask reached David: check it named a real changed limit."]


def lane_sessions(home):
    """Session ids the self-heal lane already has a filing for (any status)."""
    try:
        st = json.loads((Path(home) / "coach" / "selfheal_lane.json").read_text())
    except Exception:
        return set()
    return {v.get("session_id") for v in st.values() if isinstance(v, dict) and v.get("session_id")}


DETECT_BEFORE, DETECT_AFTER = 12 * 3600, 48 * 3600  # a detection of the hit's turn lands within this window of it


def detected_sessions(home, data=None):
    """Sessions the self-heal detector already saw, shadow or live: <coach>/selfheal.json fingerprint -> sessions,
    merged with every other host's copy <data>/*/selfheal-seen.json (coach_feed writes it; detection state is per
    host, and ask-gate runs on the hub: plugin-03d4509c8a). -> {session: [(fingerprint, first, last), ...]}. Shadow
    mode only logs, so these have no lane filing."""
    srcs = [Path(home) / "coach" / "selfheal.json"]
    if data is not None:
        srcs += sorted(Path(data).expanduser().glob("*/selfheal-seen.json"))
    out = {}
    for src in srcs:
        try:
            st = json.loads(src.read_text())
        except Exception:
            continue
        for fp, v in (st.items() if isinstance(st, dict) else ()):
            if not isinstance(v, dict):
                continue
            first, last = v.get("first") or 0, v.get("last") or v.get("first") or 0
            for s in v.get("sessions") or ():
                out.setdefault(s, []).append((fp, first, last))
    return out


def detection_for(h, detected):
    """The fingerprint that detected this hit's turn, or None. Only a detection close to the hit counts (selfheal.json
    keeps no per-session time, so the fingerprint's first..last window must reach the hit's window): a detection of
    the same session on another day is a different turn and the hit still gets its review."""
    t = h.get("t") or 0
    for fp, first, last in detected.get(h.get("session"), ()):
        if first <= t + DETECT_AFTER and last >= t - DETECT_BEFORE:
            return fp
    return None


_HIT_ON = re.compile(r"\bon (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (?:PDT|PST)\b")


def row_hit_t(row):
    """A queued ask-gate row's hit time: its `hit_t` (queued since still_flags existed), else the `on YYYY-MM-DD
    HH:MM PDT|PST` that hit_defect wrote into the body (`details`), else None."""
    t = row.get("hit_t")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return float(t)
    m = _HIT_ON.search(str(row.get("details") or ""))
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=PT).timestamp()


DOIT_NEAR = 120  # a digest turn this close to a queued row's hit time is that row's turn


def coach_data_dirs(data=None):
    """Where still_flags looks for session digests: ``data``, else $HERMES_COACH_DATA, else ~/code/hermes-coach-data."""
    return [Path(data or os.environ.get("HERMES_COACH_DATA") or "~/code/hermes-coach-data").expanduser()]


def row_is_doit(row, data=None):
    """True when a queued row's turn was an Inbox Do-it press: the session's digest has a turn near the row's hit
    time whose text starts with the row's (collapsed, clipped) quote and is exactly the proposal of the Inbox notice
    before it. Rows queued before plugin-9ab6e8e868 carry no `prev`, so the digest is the only provenance."""
    s, q = row.get("session_id"), _ws(str(row.get("quote") or "")).rstrip("…").strip()
    if not s or not q:
        return False
    t = row_hit_t(row)
    for base in coach_data_dirs(data):
        for f in glob.glob(str(base / "*" / "*" / f"{glob.escape(s)}.json")):
            try:
                with open(f) as fh:
                    d = json.load(fh)
            except Exception:
                continue
            for u in (d.get("user_turns") or []) if isinstance(d, dict) else ():
                if not isinstance(u, dict) or (t is not None and abs((u.get("t") or 0) - t) > DOIT_NEAR):
                    continue
                text = clean(u.get("text") or "", u.get("t"))
                if _ws(re.sub(r"\[Image attached[^\]]*\]", "", text)).startswith(q) and (
                        u.get("inbox_doit") is True or inbox_doit(text, u.get("prev"))):
                    return True
    return False


def row_is_review_brief(row, data=None):
    """Withdraw only a queued quote matched to a proven one-shot review opener."""
    s, t = row.get("session_id"), row_hit_t(row)
    q = _ws(str(row.get("quote") or "")).rstrip("…").strip()
    if not s or t is None or not q:
        return False
    for base in coach_data_dirs(data):
        for f in base.glob(f"*/*/{glob.escape(s)}.json"):
            d = json.loads(f.read_text())
            turns = d.get("user_turns") or []
            if d.get("source") != "oneshot" or not turns:
                continue
            u = turns[0]
            text = clean(u.get("text") or "", u.get("t"))
            if abs((u.get("t") or 0) - t) <= DOIT_NEAR and _ws(text).startswith(q) and REVIEW_BRIEF.match(text):
                return True
    return False


def _note_body(text):
    """A turn's card-note text as the weekly run quoted it: the top-level steering envelope unwrapped."""
    if re.match(r"^\s*\[OUT-OF-BAND USER MESSAGE", text):
        m = OOB.search(text)
        if m:
            return m.group(1)
    return text


def row_is_agent_note(row, data=None):
    """True when a queued row's turn was a live card note whose every stamped line an agent wrote. The row quote is
    hit_defect's squashed, clipped copy, so the line-start stamps drop_agent_notes needs are gone from it
    (agent-c761a2fac9); the session digest's turn near the hit time that starts with the quote still has them."""
    s, t = row.get("session_id"), row_hit_t(row)
    q = _ws(str(row.get("quote") or "")).rstrip("…").strip()
    if not s or t is None or not q or not NOTE_HEADER.match(q):
        return False
    for base in coach_data_dirs(data):
        for f in glob.glob(str(base / "*" / "*" / f"{glob.escape(s)}.json")):
            try:
                with open(f) as fh:
                    d = json.load(fh)
            except Exception:
                continue
            for u in (d.get("user_turns") or []) if isinstance(d, dict) else ():
                if not isinstance(u, dict) or abs((u.get("t") or 0) - t) > DOIT_NEAR:
                    continue
                raw = u.get("text") or ""
                body = _note_body(raw).strip()
                if (NOTE_HEADER.match(body) and _ws(re.sub(r"\[Image attached[^\]]*\]", "", body)).startswith(q)
                        and not clean(raw, u.get("t"))):
                    return True
    return False


def still_flags(row, homes=None, data=None):
    """Why the ask-gate monitor no longer stands by a row it queued earlier, else None. The drain re-checks a queued
    row with today's full verdict, in route_hits' order, so any later rule fix retracts the rows it would not have
    queued (plugin-656a006c84: queued 11 minutes before detection_for existed, filed a day later anyway). Only
    positive evidence withdraws: a generated quote, an Inbox Do-it press (the session digest's notice before the
    turn, plugin-9ab6e8e868), a detection of that session near the hit (selfheal.json, or another host's
    <data>/*/selfheal-seen.json: plugin-03d4509c8a), or a lane filing for that
    session (selfheal_lane.json). Any error -> None, so the row files as before."""
    try:
        q = row.get("quote") or ""
        qt = row_hit_t(row)
        if q and (generated(clean(q, qt)) or (NOTE_HEADER.match(q) and not clean(q, qt))):
            return "monitor no longer flags (ask-gate): quote is a generated prompt, not David"
        if row_is_agent_note(row, data):
            return "monitor no longer flags (ask-gate): card note written by an agent, not David"
        if row_is_review_brief(row, data):
            return "monitor no longer flags (ask-gate): one-shot review brief, not a correction"
        if row_is_doit(row, data):
            return ("monitor no longer flags (ask-gate): the turn was an Inbox Do-it press (the poster's proposal "
                    "pasted as the user's message), not David")
        s = row.get("session_id")
        if not s:
            return None
        homes = homes or [Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()]
        t = row_hit_t(row)
        for home in homes:
            if s in lane_sessions(home):
                return f"monitor no longer flags (ask-gate): the self-heal lane already has a filing for session {s}"
            if t is not None:
                fp = detection_for({"session": s, "t": t}, detected_sessions(home, data=coach_data_dirs(data)[0]))
                if fp:
                    return (f"monitor no longer flags (ask-gate): the self-heal detector already saw this turn "
                            f"({fp}, session {s})")
    except Exception:
        return None
    return None


def detected_lines(hits):
    """Weekly-post lines for hits skipped because the detector already saw them (a jump in this count shows up)."""
    if not hits:
        return []
    lines = [f"Detected (shadow/live), not a miss: {len(hits)} hit{'s' if len(hits) != 1 else ''} the self-heal "
             "detector already saw (coach/selfheal.json on this host or <host>/selfheal-seen.json), not re-filed:"]
    lines += [f"- {fmt_ts(h['t'])} · session `{h['session']}` · `{h['detected_fp']}`: \"{quote(h, 100)}\"" for h in hits]
    return lines


def hit_defect(h):
    """One ask-gate hit -> a monitor finding for the self-heal lane. Missing
    filing alone does not prove a detection miss (shadow mode never files)."""
    q = quote(h, 160)
    if h["class"] == FIX_CLASS:
        title = f"unfiled fix request needs review: \"{q[:90]}\""
        comp = "plugin"
        body = (f"Ask-gate weekly: David had to ask for a Hermes fix ({h['pattern']}) on {fmt_ts(h['t'])}, "
                f"host {h.get('host')}, session {h['session']}, and the self-heal lane has no filing for that session. "
                f"Quote: \"{q}\"")
        prompt = ("Check the incident date against detector deployment and shadow/live mode first. No filing is not "
                  "proof of a detector miss. Check whether the complaint judge or digest backstop saw this turn; "
                  "fix any proven detector gap and reuse existing ownership for the underlying defect.")
    else:
        title = f"agent asked David to do it himself: \"{q[:90]}\""
        comp = "agent"
        body = (f"Ask-gate weekly: ask-me correction ({h['pattern']}) on {fmt_ts(h['t'])}, host {h.get('host')}, "
                f"session {h['session']}. Quote: \"{q}\"")
        prompt = "Find the root cause and fix the ask-gate or the skill that should have prevented it."
    return {"monitor": "ask-gate", "key": f"ask-gate:{h['session']}:{h['class']}", "title": title, "body": body,
            "prompt": prompt, "component": comp, "session_id": h["session"], "quote": q}


def route_hits(hits, home, filer=None, signals=None, detected=None, data=None):
    """File every hit the lane does not already own. -> (owned hits, not-owned hits). In shadow mode nothing is
    owned (file_defect only logs), so the card keeps its review prompt until the lane goes live. A hit whose turn the
    detector already saw (selfheal.json, shadow or live) is neither: it is appended to ``detected`` (with
    ``detected_fp``) and never filed, since no filing there is not a detector miss (plugin-d55a1b9bb1). ``data``
    (the coach data repo) adds every other host's detections, <data>/*/selfheal-seen.json (plugin-03d4509c8a)."""
    signals = signals or _coach_mod("signals")
    known = lane_sessions(home)
    seen = detected_sessions(home, data=data)
    owned, rest = [], []
    for h in hits:
        if h["session"] in known:
            owned.append(h)
            continue
        fp = detection_for(h, seen)
        if fp:
            if detected is not None:
                detected.append(dict(h, detected_fp=fp))
            continue
        m = hit_defect(h)
        d = signals.monitor_defect(m, host=h.get("host"))
        d.update(session_id=m["session_id"], quote=m["quote"], hit_t=h.get("t"))  # hit_t: still_flags at drain
        if h.get("probe"):  # the lane's own lookup cannot see another host's session: send the evidence along
            d.update(probe=True, first_user=h.get("first_user") or "")
        try:
            res = (filer or _coach_mod("self_heal").file_defect)(d)
        except Exception as e:
            res = {"action": "error", "why": str(e)[:200]}
        (owned if signals.lane_owns(res) else rest).append(h)
    return owned, rest


BB_HANDOFF = "~/.hermes/profiles/bb-tuner/work/askgate-bb-misses.jsonl"


def hand_to_bb_tuner(hits, path):
    """Append each Baby Bear fix request bb-tuner has not been handed yet to its handoff file (bb-tuner's daily pre-run
    prints the rows it has not closed; SOUL.md 'Ask-gate misses'). -> (rows written, rows already there). The file's
    parent must exist (= bb-tuner runs on this host); otherwise nothing is written and (None, 0) comes back."""
    p = Path(path).expanduser()
    if not p.parent.is_dir():
        return None, 0
    have = set()
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                have.add(json.loads(line)["id"])
            except Exception:
                pass
    new = []
    for h in hits:
        rid = f"askgate-bb:{h['session']}:{int(h['t'])}"
        if rid in have:
            continue
        have.add(rid)
        new.append({"id": rid, "t": int(h["t"]), "when": fmt_ts(h["t"]), "week": f"{week_start(h['t']):%Y-%m-%d}",
                    "host": h.get("host"), "session": h["session"], "pattern": h["pattern"], "quote": quote(h, 400)})
    if new:
        with open(p, "a") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new), len(hits) - len(new)


REVIEW_PROMPT = ("Review this week's ask-gate KPI. First verify every hit's provenance and the preceding reply; "
                 "generated notices, probes and quoted instructions are not human corrections. For fix requests, "
                 "check exact self-heal filing/adoption and shadow/live evidence before calling an unfiled hit a "
                 "detector miss. Replay unresolved cases, fix supported detector gaps and reuse existing owners. "
                 "For genuine ask-me corrections, fix the ask-gate or skill that should have prevented them. "
                 "Record evidence limits and account for every dropped or reclassified hit.")


def deliver(hits, week, extra, rest, n_fix, n_bb, n_ask, run=subprocess.run):
    """The weekly report is status, not a decision for David: it never goes to the Inbox. Hits nothing else took
    (``rest``) are agent work, so they become ONE Kanban review card per week (idempotent); with no such hits the
    report only lands in the marker/baseline files. Returns the command run, or None."""
    if not rest:
        return None
    cmd = ["hermes", "kanban", "create",
           f"Ask-gate review: {len(rest)} unrouted hit{'s' if len(rest) != 1 else ''}, week of {week:%b %-d} "
           f"({n_fix} fix, {n_bb} Baby Bear, {n_ask} ask-me)",
           "--assignee", "default", "--idempotency-key", f"ask-gate-weekly:{week:%Y-%m-%d}",
           "--created-by", "coach", "--body", REVIEW_PROMPT + "\n\n" + post_body(hits, week, extra)]
    r = run(cmd, capture_output=True, text=True)
    sys.stderr.write(r.stdout + r.stderr)
    if r.returncode:
        sys.exit(r.returncode)
    return cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="~/code/hermes-coach-data")
    ap.add_argument("--weeks", type=int, default=6, help="number of weeks ending now (current week is partial)")
    ap.add_argument("--last-week", action="store_true", help="only the previous full Mon-Sun week")
    ap.add_argument("--baseline", help="write the markdown report here")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--post", action="store_true", help="post to the Hermes Inbox (once per week across hosts)")
    ap.add_argument("--selfheal", action="store_true", help="append the self-heal lane section to the Inbox post")
    ap.add_argument("--home", action="append", default=[], help="Hermes home(s) for --selfheal (default ~/.hermes)")
    ap.add_argument("--pull", action="store_true", help="git pull the data repo first")
    ap.add_argument("--bb-handoff", default=BB_HANDOFF,
                    help="bb-tuner's handoff file for Baby Bear fix requests (written with --post when its dir exists)")
    a = ap.parse_args()
    data = Path(a.data).expanduser()
    if a.pull:
        subprocess.run(["git", "-C", str(data), "pull", "-q"], check=False)

    cur = week_start(time.time())
    if a.last_week:
        weeks = [cur - timedelta(days=7)]
        until = cur.timestamp()
    else:
        weeks = [cur - timedelta(days=7 * i) for i in range(a.weeks - 1, -1, -1)]
        until = time.time() + 1
    doit = []  # Inbox Do-it presses: visible in the report, never counted as David's words (plugin-9ab6e8e868)
    hits, scanned = collect(data, weeks[0].timestamp(), until, doit=doit)

    if a.last_week:
        title = f"Ask-gate and had-to-ask-for-a-fix, week of {weeks[0]:%Y-%m-%d}"
    else:
        title = f"Ask-gate and had-to-ask-for-a-fix baseline: {a.weeks} weeks to {datetime.now(PT):%Y-%m-%d}"
    md = render(hits, weeks, scanned, title, doit=doit)
    if toolcall_drop_lines():
        md += "\n\n" + "\n".join(toolcall_drop_lines())
    so_lines = None
    if a.last_week:
        homes = [Path(h).expanduser() for h in a.home] or None
        so_lines = skill_outcome_lines(data, homes, weeks[0].timestamp(), until)
        so_lines = so_lines + [""] + replan_lines(homes, weeks[0].timestamp(), until)
        md += "\n\n" + "\n".join(so_lines)
    if a.json:
        dc = doit_counts(doit, weeks)
        print(json.dumps({"weeks": {f"{w:%Y-%m-%d}": dict(c, inbox_doit=dc[w]) for w, c in week_counts(hits, weeks).items()},
                          "hits": hits, "inbox_doit": doit}, indent=1))
    else:
        print(md)
    if a.baseline:
        p = Path(a.baseline).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md + "\n")
    if a.post:
        me = os.uname().nodename.split(".")[0]
        other = posted_elsewhere(data, weeks[0], me)
        if other:
            sys.stderr.write(f"week of {weeks[0]:%Y-%m-%d} already posted by {other}; not posting a second card\n")
            return
        extra = selfheal_lines(data, [Path(h).expanduser() for h in a.home] or None) if a.selfheal else None
        ask, fix, bb = by_class(hits, ASK_CLASS), by_class(hits, FIX_CLASS), by_class(hits, BB_CLASS)
        lane_hits = [h for h in hits if h["class"] != BB_CLASS]  # Baby Bear fix requests go to bb-tuner, not the lane
        rest = lane_hits
        if bb:
            n_new, n_had = hand_to_bb_tuner(bb, a.bb_handoff)
            note = (f"Handed {n_new} Baby Bear fix request{'s' if n_new != 1 else ''} to bb-tuner ({n_had} already "
                    f"there): it checks why BB's own monitoring didn't catch each one first." if n_new is not None else
                    f"bb-tuner handoff {a.bb_handoff} not on this host: the {len(bb)} Baby Bear fix requests need a review.")
            if n_new is None:
                rest = rest + bb
            extra = (extra or []) + ["", note]
        if a.selfheal:  # every other hit goes to the self-heal lane; the card only asks to review what it didn't take
            home = Path(a.home[0] if a.home else os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
            detected = []
            try:
                owned, rest_lane = route_hits(lane_hits, home, detected=detected, data=data)
            except Exception as e:
                owned, rest_lane, detected = [], lane_hits, []
                sys.stderr.write(f"self-heal routing failed: {e}\n")
            rest = rest_lane + [h for h in rest if h["class"] == BB_CLASS]
            extra = (extra or []) + ["", f"Self-heal lane took {len(owned)} of {len(lane_hits)} Hermes/agent hits (filed or "
                                         "already open)." + (f" {len(rest_lane)} still need a review (Do it)."
                                                             if rest_lane else "")]
            if detected:
                extra += [""] + detected_lines(detected)
        else:
            detected = []
        if doit:
            extra = (extra or []) + [""] + doit_lines(doit, weeks)
        if toolcall_drop_lines():
            extra = (extra or []) + [""] + toolcall_drop_lines()
        if so_lines:
            extra = (extra or []) + [""] + so_lines
            sp = data / "reports" / "askgate" / f"{weeks[0]:%Y-%m-%d}-skill-outcomes.md"
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text("\n".join(so_lines) + "\n")
        deliver(hits, weeks[0], extra, rest, len(fix), len(bb), len(ask))
        p = marker_path(data, weeks[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"posted_by": me, "posted_at": int(time.time()), ASK_CLASS: len(ask), FIX_CLASS: len(fix), BB_CLASS: len(bb),
                                 "fix_sessions": len({h["session"] for h in fix}),
                                 "detected_not_refiled": len(detected), "inbox_doit": len(doit),
                                 "legacy_notes_by_tool_call": TOOLCALL_DROPS["lines"]}) + "\n")


if __name__ == "__main__":
    main()
