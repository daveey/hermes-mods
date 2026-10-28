"""reply_check: look at the agent's final answer for a turn BEFORE the turn ends (pre_verify hook).

Five anti-patterns, measured in the user's own history (coach gap report 2026-10-14):
- handoff:     the agent hands the user a step it could plausibly do itself (click/log in/run/paste...).
- confirm:     the agent ends by asking permission for a reversible next step ("Want me to ...?").
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

import json
import os
import re
from typing import Any, Callable, Optional

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
    r"|\brefus(?:es|ing)\b|\bcontinues? to (?:alert|fail|error|break|show)\b|\bone limit\b|\blimitation\b",
    re.I)
# An explicit scope limit in the user's ask: a diagnosis is what they asked for, so report_only never applies.
SCOPE_LIMIT_RE = re.compile(
    r"\b(?:analysis|analy[sz]e|diagnos(?:is|e)|research|investigat(?:e|ion)|report|read)[- ]only\b"
    r"|\bjust (?:tell me (?:why|what|how|if|whether)|explain|answer)\b|\bonly (?:tell|explain|want to know|report)\b"
    r"|\bplan (?:it )?first\b|\bdon'?t (?:change|touch|fix|edit|modify|mutate|ship|deploy) (?:anything|it|this|that|a thing)\b"
    r"|\bdo not (?:change|touch|fix|edit|modify|mutate) (?:anything|it|this|that)\b"
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
    r"|\b(?:that'?s|it'?s|this is) (?:for )?" + _WHO + r"(?:'s)? (?:to fix|problem|job|change)\b",
    re.I)
# Tool-log evidence that the agent checked or messaged an owner this turn: the tool itself (`-> kanban_show(`), the
# deferred `tool_call` wrapper (`"name": "cross_task"`), or the CLI form in a terminal call (`hermes kanban show`).
_OWNER_TOOLS = (r"(?:cross_session|cross_task|session_mentions|kanban_(?:show|comment|create|list|link|block|unblock)|"
                r"delegate_task|process_manage|process|chat_history_lookup|inbox_post)")
OWNER_CHECK_RE = re.compile(
    r"^\s*->\s*" + _OWNER_TOOLS + r"\("
    r"|[\"']name[\"']\s*:\s*[\"']" + _OWNER_TOOLS + r"[\"']"
    r"|^\s*->\s*terminal\(.*\bhermes kanban\b.*\b(?:show|comment|create|list|tail)\b"
    r"|^\s*->\s*terminal\(.*\bhermes sessions?\b", re.I)
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


# status_only: the user asks why it's waiting / what's going on / to finish it, and the turn only reopened, unblocked,
# commented on or looked at cards (defect agent-38a817308d: four "reopened the card / verified ready" replies while the
# rollout card sat unstarted for 4 days). Deterministic, before the judge: the report_only owner-check exemption
# counts exactly those card operations as acting, so the judge path let all four through.
PROGRESS_ASK_RE = re.compile(
    r"\bwhya?\b[^.?!\n]{0,40}\b(?:wait\w*|block\w*|stuck|slow|tak\w+ so long|still|stall\w*|not (?:done|finished|"
    r"started|running|moving))\b"
    r"|\bwhat(?:'s|’s| is| are)? (?:you |we |it )?(?:doing|going on|happening|the (?:status|hold-?up|delay))\b"
    r"|\bwhats going on\b|\b(?:status|progress)\b\s*\??\s*$|\bany (?:update|news|progress)\b"
    r"|\bis (?:it|this|that) (?:done|finished|live|deployed|merged) yet\b|\bhow long\b|\bwhere are we\b"
    r"|\bstill (?:waiting|blocked|not)\b|\bwhy (?:hasn'?t|isn'?t|aren'?t|haven'?t)\b"
    r"|\b(?:finish|complete|ship|deploy|land) (?:it|this|that|them)\b|\bjust (?:finish|complete|ship|deploy|land)\b"
    r"|\bcan you just\b"
    r"|\bget (?:it|this) done\b|\bmake (?:all )?the (?:corrections|changes|fix(?:es)?)\b", re.I | re.M)
# A wall only the user (or an outside party) can clear, named in the reply: then a status turn is the right answer.
WALL_RE = re.compile(
    r"\bonly you\b|\bneeds? (?:you|your)\b|\byour (?:password|approval|login|2fa|decision|click|hands)\b"
    r"|\b(?:billing|payment|spending limit|outage)\b|\bphysical(?:ly)?\b|\bsign(?:ed)? in\b|\blog(?:ged)? in\b", re.I)
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
    """Evidence string when a progress/finish ask got a turn of card bookkeeping only, else None."""
    if not PROGRESS_ASK_RE.search(user_ask or "") or not (reply or "").strip():
        return None
    kinds = [k for k in (_tool_kind(l) for l in tool_log or []) if k]
    if "work" in kinds or "card" not in kinds:
        return None
    if WALL_RE.search(reply[-1500:]):
        return None
    ops = [l.strip()[3:].split("(", 1)[0] for l in tool_log or [] if _tool_kind(l) == "card"]
    return f"asked \"{(user_ask or '').strip()[:80]}\"; this turn only ran {', '.join(dict.fromkeys(ops))}"


STATUS_ONLY_NUDGE = (
    "[coach] status_only: the user asked about progress or to finish ({evidence}), and this turn only reopened, "
    "unblocked, commented on or looked at cards. A card being ready or a worker being alive is not progress. Before "
    "ending: do the next real step on the work yourself now (edit, test, commit, merge, deploy, prove it on the real "
    "path), or name the one specific wall that stops you (the exact error, or the decision only the user can make). "
    "Do not reopen, re-review or add another watcher card.")


RULES = ("handoff", "confirm", "unverified", "unwatched", "report_only", "status_only")
DEFAULT_RULES = ("handoff", "confirm", "unwatched", "report_only", "status_only")
# Per-rule judge thresholds (COACH_REPLY_THRESHOLDS overrides; other rules use COACH_REPLY_THRESHOLD).
# report_only: tuned on the training split of the 2026-10-27 labeled set (README, "report_only").
DEFAULT_THRESHOLDS = {"report_only": 0.5, "report_only_screen": 0.85}


def scope_limited(user_ask: str) -> bool:
    """The user explicitly limited the turn to analysis ("analysis only", "just tell me", "don't change anything")."""
    return bool(SCOPE_LIMIT_RE.search(user_ask or ""))


def prefilter(reply: str) -> list[str]:
    """Which rules this reply is a candidate for (cheap, recall-oriented)."""
    r = reply or ""
    tail = r[-700:]
    out = []
    if HANDOFF_RE.search(r):
        out.append("handoff")
    if CONFIRM_RE.search(tail):
        out.append("confirm")
    if DONE_RE.search(r[:600]) or DONE_RE.search(tail):
        out.append("unverified")
    if WAIT_RE.search(r[-1200:]):
        out.append("unwatched")
    if REPORT_RE.search(r[-1500:]):
        out.append("report_only")
    return out


# Tools that, called this turn, leave something that will wake the session later.
WAKE_TOOLS = ("delegate_task", "kanban_create", "kanban_schedule", "cronjob")


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


JUDGE_SYS = """You review an AI agent's final message for a turn, right before the turn ends, for a power user who wants
the agent to self-serve and to verify its own work. Judge ONLY from the evidence shown. Score five independent
probabilities (0-1):

- handoff: the message asks the USER to perform a step (click, sign in, run a command, paste, approve a dialog,
  restart an app, plug something in...) that the agent could plausibly have done itself with shell, browser
  automation, saved credentials, a password vault, a token broker, ssh, or background desktop control. Physical
  actions (plugging hardware, being at a device), the user's own judgment/taste, money/legal decisions, secrets
  only the user knows, and macOS privacy permission grants are NOT handoffs (score low).
  A CONDITIONAL ask ("if X isn't set up, you'll need to...", "may still need you if...") counts as a handoff when
  the agent could have checked X itself (membership, login state, a config value) and didn't: score it high.
- confirm: the message ends by asking permission for a next step that is reversible and clearly within what the
  user already asked for (e.g. "Want me to run the tests / apply the fix / commit it?"). Asking before something
  irreversible, external-facing (sending messages/emails, spending money, deleting data, force-push, publishing),
  or genuinely ambiguous in intent is NOT a needless confirm (score low). A plan presented for an explicit go
  when the user asked for a plan first is NOT needless.
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
    the agent checked that owner this turn (e.g. kanban_show / process poll) and it is actually running.
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
  Score it LOW when: the user explicitly limited scope ("analysis only", "just tell me", "plan first", "don't change
  anything"); the problem genuinely needs the user's hands, password, money, a physical device or a decision only
  they can make, and the message says so; the message is a plan the user asked for; or no unresolved problem is left.

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
owner/card has it" does not.

Return ONLY one JSON object:
{"problem": true|false, "agent_fixable": true|false|null, "outcome": "<outcome>", "user_wanted_action": true|false, "quote": "<=20 words from the reply"}

- problem: true if the reply mentions a defect, failure, bug, blocker, stale/wrong state, or something not working or not done that is STILL UNRESOLVED when the reply ends ("not fixed", "still broken", "remaining work", "X is outdated", "Y failed", "waiting on CI"). false if no unresolved problem is mentioned.
- agent_fixable: when problem is true: could an agent with the access above plausibly fix it or start fixing it itself (true), or does it genuinely need the user (password, physical action, a decision only he can make, money) or an outside party (false)? null when problem is false.
- outcome (pick the best fit for the unresolved problem the reply is mainly about):
  "fixed": the agent itself fixed or changed the thing during this turn (edited code/config, ran the command that resolves it), even if final verification is pending.
  "started": the agent started a concrete fix that is now in progress and says so (spawned/assigned a worker, card, subagent or background job specifically to fix it, opened a PR).
  "proposed": no fix yet, but the reply proposes a concrete solution or plan (what would change and how), possibly asking for a go-ahead.
  "offered": only a vague offer ("want me to fix it?", "I can look into it") with no concrete solution.
  "report_only": the reply describes or diagnoses the problem, or says it is not fixed / can be ignored / is owned by an existing task / was relayed to someone, WITHOUT fixing it, starting a new fix, or proposing a concrete solution.
  "na": no unresolved problem in the reply (a pure answer, finished work, chit-chat).
- user_wanted_action: true if the message asked the agent to do, fix, build or change something (not just a question, status check or FYI)."""
CONFIRM_FLOOR = 0.75  # screen report_only p at or above this gets the second opinion (tuned on train)
CONFIRM_DAILY_CAP = 100


def confirm_system() -> str:
    prof = profile_text()
    return CONFIRM_SYS + (f"\n\nUSER ENVIRONMENT (what this user's agents can reach):\n{prof}" if prof.strip() else "")


def confirm_prompt(user_ask: str, rich_log: list[str], reply: str) -> str:
    log = "\n".join(rich_log[-40:]) or "(no tool calls this turn)"
    return (f"USER'S REQUEST (latest):\n{(user_ask or '')[:1500]}\n\nTOOL LOG THIS TURN (oldest first, real arguments, "
            f"truncated):\n{log[:7000]}\n\nAGENT'S FINAL MESSAGE:\n{(reply or '')[:4000]}")


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


def aux_confirm(system: str, prompt: str) -> str:
    """Aux task `coach_report_only` (auxiliary.coach_report_only.{provider,model} in shared-config.json)."""
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="coach_report_only", messages=[{"role": "system", "content": system},
                                                       {"role": "user", "content": prompt}],
                    max_tokens=1000, temperature=0, timeout=90)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


def _hermes_home():
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return get_hermes_home()
    except Exception:
        from pathlib import Path
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


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


def judge_prompt(user_ask: str, tool_log: list[str], reply: str) -> str:
    log = "\n".join(tool_log[-40:]) or "(no tool calls this turn)"
    return (f"USER'S REQUEST (latest):\n{(user_ask or '')[:1500]}\n\nTOOL LOG THIS TURN (oldest first, truncated):\n"
            f"{log[:5000]}\n\nAGENT'S FINAL MESSAGE:\n{(reply or '')[:4000]}")


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
                    "criteria, linked to this chat) and name the card. If it really is the user's call, propose the "
                    "exact fix. Don't end on the diagnosis."),
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
    "that output and ask for recovery with action_request. Don't end on the diagnosis.")


def kanban_db_down(tool_log: list[str]) -> bool:
    """True when this turn's tool log shows a Kanban call refused because the board couldn't be read."""
    return any(KANBAN_DB_DOWN.search(line or "") for line in tool_log or [])


def nudge_for(rule: str, evidence: str, kanban_down: bool = False) -> str:
    tmpl = REPORT_ONLY_KANBAN_DOWN if rule == "report_only" and kanban_down else NUDGES[rule]
    return tmpl.format(evidence=evidence or "see your last message")


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
        if k.strip() in RULES or k.strip() == "report_only_screen":
            try:
                ths[k.strip()] = float(v)
            except ValueError:
                pass
    return {"rules": rules, "threshold": th, "thresholds": ths}


def aux_judge(system: str, prompt: str) -> str:
    """Aux task `coach_reply` (auxiliary.coach_reply.{provider,model}; default: the auto aux model)."""
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="coach_reply", messages=[{"role": "system", "content": system},
                                                 {"role": "user", "content": prompt}],
                    max_tokens=2000, temperature=0, timeout=60)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


def rule_threshold(rule: str, verdict: dict, thresholds: dict, default: float) -> float:
    """The threshold a verdict is held to: report_only uses its second-opinion threshold only when the strong model
    scored it, otherwise report_only_screen."""
    if rule == "report_only":
        if verdict.get("report_only_src") == "confirm":
            return thresholds.get("report_only", default)
        return thresholds.get("report_only_screen", thresholds.get("report_only", default))
    return thresholds.get(rule, default)


def check(reply: str, user_ask: str, tool_log: list[str], llm: Callable[[str, str], str],
          threshold: float = 0.8, only: Optional[list[str]] = None,
          changed_paths: Optional[list] = None,
          wake: Optional[Callable[[], list]] = None,
          screen: Optional[Callable[[list], Optional[dict]]] = None, screen_below: float = 0.2,
          shadow: bool = False, log: Optional[Callable[[dict], None]] = None,
          thresholds: Optional[dict] = None,
          confirm: Optional[Callable[[], Optional[tuple]]] = None, confirm_floor: float = 0.5,
          kanban_down: Optional[bool] = None) -> Optional[dict]:
    """Return {"rule", "p", "evidence", "nudge"} for the top rule over threshold, else None.
    `thresholds` ({rule: th}) overrides `threshold` per rule; with it, the top rule among those over their own
    threshold wins.
    `confirm() -> (p, evidence) | None` is the report_only second opinion (aux task `coach_report_only`, real tool
    arguments): when the screen's report_only p is >= `confirm_floor`, its p replaces the screen's and is compared with
    thresholds["report_only"]; None (daily cap hit, error) falls back to the screen's p against
    thresholds["report_only_screen"] (default: the report_only threshold). Without `confirm` nothing changes.
    `wake()` lists the session's wake sources; any source drops the `unwatched` candidate before the judge runs
    (no wake callable = can't tell, so `unwatched` is skipped).
    `screen(candidates) -> {rule: p} | None` (a System One model) drops screened candidates below `screen_below`
    before the LLM judge; when no candidate survives, the LLM is skipped. `shadow` = log only, drop nothing."""
    scoped = scope_limited(user_ask)
    if (not only or "report_only" in only) and not scoped:
        phrase = unverified_owner(reply, tool_log)
        if phrase:  # deterministic: no judge call, no AI score at the edge of its bar
            down = kanban_db_down(tool_log) if kanban_down is None else kanban_down
            return {"rule": "report_only", "p": 1.0, "evidence": phrase, "reason": "unverified_owner",
                    "verdict": {"report_only": 1.0, "report_only_src": "unverified_owner", "evidence": phrase},
                    "nudge": OWNER_NUDGE.format(evidence=phrase[:200]) + (OWNER_KANBAN_DOWN if down else "")}
    if (not only or "status_only" in only) and not scoped:
        ev = status_only(user_ask, reply, tool_log)
        if ev:  # deterministic: the judge path's owner-check exemption counts card bookkeeping as acting
            return {"rule": "status_only", "p": 1.0, "evidence": ev, "reason": "status_only",
                    "verdict": {"status_only": 1.0, "evidence": ev},
                    "nudge": STATUS_ONLY_NUDGE.format(evidence=ev[:200])}
    cands = [r for r in prefilter(reply) if not only or r in only]
    if "report_only" in cands and scoped:
        cands.remove("report_only")
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
        kept = [r for r in cands if r not in sp or sp[r] >= screen_below]
        if not shadow:
            if not kept:
                if log:
                    log({"name": "coach_reply", "screen": sp, "skipped_llm": True})
                return None
            cands = kept
    v = parse_verdict(llm(judge_system(), judge_prompt(user_ask, tool_log, reply)))
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
    if "report_only" in cands and confirm is not None and v["report_only"] >= confirm_floor:
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
        else:
            v["report_only_src"] = "screen_fallback"
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
    return {"rule": best, "p": v[best], "evidence": v["evidence"], "verdict": v,
            "nudge": nudge_for(best, v["evidence"], kanban_down=down)}
