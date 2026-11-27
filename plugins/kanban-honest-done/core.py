"""kanban-honest-done core: dropped-work classifier, follow-up lookup, and the two worker gates.

Pure functions plus small read-only board queries, so tests can drive them with a temp kanban.db.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sqlite3
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

TASK_ID_RE = re.compile(r"\bt_[0-9a-f]{8}(?:[0-9a-f]{8})?\b")  # legacy 8-hex and current 16-hex board ids

# Each pattern names a way a handoff admits part of the card's work was not done. Matched per sentence against the
# completion summary and metadata, never the request title. Keep these narrow: every false hit refuses a real
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
    """Classify current completion evidence, not the task's pre-existing request context.

    ``title`` is retained for caller compatibility (including sidebar/replay callers). A title may
    describe the defect being fixed; its wording cannot establish that work is still unfinished.
    """
    return dropped_work(summary)


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


# One attention pass (core's sidebar done checks, section C) asks about every recently completed card. Each
# answer used to open its own connections: the card's board, every board again for the named follow-ups, then one
# per named card. Inside ``attention_pass`` every reader below shares one connection per database, opened on first
# use and closed when the pass ends, and the ``created`` events are read once per database. Per thread; nothing
# outlives the pass. Outside a pass ``_open`` is a plain open + close, as before.
_pass = threading.local()


@contextmanager
def attention_pass():
    """One shared short-lived read per database for the calls made inside. Re-entrant; the outermost exit closes."""
    state = getattr(_pass, "state", None)
    if state is not None:
        yield
        return
    state = _pass.state = {"conns": {}, "creators": {}}
    try:
        yield
    finally:
        _pass.state = None
        for conn in state["conns"].values():
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _open(path: Path):
    """``(connection, release)``: the pass's shared connection when one is open, else a new one ``release`` closes.
    An open that fails raises ``sqlite3.Error`` and is not remembered."""
    state = getattr(_pass, "state", None)
    if state is None:
        conn = ro_connect(path)
        return conn, conn.close
    key = str(path)
    conn = state["conns"].get(key)
    if conn is None:
        conn = state["conns"][key] = ro_connect(path)
    return conn, _keep


def _keep() -> None:
    return None


def _created_by(conn: sqlite3.Connection, task_id: str) -> Optional[set[str]]:
    """Inside a pass: the cards whose ``created`` event names ``task_id`` as creator, from one read of this
    database's ``created`` events. None outside a pass or on a connection the pass does not own. A failure midway
    (no JSON1, a malformed payload) keeps the rows indexed before it. Those are not always the per-card query's
    rows: sqlite3 steps to the next row before handing back the current one, so the index loses the last
    ``created`` row before the bad payload where the per-card query loses its last matching row. On a board
    holding invalid JSON in a ``created`` event (core does not write one) a pass can therefore keep a real creator
    link the per-card query drops."""
    state = getattr(_pass, "state", None)
    if state is None or not any(conn is c for c in state["conns"].values()):
        return None
    index = state["creators"].get(id(conn))
    if index is None:
        index = state["creators"][id(conn)] = {}
        try:
            for child, creator in conn.execute(
                    "SELECT task_id, json_extract(payload,'$.creator_task_id') FROM task_events WHERE kind='created'"):
                if isinstance(creator, str):
                    index.setdefault(creator, set()).add(child)
        except sqlite3.Error:
            pass
    return index.get(task_id, set())


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
        indexed = _created_by(conn, task_id)
        if indexed is not None:
            out.update(indexed)
        else:
            try:
                out.update(r[0] for r in conn.execute(
                    "SELECT task_id FROM task_events WHERE kind='created' "
                    "AND json_extract(payload,'$.creator_task_id')=?", (task_id,)))
            except sqlite3.Error:  # no JSON1 / odd payloads
                pass
    out.discard(task_id)
    return out


def find_task(task_id: str, paths: Iterable[Path]) -> Optional[tuple[Path, dict[str, Any]]]:
    for p in paths:
        try:
            conn, release = _open(p)
        except sqlite3.Error:
            continue
        try:
            if not _has_table(conn, "tasks"):
                continue
            cur = conn.cursor()  # rows by name on this cursor only: the connection may be a pass's shared one
            cur.row_factory = sqlite3.Row
            row = cur.execute("SELECT id, title, status, body FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row:
                return p, dict(row)
        except sqlite3.Error:
            continue
        finally:
            release()
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
            conn, release = _open(p)
        except sqlite3.Error:
            continue
        try:
            related |= related_task_ids(conn, task_id)
            for n in named:  # the follow-up may record the relation on its own board
                related |= {n} if task_id in related_task_ids(conn, n) else set()
        except sqlite3.Error:
            pass
        finally:
            release()
    ok = []
    for n in named:
        if n not in related:
            continue
        found = find_task(n, paths)
        if found and found[1].get("status") in statuses:
            ok.append(n)
    rest = [n for n in named if n not in ok]
    if rest:  # a follow-up placed on another gateway shows up only in this host's Board replica
        ok += [n for n in rest if _replica_followup(task_id, n, paths, statuses)]
    return ok


def replica_paths(paths: Iterable[Path]) -> list[Path]:
    """Board replicas (``<kanban home>/kanban/sync-shadow/*.db``) next to the given live boards."""
    dirs: list[Path] = []
    for p in paths:
        p = Path(p)
        # <home>/kanban.db -> <home>/kanban/sync-shadow; <home>/kanban/boards/<slug>/kanban.db -> same dir
        dirs += [p.parent / "kanban" / "sync-shadow", p.parent.parent.parent / "sync-shadow"]
    out: list[Path] = []
    for d in dict.fromkeys(dirs):
        if d.is_dir():
            out.extend(sorted(d.glob("*.db")))
    return out


def _replica_followup(task_id: str, n: str, paths: list[Path], statuses: tuple[str, ...]) -> bool:
    """``n`` is in ``statuses`` on a Board replica and was created by / links to ``task_id`` there.

    Indexed lookups only (replicas are hundreds of MB): ``n``'s own row, its links and its ``created`` event. A
    pool worker's live board holds only the cards created or claimed there, so without this a follow-up the worker
    placed on another gateway reads as missing and gate B refuses an honest handoff."""
    for p in replica_paths(paths):
        try:
            conn, release = _open(p)
        except sqlite3.Error:
            continue
        try:
            if not _has_table(conn, "tasks"):
                continue
            row = conn.execute("SELECT status FROM tasks WHERE id=?", (n,)).fetchone()
            if not row or row[0] not in statuses:
                continue
            linked = _has_table(conn, "task_links") and conn.execute(
                "SELECT 1 FROM task_links WHERE (parent_id=? AND child_id=?) OR (parent_id=? AND child_id=?)",
                (task_id, n, n, task_id)).fetchone()
            created = False
            if not linked and _has_table(conn, "task_events"):
                for (payload,) in conn.execute(
                        "SELECT payload FROM task_events WHERE task_id=? AND kind='created'", (n,)):
                    try:
                        created = created or (json.loads(payload or "{}") or {}).get("creator_task_id") == task_id
                    except (ValueError, AttributeError):
                        pass
            if linked or created:
                return True
        except sqlite3.Error:
            continue
        finally:
            release()
    return False


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
    receipt_hits = []
    if isinstance(metadata, str):
        import json
        try:
            metadata = json.loads(metadata)
        except ValueError:
            pass  # legacy malformed metadata still goes through the prose gate
    if isinstance(metadata, dict) and "completion_receipt" in metadata:
        try:
            from hermes_completion_receipt import validate_completion_receipt
            from hermes_cli.kanban_db import complete_task
            if getattr(complete_task, "completion_receipt_admission", None) != 1:
                return "[kanban-honest-done] completion_receipt needs a receipt-capable loaded writer; nothing completed."
            receipt = validate_completion_receipt(metadata["completion_receipt"])
        except ImportError:
            return "[kanban-honest-done] completion_receipt needs a receipt-capable core; nothing completed."
        except ValueError as exc:
            return f"[kanban-honest-done] {exc}"
        # Remaining obligations have typed semantics enforced by the writer.
        # Evidence references/timestamps are not prose. Scope claims remain
        # subject to the existing classifier, but receipt refs cannot vouch for
        # a follow-up demanded by an unfinished summary or other metadata.
        receipt_hits = dropped_work("\n".join(item["scope"] + "\n" + item.get("superseded_by", "")
                                             for item in receipt["items"]))
        metadata = {key: value for key, value in metadata.items() if key != "completion_receipt"}
    found = find_task(task_id, paths)
    if title is None:
        title = found[1].get("title") if found else ""
    handoff = "\n".join(x for x in (summary or "", metadata_text(metadata) if metadata else "") if x)
    hits = receipt_hits + dropped_work_in_handoff(handoff, title or "")
    if hits and not valid_followups(task_id, handoff, paths):
        named = [t for t in dict.fromkeys(TASK_ID_RE.findall(handoff)) if t != task_id]
        return complete_refusal(task_id, hits, named)
    why = _delivery_refusal(task_id, handoff, paths)
    if why:  # I: "posted on t_X" needs the write's result, not the intent
        return why
    why = _threshold_refusal(task_id, summary, metadata, found)
    if why:  # G: a failed threshold never becomes a waiver or a guessed pass
        return why
    if found:
        return check_origin_reached(task_id, found[0], found[1].get("body") or "", metadata=metadata)
    return None


def _threshold_refusal(task_id: str, summary: str, metadata: Any, found) -> Optional[str]:
    """G, isolated: a G bug fails open for G only, so D and the other gates still run."""
    try:
        return _thresholds_mod().check_thresholds(task_id, summary, metadata,
                                                  (found[1].get("body") or "") if found else "")
    except Exception:
        return None


def _delivery_refusal(task_id: str, handoff: str, paths: list[Path]) -> Optional[str]:
    """I, isolated: a delivery-gate bug fails open for I only."""
    try:
        why = _sibling("delivery").check_deliveries(task_id, handoff, list(paths), ro_connect)
    except Exception:
        return None
    if why:
        try:
            _sibling("consent").telemetry("delivery_claim_refused", task=task_id, detail=why[:300])
        except Exception:
            pass
    return why


def _sibling(name: str):
    try:
        import importlib

        return importlib.import_module(f"{__package__}.{name}") if __package__ else _load_sibling(name)
    except ImportError:
        return _load_sibling(name)


def _load_sibling(name: str):
    import importlib.util
    import sys

    key = f"kanban_honest_done_{name}"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


def check_review(task_id: str, summary: str, paths: list[Path], metadata: Any = None) -> Optional[str]:
    """G for ``kanban_request_review``: a review handoff can't waive a failed threshold or guess a pass either."""
    return _threshold_refusal(task_id, summary, metadata, find_task(task_id, paths))


def _thresholds_mod():
    try:
        from . import thresholds  # type: ignore
        return thresholds
    except ImportError:
        import importlib.util
        import sys

        key = "kanban_honest_done_thresholds"
        mod = sys.modules.get(key)
        if mod is None:
            spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("thresholds.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod = sys.modules.setdefault(key, mod)
        return mod


# --- D: an origin chat named only in prose ------------------------------------------------------------------------

# "Origin chat: @session:default@macbook/20261025_233046_d48ade", "report ... to origin @session:X",
# "origin_session=@session:X". The id group is the bare session id (what task_chat_links / notify subs store).
ORIGIN_SESSION_RE = re.compile(
    r"\borigin(?:_session)?\b[^\n@]{0,60}@session:"
    r"(?:(?P<profile>[\w.-]+)(?:@(?P<gateway>[\w.-]+))?/)?"
    r"(?P<session>[A-Za-z0-9][\w-]{5,})(?![\w@/-])", re.I)
# '@' and '/' cannot terminate a bare session: malformed qualified tokens must
# not backtrack into the profile name. Retain all qualifiers for membership.
REPORTED_RE = re.compile(r"\bfollow-through:\s*SENT\b", re.I)
# A "SENT" note that carries the send's failure is not a send (live test: ok:false, "No connected Hermes Desktop").
FAILED_SEND_RE = re.compile(r"\"?ok\"?\s*:\s*false|\bno connected\b|\b(?:send|delivery) failed\b"
                            r"|\bfailed to (?:send|deliver)\b|\berror\b|\bnot (?:actually )?(?:sent|delivered|messaged)\b",
                            re.I)
# Quotes may span lines: self-heal cards quote the user's multi-line message ('Quote: "...\nOrigin @session:X ..."'),
# and an origin named inside someone else's words is not this card's origin chat.
_DQUOTED = re.compile(r"\"[^\"]{0,1200}\"|\u201c[^\u201d]{0,1200}\u201d")


def origin_refs(text: str) -> list[tuple[str, str, str]]:
    """(gateway, profile, session), with absent qualifiers left empty."""
    return list(dict.fromkeys((m['gateway'] or '', m['profile'] or '', m['session'])
                             for m in ORIGIN_SESSION_RE.finditer(_DQUOTED.sub('""', text or ''))))


def origin_sessions(text: str) -> list[str]:
    """Compatibility view for callers that only display the session IDs."""
    return list(dict.fromkeys(ref[2] for ref in origin_refs(text)))


def _matches_origin(wanted, reached):
    return any(wanted[2] == ref[2] and (not wanted[0] or wanted[0] == ref[0])
               and (not wanted[1] or wanted[1] == ref[1]) for ref in reached)


def _origin_receipt_context(task_id, board, owner):
    """Trusted runtime paths and scope; never infer owner profile from the reader's profile."""
    try:
        from hermes_cli.kanban_execution_observations import board_owner_profile
        from hermes_cli.kanban_origin import _hub_db, gateway_name
        from hermes_cli import kanban_origin_ledger as ledger
        from hermes_cli.config import get_hermes_home
        from hermes_cli.profiles import get_active_profile_name
        profile = board_owner_profile(board)
        if not owner or not profile:
            return None

        def live(conn, task, expected, now):
            return _sibling('origin_receipts').owner_live(conn, task, expected, now, ledger)

        return dict(owner=dict(gateway=owner, profile=profile, board=_board_slug(board), task_id=task_id),
                    gateway=gateway_name(), profile=get_active_profile_name(),
                    ledger=get_hermes_home() / 'kanban/origin-ledger.db', replica=_hub_db(), owner_live=live)
    except (ImportError, AttributeError, OSError, ValueError):
        return None


def check_origin_reached(task_id: str, board: Path, body: str, metadata: Any = None) -> Optional[str]:
    """Refusal when the card body/comments name an origin chat that will not hear this completion: no unreleased
    ``task_chat_links`` row, ``kanban_notify_subs`` subscription, or authenticated
    origin obligation receipt. SENT comments never count. Fails open on board read errors."""
    try:
        conn = ro_connect(board)
    except sqlite3.Error:
        return None
    try:
        comments = [r[0] or "" for r in conn.execute(
            "SELECT body FROM task_comments WHERE task_id=?", (task_id,))] if _has_table(conn, "task_comments") else []
        wanted = origin_refs("\n".join([body] + comments))
        if not wanted:
            return None
        reached = _reached_origins(conn, task_id, board)
        owner = _created_on(conn, task_id)
        # Worker comments are assertions, not delivery authority. Only scoped
        # links/subscriptions and authenticated ledger receipts can count.
        missing = [s for s in wanted if not _matches_origin(s, reached)]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if missing:
        # A Board-synced card run on another machine (runs_on machine:X) has a copy of its row on X's live board,
        # but its chat links are written on the owning gateway and reach X only through the Board replica
        # (t_01a1d48abbb62104: owned by macbook, run on worker-4, linked on macbook, refused on worker-4).
        for p in replica_paths([board]):
            if _board_slug(p) != _board_slug(board) or _board_profile(p) != _board_profile(board):
                continue
            try:
                rconn = ro_connect(p)
            except sqlite3.Error:
                continue
            try:
                if not _has_table(rconn, "tasks"):
                    continue
                found_here = rconn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not found_here:
                    continue
                replica_owner = _created_on(rconn, task_id)
                if owner and replica_owner != owner:
                    continue
                hit = _reached_origins(rconn, task_id, p)
                missing = [s for s in missing if not _matches_origin(s, hit)]
                owner = owner or replica_owner
            except sqlite3.Error:
                continue
            finally:
                rconn.close()
    if missing:
        context = _origin_receipt_context(task_id, board, owner)
        hit = _sibling('origin_receipts').reached(context, ro_connect) if context else set()
        missing = [s for s in missing if not _matches_origin(s, hit)]
    gone = {}
    if missing:
        try:
            from contextlib import closing
            with closing(ro_connect(board)) as creation_conn:
                row = creation_conn.execute('SELECT created_at FROM tasks WHERE id=?', (task_id,)).fetchone()
                created = row[0] if row else None
            gone = _sibling('origin_gone').evidence(
                missing, replica_paths([board]), ro_connect, task_created_at=created)
        except Exception:  # health: allow BLE001 -- unknown evidence retains the refusal
            gone = {}
    notes = '\n'.join(comments + [metadata_text(metadata)])
    missing = [ref for ref in missing if ref not in gone or gone[ref] not in notes]
    if not missing:
        return None
    audit_hint = ''.join(f'\nAuthenticated gone-origin evidence is available. Include this exact note in completion '
                         f'metadata (or a kanban_comment), then retry: {gone[ref]}' for ref in missing if ref in gone)
    labels = [f"{profile}@{gw}/{sid}" if gw else f"{profile}/{sid}" if profile else sid
              for gw, profile, sid in missing]
    return origin_refusal(task_id, labels) + audit_hint + ('' if missing[0] in gone else remote_origin_hint(
        task_id, board, body, comments, missing[0][2], owner=owner))


def _board_profile(board):
    try:
        from hermes_cli.kanban_execution_observations import board_owner_profile
        return board_owner_profile(board) or ''
    except ImportError:
        return ''


def _reached_origins(conn, task_id, board):
    """Keep scope in notification/link evidence; a matching SID elsewhere is not a link."""
    reached = set()
    names = _gateway_names()
    gateway = _created_on(conn, task_id) or (names[0] if names else '')
    profile = _board_profile(board)
    # Historical board schemas have no profile column; their links were default-profile links.
    try:
        row = conn.execute('SELECT session_id FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row and row[0]:
            reached.add((gateway, profile, row[0]))
    except sqlite3.Error:
        pass
    if _has_table(conn, 'task_chat_links'):
        columns = {r[1] for r in conn.execute('PRAGMA table_info(task_chat_links)')}
        profile_sql = 'chat_profile' if 'chat_profile' in columns else "'default'"
        for gw, prof, sid in conn.execute(
                f'SELECT chat_gateway,{profile_sql},chat_session FROM task_chat_links WHERE task_id=? AND released=0',
                (task_id,)):
            reached.add((gw or '', prof or 'default', sid))
    if _has_table(conn, 'kanban_notify_subs'):
        for platform, chat in conn.execute('SELECT platform,chat_id FROM kanban_notify_subs WHERE task_id=?', (task_id,)):
            if platform == 'hub' and chat and len(chat.split('/')) == 3:
                reached.add(tuple(chat.split('/')))
            elif platform == 'tui' and chat:
                reached.add((gateway, profile, chat))
    return reached


def _reached_chats(conn: sqlite3.Connection, task_id: str) -> set[str]:
    """Chats one board says will hear ``task_id`` finish: the creating chat, unreleased chat links, notify subs."""
    reached: set[str] = set()
    try:  # the creating chat: its 'created' chat link is written later by the sync loop's reconcile
        row = conn.execute("SELECT session_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row and row[0]:
            reached.add(str(row[0]))
    except sqlite3.Error:
        pass
    if _has_table(conn, "task_chat_links"):
        reached |= {r[0] for r in conn.execute(
            "SELECT chat_session FROM task_chat_links WHERE task_id=? AND released=0", (task_id,))}
    if _has_table(conn, "kanban_notify_subs"):
        # tui subs store the bare session; cross-gateway ('hub') subs store <gateway>/<profile>/<session>.
        reached |= {(r[0] or "").rsplit("/", 1)[-1] for r in conn.execute(
            "SELECT chat_id FROM kanban_notify_subs WHERE task_id=?", (task_id,))}
    return reached


def _created_on(conn: sqlite3.Connection, task_id: str) -> str:
    """Gateway that owns a Board-synced card (``tasks.created_on``); '' when unknown (older schema, local card)."""
    try:
        row = conn.execute("SELECT created_on FROM tasks WHERE id=?", (task_id,)).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]).strip() if row and row[0] else ""


def _board_slug(board: Path) -> str:
    """``<root>/kanban/boards/<slug>/kanban.db`` -> slug; the root ``kanban.db`` is ``default``."""
    p = Path(board)
    if p.parent.name == 'sync-shadow':
        return p.stem
    return p.parent.name if p.parent.parent.name == "boards" else "default"


def remote_origin_hint(task_id: str, board: Path, body: str, comments: list[str], session: str,
                       owner: str = "") -> str:
    """For an origin chat on ANOTHER gateway: a worker's cross_session send to a human chat is refused, and
    ``kanban_origin_adopt`` only runs inside a courier-delivered adoption. The link is made from the origin gateway
    with ``hermes-hub adopt-task``; a worker gets that run there with ``hermes-hub create-task``. '' otherwise.

    ``owner`` is the card's owning gateway (``tasks.created_on``). A Board-synced card run here via runs_on is still
    the owner's card: adopt-task must name the owner, never this executing machine, or the bind can never apply
    and a ``links --expect <this machine>/...`` wait never passes (t_01a1d48abbb62104)."""
    names = _gateway_names()
    me = names[0] if names else ""
    me = owner or me
    text = _DQUOTED.sub('""', "\n".join([body] + comments))
    gw = next((m["gw"] for m in ORIGIN_REF_RE.finditer(text) if m["sid"] == session), "")
    if not gw or not me or gw == me:
        return ""
    profile = (os.environ.get("HERMES_PROFILE") or "default").strip() or "default"
    slug = _board_slug(board)
    step = (f"hermes-hub adopt-task {me} {profile}/{slug}/{task_id} --origin-session {session} "
            f"--id adopt:{task_id}:{session}")
    delegate = (f"hermes-hub create-task {gw} {shlex.quote(f'Attach {me} card {task_id} to chat {session}')} "
                f"--body {shlex.quote(f'Run on this gateway: {step}  Then confirm: hermes-hub links {session} --expect {me}/{slug}/{task_id}')} "
                "--assignee default --origin-session ''")
    return (f"\nThat chat is on gateway {gw}, not here ({me}): if cross_session refuses the send, link the card to the "
            f"chat instead (this is what makes it hear the completion). The origin gateway must run: {step} . From "
            f"this worker, get that run there with: {delegate} , then complete again once the link shows. Use that "
            "CLI, not kanban_origin_adopt (it only runs inside a courier delivery), and do not ask a human for it.")


def origin_refusal(task_id: str, sessions: list[str]) -> str:
    s = sessions[0]
    return (
        f"[kanban-honest-done] Refused: {task_id} names origin chat @session:{s} (body/comments), but that chat is "
        "not linked to this card, so it will never see that the card finished (only the linked chats are told). "
        "Before completing:\n"
        "1) Repair the exact origin link/subscription or authenticated origin obligation through its owning agent. "
        "A cross_session send or a `follow-through: SENT` comment is not authenticated delivery evidence for this gate.\n"
        "2) Use kanban_supervisor_request operator action=comment on the live owning "
        "gateway's card to request an origin-link repair; do not invent SENT evidence. A terminated gateway may "
        "use the independently checked origin-chat-gone audit note offered below. Missing/stale evidence is "
        "unknown: refresh the normal replicas and retry, or route a repair to the owning agent. Use kanban_block "
        "only for an actual human step with human_request, not an agent-owned relay failure.\n"
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


# HERMES_KANBAN_TASK is scrubbed from a worker's tool children; they carry the card id as HERMES_KANBAN_WORKER_TASK.
_OWN_REFS = ("$HERMES_KANBAN_TASK", "${HERMES_KANBAN_TASK}", "$HERMES_KANBAN_WORKER_TASK", "${HERMES_KANBAN_WORKER_TASK}")



def _is_own(ref: str, own: str) -> bool:
    # Unknown expansion could resolve to our own card. Never evaluate it.
    return ref == own or ref.strip("\"'") in _OWN_REFS or bool(re.search(r'[$`*?\[\\]', ref))


@lru_cache(maxsize=1)
def _shell_commands():
    import importlib.util

    # Standalone tests and replay.py load core.py without a package.
    spec = importlib.util.spec_from_file_location(
        "kanban_honest_done_shell_commands", Path(__file__).with_name("shell_commands.py"))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _segments(command: str) -> list[list[str]]:
    return _shell_commands().segments(command)


def _opt(tokens: list[str], *names: str) -> Optional[str]:
    for i, t in enumerate(tokens):
        for n in names:
            if t == n and i + 1 < len(tokens):
                return tokens[i + 1]
            if t.startswith(n + "="):
                return t[len(n) + 1:]
    return None


def _kanban_verb(seg: list[str]) -> Optional[tuple[str, list[str]]]:
    """Any-position matching unless the WHOLE input proves a data exemption in G."""
    if _shell_commands().exempt_segment(seg):
        return None
    for k in range(1, len(seg)):
        if (os.path.basename(seg[k]) == "kanban" and os.path.basename(seg[k - 1]) in ("hermes", "hermes.exe")
                or seg[k] == "kanban" and seg[k - 1].endswith("hermes_cli.main")):
            hit = _verb_after(seg, k)
            if hit:
                return hit
    return None


def _verb_after(seg: list[str], i: int) -> Optional[tuple[str, list[str]]]:
    if i >= len(seg) or os.path.basename(seg[i]) != "kanban":
        return None
    j = i + 1
    while j < len(seg) and seg[j].startswith("-"):
        # argparse takes any unique prefix of --board (`--boa x`) as --board, so it consumes a value too.
        takes_value = seg[j] == "-b" or (len(seg[j]) > 2 and "--board".startswith(seg[j]))
        j += 2 if "=" not in seg[j] and takes_value else 1
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
        if verb == "request-review":
            why = check_review(own, _opt(args, "--summary") or "", paths_fn(), metadata=_opt(args, "--metadata"))
            if why:
                return why
    return None


_PY_EDIT_RE = re.compile(r"kanban\s+edit\s+[\"']?(t_[0-9a-f]{8}(?:[0-9a-f]{8})?|\$\{?HERMES_KANBAN_TASK\}?)[\"']?[^\\n]*?--(?:title|body)\b")
# Quote-delimited literals on one line, optional raw/format/bytes prefix. Fallback only: ``ast.parse`` is preferred.
_PY_STR_RE = re.compile(
    r"(?:[rRuUbBfF]{1,2})?(?:\"\"\"|''').*?(?:\"\"\"|''')"
    r"|(?:[rRuUbBfF]{0,2})\"(?:[^\"\\]|\\.)*\""
    r"|(?:[rRuUbBfF]{0,2})'(?:[^'\\]|\\.)*'"
)


def _literal_string(src: str) -> Optional[str]:
    """Decode one quote-delimited literal. ``ast.literal_eval`` does not execute it."""
    i = 0
    while i < len(src) and i < 3 and src[i] in "rRuUbBfF":
        i += 1
    if i and i < len(src) and src[i] in "\"'":
        if "b" in src[:i].lower():
            return None
        src = "".join(c for c in src[:i] if c in "rR") + src[i:]
    try:
        val = ast.literal_eval(src)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None
    return val if isinstance(val, str) else None


def _regex_string_literals(code: str) -> list[str]:
    """Decoded quote-delimited literals. Used when ``code`` is not valid Python."""
    out: list[str] = []
    for line in code.splitlines():
        for m in _PY_STR_RE.finditer(line):
            val = _literal_string(m.group(0))
            if val is not None:
                out.append(val)
    return out


def _python_string_values(code: str) -> list[str]:
    """String values in ``code``, with Python escapes decoded, without executing it.

    ``ast.parse`` recovers escapes, raw strings, implicit concatenation and multi-line
    literals. An f-string interpolation is not evaluated: only its constant parts are
    visible. Invalid syntax falls back to quote-delimited literals via ``ast.literal_eval``.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _regex_string_literals(code)
    out: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in seen:
            seen.add(node.value)
            out.append(node.value)
    return out


def check_python(code: str, own: str, paths_fn) -> Optional[str]:
    """execute_code: shell checks on decoded string literals, plus a direct edit regex.

    The value checked is what ``terminal()`` would receive. ``terminal({command!r})`` is
    therefore the shell command, not source text that still contains ``\\\\'``. The snippet
    is not executed, so a command that exists only as a runtime value is not recovered.
    """
    for m in _PY_EDIT_RE.finditer(code or ""):
        if _is_own(m.group(1), own):
            return EDIT_REFUSAL
    for cmd in _python_string_values(code or ""):
        if "kanban" not in cmd or ("complete" not in cmd and "edit" not in cmd and "request-review" not in cmd):
            continue
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
    ``honest-done: ack`` comment on the card (the human accepted the narrower result) clears it.

    One call shares one connection per database (``attention_pass``); core opens the pass around all its calls
    when it knows ``needs_attention.attention_pass``."""
    with attention_pass():
        return _needs_attention(card, paths)


def attention_watch_paths() -> list[Path]:
    """Every file ``needs_attention`` reads, for core's event-driven holder: the boards and their Board replicas.
    Lists files only; opens no database."""
    boards = board_paths()
    return boards + replica_paths(boards)


def attention_next_deadline() -> None:
    """``needs_attention`` reads no clock: its answer moves only when a watched file does."""
    return None


needs_attention.attention_pass = attention_pass  # type: ignore[attr-defined]


@lru_cache(maxsize=1024)
def _attention_classification(classifier, summary: str, title: str) -> bool:
    """Cache pure text classification, never the database-dependent attention verdict.

    Keep the function object in the key so replacement cannot reuse an older policy.
    Callers bypass texts over 16K characters, bounding retention as well as entry count.
    """
    return bool(classifier(summary, title))


def _attention_dropped_work(summary: str, title: str) -> bool:
    classifier = dropped_work_in_handoff
    if len(summary) + len(title) > 16384:
        return bool(classifier(summary, title))
    return _attention_classification(classifier, summary, title)


def _needs_attention(card: dict[str, Any], paths: Optional[list[Path]]) -> bool:
    tid = str(card.get("id") or "")
    summary, title = str(card.get("summary") or ""), str(card.get("title") or "")
    if not tid or not _attention_dropped_work(summary, title):
        return False
    if paths is None:
        state = _pass.state
        boards = state.get("boards")
        if boards is None:
            boards = state["boards"] = board_paths()
        own = [Path(card["board_path"])] if card.get("board_path") else []
        paths = own + [p for p in boards if str(p) not in {str(x) for x in own}]
    for p in paths[:1]:
        try:
            conn, release = _open(p)
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
            release()
    return not valid_followups(tid, summary, paths)


# --- F: acceptance edits need this chat's own user authority (any session) -----------------------------------------

def _acceptance_mod():
    try:
        from . import acceptance  # type: ignore
        return acceptance
    except ImportError:
        import importlib.util
        import sys

        key = "kanban_honest_done_acceptance"
        mod = sys.modules.get(key)
        if mod is None:
            spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("acceptance.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod = sys.modules.setdefault(key, mod)
        return mod


# ``--body $(cat f)`` unquoted: the lexer splits the substitution, so read the raw command for it.
_RAW_BODY_SUBST = re.compile(r"--body(?:=|\s+)\"?(\$\(\s*(?:cat\s+(?:--\s+)?|<\s*)[^()\s\"]+\s*\))")


def _task_with_body_time(task_id: str, paths) -> Optional[dict]:
    """The card plus ``body_set_at``: its newest body edit event, else its creation time."""
    hit = find_task(task_id, paths)
    if not hit:
        return None
    board, row = hit
    row = dict(row)
    try:
        conn = ro_connect(board)
        try:
            ev = conn.execute("SELECT max(created_at) FROM task_events WHERE task_id=? AND kind='edited' "
                              "AND payload LIKE '%\"body\"%'", (task_id,)).fetchone()
            cr = conn.execute("SELECT created_at FROM tasks WHERE id=?", (task_id,)).fetchone()
        finally:
            conn.close()
        row["body_set_at"] = (ev and ev[0]) or (cr and cr[0]) or 0
    except sqlite3.Error:
        row["body_set_at"] = 0
    return row


def check_shell_acceptance(command: str, session_id: str, paths_fn, env=None) -> Optional[str]:
    """F: refusal for ``hermes kanban edit <id> --body ...`` that changes acceptance without a user message id this
    chat received. Bodies built at runtime other than ``$(cat FILE)`` are not recovered (not refused)."""
    acc = _acceptance_mod()
    segs = _segments(command or "")
    assigns = acc.assignments(segs)
    raw_subst = _RAW_BODY_SUBST.findall(command or "")
    for seg in segs:
        hit = _kanban_verb(seg)
        if not hit or hit[0] != "edit":
            continue
        args = hit[1]
        target = _positional(args)
        raw = _opt(args, "--body")
        if not target or raw is None or not TASK_ID_RE.fullmatch(target.strip("\"'")):
            continue
        if raw.startswith("$") and raw_subst:
            raw = raw_subst.pop(0)
        body = acc.resolve_body(raw, assigns, env)
        if body is None:
            continue
        why = acc.check_body_edit(target.strip("\"'"), body, session_id,
                                  lambda t: _task_with_body_time(t, paths_fn()), db=acc.state_db(env))
        if why:
            return why
    return None


def check_python_acceptance(code: str, session_id: str, paths_fn, env=None) -> Optional[str]:
    for cmd in _python_string_values(code or ""):
        if "kanban" in cmd and "edit" in cmd and "--body" in cmd:
            why = check_shell_acceptance(cmd, session_id, paths_fn, env)
            if why:
                return why
    return None
