"""ask-gate core: pure helpers (ask detection, live card state, answered-asks ledger, tool log, capability sheet).

No Hermes runtime needed, so every function here is unit-testable with fakes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover
    _PT = None

# --------------------------------------------------------------------------------------------------
# ask detection (widened from open-asks): a sentence that asks David to do / decide / provide something,
# or says something is waiting on him.
# --------------------------------------------------------------------------------------------------
_ASK = re.compile(
    r"(^|\s)(want me to|should i|shall i|do you want( me)? to|would you like( me)? to|ok(ay)? (for me )?to|"
    r"which (one|option) do you want|can you|could you|would you|will you|are you able to)\b[^?]*\?"
    r"|\b(needs?|waiting (on|for)|still (needs?|waiting (on|for))|blocked on) (your|you\b|david)\b"
    r"|\bneeds? (you|david) to\b"
    r"|\bif you'?d like me to\b|\bonly open (item|question)\b"
    r"|\b(it'?s|that'?s|this is|is|are) (up to|your call|your decision|on you)\b"
    r"|\byour (call|decision|yes/no|yes or no|go-ahead|go/no-go|approval|sign-?in|password)\b"
    r"|\b(david'?s|his) call\b"
    r"|\bneeds? david\b"
    r"|\b(you|you'll|you will) (need|have) to (sign|log|click|approve|revoke|cancel|add|enable|grant|create|run|"
    r"open|paste|copy|install|decide|pick|choose|confirm|tap)\b"
    r"|\byou can (revoke|cancel|add|enable|grant|do it yourself|sign in)\b"
    r"|\b(reply|answer|say|tell me) (with )?[\"'“]?(yes|no|go)\b"
    r"|\b(let me know|tell me) (if|whether|which|when)\b"
    r"|\bplease (confirm|approve|decide|pick|choose|reply|sign|add|click|revoke|cancel)\b"
    r"|\bneeds? (?:a )?manual nudge on (?:the )?(?:kanban )?board\b"
    r"|\b(unblock|unblocks) (it|the card|t_[0-9a-f]{8})\b",
    re.I)
# Mentions that are not a live ask: hypotheticals, explicit "nothing for you", past/third person.
_NOT_ASK = re.compile(
    r"\b(if you (ever|later|still|prefer)|unless|in case|whenever you|no action (is )?needed|"
    r"none of (?:them|the (?:cards|tasks)) needs? you|"
    r"nothing (is )?(waiting|needed|needs?|left) (on|from|for) you|nothing (for|to do for) you|"
    r"nothing in this (work|card) is waiting|doesn'?t need you|don'?t need (anything|you)|"
    r"you (already|have already)|you (said|answered|approved|asked|told)|no (more|further) (input|action)|"
    r"(isn'?t|is not|aren'?t|are not) waiting on you|not waiting on you|the card asks|it asked|"
    r"i'?ll stop asking|nothing from this work is waiting|nothing\b[^.]{0,60}\bneeds? (you|david)|"
    r"no\b[^.]{0,40}\bwaiting on you|(was|were|had been|had) (waiting|blocked) (on|for) (you|david)|never (create|say|tell|ask|put)|"
    r"nothing\b[^.]{0,60}\b(?:from|on) your (?:side|end))\b",
    re.I)
# --- shapes added after the week-of-10-12 corrections (imperatives, user-named headings, "I can't" claims) ---
_VERB = (r"(?:open|check|tap|click|press|sign in|log in|go to|change|enable|disable|raise|switch (?:off|on)|"
         r"turn (?:off|on)|install|run|delete)")
# "Open items", "Run log" and the like are nouns, not instructions
_VERB_NOT_NOUN = r"(?!\s+(?:\d|items?|questions?|issues?|prs?|cards?|loops?|logs?|times?|counts?|results?|ids?)\b)"
_IMPERATIVE = re.compile(
    r"^(?:[^.!?:]{1,70}:\s+)?"                                                    # "To confirm: " / "Echo Dot pod: "
    r"(?:(?:in|on|if|once|when|after|then|otherwise|from|at|before|for)\b[^,.!?]{0,80},\s+)?"   # "On the iPhone 8, "
    r"(?:(?:and|then|also|just)\s+)*(?:please\s+)?" + _VERB + r"\b" + _VERB_NOT_NOUN, re.I)
_PLEASE_VERB = re.compile(r"\bplease\s+" + _VERB + r"\b" + _VERB_NOT_NOUN, re.I)
# headings that hand the reader steps: "Your checklist", "What I need from you:", "To confirm:", "@user", "For you"
_USER_HEAD = re.compile(
    r"^\W*(?:your (?:checklist|steps|turn|action items?|to-?dos?)|what i need from you|to confirm|to clear it|"
    r"@user|for you|decision for you|needs you|on you)\b[^:\n]{0,40}(?::|$)", re.I)
# "I can't" claims: the gap stays with the reader unless this turn's tool output shows the attempt failing
_CANT = re.compile(
    r"\bI (?:can['\u2019]?t|cannot) (?:change|check|do|open|fix|reach)\b"
    r"|^\W*Limitation:|\bwould need\b[^.]*\b(?:doesn['\u2019]?t|don['\u2019]?t) have\b"
    # "it's a settings change only you can make" (bb-tuner, week of 10-12: David answered "merge it yourself")
    r"|\bonly (?:you|david) (?:can|could) (?:make|do|fix|change|approve|grant|add|enable|set|flip|merge|review|run|unlock|"
    r"create|sign|register)\b"
    r"|\bonly (?:you|david) (?:have|has) (?:the )?(?:access|admin|rights|permission)\b", re.I)
_HOWTO_ASKED = re.compile(
    r"\bhow (?:do|can|would|should|could|to|am i supposed)\b|\bwalk me through\b|\bsteps?\b|\binstructions?\b|"
    r"\bshow me how\b|\bteach me\b|\bwhat should i do\b", re.I)
_CARD_ID = re.compile(r"\bt_[0-9a-f]{8}(?:[0-9a-f]{8})?\b")  # legacy 8-hex and current 16-hex ids
# worker handoff leftovers that point at David
_LEFTOVER = re.compile(
    r"\bdavid'?s (call|decision|choice)\b|\bup to david\b|\bdavid (should|must|needs to|will need to|can)\b"
    r"|\bneeds? david\b|\bfor david to\b|\bleft (for|to) david\b|\bask david\b|\bneeds? (your|the user'?s) "
    r"(call|decision|approval)\b|\bthe user (should|must|needs to)\b", re.I)
_DAVID_ACTION = re.compile(
    r"\bneeds? (you|david)\b|\bdavid'?s call\b|\bdavid (must|should|needs? to)\b|\bwaiting on (you|david)\b"
    r"|\bneeds? your\b", re.I)
# A handoff that hands the reader a step without naming David (10-30: "need an unblock from your side",
# "Please run this from your side: `ssh …`"). Worker handoffs only; "please <verb>" is checked via _PLEASE_VERB.
_HANDBACK = re.compile(
    r"\b(?:needs?|requires?)\b[^.!?]{0,60}\b(?:from|on) your (?:side|end)\b"
    r"|\b(?:run|do|unblock|approve|merge|restart|click|open)\b[^.!?]{0,60}\b(?:from|on) your "
    r"(?:side|end|terminal|machine|mac|laptop)\b"
    r"|\bin your (?:own )?terminal\b", re.I)


def _strip_noise(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    text = re.sub(r"`[^`\n]*`", "X", text)
    return text


_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_BOLD_SUB = re.compile(r"^\s*(?:\*\*[^*\n]+\*\*|#+\s+.+)\s*:?\s*$")


def _clean(x: str) -> str:
    return x.strip(" -*•#|_").replace("**", "").replace("__", "")


def _units(text: str) -> list[tuple[str, bool]]:
    """(unit, forced) pairs. A heading line ending in ':' that is ask-shaped absorbs the list items under it into one
    unit ("Needs you:" carries what it needs). A heading that names the reader ("Your checklist", "@user") makes
    each item under it its own *forced* unit: it is a step for the reader whatever its wording."""
    lines = _strip_noise(text or "").split("\n")
    units: list[tuple[str, bool]] = []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        bare = ln.rstrip("*_ ").lstrip("*_# ")
        if bare.endswith(":") and _ASK.search(ln) and i + 1 < len(lines) and _LIST_ITEM.match(lines[i + 1]):
            items = []
            j = i + 1
            while j < len(lines) and _LIST_ITEM.match(lines[j]) and len(items) < 5:
                items.append(_LIST_ITEM.sub("", lines[j]).strip())
                j += 1
            units.append((_clean(ln + " " + " | ".join(items)), False))
            i = j
            continue
        if _USER_HEAD.match(bare):
            tail = re.sub(r"^[^:]*:\s*", "", bare) if ":" in bare else ""
            if len(tail.strip()) >= 15:          # "To confirm: in the Nest app, open Upstairs -> History."
                first, *rest = [x for x in (_clean(y) for y in re.split(r"(?<=[.!?])\s+", ln)) if x]
                units.append((first, True))
                units.extend((x, False) for x in rest)
                i += 1
                continue
            j, n, intro = i + 1, 0, False
            while j < len(lines) and n < 12:
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                elif _LIST_ITEM.match(lines[j]):
                    units.append((_clean(_LIST_ITEM.sub("", lines[j])), True))
                    n += 1
                    j += 1
                elif _BOLD_SUB.match(nxt):       # a sub-heading inside the checklist
                    j += 1
                elif n == 0 and not intro and len(nxt) < 200:   # one intro line before the first step
                    intro = True
                    j += 1
                else:
                    break
            if n:
                i = j
                continue
        units.extend((x2, False) for x2 in (_clean(x) for x in re.split(r"(?<=[.!?])\s+", ln)) if x2)
        i += 1
    return units


def sentences(text: str) -> list[str]:
    return [u for u, _ in _units(text)]


_COND_LEAD = re.compile(r"^(?:[^.!?:]{1,70}:\s+)?(?:if|in that case|otherwise|then|once|when|after)\b", re.I)
_CHAIN = re.compile(r",\s*(?:and\s+|then\s+)?" + _VERB + r"\b", re.I)


def is_imperative(s: str) -> bool:
    """A sentence that tells the reader to do something. ``please <verb>`` always counts; a bare leading verb
    counts only when it is a fallback/conditional step ("In that case, check ...") or a
    chain of steps ("Click A, open B, then click C"), so plain narration and one-word labels stay out."""
    if _PLEASE_VERB.search(s):
        return True
    if not _IMPERATIVE.match(s):
        return False
    return bool(_COND_LEAD.match(s) or _CHAIN.search(s))


def is_soft_ask(s: str) -> bool:
    """Asks judged with the stricter instruction/claim prompt: any imperative to the reader or "I can't" claim."""
    return bool(_IMPERATIVE.match(s) or _PLEASE_VERB.search(s) or _CANT.search(s))


def asked_how_to(user_msg: str) -> bool:
    return bool(_HOWTO_ASKED.search(user_msg or ""))


def find_asks(text: str, howto: bool = False) -> list[str]:
    """Sentences that hand David something to do, decide or provide. ``howto``: he asked how to do something,
    so imperative steps are the answer, not an ask."""
    out: list[str] = []
    n_imp = 0
    for s, forced in _units(_strip_quotes(text)):
        # This negative status negates only its own "need you" span, not an
        # independent request before/after it. Keep the original for the judge.
        candidate = re.sub(r"\bnone of (?:them|the (?:cards|tasks)) needs? you\b", "X", s, flags=re.I)
        if _NOT_ASK.search(candidate) or s in out:
            continue
        hit = bool(_ASK.search(candidate)) or bool(_CANT.search(candidate))
        if not hit and forced:
            hit = not howto and not _NOT_ASK.search(s)
        if not hit and not howto and n_imp < 8 and is_imperative(candidate):
            hit, n_imp = True, n_imp + 1
        if hit:
            out.append(s[:1200] if forced else s[:600])
    return out


def _strip_quotes(text: str) -> str:
    """Quoted phrases are mentions, not asks (\"needs David\" inside an explanation)."""
    return re.sub(r"[\"“][^\"”\n]{1,80}[\"”]", "X", text or "")


def find_leftovers(text: str) -> list[str]:
    """Sentences in a worker handoff that hand a decision/action to David, named or not ("please run …",
    "… from your side")."""
    out: list[str] = []
    for s in sentences(text):
        hit = _LEFTOVER.search(s) or _DAVID_ACTION.search(s) or _PLEASE_VERB.search(s) or _HANDBACK.search(s)
        if hit and not _NOT_ASK.search(s) and s not in out:
            out.append(s)
    return out


def card_ids(text: str) -> list[str]:
    return sorted(set(_CARD_ID.findall(text or "")))


def says_david_must_act(text: str) -> bool:
    return bool(_DAVID_ACTION.search(text or "") or _LEFTOVER.search(text or ""))


# --------------------------------------------------------------------------------------------------
# paths / time
# --------------------------------------------------------------------------------------------------
def home() -> Path:
    try:
        from hermes_constants import get_hermes_home
    except ImportError:  # Standalone reporting scripts outside Hermes.
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return get_hermes_home()


def boards() -> list[Path]:
    root = home()
    paths = [root / "kanban.db"] + sorted((root / "kanban" / "boards").glob("*/kanban.db"))
    return [p for p in paths if p.is_file()]


def fmt_ts(ts: float | int | None) -> str:
    if not ts:
        return "?"
    d = datetime.fromtimestamp(float(ts), _PT) if _PT else datetime.fromtimestamp(float(ts))
    return d.strftime("%m-%d %H:%M %Z").strip()


def log_event(kind: str, **kw: Any) -> None:
    """Append-only gate log (~/.hermes/logs/ask-gate.jsonl): every verdict, hold and gate failure."""
    try:
        p = home() / "logs" / "ask-gate.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, **kw}, default=str) + "\n")
    except OSError:
        pass


def _ro_connect(path, timeout=10):
    """Read-only open. mode=ro fails on a WAL db while its -wal/-shm is absent (writers delete them on last close),
    so fall back to mode=rw + query_only, which reads the same data and never writes rows."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


# --------------------------------------------------------------------------------------------------
# L1: live card state
# --------------------------------------------------------------------------------------------------
def _ro(p: Path) -> sqlite3.Connection:
    return _ro_connect(p, timeout=2)


def linked_cards(session_id: str, limit: int = 15, db_paths: list[Path] | None = None) -> list[dict]:
    """Cards created by this chat or notifying it (kanban_notify_subs), newest first."""
    out: list[dict] = []
    if not session_id:
        return out
    for p in db_paths if db_paths is not None else boards():
        try:
            c = _ro(p)
            rows = c.execute("select id, title, status, assignee, created_at, completed_at from tasks "
                             "where session_id=? order by created_at desc limit ?", (session_id, limit)).fetchall()
            try:  # cards created elsewhere but notifying this chat count too (matches the sidebar count)
                rows += c.execute("select t.id, t.title, t.status, t.assignee, t.created_at, t.completed_at "
                                  "from tasks t where t.id in (select task_id from kanban_notify_subs "
                                  "where chat_id=?) order by t.created_at desc limit ?", (session_id, limit)).fetchall()
            except sqlite3.Error:
                pass
            for r in rows:
                out.append({"id": r[0], "title": r[1], "status": r[2], "assignee": r[3],
                            "created": r[4], "completed": r[5], "db": str(p)})
            c.close()
        except sqlite3.Error:
            continue
    seen: set[str] = set()
    out = [x for x in sorted(out, key=lambda x: x.get("created") or 0, reverse=True)
           if not (x["id"] in seen or seen.add(x["id"]))]
    return out[:limit]


def card_status(ids: list[str], db_paths: list[Path] | None = None) -> dict[str, dict]:
    """Live status of arbitrary card ids (mentioned in a reply), any board."""
    out: dict[str, dict] = {}
    if not ids:
        return out
    ph = ",".join("?" * len(ids))
    for p in db_paths if db_paths is not None else boards():
        try:
            c = _ro(p)
            for r in c.execute(f"select id, title, status, completed_at, result from tasks where id in ({ph})", ids):
                out[r[0]] = {"id": r[0], "title": r[1], "status": r[2], "completed": r[3],
                             "result": (r[4] or "")[:300]}
            c.close()
        except sqlite3.Error:
            continue
    return out


def placed_copy(db: str | Path, task_id: str) -> dict | None:
    """The copy placement forwarded to another gateway: {gateway, task_id} from the card's events, else None."""
    try:
        c = _ro(Path(db))
        rows = c.execute("select kind, payload from task_events where task_id=? and kind in "
                         "('placement_deferred','placement_forwarded','placement_cancelled',"
                         "'placement_archive_forwarded') order by id", (task_id,)).fetchall()
        c.close()
    except sqlite3.Error:
        return None
    gw = rid = None
    for kind, raw in rows:
        try:
            p = json.loads(raw or "{}")
        except ValueError:
            continue
        if kind == "placement_cancelled":
            gw = rid = None
        elif kind == "placement_deferred":
            gw = p.get("target") or gw
        else:
            gw = p.get("gateway") or gw
            rid = p.get("remote_task_id") or rid
    return {"gateway": gw, "task_id": rid} if gw and rid else None


def hub_replica(gateway: str, timeout: float = 8) -> list[dict] | None:
    """Tasks of another gateway as the hub replica sees them (`hermes-hub tasks --gw X --all --json`)."""
    import subprocess
    try:
        r = subprocess.run(["hermes-hub", "tasks", "--gw", gateway, "--all", "--json"],
                           capture_output=True, text=True, timeout=timeout)
        data = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return data if isinstance(data, list) else None


def render_cards(cards: list[dict], replica=None) -> str:
    """One line per card; a card whose copy was forwarded to another gateway gets `-> gw:id <live status>`.
    ``replica(gateway)`` returns that gateway's task dicts (default: the hub replica); None = unreachable."""
    replica = replica or hub_replica
    cache: dict[str, list[dict] | None] = {}
    lines = []
    for c in cards:
        when = f", completed {fmt_ts(c['completed'])}" if c.get("completed") else ""
        line = f"- {c['id']} [{c['status']}{when}] {str(c.get('title') or '')[:90]}"
        pc = placed_copy(c["db"], c["id"]) if c.get("db") else None
        if pc:
            gw = pc["gateway"]
            if gw not in cache:
                try:
                    cache[gw] = replica(gw)
                except Exception:
                    cache[gw] = None
            rows = cache[gw]
            hit = next((t for t in rows or [] if t.get("id") == pc["task_id"]), None)
            live = (hit or {}).get("status") or ("not in replica" if rows is not None else "status unknown")
            line += f" -> {gw}:{pc['task_id']} {live}"
        lines.append(line)
    return "\n".join(lines)


def l1_block(session_id: str, ledger: "Ledger | None", db_paths: list[Path] | None = None) -> str | None:
    cards = linked_cards(session_id, db_paths=db_paths)
    answered = ledger.relevant(session_id, limit=8) if ledger else []
    stopped = ledger.workflow_stopped(session_id) if ledger else False
    if not cards and not answered and not stopped:
        return None
    parts = ["[ask-gate] Live state, read from the database just now. It outranks anything you remember about "
             "these cards; completion notices reach the chat late."]
    if stopped:
        parts.append("The current workflow is terminal by an explicit user stop. Do not execute it, "
                     "request action, or reopen it. Only an explicit current-workflow resume can lift this state; "
                     "resuming does not grant approvals or clear retired account/action records.")
    if cards:
        parts.append("Cards linked to this chat:\n" + render_cards(cards))
    if answered:
        parts.append("Asks David has already answered or dismissed (do not ask these again):\n" + "\n".join(
            f"- {a['topic'][:120]} -> {a['answer'][:120]} ({fmt_ts(a['ts'])})" for a in answered))
    parts.append("Before saying anything waits on David, check it is true now (kanban_show / the tool that proves "
                 "the premise). If you can do it yourself (token-broker scopes, ssh, signed-in browser), do it.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------------------------------
# L4: answered-asks ledger (sqlite under ~/.hermes/state, not synced)
# --------------------------------------------------------------------------------------------------
def answer_lineage(session: str) -> list[str]:
    """Follow compression only, never delegated children or unrelated chats."""
    ids = [session]
    seen = {session}
    try:
        c = _ro_connect(home() / "state.db", timeout=2)
        try:
            while True:
                row = c.execute("select parent_session_id, source from sessions where id=?", (ids[-1],)).fetchone()
                if not row or row[1] == "subagent" or not row[0] or row[0] in seen:
                    break
                parent = c.execute("select end_reason from sessions where id=?", (row[0],)).fetchone()
                if not parent or "compress" not in str(parent[0] or "").lower():
                    break
                ids.append(row[0])
                seen.add(row[0])
        finally:
            c.close()
    except sqlite3.Error:
        pass
    return ids


def retired_matches(asks: list[str], rows: list[dict]) -> dict[int, dict]:
    """Exact normalized action identity; token overlap is not an entity key.

    A different account or action must not inherit another action's retirement.
    Paraphrases remain for semantic review, not deterministic suppression.
    """
    def key(text):
        return " ".join(text.split())
    retired = {key(r["topic"]): r for r in rows if r.get("retired")}
    return {i: {"i": i, "verdict": "already_answered", "how": "",
                "reason": "Recorded terminal user answer: " + retired[key(a)]["answer"],
                "retired": True}
            for i, a in enumerate(asks) if key(a) in retired}


def terminal_answer(answer: str) -> bool:
    """Conservative terminal-fact recognition, not an intent/approval parser.

    Negation, questions, future and conditional statements must not close work.
    Only recorded answers enter here; arbitrary tool text is not a user answer.
    """
    text = answer.lower().replace("’", "'")
    if re.search(r"\?|\b(?:not|never|if|unless|might|may|could|would|will)\b|n't\b", text):
        return False
    return bool(re.search(
        r"\b(?:is|was|are|were|been|already)\s+(?:revoked|cancelled|canceled|completed|impossible)\b"
        r"|\b(?:i|we)\s+(?:have\s+)?(?:revoked|cancelled|canceled|completed)\b", text))


# --------------------------------------------------------------------------------------------------
# repeat asks (self-heal agent-31a66d274a): the same ask restated reply after reply
# --------------------------------------------------------------------------------------------------
REPEAT_WINDOW_S = 48 * 3600
REPEAT_MAX_SHOWN = 2  # a chat may see the same ask twice in the window; the 3rd showing is held
# Only an ask that has been standing this long counts as a restatement: a step re-stated a few minutes apart while
# the user works through it live ("tap X once more") is guidance, not a nag (replay: 27 holds -> see README).
REPEAT_MIN_AGE_S = 3600
_TOPIC_STOP = set("""
a about above after again all also am an and any are as ask asked asking asks at be because been before being both but
by can could did do does doing done each either else for from further get gets got had has have having he her here hers
him his how i if in into is it its just let lets like make me more most my need needed needs no nor not now of off on
once one only or other our out over own please really right same say says see she should since so some still such than
that the their them then there these they this those through to too under until up us very want wants was we were what
when where whether which while who why will with would yes you your yours ok okay sure also either call decide decision
decisions item items open waiting wait thing things step steps todo needed reply chat card cards kanban task tasks
hermes david user whenever let's i'd i'll you'd you'll i'm it's that's don't tell know again back ready more
leave left done when
""".split())
_FACT = re.compile(
    r"\$\s?\d[\d,.]*k?|\b\d[\d,.]*\s?(?:usd|dollars?|eur|gbp)\b|\b\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?\b"
    r"|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b|\b\d+\s?(?:min(?:ute)?s?|h|hrs?|hours?|days?|weeks?)\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b|\b(?:today|tomorrow|tonight)\b"
    r"|\b(?:deadline|expir\w*|due|cost\w*|charg\w*|price\w*|bill\w*|invoice\w*|fail\w*|broke\w*|disabl\w*"
    r"|suspend\w*|outage\w*|error\w*|abus\w*|compromis\w*)\b", re.I)
_TOKEN = re.compile(r"t_[0-9a-f]{8,}|https?://\S+|[~./]?[\w][\w./~#+-]*", re.I)
_HEAD_PREFIX = re.compile(r"^\s*(?:still\s+)?(?:waiting on you|needs you|for you|your call|open asks?|"
                          r"what i need from you|to confirm)\s*:\s*", re.I)


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _is_ident(tok: str) -> bool:
    return bool(re.search(r"\d|/|~|#|\.\w", tok)) and not _FACT.fullmatch(tok)


def topic_tokens(text: str) -> set[str]:
    """Content words + identifiers (paths, card ids, numbers, urls) of one ask item, minus ask boilerplate and
    fact tokens (dates, money, status words): those are what a repeat may add, not what names the topic."""
    s = _FACT.sub(" ", _HEAD_PREFIX.sub("", text or ""))
    out: set[str] = set()
    for raw in _TOKEN.findall(s):
        tok = raw.lower().strip(".,;:!?()[]{}'\"`*-")
        if not tok or tok in _TOPIC_STOP:
            continue
        if _is_ident(tok):
            out.add(tok)
        elif len(tok) >= 3 and tok.isascii():
            out.add(_stem(tok))
    return out


def fact_tokens(text: str) -> set[str]:
    return {" ".join(m.lower().split()) for m in _FACT.findall(text or "")}


def same_topic(a: str, b: str) -> bool:
    """Conservative: a shared card id, or >=2 shared content tokens covering >=60% of the shorter ask, and never
    when both name identifiers (PR 10 vs PR 12, two different paths) that share none."""
    ta, tb = topic_tokens(a), topic_tokens(b)
    ia, ib = {t for t in ta if _is_ident(t)}, {t for t in tb if _is_ident(t)}
    if {t for t in ia if t.startswith("t_")} & {t for t in ib if t.startswith("t_")}:
        return True
    if ia and ib and not (ia & ib):
        return False
    shared = ta & tb
    return len(shared) >= 2 and len(shared) / max(1, min(len(ta), len(tb))) >= 0.6


def ask_items(asks: list[str]) -> list[str]:
    """A list heading's items arrive joined with ' | ' in one unit; each item is its own ask."""
    out: list[str] = []
    for a in asks:
        for part in (a or "").split(" | "):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def ask_context(item: str, reply: str) -> str:
    """The paragraph of the reply that carries this ask item (its new facts often sit in the next sentence)."""
    probe = " ".join((item or "").split())[:40]
    for para in re.split(r"\n\s*\n", reply or ""):
        if probe and probe in " ".join(para.split()):
            return para
    return item or ""


def repeated_asks(items: list[str], shown: list[dict], user_msgs: list[str] | None = None,
                  max_shown: int = REPEAT_MAX_SHOWN, reply: str = "", now: float | None = None) -> list[dict]:
    """Items this chat already showed >= max_shown times (distinct delivered replies) in the window, first at least
    REPEAT_MIN_AGE_S ago, that add no new fact and that the user's last message did not ask about.
    [{item, n, last_ts}]"""
    last_user = (user_msgs or [""])[-1] if user_msgs else ""
    now = time.time() if now is None else now
    out = []
    for item in items:
        prior = [r for r in shown if same_topic(item, r["text"])]
        replies = {r.get("reply") or f"{r['ts']}" for r in prior}
        if len(replies) < max_shown or now - min(r["ts"] for r in prior) < REPEAT_MIN_AGE_S:
            continue
        seen_facts: set[str] = set()
        for r in prior:
            seen_facts |= fact_tokens(r["text"]) | set(r.get("facts") or ())
        if fact_tokens(ask_context(item, reply)) - seen_facts:
            continue
        if last_user and same_topic(item, last_user):
            continue
        out.append({"item": item, "n": len(replies), "last_ts": max(r["ts"] for r in prior)})
    return out


def repeat_nudge(reps: list[dict]) -> str:
    lines = [f"- \"{r['item'][:200]}\" (asked {r['n']} times, last at {fmt_ts(r['last_ts'])})" for r in reps[:5]]
    n = max(r["n"] for r in reps)
    return ("ask-gate: your reply was NOT delivered. It restates an ask this chat has already shown:\n"
            + "\n".join(lines) + "\n"
            f"Asked {n} times, last at {fmt_ts(max(r['last_ts'] for r in reps))}, still in For-you or on its card. "
            "Don't restate it. If nothing waits on it, say once that silence means no (or name the default you will "
            "take), then close the card. Restate it only with a new fact (a status change, deadline or cost). Keep "
            "the rest of the reply.")


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else Path(os.environ.get("ASK_GATE_LEDGER") or home() / "state" / "ask-ledger.sqlite")
        self._lock = threading.Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=5)
        c.execute("create table if not exists answered (id integer primary key, ts real, session text, "
                  "topic text, answer text, card_id text)")
        c.execute("create table if not exists open_asks (session text, ts real, text text)")
        c.execute("create table if not exists retired_answers (answer_id integer primary key)")
        c.execute("create table if not exists workflow_state (session text primary key, stopped integer not null, "
                  "evidence text not null, ts real not null)")
        # One row per ask item a delivered reply showed this chat (self-heal agent-31a66d274a): lets the gate count
        # how often a chat already saw the same ask before the judge passes it as "true, unanswered" again.
        c.execute("create table if not exists shown_asks (id integer primary key, ts real, session text, "
                  "reply text, topic text, text text, facts text)")
        return c

    def _init(self) -> None:
        try:
            self._conn().close()
        except sqlite3.Error:
            pass

    def add(self, topic: str, answer: str, session: str = "", card_id: str = "", ts: float | None = None,
            retire: bool | None = None) -> None:
        with self._lock:
            try:
                c = self._conn()
                row = c.execute("insert into answered(ts, session, topic, answer, card_id) values (?,?,?,?,?)",
                                (time.time() if ts is None else ts, session, topic.strip()[:1200],
                                 answer.strip()[:400], card_id))
                if terminal_answer(answer) and retire is not False:
                    c.execute("insert into retired_answers(answer_id) values (?)", (row.lastrowid,))
                c.commit()
                c.close()
            except sqlite3.Error:
                pass

    def recent(self, window_s: float, limit: int = 20) -> list[dict]:
        try:
            c = self._conn()
            rows = c.execute("select ts, session, topic, answer, card_id from answered where ts>=? "
                             "order by ts desc limit ?", (time.time() - window_s, limit)).fetchall()
            c.close()
        except sqlite3.Error:
            return []
        return [{"ts": r[0], "session": r[1], "topic": r[2], "answer": r[3], "card_id": r[4]} for r in rows]

    @staticmethod
    def _scope(c: sqlite3.Connection, session: str) -> None:
        # Connection-local scope avoids SQLite's bound-parameter ceiling for long
        # compression chains. It never changes durable state or sibling sessions.
        c.execute("create temp table answer_scope (session text primary key)")
        c.executemany("insert or ignore into answer_scope values (?)",
                      ((sid,) for sid in answer_lineage(session)))

    @staticmethod
    def _answer_rows(rows) -> list[dict]:
        return [{"ts": ts, "session": sid, "topic": topic, "answer": answer,
                 "card_id": card, "retired": retired is not None}
                for ts, sid, topic, answer, card, retired in rows]

    def relevant(self, session: str, window_s: float = 14 * 86400, limit: int = 20) -> list[dict]:
        """Bounded context: at most limit terminal and limit ordinary records.

        Terminal context has no TTL and cannot be crowded out by ordinary chatter.
        Enforcement MUST use retired_for, not this lossy context sample.
        """
        if limit <= 0:
            return []
        c = self._conn()
        try:
            self._scope(c, session)
            base = ("select a.ts, a.session, a.topic, a.answer, a.card_id, r.answer_id "
                    "from answered a join answer_scope s on s.session=a.session "
                    "left join retired_answers r on r.answer_id=a.id ")
            terminal = c.execute(base + "where r.answer_id is not null "
                                 "order by a.ts desc, a.id desc limit ?", (limit,)).fetchall()
            ordinary = c.execute(base + "where r.answer_id is null and a.ts>=? "
                                 "order by a.ts desc, a.id desc limit ?",
                                 (time.time() - window_s, limit)).fetchall()
            return self._answer_rows(terminal + ordinary)
        finally:
            c.close()

    def retired_for(self, session: str, asks: list[str]) -> list[dict]:
        """Candidate-specific durable facts; bounded by unique candidate count.

        Whitespace normalization matches retired_matches. Punctuation and case
        remain significant; this is not a semantic account-identity resolver.
        """
        if not asks:
            return []
        c = self._conn()
        try:
            self._scope(c, session)
            c.create_function("action_key", 1, lambda text: " ".join(text.split()))
            rows = []
            for key in dict.fromkeys(" ".join(a.split()) for a in asks):
                row = c.execute(
                    "select a.ts, a.session, a.topic, a.answer, a.card_id, r.answer_id "
                    "from answered a join answer_scope s on s.session=a.session "
                    "join retired_answers r on r.answer_id=a.id where action_key(a.topic)=? "
                    "order by a.ts desc, a.id desc limit 1", (key,)).fetchone()
                if row is not None:
                    rows.append(row)
            return self._answer_rows(rows)
        finally:
            c.close()

    def record_workflow_directive(self, session: str, message: str) -> bool:
        """Bind only explicit whole-message current-workflow directives.

        This scope is the chat's compression lineage, not a global project-name
        match. A resume clears workflow suppression, never account retirements
        or security approvals. Ambiguous/quoted/named-other-project text is ignored.
        """
        text = message.strip().replace("’", "'")
        stopped = bool(re.fullmatch(
            r"(?:(?:cancel|stop|halt) this (?:project|workflow)|we are done with this (?:project|workflow))[.!]?",
            text, re.I))
        resumed = bool(re.fullmatch(r"(?:resume|reopen) this (?:project|workflow)[.!]?", text, re.I))
        if not session or not (stopped or resumed):
            return False
        with self._lock:
            c = self._conn()
            try:
                c.execute("insert into workflow_state values (?,?,?,?) on conflict(session) do update set "
                          "stopped=excluded.stopped, evidence=excluded.evidence, ts=excluded.ts",
                          (session, int(stopped), message, time.time()))
                c.execute("delete from open_asks where session=?", (session,))
                c.execute("insert into open_asks values (?,?,?)", (session, time.time(), "[]"))
                c.commit()
            finally:
                c.close()
        return True

    def workflow_stopped(self, session: str) -> bool:
        c = self._conn()
        try:
            self._scope(c, session)
            row = c.execute(
                "select w.stopped from workflow_state w join answer_scope s on s.session=w.session "
                "order by s.rowid limit 1").fetchone()
            return bool(row[0]) if row is not None else False
        finally:
            c.close()

    def record_shown(self, session: str, items: list[str], reply_hash: str = "", ts: float | None = None,
                     reply: str = "") -> None:
        """Remember each ask item a DELIVERED reply showed this chat (held drafts are never recorded), with the
        fact tokens of its paragraph so a later repeat can be told apart by a new fact."""
        items = [i for i in dict.fromkeys(x.strip() for x in items if isinstance(x, str)) if i]
        if not session or not items:
            return
        now = time.time() if ts is None else ts
        with self._lock:
            try:
                c = self._conn()
                c.executemany("insert into shown_asks(ts, session, reply, topic, text, facts) values (?,?,?,?,?,?)",
                              [(now, session, reply_hash, " ".join(sorted(topic_tokens(i))), i[:1200],
                                "\n".join(sorted(fact_tokens(ask_context(i, reply))))) for i in items])
                c.commit()
                c.close()
            except sqlite3.Error:
                pass

    def shown_rows(self, session: str, window_s: float = REPEAT_WINDOW_S) -> list[dict]:
        """Shown ask items in this chat's compression lineage within the window, newest first."""
        try:
            c = self._conn()
        except sqlite3.Error:
            return []
        try:
            self._scope(c, session)
            rows = c.execute("select a.ts, a.session, a.reply, a.text, a.facts from shown_asks a "
                             "join answer_scope s on s.session=a.session where a.ts>=? order by a.ts desc, a.id desc",
                             (time.time() - window_s,)).fetchall()
        except sqlite3.Error:
            return []
        finally:
            c.close()
        return [{"ts": r[0], "session": r[1], "reply": r[2], "text": r[3],
                 "facts": set(filter(None, (r[4] or "").split("\n")))} for r in rows]

    def set_open_asks(self, session: str, asks: list[str]) -> None:
        with self._lock:
            try:
                c = self._conn()
                c.execute("delete from open_asks where session=?", (session,))
                # An explicit empty snapshot shadows inherited pending asks.
                c.execute("insert into open_asks values (?,?,?)", (session, time.time(), json.dumps(asks)))
                c.commit()
                c.close()
            except sqlite3.Error:
                pass

    def open_asks(self, session: str, max_age_s: float = 6 * 3600) -> list[str]:
        return self.pending_snapshot(session, max_age_s) or []

    def pending_snapshot(self, session: str, max_age_s: float = 6 * 3600) -> list[str] | None:
        """None means no recorded snapshot; [] is authoritative, including expiry."""
        ids = answer_lineage(session)
        try:
            c = self._conn()
            try:
                row = None
                for sid in ids:
                    row = c.execute("select ts, text from open_asks where session=? order by ts desc limit 1",
                                    (sid,)).fetchone()
                    if row is not None:
                        break
            finally:
                c.close()
        except sqlite3.Error:
            return []
        if row is None:
            return None
        if time.time() - row[0] > max_age_s:
            return []
        try:
            return list(json.loads(row[1]))
        except ValueError:
            return []


# Cheap pre-check before spending an aux call on classifying the user's message.
_ANSWERISH = re.compile(
    r"\b(done|did it|already|handled|completed?|revoked|cancel+ed|i keep|stop asking|don'?t ask|why (are|do) you "
    r"(keep )?ask|do it yourself|you can do it|you do it|go ahead|yes|yep|yeah|no|nope|skip|not now|never ?mind|"
    r"fine|ok(ay)?|sure|do it|ship it|sounds good|approved?|keep asking|just do)\b", re.I)
_STRONG_DISMISS = re.compile(
    r"\b(stop asking|don'?t ask|why (are|do) you (keep )?ask\w*|already (did|done|handled|revoked|cancel+ed)|"
    r"i keep (completing|telling|saying)|do it yourself|you can do it|have been revoked|was handled)\b", re.I)


def looks_like_answer(msg: str) -> bool:
    return bool(_ANSWERISH.search(msg or ""))


def is_strong_dismissal(msg: str) -> bool:
    return bool(_STRONG_DISMISS.search(msg or ""))


# --------------------------------------------------------------------------------------------------
# tool log (this turn)
# --------------------------------------------------------------------------------------------------
_NON_EVIDENCE = {"kanban_create", "kanban_block", "kanban_complete", "kanban_comment", "kanban_heartbeat",
                 "kanban_link", "kanban_request_review", "kanban_request_changes", "clarify", "todo_list",
                 "send_message"}


class TurnLog:
    """Per-session record of this turn's tool calls, fed by post_tool_call and reset at pre_llm_call."""

    def __init__(self) -> None:
        self._d: dict[str, list[dict]] = {}
        self._users: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def reset(self, session: str) -> None:
        with self._lock:
            self._d[session] = []

    def add(self, session: str, tool: str, args: Any, result: Any) -> None:
        with self._lock:
            lst = self._d.setdefault(session, [])
            lst.append({"tool": tool, "args": _short(args, STORE_CHARS), "result": _short(result, STORE_CHARS),
                        "t": time.time()})
            del lst[:-60]

    def get(self, session: str) -> list[dict]:
        with self._lock:
            return list(self._d.get(session, []))

    def set_users(self, session: str, msgs: list[str]) -> None:
        with self._lock:
            self._users[session] = msgs[-30:]

    def users(self, session: str) -> list[str]:
        with self._lock:
            return list(self._users.get(session, []))


def _short(v: Any, n: int) -> str:
    if isinstance(v, str):
        s = v
    else:
        try:
            s = json.dumps(v, default=str)
        except (TypeError, ValueError):
            s = str(v)
    return clip(re.sub(r"\s+", " ", s).strip(), n)


# Tool results put the verdict last (an HTTP 422 after the request echo, an exit code after the output), so a
# head-only cut hid exactly the failure a human_request cites. Keep both ends.
STORE_CHARS = 1600


def clip(s: str, n: int) -> str:
    """``s`` cut to at most ``n`` chars, keeping its head and its tail (where errors and exit codes are)."""
    if len(s) <= n:
        return s
    head = max(1, n * 2 // 5)
    tail = max(1, n - head - 3)
    return s[:head] + " … " + s[-tail:]


def tools_from_messages(turn_messages: list[Any]) -> list[dict]:
    """Tool calls + results out of an OpenAI-format message list (dicts or attr objects)."""
    def g(o: Any, k: str, d: Any = None) -> Any:
        return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)
    calls: dict[str, dict] = {}
    order: list[dict] = []
    for m in turn_messages or []:
        role = g(m, "role")
        if role == "assistant":
            for tc in g(m, "tool_calls") or []:
                fn = g(tc, "function") or {}
                ent = {"tool": g(fn, "name") or g(tc, "name") or "?",
                       "args": _short(g(fn, "arguments") or g(tc, "arguments") or "", STORE_CHARS), "result": ""}
                calls[str(g(tc, "id"))] = ent
                order.append(ent)
        elif role == "tool":
            ent = calls.get(str(g(m, "tool_call_id")))
            if ent is not None:
                ent["result"] = _short(g(m, "content") or "", STORE_CHARS)
    return order


def merge_tool_logs(*logs: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for lg in logs:
        for e in lg:
            k = (e["tool"], e["args"][:120])
            if k in seen:
                continue
            seen.add(k)
            out.append(e)
    return out


def render_tools(tools: list[dict], limit: int = 40) -> str:
    if not tools:
        return "(no tool calls this turn)"
    return "\n".join(f"- {t['tool']}({clip(t['args'], 220)}) -> {clip(t['result'], 360)}" for t in tools[-limit:])


def evidence_calls(claim: str, tools: list[dict], max_calls: int = 6) -> list[dict]:
    """The evidence-producing calls of this turn that ``claim`` names (same rule as ``checked_matches``), newest
    last. The judge gets these at full stored length so it can read the result the evidence cites."""
    return [t for t in tools if _names_call(claim, t)][-max_calls:]


def render_evidence(claim: str, tools: list[dict]) -> str:
    calls = evidence_calls(claim, tools)
    if not calls:
        return ""
    return ("TOOL CALLS THE REQUEST'S EVIDENCE CITES (longer excerpts, head and tail kept):\n"
            + "\n".join(f"- {t['tool']}({clip(t['args'], 500)}) -> {t['result']}" for t in calls))


# --------------------------------------------------------------------------------------------------
# L3: `Checked:` line must name a tool call made this turn
# --------------------------------------------------------------------------------------------------
_CHECKED = re.compile(r"(?im)^\W*checked\W*\s*:\s*(.+)$")
_STOP = {"this", "that", "with", "from", "have", "output", "result", "status", "command", "tool", "called",
         "ran", "run", "shows", "showed", "show", "returned", "returns", "turn", "line"}


def checked_line(text: str) -> str | None:
    m = _CHECKED.search(text or "")
    return m.group(1).strip() if m and len(m.group(1).strip()) >= 8 else None


def checked_matches(claim: str, tools: list[dict]) -> bool:
    """True if some evidence-producing tool call of this turn is what the claim names.

    A claim matches a call if the call's tool name appears in it, or at least two distinctive claim tokens
    appear in the call's arguments or result. ``kanban_create``/``kanban_block`` and other writes never count.
    """
    return any(_names_call(claim, t) for t in tools)


def _names_call(claim: str, t: dict) -> bool:
    if t["tool"] in _NON_EVIDENCE:
        return False
    low = claim.lower()
    if t["tool"].lower() in low:
        return True
    toks = {w for w in re.findall(r"[a-z0-9_@./:-]{4,}", low) if w not in _STOP}
    hay = (t["args"] + " " + t["result"]).lower()
    return sum(1 for tok in toks if tok in hay) >= 2


def validate_human_request(value: Any) -> dict[str, str]:
    """Mirror the core wire contract without requiring a particular core version."""
    fields = ("action", "why_human", "scope", "evidence", "interaction")
    if not isinstance(value, dict) or any(
        not isinstance(value.get(k), str) or not value[k].strip() for k in fields
    ):
        raise ValueError("human_request requires nonblank action, why_human, scope, evidence and interaction strings")
    request = {k: value[k].strip() for k in fields}
    if request["interaction"] not in {"approval", "reply", "user_terminal", "physical", "external"}:
        raise ValueError("human_request interaction must be approval/reply/user_terminal/physical/external")
    return request


def needs_checked(tool_name: str, args: dict, is_worker: bool) -> tuple[bool, str]:
    """(does this tool call wait on David / need the Checked gate, the text to scan)."""
    if not isinstance(args, dict):
        return False, ""
    if tool_name in {"kanban_block", "kanban_create"} and args.get("human_request") is not None:
        request = validate_human_request(args["human_request"])
        return True, json.dumps(request, ensure_ascii=False)
    if tool_name == "kanban_block":
        reason = str(args.get("reason") or "")
        if says_david_must_act(reason):
            return True, reason
    if tool_name == "kanban_create":
        title = str(args.get("title") or "")
        full = str(args.get("body") or "")
        if args.get("initial_status") == "blocked":
            body = f"{title}\n{full}"
        else:
            # Ready/todo cards only state a goal up front; later paragraphs are
            # spec, quotes or code that may merely mention a blocked card.
            first = re.split(r"\n\s*\n", full.strip(), maxsplit=1)[0] if full.strip() else ""
            body = f"{title}\n{first}"
        if says_david_must_act(body):
            return True, body
    return False, ""


# --------------------------------------------------------------------------------------------------
# capability sheet (generated at runtime, never committed)
# --------------------------------------------------------------------------------------------------
_CAP_CACHE: dict[str, Any] = {"t": 0.0, "text": ""}


def broker_scopes() -> list[str]:
    """Read metadata only; prefer the current user client over stale PATH installs.

    A successful empty catalog is authoritative. Transport/parse failures may
    try a distinct installed client, but never request or vend credentials.
    """
    candidates = [str(Path.home() / ".local/bin/token-broker"), shutil.which("token-broker")]
    seen = set()
    for exe in candidates:
        if not exe:
            continue
        try:
            resolved = str(Path(exe).resolve())
        except (OSError, RuntimeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            result = subprocess.run([exe, "scopes"], capture_output=True, text=True, timeout=8)
            if result.returncode:
                continue
            data = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        names = []
        for row in data:
            name = row.get("name") if isinstance(row, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./:@-]+", name):
                continue
            tier = row.get("tier")
            tier = tier if tier in ("auto", "approval") else "?"
            names.append(f"{name} ({tier})")
        return names
    return []


def ssh_hosts(cfg: Path | None = None) -> list[str]:
    p = cfg or Path.home() / ".ssh" / "config"
    try:
        text = p.read_text()
    except OSError:
        return []
    hosts: list[str] = []
    for m in re.finditer(r"(?im)^\s*host\s+(.+)$", text):
        for h in m.group(1).split():
            if "*" not in h and "?" not in h and h not in hosts:
                hosts.append(h)
    return hosts


def chrome_accounts() -> list[str]:
    """Signed-in Chrome profile emails (names only) from Chrome's Local State, if cheaply readable."""
    p = Path.home() / "Library/Application Support/Google/Chrome/Local State"
    try:
        info = json.loads(p.read_text()).get("profile", {}).get("info_cache", {})
    except (OSError, ValueError):
        return []
    return sorted({v.get("user_name") for v in info.values() if isinstance(v, dict) and v.get("user_name")})


def vault_entries() -> list[str]:
    try:
        from tools.registry import registry  # type: ignore
        out = registry.dispatch("browser_vault", {"action": "list"})
        d = out if isinstance(out, (dict, list)) else json.loads(out)
        items = d.get("entries") if isinstance(d, dict) else d
        return [str(i.get("name") or i.get("site") or i)[:60] for i in (items or [])][:30]
    except Exception:
        return []


def capability_sheet(force: bool = False, ttl_s: float = 600) -> str:
    # Advisory evidence for the LLM judge only. It is NOT a deterministic pre-render
    # capability check: a listed scope/host is never permission and is not enforced here.
    if not force and _CAP_CACHE["text"] and time.time() - _CAP_CACHE["t"] < ttl_s:
        return _CAP_CACHE["text"]
    scopes, hosts, chrome, vault = broker_scopes(), ssh_hosts(), chrome_accounts(), vault_entries()
    lines = ["Things the agent can do itself (generated from local config just now):"]
    lines.append("- token-broker scopes (names only; a scope named like *-minter can mint narrower tokens): "
                 + (", ".join(scopes) if scopes else "none readable"))
    lines.append("- ssh hosts: " + (", ".join(hosts) if hosts else "none"))
    lines.append("- Chrome profiles signed in (agent can drive them): " + (", ".join(chrome) if chrome else "unknown"))
    lines.append("- browser vault entries: " + (", ".join(vault) if vault else "none/unknown"))
    text = "\n".join(lines)
    _CAP_CACHE.update(t=time.time(), text=text)
    return text


def text_hash(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", text.lower()).strip().encode()).hexdigest()[:12]
