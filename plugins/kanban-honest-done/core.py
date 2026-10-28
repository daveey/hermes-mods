"""kanban-honest-done core: dropped-work classifier, follow-up lookup, and the two worker gates.

Pure functions plus small read-only board queries, so tests can drive them with a temp kanban.db.
"""

from __future__ import annotations

import os
import re
import shlex
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

TASK_ID_RE = re.compile(r"\bt_[0-9a-f]{8}\b")

# Each pattern names a way a handoff admits part of the card's work was not done. Matched per sentence against the
# completion summary (and the card title, see TITLE_KINDS). Keep these narrow: every false hit refuses a real
# completion. `replay.py` measures them against a real board.
DROP_PATTERNS: tuple[tuple[str, str], ...] = (
    ("split to follow-up", r"\bsplit (?:off |out )?(?:in)?to (?:a |an |its own )?(?:separate )?follow[- ]?ups?\b"),
    ("dropped from scope", r"\bdropped (?:from|out of) (?:this |the |its |my )?(?:card'?s? )?(?:scope|acceptance)\b"),
    ("could not", r"\b(?:i |we )?(?:could not|couldn't|was unable to|were unable to|wasn't able to|was not able to"
                  r"|did not|didn't|have not|haven't|has not|hasn't) (?:yet )?(?:get to )?"
                  r"(?:run|verify|test|check|confirm|complete|finish|install|exercise|prove)\b"),
    ("not run", r"\bnot (?:yet )?(?:been )?run\b"),
    ("deferred", r"\b(?:remains?|stays?|is|are|was|were|left|still|got|been) (?:still )?deferred\b"
                 r"|\bdeferred (?:to|until|for) (?:a |the )?(?:follow[- ]?up|later|next)\b"),
    ("unverified", r"\b(?:is|are|was|were|remains?|stays?|still|left|went) (?:still |as )?unverified\b"
                   r"|\bunverified\s*[:)]"),
    # The most common handoff wording: "acceptance b not verified", "DOM check not performed".
    ("not verified", r"\bnot (?:yet )?(?:be(?:en)? )?(?:independently |directly |live[- ])?"
                     r"(?:verified|performed|checked|confirmed|tested|exercised|validated)\b"
                     r"(?! (?:in|into|out|off)\b)"),
    ("out of scope", r"\b(?:moved|left|ruled|declared|marked|treated as|is|was) out of scope\b"
                     r"|\bout of scope for this card\b"),
    ("follow-up", r"^\W*(?:[\w-]+ )?follow[- ]?ups?\s*(?:\([^)]{0,40}\))?\s*:"
                  r"|\b(?:left|filed|kept|noted|tracked) (?:it |that |this )?(?:for|as) (?:a )?follow[- ]?up\b"
                  r"|\b(?:as|for|needs?|in) (?:a )?(?:separate )?follow[- ]?up(?: card| task)?\b"
                  r"|\bfollow[- ]?up (?:card|task|item|work)s?\b"
                  r"|\bfollow[- ]?up is to\b"),
)
# Title text is the card's own name, and many cards are follow-ups by design ("Zoom rooms 6: follow-ups"). Only
# a title that says the card itself shed work counts.
TITLE_KINDS = {"split to follow-up", "dropped from scope", "deferred"}

# A sentence that says nothing was dropped, or that a deferral was the requester's call, is not an admission.
NEGATION_RE = re.compile(
    r"\bno (?:open |remaining |further )?follow[- ]?ups?\b|\bnothing (?:was |is )?(?:deferred|dropped|left|skipped)\b"
    r"|\bnone (?:deferred|dropped|left)\b|\bno follow[- ]?up (?:needed|required)\b"
    r"|\bat (?:the )?[\w-]+(?:'s)? request\b|\b(?:update|deploy(?:ment)?|install|sync|apply)\w* (?:is |was )?deferred\b"
    r"|\bdeferred (?:tool|loading|import|schema)", re.I)
# Follow-up commits / PRs / fixes are finished work, not open work.
DONE_FOLLOWUP_RE = re.compile(r"\bfollow[- ]?up (?:commit|pr|fix|patch|push|merge)\b|\bfollow[- ]?up [0-9a-f]{7,}\b", re.I)

_COMPILED = tuple((kind, re.compile(rx, re.I)) for kind, rx in DROP_PATTERNS)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
# Quoted text is someone else's words (an error message, a user utterance), not the worker's own admission.
_QUOTED = re.compile(r"\"[^\"\n]{0,400}\"|\u201c[^\u201d\n]{0,400}\u201d|`[^`\n]{0,400}`|(?<!\w)'[^'\n]{1,120}'(?!\w)")

OPEN_STATUSES = ("triage", "todo", "ready", "running", "scheduled", "blocked", "review")
# A follow-up that already finished also proves the work was handled.
ACCEPTED_STATUSES = OPEN_STATUSES + ("done",)


def dropped_work(text: str, kinds: Optional[set[str]] = None) -> list[tuple[str, str]]:
    """``[(kind, sentence), ...]`` for each sentence of ``text`` that admits dropped / deferred / unrun work."""
    hits: list[tuple[str, str]] = []
    for sentence in _SENTENCE_SPLIT.split(_QUOTED.sub('""', text or "")):
        s = sentence.strip()
        if not s or s.endswith("?") or NEGATION_RE.search(s):
            continue
        for kind, rx in _COMPILED:
            if kinds is not None and kind not in kinds:
                continue
            m = rx.search(s)
            if not m:
                continue
            if kind == "follow-up" and DONE_FOLLOWUP_RE.search(s) and not re.search(r"follow[- ]?ups?\s*:", s, re.I):
                continue
            hits.append((kind, s[:240]))
            break
    return hits


def dropped_work_in_handoff(summary: str, title: str = "") -> list[tuple[str, str]]:
    return dropped_work(title, TITLE_KINDS) + dropped_work(summary)


def metadata_text(metadata: Any) -> str:
    """Every text value in a completion's ``metadata`` (``not_done``, ``remaining``, nested lists…), one per line as
    ``key: value``. Handoffs put the honest caveats there, out of the one-line summary. Accepts a dict or its JSON."""
    if isinstance(metadata, str):
        try:
            import json

            metadata = json.loads(metadata)
        except ValueError:
            return metadata
    lines: list[str] = []

    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, str(k))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and v.strip():
            lines.append(f"{key}: {v}" if key else v)

    walk(metadata)
    return chr(10).join(lines)


# --- board access -------------------------------------------------------------------------------------------------

def ro_connect(path: Path) -> sqlite3.Connection:
    """Read-only open that survives a WAL db whose -wal/-shm were just deleted (plain ``mode=ro`` then fails)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return conn
    except sqlite3.Error:
        if not Path(path).exists():
            raise
        conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=1.0)
        conn.execute("PRAGMA query_only=ON")
        return conn


def board_paths(env=None) -> list[Path]:
    """Every board db: the worker's own (``HERMES_KANBAN_DB``) first, then the default + ``kanban/boards/*``."""
    env = os.environ if env is None else env
    out: list[Path] = []
    own = env.get("HERMES_KANBAN_DB")
    if own:
        out.append(Path(own))
    home = Path(env.get("HERMES_HOME") or Path.home() / ".hermes")
    try:  # the runtime knows the real layout (profiles, HERMES_KANBAN_HOME)
        from hermes_cli import kanban_db  # type: ignore

        root = Path(kanban_db.kanban_home())
        boards = Path(kanban_db.boards_root())
    except Exception:
        root, boards = home, home / "kanban" / "boards"
    out.append(root / "kanban.db")
    if boards.is_dir():
        out.extend(sorted(p for p in boards.glob("*/kanban.db")))
    seen: set[str] = set()
    uniq = []
    for p in out:
        key = os.path.realpath(p)
        if key not in seen and Path(p).is_file():
            seen.add(key)
            uniq.append(Path(p))
    return uniq


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def related_task_ids(conn: sqlite3.Connection, task_id: str) -> set[str]:
    """Cards this one created (``created`` event ``creator_task_id``) or is linked to either way."""
    out: set[str] = set()
    if _has_table(conn, "task_links"):
        out.update(r[0] for r in conn.execute(
            "SELECT child_id FROM task_links WHERE parent_id=? UNION SELECT parent_id FROM task_links WHERE child_id=?",
            (task_id, task_id)))
    if _has_table(conn, "task_events"):
        try:
            out.update(r[0] for r in conn.execute(
                "SELECT task_id FROM task_events WHERE kind='created' AND json_extract(payload,'$.creator_task_id')=?",
                (task_id,)))
        except sqlite3.Error:  # no JSON1 / odd payloads
            pass
    out.discard(task_id)
    return out


def find_task(task_id: str, paths: Iterable[Path]) -> Optional[tuple[Path, dict[str, Any]]]:
    for p in paths:
        try:
            conn = ro_connect(p)
        except sqlite3.Error:
            continue
        try:
            conn.row_factory = sqlite3.Row
            if not _has_table(conn, "tasks"):
                continue
            row = conn.execute("SELECT id, title, status, body FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row:
                return p, dict(row)
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return None


def valid_followups(task_id: str, text: str, paths: list[Path],
                    statuses: tuple[str, ...] = ACCEPTED_STATUSES) -> list[str]:
    """Card ids named in ``text`` that exist (any board), are in ``statuses`` and were created by / link to
    ``task_id`` (relation read on the named card's board and on the task's own board)."""
    named = [t for t in dict.fromkeys(TASK_ID_RE.findall(text or "")) if t != task_id]
    if not named:
        return []
    related: set[str] = set()
    for p in paths:
        try:
            conn = ro_connect(p)
        except sqlite3.Error:
            continue
        try:
            related |= related_task_ids(conn, task_id)
            for n in named:  # the follow-up may record the relation on its own board
                related |= {n} if task_id in related_task_ids(conn, n) else set()
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    ok = []
    for n in named:
        if n not in related:
            continue
        found = find_task(n, paths)
        if found and found[1].get("status") in statuses:
            ok.append(n)
    return ok


# --- gate messages ------------------------------------------------------------------------------------------------

EDIT_REFUSAL = ("[kanban-honest-done] Don't narrow your own card. Fix the blocker, or kanban_block with the exact "
                "physical step, or create the follow-up with kanban_create and keep going. (Comments are fine: "
                "kanban_comment. Only the parent session or the human may change this card's title/body.)")


def complete_refusal(task_id: str, hits: list[tuple[str, str]], named: list[str]) -> str:
    quoted = "; ".join(f"[{k}] \"{s}\"" for k, s in hits[:3])
    why = (f" The id(s) you named ({', '.join(named)}) are not open cards created by or linked to {task_id}."
           if named else "")
    return (
        f"[kanban-honest-done] Refused: this handoff says part of {task_id}'s work was not done: {quoted}.{why} "
        "A card is only done when everything its body asks for is done. Do one of these, then complete again:\n"
        "1) Finish the missing part now, and rewrite the summary to report it done.\n"
        f"2) Create the follow-up card for the missing part (kanban_create with parents=[\"{task_id}\"] — it then "
        "carries creator_task_id), and name its t_xxxxxxxx id in the summary. For a step only a human can do, "
        "create the card and kanban_block it kind=needs_input with the exact physical step.\n"
        "3) If it can't continue without a human, kanban_block THIS card with the exact step instead of completing.\n"
        "Don't edit this card's title/body to remove the requirement."
    )


# --- gates --------------------------------------------------------------------------------------------------------

def own_task_id(env=None) -> Optional[str]:
    env = os.environ if env is None else env
    return env.get("HERMES_KANBAN_TASK") or None


def check_complete(task_id: str, summary: str, paths: list[Path], title: Optional[str] = None,
                   metadata: Any = None) -> Optional[str]:
    """Refusal message for completing ``task_id`` with ``summary`` (+ ``metadata``), or None to allow.

    B: the handoff (summary AND every metadata text value) admits unfinished work with no follow-up card.
    D: the card names an origin chat in prose that is neither linked to the card nor reported to."""
    found = find_task(task_id, paths)
    if title is None:
        title = found[1].get("title") if found else ""
    handoff = "\n".join(x for x in (summary or "", metadata_text(metadata) if metadata else "") if x)
    hits = dropped_work_in_handoff(handoff, title or "")
    if hits and not valid_followups(task_id, handoff, paths):
        named = [t for t in dict.fromkeys(TASK_ID_RE.findall(handoff)) if t != task_id]
        return complete_refusal(task_id, hits, named)
    if found:
        return check_origin_reached(task_id, found[0], found[1].get("body") or "")
    return None


# --- D: an origin chat named only in prose ------------------------------------------------------------------------

# "Origin chat: @session:default@macbook/20261025_233046_d48ade", "report ... to origin @session:X",
# "origin_session=@session:X". The id group is the bare session id (what task_chat_links / notify subs store).
ORIGIN_SESSION_RE = re.compile(
    r"\borigin(?:_session)?\b[^\n@]{0,60}@session:(?:[\w.-]+(?:@[\w.-]+)?/)?([A-Za-z0-9][\w-]{5,})", re.I)
REPORTED_RE = re.compile(r"\bfollow-through:\s*SENT\b", re.I)
_DQUOTED = re.compile(r"\"[^\"\n]{0,400}\"|\u201c[^\u201d\n]{0,400}\u201d")


def origin_sessions(text: str) -> list[str]:
    """Session ids a card's prose names as its origin chat (quoted text is someone else's words: skipped)."""
    return list(dict.fromkeys(ORIGIN_SESSION_RE.findall(_DQUOTED.sub('""', text or ""))))


def check_origin_reached(task_id: str, board: Path, body: str) -> Optional[str]:
    """Refusal when the card body/comments name an origin chat that will not hear this completion: no unreleased
    ``task_chat_links`` row, no ``kanban_notify_subs`` subscription, and no ``follow-through: SENT`` comment naming
    it. Fails open (None) on any board read error."""
    try:
        conn = ro_connect(board)
    except sqlite3.Error:
        return None
    try:
        comments = [r[0] or "" for r in conn.execute(
            "SELECT body FROM task_comments WHERE task_id=?", (task_id,))] if _has_table(conn, "task_comments") else []
        wanted = origin_sessions("\n".join([body] + comments))
        if not wanted:
            return None
        reached: set[str] = set()
        if _has_table(conn, "task_chat_links"):
            reached |= {r[0] for r in conn.execute(
                "SELECT chat_session FROM task_chat_links WHERE task_id=? AND released=0", (task_id,))}
        if _has_table(conn, "kanban_notify_subs"):
            # tui subs store the bare session; cross-gateway ('hub') subs store <gateway>/<profile>/<session>.
            reached |= {(r[0] or "").rsplit("/", 1)[-1] for r in conn.execute(
                "SELECT chat_id FROM kanban_notify_subs WHERE task_id=?", (task_id,))}
        for c in comments:
            if REPORTED_RE.search(c):
                reached |= {s for s in wanted if s in c}
        missing = [s for s in wanted if s not in reached]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return origin_refusal(task_id, missing) if missing else None


def origin_refusal(task_id: str, sessions: list[str]) -> str:
    s = sessions[0]
    return (
        f"[kanban-honest-done] Refused: {task_id} names origin chat @session:{s} (body/comments), but that chat is "
        "not linked to this card, so it will never see that the card finished (only the linked chats are told). "
        "Before completing:\n"
        "1) Send that chat the result yourself: cross_session action=send, target = the @session link from the "
        "card body, message = what finished, the proof, and anything still open.\n"
        f"2) Record it with kanban_comment on {task_id}: \"follow-through: SENT via cross_session to @session:{s} "
        "at <time>; delivery verified|not verified\" (verified = the send returned delivered/replied). Then complete "
        "again.\n"
        "3) If the send fails (no relay, chat gone), kanban_block this card with that error instead of completing.\n"
        "(Whoever creates such a card should bind the chat up front: kanban_create origin_session='@session:…'.)"
        + (f" Other unlinked origin chats: {', '.join(sessions[1:])}." if len(sessions) > 1 else "")
    )


# --- E: a card created FOR another gateway's chat must be bound to it -----------------------------------------

ORIGIN_REF_RE = re.compile(
    r"\borigin(?:_session)?\b[^\n@]{0,60}@session:(?P<profile>[\w.-]+)@(?P<gw>[\w.-]+)/(?P<sid>[A-Za-z0-9][\w-]{5,})",
    re.I)


def _gateway_names() -> Optional[tuple[str, set[str]]]:
    try:
        from hermes_cli import kanban_origin  # type: ignore

        return kanban_origin.gateway_names()
    except Exception:
        return None


def check_create(args: dict, env=None, names_fn=None) -> Optional[str]:
    """Refusal for a ``kanban_create`` whose title/body names an origin chat on ANOTHER gateway while the card is
    not bound to it (no ``origin_session`` argument / ``HERMES_ORIGIN_SESSION`` naming that chat). Unbound, the
    chat never sees the card or its completion. None (allow) when this gateway's name is unknown."""
    env = os.environ if env is None else env
    text = _DQUOTED.sub('""', f"{args.get('title') or ''}\n{args.get('body') or ''}")
    refs = [m.groupdict() for m in ORIGIN_REF_RE.finditer(text)]
    if not refs:
        return None
    bound = f"{args.get('origin_session') or ''} {env.get('HERMES_ORIGIN_SESSION') or ''}"
    if any(r["sid"] in bound for r in refs):
        return None
    names = (names_fn or _gateway_names)()
    if not names or not names[0]:
        return None
    me, known = names
    remote = [r for r in refs if r["gw"] != me and (not known or r["gw"] in known)]
    if not remote:
        return None
    r = remote[0]
    ref = f"@session:{r['profile']}@{r['gw']}/{r['sid']}"
    return (f"[kanban-honest-done] Refused: this card's text names origin chat {ref}, but the card would not be "
            "bound to it, so that chat never sees the card or hears when it finishes (only the creating chat is "
            f"told). Call kanban_create again with origin_session='{ref}' (same title/body). If that chat is not "
            "what the card is for, reword the body so it doesn't call it the origin.")


_OWN_REFS = ("$HERMES_KANBAN_TASK", "${HERMES_KANBAN_TASK}")
_SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")"}


def _is_own(ref: str, own: str) -> bool:
    return ref == own or ref.strip("\"'") in _OWN_REFS


def _segments(command: str) -> list[list[str]]:
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        tokens = command.split()
    segs, cur = [], []
    for t in tokens:
        if t in _SEPARATORS or set(t) <= set(";&|()\n"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _opt(tokens: list[str], *names: str) -> Optional[str]:
    for i, t in enumerate(tokens):
        for n in names:
            if t == n and i + 1 < len(tokens):
                return tokens[i + 1]
            if t.startswith(n + "="):
                return t[len(n) + 1:]
    return None


def _kanban_verb(seg: list[str]) -> Optional[tuple[str, list[str]]]:
    """``(verb, args after verb)`` for a ``hermes kanban <verb> ...`` segment (global flags allowed)."""
    for i, t in enumerate(seg):
        if os.path.basename(t) == "kanban" and i > 0 and os.path.basename(seg[i - 1]) in ("hermes", "hermes.exe") \
                or (t == "kanban" and i > 0 and seg[i - 1].endswith("hermes_cli.main")):
            j = i + 1
            while j < len(seg) and seg[j].startswith("-"):
                j += 2 if "=" not in seg[j] and seg[j] in ("--board", "-b") else 1
            if j < len(seg):
                return seg[j], seg[j + 1:]
    return None


def _positional(args: list[str]) -> Optional[str]:
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and "=" not in a:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a
    return None


def check_shell(command: str, own: str, paths_fn) -> Optional[str]:
    """Refusal for a worker shell command that edits its own card's title/body or completes it dishonestly."""
    for seg in _segments(command or ""):
        hit = _kanban_verb(seg)
        if not hit:
            continue
        verb, args = hit
        target = _positional(args)
        if not target or not _is_own(target, own):
            continue
        if verb == "edit" and (_opt(args, "--title") is not None or _opt(args, "--body") is not None):
            return EDIT_REFUSAL
        if verb in ("complete", "done"):
            summary = _opt(args, "--summary") or _opt(args, "--result") or ""
            why = check_complete(own, summary, paths_fn(), metadata=_opt(args, "--metadata"))
            if why:
                return why
    return None


_PY_EDIT_RE = re.compile(r"kanban\s+edit\s+[\"']?(t_[0-9a-f]{8}|\$\{?HERMES_KANBAN_TASK\}?)[\"']?[^\n]*?--(?:title|body)\b")


def check_python(code: str, own: str, paths_fn) -> Optional[str]:
    """execute_code: the same shell checks on every string literal-ish line, plus a direct edit regex."""
    for m in _PY_EDIT_RE.finditer(code or ""):
        if _is_own(m.group(1), own):
            return EDIT_REFUSAL
    for line in (code or "").splitlines():
        if "kanban" in line and ("complete" in line or "edit" in line):
            for lit in re.findall(r"(?:\"\"\"|''')(.*?)(?:\"\"\"|''')|\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'", line):
                cmd = next((x for x in lit if x), "")
                if "kanban" in cmd:
                    why = check_shell(cmd, own, paths_fn)
                    if why:
                        return why
    return None


# --- C: sidebar "needs attention" check (registered with core's kanban_session_activity) -------------------------

ACK_RE = re.compile(r"\bhonest-done:\s*(?:ack|ok|accepted)\b", re.I)


def _acked(conn: sqlite3.Connection, task_id: str) -> bool:
    if not _has_table(conn, "task_comments"):
        return False
    return any(ACK_RE.search(r[0] or "") for r in conn.execute(
        "SELECT body FROM task_comments WHERE task_id=?", (task_id,)))


def needs_attention(card: dict[str, Any], paths: Optional[list[Path]] = None) -> bool:
    """True when a completed card's handoff admits dropped work and no follow-up card carries it.

    Follow-up = any non-archived card it created / links to, or a named one passing ``valid_followups``. A
    ``honest-done: ack`` comment on the card (the human accepted the narrower result) clears it."""
    tid = str(card.get("id") or "")
    summary, title = str(card.get("summary") or ""), str(card.get("title") or "")
    if not tid or not dropped_work_in_handoff(summary, title):
        return False
    if paths is None:
        own = [Path(card["board_path"])] if card.get("board_path") else []
        paths = own + [p for p in board_paths() if str(p) not in {str(x) for x in own}]
    for p in paths[:1]:
        try:
            conn = ro_connect(p)
        except sqlite3.Error:
            break
        try:
            if _acked(conn, tid):
                return False
            related = related_task_ids(conn, tid)
            if related:
                ph = ",".join("?" for _ in related)
                live = conn.execute(f"SELECT 1 FROM tasks WHERE id IN ({ph}) AND status != 'archived' LIMIT 1",
                                    tuple(related)).fetchone()
                if live:
                    return False
        except sqlite3.Error:
            return False
        finally:
            conn.close()
    return not valid_followups(tid, summary, paths)
