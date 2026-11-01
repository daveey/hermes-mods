"""user-steps core: find what a chat reply asks the USER to do, keep a per-chat ledger, frame it in the reply.

Pure-ish functions + small classes so tests need no Hermes runtime:
  - extract(): aux-model call (injected ``llm``) -> validated steps + resolved ids; regex fallback on failure.
  - Ledger: ~/.hermes/user-steps/<lineage root>.json (atomic writes, fcntl lock), read across compression lineage.
  - frame(): replace the paragraph holding each step's verbatim span with ``::user-step{...}`` (text kept in an attr).
  - process_reply(): the whole transform_llm_output pipeline, with injected llm / ledger / card ops.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

KINDS = ("approve", "do", "choose", "input")
MAX_NEW_PER_REPLY = 5
MAX_NEW_PER_HOUR = 12
ATTR_BUDGET = 1024
DIRECTIVE = "user-step"
OPTION_MAX = 200
# Retired migration fallback: one generic button that asked the USER to request agent work.
LEGACY_FALLBACK = [{'id': 'request-context', 'label': 'Request complete action', 'effect': 'revise'}]


def home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


# ---- session lineage (compression rotates the stored id) -----------------------------------------------

def _state_db(h: Optional[Path] = None) -> Path:
    return (h or home()) / "state.db"


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


def _row(db: Path, sid: str) -> Optional[tuple]:
    try:
        c = _ro_connect(db, timeout=2)
        try:
            return c.execute("select id, parent_session_id, end_reason, source from sessions where id=?",
                             (sid,)).fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return None


def lineage(session_id: str, db: Optional[Path] = None) -> list[str]:
    """[session_id, parent, grandparent, ...] following only compression hops (newest first)."""
    db = db or _state_db()
    out = [session_id] if session_id else []
    cur = session_id
    for _ in range(24):
        r = _row(db, cur)
        parent = r[1] if r else None
        if not parent or parent in out:
            break
        pr = _row(db, parent)
        if not pr or "compress" not in str(pr[2] or "").lower():
            break
        out.append(parent)
        cur = parent
    return out


def lineage_root(session_id: str, db: Optional[Path] = None) -> str:
    ids = lineage(session_id, db)
    return ids[-1] if ids else session_id


def session_source(session_id: str, db: Optional[Path] = None) -> str:
    r = _row(db or _state_db(), session_id)
    return str(r[3] or "") if r else ""


# ---- ledger --------------------------------------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
MAX_LEDGER_BYTES = 1024 * 1024  # bounded lifecycle snapshots, including JSON parsing


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate ledger JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("invalid ledger JSON constant")


def _validate_ledger(d, root, *, legacy=False):
    if not isinstance(d, dict) or d.get("root") != root:
        raise ValueError("ledger root mismatch or invalid document")
    if not isinstance(d.get("steps"), list) or any(not isinstance(s, dict) for s in d["steps"]):
        raise ValueError("invalid ledger steps")
    if legacy and "revision" not in d:
        return
    revision = d.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("invalid or missing ledger revision")


class Ledger:
    """One JSON file per lineage root: {root, steps:[...], card:{id,board}|null, gen, user_msg}."""

    def __init__(self, root_dir: Optional[Path] = None, db: Optional[Path] = None):
        self.dir = Path(root_dir) if root_dir else home() / "user-steps"
        self.db = db

    def root(self, session_id: str) -> str:
        return lineage_root(session_id, self.db or _state_db())

    def path(self, root: str) -> Path:
        return self.dir / (_SAFE.sub("_", root) + ".json")

    @contextmanager
    def edit(self, root: str):
        self.dir.mkdir(parents=True, exist_ok=True)
        lock = open(self.dir / ".lock", "a+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                d = self._read_strict(root, legacy=True, bounded=False)
            except FileNotFoundError:
                d = {"root": root, "steps": [], "card": None, "gen": 0}
            revision = d.get("revision", 0)
            yield d
            # The writer, not the edit caller/model, owns this counter.
            d["revision"] = revision + 1
            d["updated"] = time.time()
            _validate_ledger(d, root)
            raw = json.dumps(d, ensure_ascii=False, indent=1, allow_nan=False).encode("utf-8")

            p = self.path(root)
            tmp = p.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                with tmp.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, p)
                try:
                    fd = os.open(self.dir, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                except OSError:
                    # Replacement already committed. Do not report an abort or
                    # prevent card synchronization, but surface durability loss.
                    logging.getLogger("user-steps").error(
                        "Ledger committed; directory durability could not be confirmed", exc_info=True)
            finally:
                tmp.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def _read_strict(self, root: str, *, legacy: bool = False, bounded: bool = True) -> dict:
        if not isinstance(root, str) or not root:
            raise ValueError("ledger root must be a nonempty string")
        # No symlinks or blocking special files; size-check and read the same fd.
        def opener(path, flags):
            return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK)
        with open(self.path(root), "rb", opener=opener) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("ledger source must be a regular file")
            if bounded and info.st_size > MAX_LEDGER_BYTES:
                raise ValueError("ledger size exceeds limit")
            raw = stream.read(MAX_LEDGER_BYTES + 1) if bounded else stream.read()
        if bounded and len(raw) > MAX_LEDGER_BYTES:
            raise ValueError("ledger size exceeds limit")
        try:
            d = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
        except RecursionError as exc:
            raise ValueError("ledger JSON nesting exceeds limit") from exc
        _validate_ledger(d, root, legacy=legacy)
        return d

    def read_exact(self, root: str, step_id: str) -> dict:
        """Read only root's file; return {root, revision, step}, never scan/resolve lineage.

        OSError means missing/unreadable source, ValueError invalid/oversized source
        (including legacy files until their next edit), LookupError a missing step.
        Duplicate target IDs are invalid. IDs are opaque exact source keys; a
        directive consumer must separately validate its own ID grammar.
        The detached snapshot carries raw source
        evidence only: resolved status can suppress an ask, but neither it nor
        resolved_by is trusted provenance or authority to reopen anything.
        """
        if not isinstance(step_id, str) or not step_id:
            raise ValueError("step ID must be a nonempty string")
        d = self._read_strict(root)
        matches = [s for s in d["steps"] if s.get("id") == step_id]
        if len(matches) > 1:
            raise ValueError("duplicate ledger step ID")
        if not matches:
            raise LookupError("step not found in exact ledger")
        return {"root": root, "revision": d["revision"], "step": matches[0]}

    def _read(self, root: str) -> dict:
        try:
            d = json.loads(self.path(root).read_text())
            if isinstance(d, dict):
                d.setdefault("steps", [])
                return d
        except (OSError, ValueError):
            pass
        return {"root": root, "steps": [], "card": None, "gen": 0}

    def read(self, session_id: str) -> dict:
        return self._read(self.root(session_id))

    def open_steps(self, session_id: str) -> list[dict]:
        return [s for s in self.read(session_id).get("steps", []) if s.get("status") == "open"]

    def find(self, step_id: str, session_id: str = "") -> tuple[Optional[str], Optional[dict]]:
        """(root, step). Uses the session hint first, then scans every ledger (ids are globally unique)."""
        if session_id:
            root = self.root(session_id)
            for s in self._read(root).get("steps", []):
                if s.get("id") == step_id:
                    return root, s
        for p in sorted(self.dir.glob("*.json")) if self.dir.is_dir() else []:
            try:
                d = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            for s in d.get("steps", []):
                if s.get("id") == step_id:
                    return d.get("root") or p.stem, s
        return None, None


def new_step_id() -> str:
    return "us_" + uuid.uuid4().hex[:10]


# ---- noise stripping / sentences (shared with the regex fallback and the guards) -----------------------

_FENCE = re.compile(r"```.*?(```|\Z)", re.S)


def _strip_noise(text: str) -> str:
    text = _FENCE.sub(" ", text or "")
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    text = re.sub(r"`[^`\n]*`", "X", text)
    return text


def _norm(s: str) -> str:
    s = re.sub(r"[*_`~]", "", s or "")
    s = s.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def sentences(text: str) -> list[str]:
    return [s.strip(" -*•#|") for s in re.split(r"(?<=[.!?])\s+|\n+", _strip_noise(text)) if s.strip(" -*•#|")]


# Asks (regex fallback, carried over from open-asks). Narrow: a false step is noise.
_ASK = re.compile(
    r"(^|\s)(want me to|should i|shall i|do you want( me)? to|would you like( me)? to|ok(ay)? (for me )?to|"
    r"which (one|option) do you want|can you (confirm|approve|decide|pick|choose|check|reply|tell me)|"
    r"could you (confirm|approve|decide|pick|choose|check|reply|tell me))\b[^?]*\?"
    r"|\b(needs?|waiting (on|for)|still needs?) (your|you to)\b[^.?!]{0,60}\b"
    r"(yes|no|yes/no|yes or no|go|go-ahead|ok|okay|approval|decision|call|answer|password|sign-?in|input|pick|choice|"
    r"confirm|approve|decide|click|tap|reply)\b"
    r"|\b(reply|answer|say|tell me) (with )?[\"'“]?(yes|no|go)\b"
    r"|\byour (yes/no|yes or no|go-ahead|go/no-go)\b"
    r"|\b(let me know|tell me) (if|whether|which)\b"
    r"|\bplease (confirm|approve|decide|pick|choose|reply)\b",
    re.I)
# Mentions that are not a live ask: hypotheticals, explicit "nothing for you", past/third person.
_NOT_ASK = re.compile(
    r"\b(if you (ever|later|still|prefer|want|need|would)|unless|in case|whenever you|no action (is )?needed|"
    r"nothing (is )?(waiting|needed|needs?) (on|from|for) you|nothing for you|you (already|have already)|"
    r"you (said|answered|approved|asked)|don'?t need (anything|you)|no (more|further) (input|action)|"
    r"the card asks|it asked|would have asked|next time)\b",
    re.I)
_APPROVE = re.compile(r"\b(want me to|should i|shall i|do you want me to|would you like me to|ok(ay)? (for me )?to|"
                      r"go-ahead|go/no-go|approve|approval)\b", re.I)
_DO = re.compile(r"\b(can you|could you|please) (check|click|tap|open|plug|sign|log|restart|run)\b", re.I)


def regex_steps(text: str) -> list[dict]:
    out: list[dict] = []
    for s in sentences(text or ""):
        if _asks_outside_quotes(s) and not _NOT_ASK.search(s) and all(o["verbatim_span"] != s for o in out):
            kind = "approve" if _APPROVE.search(s) else "do" if _DO.search(s) else "input"
            out.append({"text": s[:300], "kind": kind, "verbatim_span": s, "options": []})
    return out


# ---- the aux-model extractor ---------------------------------------------------------------------------

SYSTEM = """You read an AI assistant's final chat reply to its user and list what the reply asks the USER to do \
or decide right now. Also say which previously-open steps are now resolved.

Return ONLY JSON:
{"steps":[{"text":"...","kind":"approve|do|choose|input","verbatim_span":"...","options":["..."]}],
 "resolved":["<open step id>", ...],
 "repeats":[{"id":"<open step id>","verbatim_span":"..."}],
 "elsewhere":[{"title":"<exact ELSEWHERE title>","verbatim_span":"..."}]}

Kinds:
- approve: the assistant proposes an action and waits for a go/no ("Want me to deploy it?", "OK to delete X?").
- do: the user must perform something outside the chat (click, plug in, sign in, buy, test on a device),
  then tell the assistant it is done.
- choose: the user must pick one of 2-4 named options. Put the options in "options", recommended first, each
  under 40 characters.
- input: the user must type information or an open answer (a name, a value, a free-form decision).

Rules:
- Extract requests, never invent them. A recommendation or implementation plan is NOT a request to choose
  or approve it. Do not add an "another option" choice that the reply never offered.
- Only LIVE asks addressed to the user in THIS reply. Not: mentions of what the user could do someday,
  hypotheticals ("if you ever want...", "unless you..."), past asks, things already answered, asks to third
  parties, text inside code blocks or quotes, rhetorical questions, or the assistant's own next steps.
- "text": one short imperative line for the user (<= 120 chars), e.g. "Approve deploying build 412 to prod".
- "verbatim_span": an EXACT substring copied from the reply (one sentence or line) where the ask is made.
- One step per distinct ask; at most 5.
- The user already sees every OPEN STEP as a tile. A sentence that only restates, reminds about or points at an
  OPEN STEP (the same decision, even reworded or summarised: "X is still waiting for your approval", "they stay in
  your list until you answer them", or the approve/change/decline menu of an open approval) is NOT a new step: put
  {"id": <that open step id>, "verbatim_span": <exact sentence>} in "repeats" instead. When unsure whether it is
  the same decision, list it as a new step.
- "resolved": ids from OPEN STEPS that the USER'S LAST MESSAGE answered, did, declined or made moot, or that the
  reply says are now done. Leave a step out when unsure. A step this reply still asks, restates or points at is
  never resolved (it is a repeat). An automatic notice in USER'S LAST MESSAGE (a Kanban \"✔ [...] done\" notice,
  an \"[owner-wake]\" note) was not typed by the user and answers no step.
- Status reports are not asks: "X is in review", "not yet approved", "still open", "waiting on CI/a worker/a
  reviewer". A Kanban card in review is checked by an automated reviewer, not by the user.
- A sentence that lists, counts or summarizes decisions waiting in other chats, queues or cards ('your For you
  queue holds…', 'still waiting on you in the hub chat: …') is a status report, not a new ask, even when it
  names each decision.
- ELSEWHERE lists asks open in other chats. A sentence restating one of these points at an ask that lives
  elsewhere: never a new step. Put {"title": "<exact ELSEWHERE title>", "verbatim_span": "<exact sentence>"} in
  "elsewhere" instead. Those are neither minted nor framed.
- If the reply says nothing is needed from the user, return no steps unless a sentence explicitly asks the user.
- No asks: {"steps":[],"resolved":[...],"repeats":[...],"elsewhere":[...]}."""


def build_prompt(reply: str, open_steps: list[dict], user_msg: str, elsewhere: Optional[list] = None) -> str:
    opened = "\n".join(f"- {s['id']} ({s.get('kind')}): {s.get('text')}" for s in open_steps) or "(none)"
    titles: list[str] = []
    for raw in elsewhere or []:
        title = str(raw).strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= 30:
            break
    listed = "\n".join(f"- {t}" for t in titles) or "(none)"
    return (f"OPEN STEPS:\n{opened}\n\n"
            f"ELSEWHERE (open asks in other chats; a sentence restating one is never a new step):\n{listed}\n\n"
            f"USER'S LAST MESSAGE:\n{(user_msg or '(none)')[:1500]}\n\n"
            f"ASSISTANT REPLY:\n{(reply or '')[:12000]}")


def _parse_json(raw: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    d = json.loads(m.group(0))
    if not isinstance(d, dict):
        raise ValueError("model output is not an object")
    return d


def _blocks(text: str) -> list[dict]:
    """Markdown blocks split on blank lines, with code fences kept whole: [{start, end, text, code, quote}]."""
    out: list[dict] = []
    lines = (text or "").split("\n")
    pos = 0
    cur: Optional[dict] = None
    fence = False
    for ln in lines:
        start, end = pos, pos + len(ln)
        pos = end + 1
        is_fence = ln.lstrip().startswith("```")
        if fence:
            cur["end"] = end
            if is_fence:
                fence = False
                out.append(cur)
                cur = None
            continue
        if is_fence:
            if cur:
                out.append(cur)
            cur = {"start": start, "end": end, "code": True}
            fence = True
            continue
        if not ln.strip():
            if cur:
                out.append(cur)
                cur = None
            continue
        if cur is None:
            cur = {"start": start, "end": end, "code": False}
        else:
            cur["end"] = end
    if cur:
        out.append(cur)
    for b in out:
        b["text"] = text[b["start"]:b["end"]]
        b["quote"] = not b["code"] and all(l.lstrip().startswith(">") for l in b["text"].split("\n") if l.strip())
    return out


def following_fence(reply: str, block: dict) -> str:
    """The fenced code block that immediately follows ``block`` (only whitespace between), else ''. It is the
    literal payload of the step the block asks for (a command or message to send), so it must stay with the step."""
    blocks = _blocks(reply)
    for i, b in enumerate(blocks):
        if b["start"] == block["start"] and b["end"] == block["end"]:
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if nxt and nxt["code"] and not reply[block["end"]:nxt["start"]].strip():
                return nxt["text"]
            return ""
    return ""


def fence_literal(fence: str) -> str:
    """Inner text of a fenced block (fence lines and info string removed)."""
    lines = (fence or "").strip().split("\n")
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


PAYLOAD_TITLE_MAX = 120


def payload_title(title: str, fence: str) -> str:
    """A step whose payload is a following code block never gets a title that paraphrases it: keep the title only if
    it already quotes the literal; else quote the literal (short, one line) or point at the block below."""
    lit = fence_literal(fence)
    if not lit or lit in (title or ""):
        return title
    if "\n" not in lit and len(lit) <= PAYLOAD_TITLE_MAX:
        return f"Use this exact text: {lit}"
    return "Use the exact text in the block below"


def locate(reply: str, span: str) -> Optional[dict]:
    """The prose block (not code, not quote) whose text contains ``span`` (markdown/whitespace-insensitive)."""
    ns = _norm(span)
    if len(ns) < 4:
        return None
    quoted_only = None
    for b in _blocks(reply):
        if b["code"] or b["quote"]:
            continue
        nb = _norm(re.sub(r"`[^`\n]*`", lambda m: m.group(0).strip("`"), b["text"]))
        if ns in nb:
            # A block that only quotes the phrase ("titled \"Reply yes or no\"") must not hide a later block that
            # really says it: prefer the first block whose unquoted text contains the span.
            if ns in _norm(_blank_quotes(re.sub(r"`[^`\n]*`", lambda m: m.group(0).strip("`"), b["text"]))):
                return b
            quoted_only = quoted_only or b
    return quoted_only


_HANDOFF: dict[str, Any] = {}


def _handoff():
    """ask-gate's shared hand-off detector (plugins/ask-gate/handoff.py), loaded by file path. None if absent."""
    if "mod" not in _HANDOFF:
        _HANDOFF["mod"] = None
        for base in (Path(__file__).resolve().parent.parent, Path(__file__).parent.parent):
            f = base / "ask-gate" / "handoff.py"
            if f.is_file():
                try:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("user_steps_handoff", f)
                    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    _HANDOFF["mod"] = mod
                    break
                except Exception:
                    pass
    return _HANDOFF["mod"]


def is_terminal_handoff(text: str, span: str, reply: str = "") -> bool:
    """A step a shell could do (run/paste a command, add an ssh key, sudo, type a password at a prompt) is never a
    For-you step: the agent must call run_in_user_terminal instead. Physical steps (taps, GUI logins, hardware) stay."""
    h = _handoff()
    if h is None:
        return False
    i = reply.find(span) if span else -1
    ctx = reply[i:i + len(span) + 400] if i >= 0 else span
    return bool(h.find_handoffs(text) or h.find_handoffs(span) or h.find_handoffs(ctx))


# Recommendations are often misread by the extractor as a choice menu. Require
# an actual request in the cited span, not an imperative invented in step.text.
_RECOMMENDATION = re.compile(r"\b(?:recommend(?:ed|ation|ations|s)?|suggest(?:ed|ion|ions|s)?)\b", re.I)
_EXPLICIT_REQUEST = re.compile(
    r"\b(?:can you|could you|would you|will you|you (?:must|need to|have to)|"
    r"(?:needs?|requires?|awaiting|waiting for) your|let me know|tell me|"
    r"do you (?:approve|consent|want|give me permission)|do i have your (?:permission|consent|approval)|"
    r"(?:is|would) it (?:be )?(?:ok|okay|all right) for me|want me to|may i|can i|could i|would you like|should i|shall i|which .{0,80}(?:want|choose|prefer))\b"
    r"|\bplease\s+(?:approve|confirm|authorize|choose|sign in|log in)\b"
    r"|(?:^|[.!;,]\s+)(?:please\s+)?(?:choose|pick|approve|confirm|decide|select|reply|"
    r"sign in|log in|tap|click|scan|plug|open|authorize|grant)\b",
    re.I,
)


def recommendation_without_request(span: str, block: str = "") -> bool:
    # Use the containing paragraph to catch excerpts which omit "Recommended".
    # Quoted labels, code and rhetorical question marks are not requests.
    def request_text(value: str) -> str:
        unquoted = re.sub(r"(?<!\w)'[^'\n]+'", " ", _strip_noise(value))
        return _norm(re.sub(r'["“][^"”\n]*["”]', " ", unquoted))

    context = _norm(block or span)
    clean = request_text(span)
    # Sentence boundaries belong to the source, not an arbitrary extracted
    # excerpt. A colon-prefixed recommendation cannot become an imperative
    # merely because the extractor starts its evidence after that colon.
    source = request_text(block or span)
    grounded = False
    if clean:
        for occurrence in re.finditer(re.escape(clean), source):
            for match in _EXPLICIT_REQUEST.finditer(source):
                # Ignore the boundary punctuation when the evidence begins at
                # the request itself, but retain the source occurrence identity.
                start = match.start() + len(match.group(0)) - len(match.group(0).lstrip(".!;, "))
                if occurrence.start() <= start and match.end() <= occurrence.end():
                    grounded = True
                    break
            if grounded:
                break
    return bool(_RECOMMENDATION.search(context) and not grounded)


def action_identity(step: dict) -> tuple:
    """Exact validated source fields, not a topic/account key or reopen proof.

    Include evidence and ordered choices: identical labels alone cannot bind two
    actions. No further normalization, fuzzy matching, or model-supplied IDs.
    Legacy steps with these fields participate without a separate key migration.
    """
    return (step.get("kind"), step.get("text"), step.get("verbatim_span"),
            tuple(step.get("options") or []))


# A reply that itself says nothing is waiting on the user cannot also carry an ask in a plain status line
# ("The card is in review, not yet approved."). Only an unscoped declaration counts: a whole sentence that is
# the declaration ("Nothing is currently assigned to you."). A scoped one ("nothing is needed from you for the
# deploy", "t_A: nothing for you") says nothing about the rest of the reply, whose real asks may carry no
# request marker ("I need the new live key"). Deliberately NOT "nothing else for you" (one item remains).
_NO_USER_ACTION = re.compile(
    r"(?:(?:so|and|right now|for now|currently),? )?(?:"
    r"(?:there is |there's )?nothing (?:is )?(?:currently )?"
    r"(?:assigned to|needed from|required from|waiting on|waiting for|pending (?:on|for)) you"
    r"|nothing (?:is )?(?:needed|required) (?:from|of) you"
    r"|no (?:user |human )?(?:action|input) (?:is )?(?:needed|required)(?: (?:from|of) you)?"
    r"|you (?:don't|do not) (?:need|have) to do anything"
    r"|there's nothing (?:you need to do|for you to do)"
    r")(?: (?:right now|currently|at the moment|for now))?",
    re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_LIST_MARK = re.compile(r"^(?:[-*\u2022>]\s*|\d+[.)]\s*|#+\s*)+")
_QUOTED = re.compile(r'"[^"\n]*"')
_DIRECT_REQUEST = re.compile(
    r"\?"
    r"|\b(?:please|kindly)\b"
    r"|\b(?:can|could|would|will) you\b"
    r"|\byou(?:'ll| will)? (?:need to|must|have to|should)\b"
    r"|\b(?:let me know|tell me|your call|up to you)\b"
    r"|\b(?:i|we) (?:still )?need (?:you|your|the|a|an|one)\b|\bneeds? your\b|\bwaiting (?:on|for) (?:you|your)\b"
    r"|\b(?:until|once) you\b|\byour (?:sign-?off|approval|go-ahead|ok)\b"
    r"|(?:^|[.!:;,\u2014\u2013]\s*|\n\s*(?:[-*\u2022]|\d+[.)])\s*)(?:please\s+)?"
    r"(?:approve|confirm|decide|choose|pick|select|reply|answer|say|sign in|log in|tap|click|scan|plug|open|"
    r"authorize|grant|allow|enter|paste|type|record|send|go to|run|install|restart|unlock|press|connect|buy|"
    r"accept|review|check|test|try)\b(?!\s+of\b)",
    re.I)


def _plain(text: str) -> str:
    """Markdown emphasis and smart quotes off; line structure kept (bullets/sentence starts matter)."""
    t = re.sub(r"[*_`~]", "", text or "").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"[ \t]+", " ", t).strip()


def declares_no_user_action(reply: str) -> bool:
    """Some sentence of the reply is, in full, an unscoped "nothing is needed from you" declaration."""
    for sentence in _SENTENCE_BREAK.split(_plain(_strip_noise(reply))):
        s = _LIST_MARK.sub("", sentence.strip()).rstrip(" .!").strip()
        if s and _NO_USER_ACTION.fullmatch(s):
            return True
    return False


def directly_asks(span: str) -> bool:
    """The evidence is phrased as a request of the user. Quoted text (someone else's words) does not count."""
    return bool(_DIRECT_REQUEST.search(_QUOTED.sub('""', _plain(span))))


# A paragraph that reports the user's pending decisions, queue or asks in other chats. A step whose evidence
# sits in one is a status line, not a new ask in this chat. Gaps absorb quotes ("Your \"For you\" queue now holds").
_DIGEST_MARK = re.compile(
    r"(?:\bfor you|\byour).{0,40}?\b(?:queue|list|inbox)\b.{0,80}?\b(?:now\s+)?(?:holds|has|contains|shows)\b"
    r"|waiting\s+(?:on|for)\s+you\s+in\b"
    r"|\bin\s+(?:its|their)\s+own\s+chat\b"
    r"|\b(?:decisions?|asks?)\b(?:\s+\w+){0,3}\s+(?:waiting|pending|open)\b(?:\s+\w+){0,3}\s+(?:in|on)\b",
    re.I | re.S)


def in_digest_report(span: str, paragraph: str) -> bool:
    """The span sits in a digest of asks elsewhere, and does not itself ask the user to act in this chat.

    Keep the step only when the span is a direct request and the digest marker is outside the span.
    """
    if not (_DIGEST_MARK.search(paragraph) or _DIGEST_MARK.search(_plain(paragraph))):
        return False
    if _DIGEST_MARK.search(span) or _DIGEST_MARK.search(_plain(span)):
        return True
    return not directly_asks(span)


_ASSENT = re.compile(
    r"^(?:(?:i'?d say|i would say|i think|i guess|so|well|then),?\s+)*"
    r"(?:yes|yeah|yep|yup|sure|ok(?:ay)?|sounds good|go ahead|agreed|absolutely|definitely|of course|fine|"
    r"correct|right|do it|please do|let'?s do it)(?:,?\s+(?:please|then|thanks))?$", re.I)


def assent_echo(text: str) -> bool:
    """A step under 6 words that only expresses assent ("I'd say yes.") is an echo, not an ask."""
    t = re.sub(r"\s+", " ", str(text or "")).strip(" \"'`*_.!")
    return 0 < len(t.split()) < 6 and bool(_ASSENT.match(t))


# ---- quoted text, one item per block, and the question as the title -----------------------------------
_QUOTE_RANGE = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')
# A bare reply prompt ("Reply yes or no here."): it points at a question made elsewhere in the reply.
_REPLY_PROMPT = re.compile(r"\b(?:reply|answer|respond)\b[^.?!\n]{0,30}\b(?:yes|no|go)\b", re.I)


def _blank_quotes(text: str) -> str:
    """``text`` with every quoted range replaced by spaces (same length, so offsets still line up)."""
    return _QUOTE_RANGE.sub(lambda m: " " * len(m.group(0)), text or "")


def span_is_quoted(span: str, block_text: str) -> bool:
    """The ask phrase is only someone's quoted words, never the assistant's own request.

    True when the span lies inside a quoted range of its block, or when it contains quotes and, with the quoted
    parts removed, no longer asks anything (a sentence that merely names a phrase: ``titled "Reply yes or no"``).
    """
    ns = _norm(span)
    if not ns:
        return False
    for m in _QUOTE_RANGE.finditer(block_text or ""):
        if ns in _norm(m.group(0)):
            return True
    if _QUOTE_RANGE.search(span or ""):
        rest = _QUOTE_RANGE.sub(" ", span)
        return not (_DIRECT_REQUEST.search(_plain(rest)) or _ASK.search(rest))
    return False


def _asks_outside_quotes(sentence: str) -> bool:
    """The sentence asks something in the assistant's own words: some ``_ASK`` match starts outside every quoted
    range, and the sentence is not merely naming a quoted phrase (``span_is_quoted``, the model path's rule).
    ``Reply "go" and I'll ship it`` asks (its match starts at the unquoted ``Reply``); ``titled "Reply yes or no
    here."`` does not (its only match starts inside the quotes)."""
    ranges = [m.span() for m in _QUOTE_RANGE.finditer(sentence or "")]
    outside = any(not any(a <= m.start() < b for a, b in ranges) for m in _ASK.finditer(sentence or ""))
    return outside and not span_is_quoted(sentence, sentence)


def _question_in(block_text: str) -> Optional[str]:
    """Last sentence of a prose block that is a question outside quotes, without its bold label."""
    blanked = _blank_quotes(block_text)
    found = None
    pos = 0
    for part in re.split(r"(?<=[.!?])\s+|\n+", blanked):
        i = blanked.find(part, pos)
        if i < 0 or not part.strip():
            continue
        pos = i + len(part)
        if part.rstrip().endswith("?"):
            found = block_text[i:pos]
    if not found:
        return None
    q = _LIST_MARK.sub("", _plain(found).strip())
    q = re.sub(r"^(?:[^:\n]{1,40}:\s+)(?=\S)", "", q) if re.match(r"^[^:\n]{1,40}:\s", q) else q
    q = re.sub(r"\s+", " ", q).strip()
    return (q[:1].upper() + q[1:])[:300] if len(q) >= 12 else None


def question_above(reply: str, block: dict) -> Optional[str]:
    """Nearest question in the same reply section above ``block``: prose blocks only, back to a heading."""
    prior = [b for b in _blocks(reply) if b["start"] < block["start"]]
    for b in reversed(prior[-6:]):
        if b["code"] or b["quote"]:
            continue
        if re.match(r"\s*#{1,6}\s", b["text"]):
            break
        q = _question_in(b["text"])
        if q:
            return q
    return None


def ask_slot(block: dict, span: str) -> tuple:
    """Where an item lives: its block, plus the list line when the block is a list (each bullet is its own ask)."""
    lines = block["text"].split("\n")
    if len(lines) > 1 and all(_LIST_MARK.match(l.strip()) for l in lines[1:] if l.strip()):
        ns = _norm(span)
        for i, ln in enumerate(lines):
            if ns and ns in _norm(ln):
                return (block["start"], i)
    return (block["start"], -1)


def question_title(text: str, span: str, reply: str, block: dict) -> str:
    """The title of an item is the question it answers. A span that is not a question, in a block that tells the
    user to reply yes/no, takes the block's own question or else the nearest question above it; anything else
    keeps the extractor's text."""
    if "?" in _blank_quotes(_plain(span)) or not _REPLY_PROMPT.search(_blank_quotes(_plain(block["text"]))):
        return text
    return _question_in(block["text"]) or question_above(reply, block) or text


def validate(raw_steps: Any, reply: str, open_steps: list[dict], dropped: Optional[list] = None,
             *, continuity: bool = False) -> list[dict]:
    out: list[dict] = []
    # The creation boundary needs repeats too, including those resolved while the
    # model runs. Ordinary extractor callers retain their old deduplication.
    seen: set[Any] = set() if continuity else {_norm(s.get("text", "")) for s in open_steps}
    quiet = declares_no_user_action(reply)
    used_blocks: set[tuple[int, int]] = set()
    for s in raw_steps if isinstance(raw_steps, list) else []:
        if not isinstance(s, dict):
            continue
        text = re.sub(r"\s+", " ", str(s.get("text") or "")).strip()[:300]
        span = str(s.get("verbatim_span") or "").strip()
        kind = str(s.get("kind") or "").strip().lower()
        # Options keep their full text: migration only turns ones literally present in the
        # source into buttons, so a cut-off label would never match (or would show mid-word).
        opts = [re.sub(r"\s+", " ", str(o)).strip()[:OPTION_MAX] for o in (s.get("options") or []) if str(o).strip()][:4]
        if not text or not span:
            continue
        if kind not in KINDS:
            kind = "input"
        if kind == "choose" and len(opts) < 2:
            kind = "input"
        if kind != "choose":
            opts = []
        if _NOT_ASK.search(span):
            continue  # hypothetical / mention-only, whatever the model said
        if assent_echo(span) or assent_echo(text):
            continue  # "I'd say yes." echoes assent; it asks nothing
        if quiet and not directly_asks(span):
            continue  # the reply says nothing waits on the user; a status line is not an ask
        block = locate(reply, span)
        if block is None or _norm(span) not in _norm(block["text"]):
            continue  # full evidence required; locate's framing prefix is not validation
        if span_is_quoted(span, block["text"]):
            continue  # a phrase inside quotation marks is cited, not asked
        slot = ask_slot(block, span)
        if slot in used_blocks:
            continue  # one block (one list line, for bullet lists of distinct asks) yields at most one item
        if in_digest_report(span, block["text"]):
            continue  # a digest of asks that live elsewhere is a status report, not a new tile
        if recommendation_without_request(span, block["text"]):
            continue  # a recommendation is not permission to manufacture a choice
        text = question_title(text, span, reply, block)
        fence = following_fence(reply, block)
        if fence:
            text = payload_title(text, fence)
        candidate = {"text": text, "kind": kind, "verbatim_span": span, "options": opts}
        if fence:
            candidate["payload"] = fence
        key = action_identity(candidate) if continuity else _norm(text)
        if key in seen:
            continue
        if is_terminal_handoff(text, span, reply):
            if dropped is not None:
                dropped.append(text)
            continue  # terminal-doable: the agent runs it via run_in_user_terminal, no For-you card
        seen.add(key)
        used_blocks.add(slot)
        out.append(candidate)
    # Suppression/reuse must not be truncated by a *new* action budget.
    return out if continuity else out[:MAX_NEW_PER_REPLY]


def _elsewhere_titles(raw: Optional[list], limit: int = 30) -> list[str]:
    titles: list[str] = []
    for item in raw or []:
        title = str(item).strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def _span_restates(title: str, span: str) -> bool:
    """The span restates this elsewhere title. An unrelated ask paired with a listed title is not a mapping."""
    key = ask_key(title)
    if len(key) >= _ASK_KEY_MIN and key in ask_key(span):
        return True
    words = _words(title)
    return len(words) >= 3 and _overlap(words, _words(span)) >= _OVERLAP


def elsewhere_mapped(raw: Any, reply: str, titles: list[str]) -> list[dict]:
    """Model mappings whose title is an ELSEWHERE ask and whose span is in the reply.

    A mapped span is not a new step: callers drop it from steps and must not frame it.
    A title the prompt did not list cannot suppress a real ask, and a listed title cannot
    suppress a span that does not restate it.
    """
    known = {ask_key(t) for t in titles if len(ask_key(t)) >= _ASK_KEY_MIN}
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        span = str(item.get("verbatim_span") or "").strip()
        if ask_key(title) not in known or locate(reply, span) is None or not _span_restates(title, span):
            continue
        out.append({"title": title, "verbatim_span": span})
    return out


def without_elsewhere(steps: list[dict], mapped: list[dict]) -> list[dict]:
    drop = {ask_key(m["verbatim_span"]) for m in mapped}
    return [s for s in steps if ask_key(s.get("verbatim_span")) not in drop]


LLM = Callable[[str, str], str]


# A step text shorter than this is too generic to prove a restatement by substring ("Approve it?").
_RESTATE_MIN_CHARS = 20
# A turn the runtime started, not the user: Kanban notices (✔ done, ✖ failed, ⏸ blocked, [Kanban ...]), owner
# wakes, system and chat-close notes. Such a "last message" answers nothing.
_RUNTIME_MSG = re.compile(r"^\s*(?:[\u2714\u2716\u23f8\u26a0]\ufe0f?\s*\[|\[(?:owner-wake|System|chat-cl|Kanban|Automatic)\b)")


def _runtime_message(user_msg: str) -> bool:
    """The turn's last message is a runtime notice. An empty one is unknown (the hook missed it), not a notice."""
    return bool(_RUNTIME_MSG.match(user_msg or ""))


def _still_asked(step: dict, reply: str, repeats: list[dict], user_msg: str = "") -> bool:
    """Does this reply still ask the open step? Then the model's "resolved" for it is wrong and is dropped.

    Only in a turn a runtime notice started (nothing the user said can have answered it): the model itself listed
    the step as a repeat, or the reply quotes the step's text (or the sentence that first asked it). The model
    resolved an open decision whose reply ended "Your call: \\"<the step's text>\\" is in your For you queue" (a
    kanban-notice turn): the tile vanished unanswered and the chat's outcome fell to
    unresolved{no_summary} (hub 20261030_071719_cde14c). After a typed message the model's verdict stands: "Going
    ahead with <step text>" after the user's "go" is an answer, and a re-ask after a decline must stay retired."""
    if not _runtime_message(user_msg):
        return False
    if any(r.get("id") == step.get("id") for r in repeats):
        return True
    body = _norm(reply).replace('"', "")
    for raw in (step.get("text"), step.get("verbatim_span")):
        quoted = _norm(str(raw or "")).replace('"', "")
        if len(quoted) >= _RESTATE_MIN_CHARS and quoted in body:
            return True
    return False


def extract(reply: str, open_steps: list[dict], user_msg: str, llm: Optional[LLM],
            *, continuity: bool = False, elsewhere: Optional[list] = None) -> dict:
    """{steps, resolved, source: 'model'|'regex', error?}.

    ``elsewhere`` is titles of open asks in other chats. Steps the model maps onto
    one of them are omitted: neither minted nor framed.
    """
    ids = {s["id"] for s in open_steps}
    by_id = {s["id"]: s for s in open_steps}
    titles = _elsewhere_titles(elsewhere)
    dropped: list[str] = []
    if llm is not None:
        try:
            d = _parse_json(llm(SYSTEM, build_prompt(reply, open_steps, user_msg, titles)))
            resolved = [str(x) for x in (d.get("resolved") or []) if str(x) in ids]
            raw = d.get("repeats")
            repeats = [{"id": str(x["id"]), "verbatim_span": str(x["verbatim_span"])}
                       for x in (raw if isinstance(raw, list) else []) if isinstance(x, dict) and str(x.get("id")) in ids
                       and isinstance(x.get("verbatim_span"), str) and locate(reply, x["verbatim_span"]) is not None]
            resolved = [x for x in resolved if not _still_asked(by_id[x], reply, repeats, user_msg)]
            mapped = elsewhere_mapped(d.get("elsewhere"), reply, titles)
            steps = without_elsewhere(validate(d.get("steps"), reply, open_steps, dropped, continuity=continuity), mapped)
            return {"steps": steps, "resolved": resolved, "repeats": repeats, "elsewhere": mapped,
                    "source": "model", "dropped": dropped}
        except Exception as exc:  # fall back to the regex detector; resolution then only comes from buttons
            err = f"{type(exc).__name__}: {exc}"[:200]
            dropped.clear()
            return {"steps": validate(regex_steps(reply), reply, open_steps, dropped, continuity=continuity), "resolved": [], "source": "regex",
                    "error": err, "dropped": dropped}
    return {"steps": validate(regex_steps(reply), reply, open_steps, dropped, continuity=continuity), "resolved": [], "source": "regex",
            "dropped": dropped}


# ---- in-message frame ----------------------------------------------------------------------------------

def enc(s: str) -> str:
    """Attr-safe text: the directive grammar forbids braces and double quotes inside attrs."""
    return (s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', "\\q").replace("{", "\\(")
            .replace("}", "\\)"))


def dec(s: str) -> str:
    out, i = [], 0
    m = {"\\": "\\", "n": "\n", "q": '"', "(": "{", ")": "}"}
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in m:
            out.append(m[s[i + 1]])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def directive(ids: list[str], session_id: str, text: str = "") -> str:
    head = f'::{DIRECTIVE}{{id="{",".join(ids)}" s="{_SAFE.sub("", session_id)}"'
    if text:
        head += f' t="{enc(text)}"'
    return head + "}"


def frame(reply: str, steps: list[dict], session_id: str) -> str:
    """Replace each step's paragraph with a directive that carries the paragraph text. A paragraph too long for
    the attr budget falls back to its single line; a line still too long stays as text and the directive goes
    right after it (empty ``t``). Text is never dropped."""
    by_block: dict[tuple, dict] = {}
    for s in steps:
        b = locate(reply, s["verbatim_span"])
        if b is None:
            continue
        key = (b["start"], b["end"])
        by_block.setdefault(key, {"block": b, "ids": []})["ids"].append(s["id"])
    edits: list[tuple[int, int, str]] = []
    for (start, end), g in by_block.items():
        ids = g["ids"]
        btext = g["block"]["text"]
        base = len(directive(ids, session_id, "x")) - 1
        if base + len(enc(btext)) <= ATTR_BUDGET:
            edits.append((start, end, directive(ids, session_id, btext)))
            continue
        # one line of the block
        span = next(s["verbatim_span"] for s in steps if s["id"] == ids[0])
        off = start
        placed = False
        for ln in btext.split("\n"):
            if _norm(span)[:60] in _norm(ln):
                if base + len(enc(ln)) <= ATTR_BUDGET:
                    edits.append((off, off + len(ln), directive(ids, session_id, ln)))
                else:
                    edits.append((off + len(ln), off + len(ln), "\n\n" + directive(ids, session_id)))
                placed = True
                break
            off += len(ln) + 1
        if not placed:
            edits.append((end, end, "\n\n" + directive(ids, session_id)))
    out = reply
    for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + rep + out[end:]
    return out


# ---- rolling "waiting on you" card ---------------------------------------------------------------------

def card_title(open_steps: list[dict]) -> str:
    first = open_steps[0]["text"] if open_steps else ""
    more = f" (+{len(open_steps) - 1} more)" if len(open_steps) > 1 else ""
    t = f"For you: {first}"
    return (t if len(t) <= 100 else t[:99].rstrip() + "…") + more


def card_body(session_ref: str, open_steps: list[dict]) -> str:
    lines = "\n".join(f"{i}. {s['text']}  ({s.get('kind')}, `{s['id']}`)" for i, s in enumerate(open_steps, 1))
    return (f"Chat {session_ref} is waiting on you for these steps. Filed and kept up to date by the "
            "`user-steps` plugin; it archives this card when the last step is resolved.\n\n"
            f"## Steps\n{lines}\n\n"
            "## How this closes\n"
            "- Click the buttons in the chat's **For you** strip (or in the framed box in the message), or just "
            "answer in the chat.\n"
            "- Archiving this card by hand does not resolve the steps; unblocking it sends no answer.\n")


class Cards:
    """Interface for the rolling card. Default implementation lives in the plugin (__init__/plugin_api)."""

    def create(self, session_id: str, title: str, body: str, key: str) -> Optional[dict]:  # -> {id, board}
        raise NotImplementedError

    def update(self, card: dict, title: str, body: str) -> None:
        raise NotImplementedError

    def archive(self, card: dict) -> None:
        raise NotImplementedError


def sync_card(ledger: Ledger, root: str, session_id: str, session_ref: str, cards: Optional[Cards]) -> Optional[dict]:
    """Make the rolling card match the ledger: create while steps are open, update its body, archive at zero."""
    if cards is None:
        return None
    d = ledger._read(root)
    opened = [s for s in d.get("steps", []) if s.get("status") == "open"]
    card = d.get("card")
    sig = hashlib.sha1(json.dumps([s["id"] for s in opened]).encode()).hexdigest()[:12]
    if not opened:
        if card:
            if cards.archive(card) is False:
                return card  # archive failed: keep the pointer so the next sync retries
            with ledger.edit(root) as e:
                e["card"] = None
                e.setdefault("archived_cards", []).append(card.get("id"))
        return None
    title, body = card_title(opened), card_body(session_ref, opened)
    if not card:
        gen = int(d.get("gen", 0)) + 1
        made = cards.create(session_id, title, body, f"user-steps:{root}:{gen}")
        if made and made.get("id"):
            with ledger.edit(root) as e:
                e["card"] = {**made, "sig": sig}
                e["gen"] = gen
            return made
        return None
    if card.get("sig") != sig:
        cards.update(card, title, body)
        with ledger.edit(root) as e:
            if e.get("card"):
                e["card"]["sig"] = sig
    return card


# ---- REST payloads (here, not in plugin_api.py, so edits apply without a backend restart) ----------------

_PUBLIC = ("id", "text", "kind", "options", "status", "created", "session", "paragraph", "resolution", "resolved_at",
           "requestId", "requestOwner", "requestRevision")


def public_step(step: dict) -> dict:
    return {k: step.get(k) for k in _PUBLIC if k in step}


def steps_payload(ledger: "Ledger", session: str) -> dict:
    """GET /steps: open steps oldest first, the rolling card, and every rolling card id this chat ever had."""
    if not session:
        return {"root": None, "steps": [], "card": None, "own_cards": []}
    root = ledger.root(session)
    d = ledger._read(root)
    opened = sorted((s for s in d.get("steps", []) if s.get("status") == "open"), key=lambda s: s.get("created", 0))
    own = [c for c in [(d.get("card") or {}).get("id"), *d.get("archived_cards", [])] if c]
    return {"root": root, "steps": [public_step(s) for s in opened], "card": d.get("card"), "own_cards": own[-50:]}


def _canonical_open(step: dict) -> bool:
    """False only when the step's canonical request is provably decided or retired; any doubt keeps it open."""
    if not step.get("requestId") or not (home() / "user-steps" / "actions.sqlite3").is_file():
        return True
    try:
        r = ActionStore().get(server_owner(step.get("session") or ""), step["requestId"])
        return r["state"] in ("open", "needs_context") and not r.get("resolution")
    except Exception:
        return True


def open_steps(session_id: str, ledger: Optional["Ledger"] = None) -> list[dict]:
    """Public helper (session-outcome): this chat's open steps across its lineage, oldest first, public fields
    plus ``choices``. A step whose canonical request was already decided or withdrawn is left out; an open
    canonical request of the chat with no open ledger step is added (``_open_canonical_only``)."""
    if not session_id:
        return []
    lg = ledger or Ledger()
    opened = sorted(lg.open_steps(session_id), key=lambda s: s.get("created", 0))
    out = [{**public_step(s), **({"choices": s["choices"]} if s.get("choices") else {})}
           for s in opened if _canonical_open(s)]
    return out + _open_canonical_only(session_id, lg, opened)


def _open_canonical_only(session_id: str, lg: "Ledger", opened: list[dict]) -> list[dict]:
    """Open, undecided canonical requests of this chat's lineage that no open ledger step mirrors.

    The For you queue shows canonical requests; the ledger is a legacy mirror. When the two disagree (the extractor
    retired the ledger step of a backfilled request while the request itself stayed open), the chat's outcome must
    follow what the user sees: a ``decision``, not unresolved{no_summary} (hub 20261030_071719_cde14c). Any error
    adds nothing."""
    if not (home() / "user-steps" / "actions.sqlite3").is_file():
        return []
    mirrored_rids = {s.get("requestId") for s in opened if s.get("requestId")}
    mirrored_steps = {s.get("id") for s in opened}
    out: list[dict] = []
    try:
        store = ActionStore()
        for sid in lineage(session_id, lg.db) or [session_id]:
            for r in store.requests(server_owner(sid)):
                if r.get("state") not in ("open", "needs_context") or r.get("resolution"):
                    continue
                aliased = {a.get("id") for a in r.get("aliases") or [] if a.get("type") == "user_step"}
                if r["requestId"] in mirrored_rids or aliased & mirrored_steps:
                    continue
                choices = [{"label": c["label"], "value": str(c.get("id") or c["label"])}
                           for c in r.get("choices") or [] if c.get("label")]
                out.append({"id": r["requestId"], "text": r.get("title", ""), "kind": r.get("kind"),
                            "status": "open", "session": sid, **({"choices": choices} if choices else {})})
    except Exception:
        return out
    return out


def add_step(session_id: str, question: str, options: list, *, ledger: Optional["Ledger"] = None,
             now: Optional[float] = None) -> dict:
    """Public helper (session-outcome): record one explicit ask, no extraction. ``options`` are labels or
    {label, value}; two or more make a ``choose`` step whose labels become its buttons. An open step with the
    same question in this chat is returned instead of a duplicate. Grants nothing."""
    q = re.sub(r"\s+", " ", str(question or "")).strip()[:300]
    if not session_id or not q:
        raise ValueError("session_id and question are required")
    choices = []
    for o in options or []:
        label = str((o.get("label") if isinstance(o, dict) else o) or "").strip()[:OPTION_MAX]
        if label:
            value = o.get("value", label) if isinstance(o, dict) else label
            choices.append({"label": label, "value": str(value)[:OPTION_MAX]})
    choices = choices[:4]
    labels = [c["label"] for c in choices]
    kind = "choose" if len(labels) >= 2 else "input"
    lg = ledger or Ledger()
    root = lg.root(session_id)
    with lg.edit(root) as d:
        for s in d["steps"]:
            if s.get("status") == "open" and s.get("session") == session_id and _norm(s.get("text", "")) == _norm(q):
                return dict(s)
        step = {"id": new_step_id(), "text": q, "kind": kind, "verbatim_span": q,
                "options": labels if kind == "choose" else [], "choices": choices, "status": "open",
                "created": now or time.time(), "session": session_id, "source": "session_outcome",
                # literal_options() only turns labels found in the source paragraph into buttons
                "paragraph": q + ("\n\nOptions: " + " / ".join(labels) if labels else "")}
        d["steps"].append(step)
    return dict(step)


def retitle_open_steps(session_id: str, reply: str, *, ledger: Optional["Ledger"] = None) -> list[str]:
    """Repair open steps minted before ``question_title`` existed: a step whose text is the recommendation
    sentence ("I'd say yes: ...") takes the question the reply asked, found via ``question_title`` in the reply
    that produced it. Only open, non-canonical-revised steps whose span sits in ``reply`` change; returns ids."""
    lg = ledger or Ledger()
    fixed: list[str] = []
    # A stored reply carries its asks as directives (t="..."); the quotes around t would blank the very text we read.
    reply = re.sub(r'::' + DIRECTIVE + r'\{[^{}]*?\bt="((?:[^"\\]|\\.)*)"\}', lambda m: dec(m.group(1)), reply or "")
    with lg.edit(lg.root(session_id)) as d:
        for s in d["steps"]:
            span = str(s.get("verbatim_span") or "")
            if s.get("status") != "open" or s.get("session") != session_id or not span:
                continue
            block = locate(reply, span)
            if block is None or _norm(span) not in _norm(block["text"]):
                continue
            title = question_title(s.get("text", ""), span, reply, block)
            if title and title != s.get("text"):
                s["text"] = title
                fixed.append(s["id"])
    return fixed


# ---- resolve / pipeline --------------------------------------------------------------------------------

def resolve(ledger: Ledger, root: str, ids: list[str], resolution: str, by: str) -> list[str]:
    done: list[str] = []
    with ledger.edit(root) as d:
        for s in d.get("steps", []):
            if s.get("id") in ids and s.get("status") == "open" and not s.get('requestId'):
                s.update(status="resolved", resolution=(resolution or "")[:200], resolved_by=by,
                         resolved_at=time.time())
                done.append(s["id"])
    return done


HELD_REPLY = "This reply has been held because it repeats a retired request. No new action is requested."
# Resolution text of a step whose request was withdrawn: no decision; older renderers prefix it with a check.
NO_LONGER_NEEDED = "No longer needed"


def process_reply(reply: str, session_id: str, *, user_msg: str = "", llm: Optional[LLM] = None,
                  ledger: Ledger, cards: Optional[Cards] = None, framed: bool = True,
                  session_ref: str = "", now: Optional[float] = None,
                  action_store=None, action_owner=None, turn_requests=(), card_asks=()) -> dict:
    """Run one final reply through the pipeline. Returns {text (None = unchanged), steps, resolved, source}.

    ``turn_requests``: ids of requests the producer tool published or revised for this session in the turn
    this reply ends (server-recorded provenance). Only those can absorb a title or menu restatement.
    ``card_asks``: ``kanban_asks`` entries (blocked cards' own asks linked to this chat or named by the turn's
    runtime message). A step restating one publishes nothing; the ledger records it as ``restates_card_ask``."""
    # A positive retirement finding must survive later persistence/card failures.
    # Local state is not model-supplied and grants no reopen/execution authority.
    retired = []
    try:
        return _process_reply(reply, session_id, user_msg=user_msg, llm=llm, ledger=ledger,
                              cards=cards, framed=framed, session_ref=session_ref, now=now,
                              retired=retired, action_store=action_store, action_owner=action_owner,
                              turn_requests=turn_requests, card_asks=card_asks)
    except Exception:
        if not retired:
            raise
        logging.getLogger("user-steps").exception("publication failed after retirement; preserving hold")
        return {"text": HELD_REPLY, "steps": [], "resolved": [], "source": "retirement-hold"}


def _process_reply(reply: str, session_id: str, *, user_msg: str, llm: Optional[LLM],
                   ledger: Ledger, cards: Optional[Cards], framed: bool,
                   session_ref: str, now: Optional[float], retired: list,
                   action_store=None, action_owner=None, turn_requests=(), card_asks=()) -> dict:
    """Reconcile exact actions under the source lock before creation/publication.

    Resolution is suppression-only. Neither model metadata nor a later repeated
    extraction can reopen a source action. This is not semantic deduplication.
    """
    now = now or time.time()
    root = ledger.root(session_id)
    opened = ledger.open_steps(session_id)
    if not opened and not _worth_asking(reply):
        return {"text": None, "steps": [], "resolved": [], "source": "skip"}
    # Producer-published asks are tiles too: the model must see them to report a restatement as a repeat.
    # Their ids only feed "repeats"; ledger resolution below ignores any step that has a requestId.
    listed = list(opened)
    tiles = {s['id']: tile_texts(s) for s in opened if s.get('id')}
    if action_store is not None:
        mirrored = {s.get('requestId') for s in opened}
        for a in open_asks(action_store, action_owner):
            if a['id'] not in mirrored:
                listed.append({'id': a['id'], 'kind': a['kind'], 'text': a['title']})
                tiles[a['id']] = tile_texts(a)
    # Blocked cards' own asks: listed so the model can name a relay of one as a repeat (its id is never a ledger
    # step, so it is never resolved here and never framed).
    card_asks = [e for e in card_asks or () if isinstance(e, dict) and e.get('kind') == KANBAN_ASK]
    listed += [{'id': e['id'], 'kind': e['kind'], 'text': card_ask_listing(e)} for e in card_asks]
    tiles.update({e['id']: tile_texts(e) for e in card_asks})
    # Other chats' open asks are only a relay source in a turn a runtime notice started (relay provenance). In a
    # turn the user started, an equal or similar title elsewhere is no source association: never suppress by it.
    relay = _runtime_message(user_msg)
    elsewhere = elsewhere_titles(action_store, action_owner) if action_store is not None and relay else []
    res = extract(reply, listed, user_msg, llm, continuity=True, elsewhere=elsewhere)
    card_ids = {e['id'] for e in card_asks}
    res["resolved"] = [x for x in res["resolved"] if x not in card_ids]  # a card's ask is never resolved from chat
    # A repeat the reply does not corroborate is neither framed nor mapped: the other rules judge that sentence.
    # (``_still_asked`` already ran inside extract on the model's raw repeats and is unchanged.)
    res["repeats"], uncorroborated = split_repeats(res.get("repeats") or (), tiles, reply)
    if uncorroborated:
        with ledger.edit(root) as d:
            log_ = d.setdefault("repeat_checks", [])
            for r in uncorroborated:
                log_.append({"verdict": "repeat_uncorroborated", "tile": r.get("id"),
                             "verbatim_span": r.get("verbatim_span"), "session": session_id, "at": now})
            del log_[:-50]
    card_hits = [(s, card_ask_for(s, card_asks, res.get("repeats") or (), reply)) for s in res["steps"]]
    restated = [(s, e) for s, e in card_hits if e is not None]
    res["steps"] = [s for s, e in card_hits if e is None]
    # The model may also leave a relay out of "steps" and report it only as a repeat of the card's id: record that
    # too, so every restatement shows in the ledger whichever way the extractor reported it.
    by_id, hit_ids = {e['id']: e for e in card_asks}, {e['id'] for _, e in restated}
    for rep in res.get("repeats") or ():
        e = by_id.get(rep.get("id")) if isinstance(rep, dict) else None
        if e is not None and e['id'] not in hit_ids:
            hit_ids.add(e['id'])
            restated.append(({"text": None, "verbatim_span": rep.get("verbatim_span")}, e))
    published, resolved = [], []
    text = reply
    changed = False
    if restated:
        # The card's own ask is the request: nothing is minted, framed or held for it. Record why, bounded.
        with ledger.edit(root) as d:
            log_ = d.setdefault("card_asks", [])
            for s, e in restated:
                log_.append({"verdict": "restates_card_ask", "card": e["task_id"], "board": e["board"],
                             "id": e["id"], "text": s.get("text"), "verbatim_span": s.get("verbatim_span"),
                             "session": session_id, "at": now})
            del log_[:-50]
    if res["steps"] or res["resolved"]:
        # Re-read after extraction under Ledger.edit's flock. Resolve, match,
        # budget, and append in one transaction; never decide from opened above.
        with ledger.edit(root) as d:
            seen = {_norm(s.get("text", "")) for s in d["steps"] if s.get("status") == "open"}
            for step in d["steps"]:
                if action_store is not None and step.get('requestId') and step.get('session') == session_id:
                    current = action_store.get(action_owner, step['requestId'])
                    if current['state'] == 'withdrawn':
                        # Retired without a decision: a later re-ask is a fresh ask, never a held repeat.
                        if step.get('status') == 'open':
                            step.update(status='resolved', resolution=f'{NO_LONGER_NEEDED}.',
                                        resolved_by='canonical', resolved_at=now)
                        step['withdrawn'] = True
                    elif current['state'] in ('superseded', 'resolved', 'cancelled'):
                        step.update(status='resolved', resolution='canonical request retired',
                                    resolved_by='canonical', resolved_at=now)
                if step.get("id") in res["resolved"] and step.get("status") == "open" and not step.get("requestId"):
                    step.update(status="resolved", resolution="answered in chat", resolved_by="model",
                                resolved_at=now)
                    resolved.append(step["id"])
            matches = [(s, [old for old in d["steps"] if action_identity(old) == action_identity(s)])
                       for s in res["steps"]]
            # A withdrawn ask was never answered or declined, so asking it again is not a repeat.
            suppressed = [s for s, old in matches
                          if any(x.get("status") == "resolved" and not x.get("withdrawn") for x in old)]
            held = False
            if suppressed:
                # Evidence does not delimit dependent instructions in other blocks.
                # Hold the whole draft rather than leave an orphaned approval ask.
                retired.extend(suppressed)
                text, held = HELD_REPLY, True
            recent = sum(1 for s in d["steps"] if now - s.get("created", 0) < 3600)
            budget = min(MAX_NEW_PER_REPLY, max(0, MAX_NEW_PER_HOUR - recent))
            asks = open_asks(action_store, action_owner, d["steps"]) if action_store is not None else []
            # The prompt's bounded ELSEWHERE list is advisory, not a publication guard.
            # Recheck all other-session titles after extraction; suppression only, never
            # borrow their ownership, frame their ids, or decide/revise their requests.
            # Relay provenance required: only a turn a runtime notice started relays another chat's ask. In a
            # turn the user started, an equal title is no source association: that ask stays its own request.
            other_keys = elsewhere_open_keys(action_store, action_owner) if relay else set()
            mapped: dict[str, dict] = {}
            # Retain old label dedup only for genuinely new actions, never use it
            # as an authority binding or to bypass exact retirement/reuse.
            for candidate, prior in matches:
                block = locate(text, candidate["verbatim_span"])
                if held or candidate in suppressed or block is None:
                    continue
                key, span = ask_key(candidate.get('text')), ask_key(candidate.get('verbatim_span'))
                if ((len(key) >= _ASK_KEY_MIN and key in other_keys)
                        or (len(span) >= _SPAN_KEY_MIN and len(span.split()) >= 5 and span in other_keys)):
                    continue
                if action_store is not None:
                    same = same_open_ask(candidate, asks, res.get("repeats") or (), mapped, turn_requests, reply=text)
                    if same is not None:
                        # A re-ask of an open tile: frame it onto that tile, never mint a second one.
                        mapped.setdefault(ask_key(candidate["verbatim_span"]), same)
                        if same["step"] is not None and all(p.get("id") != same["step"]["id"] for p in published):
                            published.append(dict(same["step"]))
                        continue
                existing = next((s for s in prior if s.get("status") == "open"), None)
                if existing is not None:
                    published.append(dict(existing))
                    continue
                if budget <= 0 or (action_store is None and _norm(candidate["text"]) in seen):
                    continue
                step = {**candidate, "id": new_step_id(), "status": "open", "created": now,
                        "session": session_id,
                        "paragraph": block["text"] + ("\n\n" + candidate["payload"] if candidate.get("payload") else "")}
                if action_store is not None:
                    canonical = action_store.migrate_step(action_owner, step)
                    step.update(requestId=canonical["requestId"], requestOwner=canonical["owner"],
                                requestRevision=canonical["revision"])
                    made = {'id': canonical['requestId'], 'kind': canonical['kind'], 'title': canonical['title'],
                            'step': step, 'fresh': True}
                    asks.append(made)
                    mapped.setdefault(ask_key(candidate["verbatim_span"]), made)
                d["steps"].append(step)
                published.append(dict(step))
                seen.add(_norm(candidate["text"]))
                budget -= 1
                changed = True
    if action_store is not None and text != HELD_REPLY and res.get("repeats"):
        # A sentence the model marked as a repeat of an open extracted tile is framed onto that tile too, so the
        # message points at the existing tile instead of reading as a new ask. Framing only: nothing is created.
        by_id = {s.get("id"): s for s in ledger.open_steps(session_id) if s.get("requestId")}
        mapped_spans = {ask_key(m.get("verbatim_span")) for m in (res.get("elsewhere") or [])}
        for r in res["repeats"]:
            step = by_id.get(r["id"])
            if step is not None and all(p.get("id") != step["id"] for p in published) \
                    and locate(text, r["verbatim_span"]) is not None \
                    and repeat_corroborated(r["verbatim_span"], _paragraph(text, r["verbatim_span"]), tile_texts(step)) \
                    and ask_key(r.get("verbatim_span")) not in mapped_spans:
                published.append({**step, "verbatim_span": r["verbatim_span"]})
    if published and framed:
        text = frame(text, published, session_id)
    if changed or resolved:
        sync_card(ledger, root, session_id, session_ref or f"@session:default/{session_id}", cards)
    return {"text": text if text != reply else None, "steps": published, "resolved": resolved, "source": res["source"],
            "dropped": res.get("dropped") or [],
            **({"card_asks": [e["id"] for _, e in restated]} if restated else {}),
            **({"error": res["error"]} if res.get("error") else {})}


_WORTH = re.compile(
    r"\?|\byou(r|'ll|'d)?\b|\bplease\b|\b(let|tell) me\b|\bneeds? (your|you)\b"
    # Imperative verbs also count after a comma or "or/and/then": "To get it, tap the bar, or go to Settings"
    # names no "you" and starts no sentence with the verb, and a skip here never reaches the model.
    r"|(^|[.!:,;]\s+|\b(?:or|and|then)\s+|\n\s*(?:[-*•]|\d+[.)])?\s*)(plug|open|click|tap|sign|log|run|check|install|restart|reboot|reply|"
    r"send|grant|allow|approve|confirm|pick|choose|decide|test|try|buy|enter|paste|scan|unlock|turn|press|hold|"
    r"connect|charge|go to|update|add|remove|delete|upload|share|accept)\b", re.I | re.M)


def _worth_asking(reply: str) -> bool:
    """Cheap pre-filter so a plain report with no open steps never costs a model call."""
    t = _strip_noise(reply or "")
    return len(t.strip()) >= 15 and bool(_WORTH.search(t))


def reminder(open_steps: list[dict]) -> str:
    lines = ["[user-steps] Steps still waiting on the user in this chat (shown in the Desktop 'For you' strip "
             "with buttons; a click arrives as a short user message like \"✓ Done: …\", \"Go: …\", \"Chose: …\"):"]
    for s in open_steps[:10]:
        opts = f" options: {' | '.join(s.get('options') or [])}" if s.get("options") else ""
        lines.append(f"- {s['id']} ({s.get('kind')}): {s.get('text')}{opts}")
    lines.append("Act on any the user's message answers. Don't re-ask an open step verbatim; the strip already "
                 "shows it. Steps are marked resolved automatically from the user's reply.")
    return "\n".join(lines)


# ---- one decision, one open request ----------------------------------------------------------------------
# The aux model rewords every restatement of an ask, so exact action identity alone re-minted a tile per reply
# (hub chats 37ae77/67cb2d, MacBook 6212f7/f0271d). These rules map a re-ask to the ONE open, undecided request
# of the same session it repeats. They only ever prevent a new tile; they never decide, resolve or withdraw
# anything, and a decided/withdrawn/closed request is never a target. When a rule is not sure, the ask is kept.

_EXTRACTED_KIND = {'approve': 'approval', 'choose': 'choice', 'input': 'input', 'do': 'external_step'}
_MENU = re.compile(r"\bapprov\w*\b.*\b(?:chang|revis)\w*\b.*\bdeclin\w*", re.I | re.S)
_MENU_WORDS = re.compile(r"^(?:approv\w*|chang\w*|revis\w*|declin\w*|choos\w*|request\w*|plan|whether|either|"
                         r"decide|pick|option\w*)$")
_ASK_KEY_MIN = 8          # a title/text key shorter than this ("OK?") identifies nothing
_SPAN_KEY_MIN = 30        # an evidence sentence must be this specific to identify an ask on its own
_OVERLAP = 0.6


def ask_key(text) -> str:
    """Comparison key for a title or evidence sentence: markdown, list marks and end punctuation off."""
    return _LIST_MARK.sub("", _norm(str(text or ""))).strip(" ?.!:;,")


def _words(text, drop=None) -> set:
    out = {w for w in re.findall(r"[a-z0-9][a-z0-9'-]{3,}", _CARD_ID.sub(" ", _norm(str(text or ""))))}
    return {w for w in out if not (drop and drop.match(w))}


def _overlap(a: set, b: set) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def elsewhere_titles(store, owner, limit=30) -> list[str]:
    """Titles of open, undecided requests in other sessions of this gateway and profile.

    Newest first, at most 30. Titles only: a restatement of one of these is not a new ask.
    """
    if store is None or not owner or not owner.get('storedSessionId'):
        return []
    cap = max(0, min(int(limit), 30))
    with store._tx() as c:
        rows = c.execute(
            'SELECT v.body FROM requests r JOIN revisions v '
            'ON v.namespace=r.namespace AND v.id=r.id AND v.revision=r.revision '
            'WHERE r.namespace=? AND r.session!=? AND r.state IN (\'open\',\'needs_context\') '
            'AND r.resolution IS NULL ORDER BY r.rowid DESC LIMIT ?',
            (store._ns(owner), owner['storedSessionId'], cap)).fetchall()
    out: list[str] = []
    for row in rows:
        try:
            title = str(json.loads(row[0]).get('title') or '').strip()
        except (ValueError, TypeError):
            continue
        if title and title not in out:
            out.append(title)
    return out


def elsewhere_open_keys(store, owner) -> set[str]:
    """All undecided other-session title keys in this namespace, for suppression only.

    Unlike the model's bounded title context this read has no recency cap. Exact
    normalized equality only: no fuzzy topic/menu matching across owner boundaries.
    This is a publication-time snapshot, not atomic cross-session admission.
    """
    if store is None or not owner or not owner.get('storedSessionId'):
        return set()
    with store._read() as c:
        rows = c.execute(
            'SELECT v.body FROM requests r JOIN revisions v '
            'ON v.namespace=r.namespace AND v.id=r.id AND v.revision=r.revision '
            "WHERE r.namespace=? AND r.session!=? AND r.state IN ('open','needs_context') "
            'AND r.resolution IS NULL',
            (store._ns(owner), owner['storedSessionId'])).fetchall()
    return {ask_key(json.loads(row[0]).get('title')) for row in rows}


def open_asks(store, owner, steps=()) -> list[dict]:
    """[{id, kind, title, step}] for this session's open, undecided canonical requests, oldest first.
    ``step`` is the open legacy ledger step mirroring the request (extracted asks), else None."""
    sid = owner['storedSessionId']
    by_rid = {s['requestId']: s for s in steps
              if s.get('requestId') and s.get('status') == 'open' and s.get('session') == sid}
    return [{'id': r['requestId'], 'kind': r['kind'], 'title': r['title'], 'step': by_rid.get(r['requestId']),
             'labels': [c.get('label') for c in r.get('choices', [])]}
            for r in store.requests(owner)
            if r['state'] in ('open', 'needs_context') and not r.get('resolution')]


# ---- a blocked Kanban card's own ask ----------------------------------------------------------------------
# A blocked card with a human_request already IS the request in For you. A reply that relays it (a chat-closeout
# "tell the user once", a Kanban blocked notice) minted a second, unlinked tile for the same decision: whichever
# David answered, the other went stale (MacBook us_c9a2e43afb restating t_01a195f002051277). These entries let the
# extractor and the gate see those asks; a step that restates one publishes nothing. Read-only: a card's ask is
# never answered, decided, edited or unblocked from here.

KANBAN_ASK = 'kanban_ask'
_CARD_ASK_OVERLAP = 0.6


def _card_notice(user_msg: str) -> bool:
    """A runtime turn that may name cards whose asks this reply relays. ``_runtime_message`` misses a bare
    "[chat-closeout]" (its ``chat-cl\\b`` needs a word end), which reaches the hook unlabeled when the row has no
    display_kind; that check is left as is for its other callers."""
    return _runtime_message(user_msg) or (user_msg or "").lstrip().startswith("[chat-closeout]")


def kanban_asks(session_id: str, user_msg: str = "", *, boards=None, db: Optional[Path] = None) -> list[dict]:
    """[{id: 'kanban:<board>:<task_id>', kind: 'kanban_ask', text: human_request.action, ...}] for blocked cards
    with a human_request on every live local board that are linked to this session's lineage
    (kanban_notify_subs.chat_id) or named in this turn's runtime message (chat-closeout, Kanban notice).
    An unreadable board contributes nothing (fail open: the reply's asks publish as before)."""
    if not session_id:
        return []
    chats = set(lineage(session_id, db)) | {session_id}
    named = set(_CARD_ID.findall(user_msg or "")) if _card_notice(user_msg) else set()
    out: list[dict] = []
    for slug, path in (boards if boards is not None else kanban_boards()):
        try:
            c = _ro_connect(path, timeout=2)
        except sqlite3.Error:
            continue
        try:
            marks = ",".join("?" * len(chats))
            try:
                linked = {r[0] for r in c.execute(
                    f"SELECT DISTINCT task_id FROM kanban_notify_subs WHERE chat_id IN ({marks})", tuple(chats))}
            except sqlite3.Error:
                linked = set()
            ids = linked | named
            if not ids:
                continue
            cols = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
            title_col = "title" if "title" in cols else "''"
            rows = c.execute(
                f"SELECT id, human_request, {title_col} FROM tasks WHERE status='blocked' "
                f"AND human_request IS NOT NULL AND id IN ({','.join('?' * len(ids))})", tuple(sorted(ids))).fetchall()
        except sqlite3.Error:
            continue
        finally:
            c.close()
        for tid, raw, title in rows:
            try:
                hr = json.loads(raw) if isinstance(raw, str) else None
            except ValueError:
                hr = None
            hr = hr if isinstance(hr, dict) else {}
            action = str(hr.get("action") or "").strip()
            if not action:
                continue
            labels = [str(ch.get("label")) for ch in (hr.get("choices") or []) if isinstance(ch, dict) and ch.get("label")]
            out.append({"id": f"kanban:{slug}:{tid}", "kind": KANBAN_ASK, "text": action, "board": slug,
                        "task_id": tid, "labels": labels, "title": str(title or "").strip()})
    return out


def card_ask_listing(entry: dict) -> str:
    """How the extractor prompt shows a card's ask: ``card <id> "<title>": ask: <action>``. Relays name the card by
    its title topic ("unblock the fseventsd card"), so the model needs the title to see a relay as a repeat."""
    title = str(entry.get("title") or "").replace('"', "'").strip()
    return f'card {entry.get("task_id")}' + (f' "{title}"' if title else "") + f': ask: {entry.get("text")}'


# Generic words a relay shares with any card's title: never enough to say which card a relay names.
_GENERIC_CARD_WORDS = frozenset({"card", "cards", "agent", "window", "request", "macbook", "hermes", "prompt",
                                 "terminal"})
# Function words and the decision verbs themselves: not content, so they identify no card either.
_FUNCTION_WORDS = frozenset({
    "unblock", "unblocks", "archive", "archives", "approve", "that", "this", "these", "those", "then", "than",
    "with", "from", "into", "onto", "when", "what", "which", "where", "while", "your", "yours", "it's", "have",
    "been", "will", "once", "only", "still", "there", "their", "they", "them", "about", "after", "before",
    "should", "would", "could", "also", "just", "more", "some", "each", "does", "done", "need", "needs", "here",
    "it'll", "you're", "let's", "over", "under", "back", "again", "both", "either", "please"})
_CARD_DECISION = re.compile(r"\b(?:unblock|archive)\b", re.I)
_CARD_NOUN = re.compile(r"\bcards?\b", re.I)
# "Unblock X, then approve Y" / "Unblock X and approve Y": the follow-on is the card's own next step.
_FOLLOW_ON = re.compile(r"\s*(?:[,;]\s*)?\b(?:and then|then|and|after that)\b\s+|\s*;\s+", re.I)
_CARD_TITLE_OVERLAP = 0.5


def _distinctive(text) -> set:
    return {w for w in _words(text) if w not in _GENERIC_CARD_WORDS and w not in _FUNCTION_WORDS}


def _first_action(text) -> str:
    t = str(text or "")
    m = _FOLLOW_ON.search(t)
    return t[:m.start()].strip(" ,;") if m and m.start() > 0 else ""


def _names_card(named: set, task_id) -> bool:
    tid = str(task_id or "")
    return bool(tid) and any(tid == n or tid.startswith(n) for n in named)


def card_ask_for(candidate: dict, entries: list[dict], repeats=(), reply: str = "") -> Optional[dict]:
    """The blocked card's ask ``candidate`` (an extracted step) restates, or None.

    Covered when: the model marked this exact sentence as a repeat of the card's entry and the sentence (or its
    paragraph) corroborates it (``repeat_corroborated``); the step's text or sentence contains the card's ask (or a
    specific sentence of the reply is part of it); its menu offers at least two of the card's choice labels; most
    of the step's own words are the ask's words (a paraphrase), which needs at least three content words on both
    sides so a short generic ask never matches. For an entry that carries the card's title, also: the step tells
    the user to unblock or archive a card and shares a distinctive word with exactly one listed card's title (or
    names its id); or at least half its words are the card's title plus ask words. A compound step ("Unblock X and
    approve the Touch ID prompt") is covered when its first action is: the follow-on is the card's own next step."""
    cards = [e for e in entries or () if e.get("kind") == KANBAN_ASK]
    if not cards:
        return None
    hit = _card_ask_match(candidate, cards, repeats, reply)
    if hit is not None:
        return hit
    first = {"text": _first_action(candidate.get("text")), "verbatim_span": _first_action(candidate.get("verbatim_span")),
             "options": []}
    if first["text"] or first["verbatim_span"]:
        return _card_ask_match(first, cards, (), "")
    return None


def _card_ask_match(candidate: dict, cards: list[dict], repeats=(), reply: str = "") -> Optional[dict]:
    text, span = ask_key(candidate.get("text")), ask_key(candidate.get("verbatim_span"))
    by_id = {e["id"]: e for e in cards}
    if len(span) >= _ASK_KEY_MIN:
        for r in repeats or ():
            if isinstance(r, dict) and r.get("id") in by_id and ask_key(r.get("verbatim_span")) == span \
                    and repeat_corroborated(candidate.get("verbatim_span"), _paragraph(reply, candidate.get("verbatim_span")),
                                            tile_texts(by_id[r["id"]])):
                return by_id[r["id"]]
    long_span = len(span) >= _SPAN_KEY_MIN and len(span.split()) >= 5
    options = {ask_key(o) for o in candidate.get("options") or [] if ask_key(o)}
    raw = f"{candidate.get('text') or ''} {candidate.get('verbatim_span') or ''}"
    mine = _words(candidate.get("text")) | _words(candidate.get("verbatim_span"))
    named = set(_CARD_ID.findall(raw))
    for e in reversed(cards):
        action = ask_key(e["text"])
        if len(action) >= _ASK_KEY_MIN and (action in text or action in span):
            return e
        if long_span and span in action:
            return e
        if len(options) >= 2 and len(options & {ask_key(x) for x in e.get("labels") or []}) >= 2:
            return e
        theirs = _words(e["text"])
        if len(mine) >= 3 and len(theirs) >= 3:
            share = len(mine & theirs) / len(mine)
            if share >= _CARD_ASK_OVERLAP or (e.get("task_id") in named and share >= 0.4):
                return e
    titled = [e for e in cards if str(e.get("title") or "").strip()]
    if _CARD_DECISION.search(raw) and (_CARD_NOUN.search(raw) or named):
        topic = _distinctive(raw)
        hits = [e for e in titled if (topic & _distinctive(e.get("title"))) or _names_card(named, e.get("task_id"))]
        if len(hits) == 1:
            return hits[0]
    hits = [e for e in titled if len(mine) >= 3 and len(_words(e["title"]) | _words(e["text"])) >= 3
            and len(mine & (_words(e["title"]) | _words(e["text"]))) / len(mine) >= _CARD_TITLE_OVERLAP]
    return hits[0] if len(hits) == 1 else None  # two cards that fit equally well: the relay names neither


# ---- repeat corroboration ---------------------------------------------------------------------------------
# The extractor's "repeats" named the session's only open tile for unrelated status paragraphs (MacBook chat
# ..bef6c6: four report paragraphs rendered as the CuaDriver tile). A repeat is acted on (framed onto the tile,
# or mapped onto it) only when the reply itself corroborates it; otherwise the other rules judge the sentence.

_REPEAT_MIN_SHARED = 2
# A topic-free reminder ("They'll stay in your list until you answer them", "That one is still yours to call")
# can only point at an open ask: none of its words name a subject of its own. Such a sentence corroborates a
# repeat by itself; a sentence with a topic must share it with the tile.
_REMINDER_WORDS = frozenset({
    "stay", "stays", "list", "until", "answer", "answered", "call", "whenever", "wait", "waiting", "pending",
    "decide", "decision", "queue", "tile", "they'll", "they're", "ready", "rush", "urgent", "later", "time",
    "anytime", "yet", "still", "whatever", "think", "free", "take", "look"})
_REMINDER_MAX_WORDS = 15


def _topic_free(span) -> bool:
    t = str(span or "").strip()
    if not t or len(t.split()) > _REMINDER_MAX_WORDS or _CARD_ID.search(t):
        return False
    return not {w for w in _words(t) if w not in _FUNCTION_WORDS and w not in _REMINDER_WORDS}


def tile_texts(tile: dict) -> list[str]:
    """What identifies an open tile: its text/title, the sentence it was extracted from, a card's title and id."""
    out = [tile.get(k) for k in ("text", "title", "verbatim_span", "task_id")]
    step = tile.get("step")
    if isinstance(step, dict):
        out += [step.get("text"), step.get("verbatim_span")]
    return [str(x) for x in out if x]


def _paragraph(reply: str, span) -> str:
    if not reply or not span:
        return ""
    block = locate(reply, str(span))
    return block["text"] if block else ""


def repeat_corroborated(span, paragraph, texts) -> bool:
    """The sentence (or its paragraph) backs the model's claim that it repeats this tile: it quotes the tile's
    text or sentence (a normalized substring of at least ``_RESTATE_MIN_CHARS``), shares at least two content words
    with it, or names the same card id. A short topic-free reminder (``_topic_free``) names nothing else, so it
    corroborates by itself."""
    if _topic_free(span):
        return True
    mine = f"{span or ''}\n{paragraph or ''}"
    body = _norm(mine).replace('"', "")
    for t in texts or ():
        q = _norm(str(t or "")).replace('"', "")
        if len(q) >= _RESTATE_MIN_CHARS and q in body:
            return True
    theirs = set()
    for t in texts or ():
        theirs |= _words(t)
    if len(_words(mine) & theirs) >= _REPEAT_MIN_SHARED:
        return True
    ids = set(_CARD_ID.findall(" ".join(str(t) for t in texts or ())))
    return bool(ids & set(_CARD_ID.findall(mine)))


def split_repeats(repeats, tiles: dict, reply: str) -> tuple[list, list]:
    """(corroborated, uncorroborated) model repeats. ``tiles`` maps a listed id to its ``tile_texts``. A repeat of
    an id not listed is kept as is (the callers already ignore unknown ids)."""
    kept, dropped = [], []
    for r in repeats or ():
        if not isinstance(r, dict):
            continue
        texts = tiles.get(r.get("id"))
        if texts is None or repeat_corroborated(r.get("verbatim_span"), _paragraph(reply, r.get("verbatim_span")),
                                                texts):
            kept.append(r)
        else:
            dropped.append(r)
    return kept, dropped


def same_open_ask(candidate: dict, asks: list[dict], repeats=(), mapped: Optional[dict] = None,
                  turn_requests=(), reply: str = "") -> Optional[dict]:
    """The open ask ``candidate`` (an extracted step) provably repeats, or None (it is its own ask).

    Exact provenance only, never title, word or menu similarity on its own:
    - the model marked this exact sentence as a repeat of an open id (request id or extracted step id) and the
      sentence or its paragraph corroborates it (``repeat_corroborated``);
    - the evidence sentence (a specific one) is the sentence an open extracted ask came from in an EARLIER reply;
    - the request was published or revised by the producer tool in this very turn (``turn_requests``,
      server-recorded) and the candidate restates its title, or is the approve/change/decline menu of the only
      such approval (or its sentence was already read as that approval in this reply).
    Two open requests with the same title are different requests: an equal title alone maps onto neither, and
    a card both name is context, not identity. With several candidates the newest open ask wins."""
    text, span = ask_key(candidate.get('text')), ask_key(candidate.get('verbatim_span'))
    long_span = len(span) >= _SPAN_KEY_MIN and len(span.split()) >= 5
    turn = {str(x) for x in turn_requests or ()}

    def pick(xs):
        return xs[-1] if xs else None

    ids = {}
    for a in asks:  # the model sees extracted tiles under their ledger step id, producer ones under the request id
        ids[a['id']] = a
        if a['step'] is not None and a['step'].get('id'):
            ids[a['step']['id']] = a
    if len(span) >= _ASK_KEY_MIN:
        for r in repeats or ():
            if isinstance(r, dict) and r.get('id') in ids and ask_key(r.get('verbatim_span')) == span \
                    and repeat_corroborated(candidate.get('verbatim_span'),
                                            _paragraph(reply, candidate.get('verbatim_span')), tile_texts(ids[r['id']])):
                return ids[r['id']]
    # One sentence can carry two different asks: the same-sentence rule is for LATER replies only.
    hit = pick([a for a in asks if long_span and a['step'] is not None and not a.get('fresh')
                and span == ask_key(a['step'].get('verbatim_span'))])
    if hit:
        return hit
    mine = [a for a in asks if a['id'] in turn]
    hit = pick([a for a in mine if (len(text) >= _ASK_KEY_MIN and text == ask_key(a['title']))
                or (long_span and span == ask_key(a['title']))])
    if hit:
        return hit
    options = [str(o) for o in candidate.get('options') or []]
    if candidate.get('kind') == 'choose' and _MENU.search(' '.join([str(candidate.get('text') or '')] + options)):
        # The same sentence already read as an approval in this reply (minted from it just now, or mapped by one
        # of the provenance rules above): one sentence, one approval. ``mapped`` only ever holds such entries.
        prior = (mapped or {}).get(span)
        if prior is not None and prior['kind'] == 'approval':
            return prior
        approvals = [a for a in mine if a['kind'] == 'approval']
        if len(approvals) == 1:
            a = approvals[0]
            labels = {ask_key(x) for x in a.get('labels') or []}
            topic = _words(candidate.get('text'), _MENU_WORDS)
            if (len(labels & {ask_key(o) for o in options}) >= 2
                    and (not topic or _overlap(topic, _words(a['title'])) >= 0.5)):
                return a
    return None


def restates_open_ask(candidate: dict, asks: list[dict], repeats=(), mapped: Optional[dict] = None,
                      reply: str = "") -> Optional[dict]:
    """Detector only (chat.py duplicate watchers): the open ask ``candidate`` looks like it restates, or None.

    NEVER an identity: nothing may be framed onto, folded into or suppressed by this match, because title,
    word and menu overlap are not provenance. Identity is ``same_open_ask``. Heuristics kept for detection:

    Same decision when: its text IS an open ask's title; its evidence sentence (a specific one) is an open
    ask's title or the sentence an open ask was extracted from in an earlier reply; the model marked that
    sentence as a repeat of an open id; its text names exactly one Kanban card that an open ask of the same
    kind names, and the two say mostly the same words; or it is the approve/change/decline menu of the only
    open approval (the same labels and topic), or its sentence was already read as one approval. With several
    candidates the newest open ask wins (older duplicates are left for withdrawal, not merged)."""
    text, span = ask_key(candidate.get('text')), ask_key(candidate.get('verbatim_span'))
    long_span = len(span) >= _SPAN_KEY_MIN and len(span.split()) >= 5

    def pick(xs):
        return xs[-1] if xs else None

    hit = pick([a for a in asks if (len(text) >= _ASK_KEY_MIN and text == ask_key(a['title']))
                or (long_span and span == ask_key(a['title']))
                # One sentence can carry two different asks: the same-sentence rule is for LATER replies only.
                or (long_span and a['step'] is not None and not a.get('fresh')
                    and span == ask_key(a['step'].get('verbatim_span')))])
    if hit:
        return hit
    ids = {}
    for a in asks:  # the model sees extracted tiles under their ledger step id, producer ones under the request id
        ids[a['id']] = a
        if a['step'] is not None and a['step'].get('id'):
            ids[a['step']['id']] = a
    if len(span) >= _ASK_KEY_MIN:
        for r in repeats or ():
            if isinstance(r, dict) and r.get('id') in ids and ask_key(r.get('verbatim_span')) == span \
                    and repeat_corroborated(candidate.get('verbatim_span'),
                                            _paragraph(reply, candidate.get('verbatim_span')), tile_texts(ids[r['id']])):
                return ids[r['id']]
    kind = _EXTRACTED_KIND.get(candidate.get('kind'), 'input')
    cards = set(_CARD_ID.findall(str(candidate.get('text') or '')))
    if len(cards) == 1:
        words = _words(candidate.get('text'))
        hit = pick([a for a in asks if a['kind'] == kind and set(_CARD_ID.findall(a['title'])) == cards
                    and _overlap(words, _words(a['title'])) >= _OVERLAP])
        if hit:
            return hit
    options = [str(o) for o in candidate.get('options') or []]
    if candidate.get('kind') == 'choose' and _MENU.search(' '.join([str(candidate.get('text') or '')] + options)):
        prior = (mapped or {}).get(span)  # the same sentence already read as that approval in this reply
        if prior is not None and prior['kind'] == 'approval':
            return prior
        approvals = [a for a in asks if a['kind'] == 'approval']
        if len(approvals) == 1:
            a = approvals[0]
            labels = {ask_key(x) for x in a.get('labels') or []}
            topic = _words(candidate.get('text'), _MENU_WORDS)
            if (len(labels & {ask_key(o) for o in options}) >= 2
                    and (not topic or _overlap(topic, _words(a['title'])) >= 0.5)):
                return a
    return None


# "No decision needed: ..." / scope "Settled, nothing to decide." Unscoped only: "No action required from ops:
# approve the budget?" or "No input needed for steps 1-3; step 4 needs approval" are real asks.
_NOOP_TITLE = re.compile(
    r"^(?:no (?:decision|action|answer|input|reply|choice)s? (?:is |are )?(?:needed|required)"
    r"|nothing to (?:decide|answer|approve))(?:\s*(?:[:.\u2014\u2013-]|$))", re.I)
_NOOP_SCOPE = re.compile(r"^(?:settled[,:;.]?\s*)?nothing (?:is )?(?:left )?to decide[.!]?$", re.I)


def declares_no_ask(request: dict) -> bool:
    """The request itself says there is nothing to decide. Such a tile is a status note, never an ask: it is
    withdrawn, not published."""
    title = _LIST_MARK.sub("", _plain(str(request.get('title') or ""))).strip()
    scope = request.get('scope')
    md = _LIST_MARK.sub("", _plain(str(scope.get('markdown') or "") if isinstance(scope, dict) else "")).strip()
    first = _SENTENCE_BREAK.split(md)[0].strip() if md else ""
    return bool(_NOOP_TITLE.match(title) or _NOOP_SCOPE.match(first))


# ---- ActionRequest v2: immutable scope versions + durable decision outbox -----

class ActionError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# Who may withdraw an ask: the board/reconciler (system), the owning chat (agent), the person (user).
WITHDRAW_BY = ('system', 'agent', 'user')


def _iso(ts) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ActionError('Invalid object fields')


def _string(value):
    if not isinstance(value, str) or not value.strip():
        raise ActionError('Expected nonempty string')


def validate_owner(owner):
    _object(owner, ('gatewayId', 'profile', 'storedSessionId'))
    for value in owner.values(): _string(value)


def validate_ref(ref):
    _object(ref, ('gatewayId', 'profile', 'type', 'id'), ('board',))
    for value in ref.values(): _string(value)
    if ref['type'] not in ('message', 'user_step', 'kanban'):
        raise ActionError('Invalid source type')
    if (ref['type'] == 'kanban') != ('board' in ref):
        raise ActionError('Kanban references require an explicit board')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def validate_action(value):
    _object(value, ('schemaVersion', 'owner', 'requestId', 'revision', 'kind', 'title', 'scope',
                    'limits', 'exclusions', 'outcomes', 'choices', 'aliases', 'state'), ('resolution',))
    if type(value['schemaVersion']) is not int or value['schemaVersion'] != 2:
        raise ActionError('schemaVersion must be 2')
    validate_owner(value['owner'])
    try:
        if str(uuid.UUID(value['requestId'])) != value['requestId']: raise ValueError()
    except (ValueError, TypeError, AttributeError): raise ActionError('requestId must be a canonical UUID')
    if type(value['revision']) is not int or value['revision'] < 1:
        raise ActionError('revision must be a positive integer')
    if value['kind'] not in ('approval', 'choice', 'input', 'external_step'):
        raise ActionError('Invalid kind')
    _string(value['title'])
    scope = value['scope']; _object(scope, ('markdown', 'complete', 'sourceRefs'))
    if not isinstance(scope['markdown'], str) or type(scope['complete']) is not bool:
        raise ActionError('Invalid scope')
    if scope['complete']: _string(scope['markdown'])
    for refs in (scope['sourceRefs'], value['aliases']):
        if not isinstance(refs, list): raise ActionError('References must be arrays')
        for ref in refs: validate_ref(ref)
        if len({_json(ref) for ref in refs}) != len(refs): raise ActionError('Duplicate reference')
    for field in ('limits', 'exclusions', 'choices'):
        if not isinstance(value[field], list): raise ActionError('Expected array')
    for limit in value['limits']:
        _object(limit, ('key', 'value'), ('permissionRequired',))
        _string(limit['key']); _string(limit['value'])
        # permissionRequired is a flag, not text: a bare True used to fail as 'Expected nonempty string'.
        if 'permissionRequired' in limit and type(limit['permissionRequired']) is not bool:
            raise ActionError('permissionRequired must be a boolean')
    for text in value['exclusions']: _string(text)
    _object(value['outcomes'], ('revise',) if value['kind'] == 'approval' else (),
            ('approve', 'decline', 'answer') if value['kind'] == 'approval' else ('revise', 'approve', 'decline', 'answer'))
    for text in value['outcomes'].values(): _string(text)
    ids = set()
    for choice in value['choices']:
        _object(choice, ('id', 'label', 'effect'), ('authorizes', 'explain', 'recommended'))
        _string(choice['id']); _string(choice['label'])
        # 'explain': one plain line on what happens if the user picks this (shown under the button).
        # 'recommended': the agent's pick, at most one per request, only True.
        if 'explain' in choice:
            _string(choice['explain'])
        if 'recommended' in choice and choice['recommended'] is not True:
            raise ActionError('recommended is only valid as true')
        # 'authorizes' marks a go answer ("now") to a run-timing question: only on answers, only True.
        if 'authorizes' in choice and (choice['authorizes'] is not True or choice['effect'] != 'answer'
                                       or value['kind'] not in ('choice', 'input')):
            raise ActionError('authorizes is only valid as true on an answer choice')
        if choice['id'] in ids or choice['effect'] not in ('approve', 'revise', 'decline', 'answer'):
            raise ActionError('Invalid or duplicate choice')
        ids.add(choice['id'])
        if choice['effect'] in ('approve', 'decline') and choice['effect'] not in value['outcomes']:
            raise ActionError('Choice requires a stated outcome')
    if not ids: raise ActionError('At least one choice is required')
    if sum(1 for c in value['choices'] if c.get('recommended')) > 1:
        raise ActionError('At most one choice can be recommended')
    if value['state'] not in ('needs_context', 'open', 'resolved', 'superseded', 'cancelled', 'withdrawn'):
        raise ActionError('Invalid state')
    if value['state'] == 'open' and not scope['complete']:
        raise ActionError('Incomplete requests must need context')
    if 'resolution' in value:
        r = value['resolution']; _object(r, ('choiceId', 'operationId', 'revision', 'recordedAt'))
        for key in ('choiceId', 'operationId', 'recordedAt'): _string(r[key])
        if type(r['revision']) is not int or r['revision'] < 1: raise ActionError('Invalid resolution revision')
    return json.loads(_json(value))


def server_owner(session: str) -> dict:
    """Use the core install identity and task-scoped profile, not launcher/session env.

    current_profile_name honors multiplex HERMES_HOME context overrides. The install
    ID is shared with the core hosted-room authority; no plugin-specific provisioning.
    Missing core identity fails closed rather than inventing a local namespace.
    """
    try:
        from hermes_cli.install_identity import get_install_id
        from hermes_cli.profiles import current_profile_name
        install_id = get_install_id()
        if not install_id: raise ValueError('Missing install identity')
        owner = {'gatewayId': f'install:{install_id}', 'profile': current_profile_name(),
                 'storedSessionId': session}
        validate_owner(owner)
        return owner
    except Exception as exc:
        raise ActionError('Server gateway identity or stored session missing', 503) from exc


def validate_admission_text(value):
    """Reject paste controls before freezing intent, never silently alter scope.

    Core's sanitizer is authoritative when installed. The conservative standalone
    guard also rejects escaped controls in JSON and keeps offline store validation
    safe without importing the gateway (which would create another runtime).
    """
    text = _json(value)
    if re.search(r'(?:\[20[01]~|\^\[\[20[01]~|(?:^|[\s>:\]\)])00~|01~(?=$|[\s<\[()\):;.,!?])|~\[\[e)', text):
        raise ActionError('Remove terminal paste control markers before deciding')
    try:
        from hermes_cli.input_sanitize import sanitize_user_prompt_text
    except ImportError:
        return
    if sanitize_user_prompt_text(text) != text:
        raise ActionError('Decision text must be sanitized before recording')


# A decision recorded while its session is mid-turn: the runtime refuses admission until the turn ends. The
# dashboard's background retrier and the Desktop row both re-dispatch the SAME operation until it lands.
BUSY_ERROR = 'Session is busy with another turn; this decision is delivered automatically when it ends.'


class ActionStore:
    """SQLite FULL-sync transaction commits decision + frozen delivery intent before dispatch.

    No fuzzy identity, cross-session search, or automatic compression/branch inheritance.
    Immutable revision documents are separate from resolution and delivery projections.
    """
    def __init__(self, path=None, timeout=30):
        self.path = Path(path) if path else home() / 'user-steps' / 'actions.sqlite3'
        self.timeout = timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_wal()
        with self._connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS requests (
                  namespace TEXT, id TEXT, session TEXT, revision INTEGER, state TEXT, resolution TEXT,
                  PRIMARY KEY(namespace,id));
                CREATE TABLE IF NOT EXISTS revisions (
                  namespace TEXT, id TEXT, revision INTEGER, body TEXT,
                  PRIMARY KEY(namespace,id,revision));
                CREATE TABLE IF NOT EXISTS aliases (
                  ref TEXT PRIMARY KEY, namespace TEXT, id TEXT);
                CREATE TABLE IF NOT EXISTS operations (
                  id TEXT PRIMARY KEY, namespace TEXT, session TEXT, request TEXT, revision INTEGER,
                  idem TEXT, fingerprint TEXT, body TEXT,
                  UNIQUE(namespace,session,idem), UNIQUE(namespace,request,revision));
                CREATE TABLE IF NOT EXISTS chat_retirements (
                  namespace TEXT, request TEXT, revision INTEGER, evidence TEXT,
                  PRIMARY KEY(namespace,request,revision));
                CREATE TABLE IF NOT EXISTS clarification_upgrades (
                  namespace TEXT, request TEXT, revision INTEGER, body TEXT,
                  PRIMARY KEY(namespace,request,revision));
                -- Side table, not new request columns: older processes INSERT into requests positionally.
                CREATE TABLE IF NOT EXISTS lifecycle (
                  namespace TEXT, id TEXT, created_at REAL, withdrawal TEXT, notified_at REAL,
                  asked_revision INTEGER, PRIMARY KEY(namespace,id));
            ''')
            if 'asked_revision' not in {row[1] for row in c.execute('PRAGMA table_info(lifecycle)')}:
                c.execute('ALTER TABLE lifecycle ADD COLUMN asked_revision INTEGER')

    def _ensure_wal(self):
        # WAL so readers never wait on a committing writer (rollback journal shut every reader out for the whole
        # busy timeout: self-heal plugin-dca5d02c20). The mode is persistent in the file; switching needs no
        # other open connection, so it is best effort here and retried on the next open.
        c = sqlite3.connect(self.path, timeout=min(self.timeout, 2))
        try:
            if c.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                c.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError as exc:
            logging.getLogger('user-steps').warning('user-steps: could not switch %s to WAL yet: %s', self.path, exc)
        finally:
            c.close()

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=self.timeout)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA synchronous=FULL')
        return c

    @contextmanager
    def _read(self):
        # Rollback-journal readers must not request RESERVED/PENDING writer locks:
        # even a no-op BEGIN IMMEDIATE commit waits for every other shared reader,
        # then blocks new polls at schema/PRAGMA reads for the full busy timeout.
        c = self._connect()
        try:
            c.execute('PRAGMA query_only=ON')
            c.execute('BEGIN')
            yield c
        finally:
            c.close()  # Ends the snapshot, including when decoding/validation fails.

    @contextmanager
    def _tx(self):
        c = self._connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback(); raise
        finally: c.close()

    @staticmethod
    def _ns(owner):
        validate_owner(owner)
        return _json([owner['gatewayId'], owner['profile']])

    def _get(self, c, owner, rid):
        row = c.execute('SELECT * FROM requests WHERE namespace=? AND id=? AND session=?',
                        (self._ns(owner), rid, owner['storedSessionId'])).fetchone()
        if not row: raise ActionError('Unknown request in this session', 404)
        body = json.loads(c.execute('SELECT body FROM revisions WHERE namespace=? AND id=? AND revision=?',
                                   (row['namespace'], rid, row['revision'])).fetchone()[0])
        body['state'] = row['state']
        if row['resolution']: body['resolution'] = json.loads(row['resolution'])
        return body

    def get(self, owner, rid):
        with self._read() as c: return self._get(c, owner, rid)

    def requests(self, owner, *, include_withdrawn=False):
        # Withdrawn rows are omitted by default: route bodies mounted before this state existed call this, and
        # renderers that predate it reject a whole queue that holds an unknown state.
        with self._read() as c:
            ids = c.execute('SELECT id, state FROM requests WHERE namespace=? AND session=? ORDER BY rowid',
                            (self._ns(owner), owner['storedSessionId'])).fetchall()
            return [self._get(c, owner, row[0]) for row in ids if include_withdrawn or row[1] != 'withdrawn']

    def _aliases(self, c, owner, r):
        ns = self._ns(owner)
        for ref in r['aliases']:
            # Remote aliases are provenance only; never resolve/mutate another gateway's card.
            old = c.execute('SELECT namespace,id FROM aliases WHERE ref=?', (_json(ref),)).fetchone()
            if old and tuple(old) != (ns, r['requestId']):
                raise ActionError('Alias already belongs to another request', 409)
            c.execute('INSERT OR IGNORE INTO aliases VALUES (?,?,?)', (_json(ref), ns, r['requestId']))

    def create(self, owner, value, *, bind=None, created_at=None):
        r = validate_action(value); ns = self._ns(owner)
        if r['owner'] != owner: raise ActionError('Owner mismatch', 403)
        if r['revision'] != 1 or 'resolution' in r or r['state'] not in ('open', 'needs_context'):
            raise ActionError('New request must be unresolved revision 1')
        try:
            with self._tx() as c:
                c.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL)',
                          (ns, r['requestId'], owner['storedSessionId'], 1, r['state']))
                self._aliases(c, owner, r)
                c.execute('INSERT INTO revisions VALUES (?,?,?,?)', (ns, r['requestId'], 1, _json(r)))
                c.execute('INSERT OR IGNORE INTO lifecycle(namespace,id,created_at,asked_revision) VALUES (?,?,?,1)',
                          (ns, r['requestId'], float(created_at) if created_at else time.time()))
                if bind is not None: bind(r, 0)
        except sqlite3.IntegrityError: raise ActionError('Request already exists', 409)
        return r

    def history(self, owner, rid):
        with self._read() as c:
            self._get(c, owner, rid)
            return [json.loads(row[0]) for row in c.execute(
                'SELECT body FROM revisions WHERE namespace=? AND id=? ORDER BY revision', (self._ns(owner), rid))]

    def revise(self, owner, rid, expected, replacement, *, bind=None, fresh=True, undecided=False):
        with self._tx() as c:
            old = self._get(c, owner, rid); ns = self._ns(owner)
            if type(expected) is not int or old['revision'] != expected: raise ActionError('Stale revision', 409)
            if undecided and (old.get('resolution') or old['state'] not in ('open', 'needs_context')):
                # An automatic re-ask revision lost a race with a decision: the decision wins.
                raise ActionError('A decision is already recorded for this revision', 409)
            pending = c.execute('SELECT body FROM operations WHERE namespace=? AND request=? AND revision=?',
                                (ns, rid, expected)).fetchone()
            if pending and json.loads(pending[0])['dispatchState'] not in ('delivered', 'cancelled'):
                raise ActionError('Decision delivery is pending', 409)
            r = dict(replacement, revision=expected + 1)
            if r.get('owner') != owner or r.get('requestId') != rid or 'resolution' in r:
                raise ActionError('Replacement identity mismatch')
            r = validate_action(r)
            if r['state'] not in ('open', 'needs_context'): raise ActionError('Replacement must require a fresh decision')
            # Provenance associations cannot be reassigned or silently removed.
            if not {_json(x) for x in old['aliases']} <= {_json(x) for x in r['aliases']}:
                raise ActionError('Replacement must preserve aliases')
            self._aliases(c, owner, r)
            c.execute('INSERT INTO revisions VALUES (?,?,?,?)', (ns, rid, r['revision'], _json(r)))
            c.execute('UPDATE requests SET revision=?,state=?,resolution=NULL WHERE namespace=? AND id=?',
                      (r['revision'], r['state'], ns, rid))
            if fresh or old['state'] == 'withdrawn':
                # A producer revision is a fresh ask (a revival too): its age restarts, so card movement before
                # it cannot retire it. A server-side re-render (fresh=False) keeps the original ask time.
                c.execute('INSERT INTO lifecycle(namespace,id,created_at,asked_revision) VALUES (?,?,?,?) '
                          'ON CONFLICT(namespace,id) DO UPDATE SET created_at=excluded.created_at, '
                          'asked_revision=excluded.asked_revision, withdrawal=NULL, notified_at=NULL',
                          (ns, rid, time.time(), r['revision']))
            else:
                c.execute('INSERT INTO lifecycle(namespace,id,asked_revision) VALUES (?,?,?) ON CONFLICT(namespace,id) '
                          'DO UPDATE SET asked_revision=excluded.asked_revision', (ns, rid, r['revision']))
            if bind is not None: bind(r, expected)
            return r

    def _operation(self, c, owner, oid=None, key=None):
        column, value = ('id', oid) if oid else ('idem', key)
        row = c.execute(f'SELECT body FROM operations WHERE namespace=? AND session=? AND {column}=?',
                        (self._ns(owner), owner['storedSessionId'], value)).fetchone()
        if not row: raise ActionError('Unknown operation in this session', 404)
        op = json.loads(row[0])
        return {'operation': op, 'request': self._get(c, owner, op['requestId'])}

    def operation(self, owner, oid=None, key=None):
        with self._read() as c: return self._operation(c, owner, oid, key)

    def decide(self, owner, decision):
        _object(decision, ('requestId', 'expectedRevision', 'choiceId', 'idempotencyKey'), ('revisionText',))
        for key in ('requestId', 'choiceId', 'idempotencyKey'): _string(decision[key])
        if len(decision['idempotencyKey']) > 255: raise ActionError('Idempotency key too long')
        if type(decision['expectedRevision']) is not int: raise ActionError('Invalid expectedRevision')
        text = decision.get('revisionText')
        if text is not None and not isinstance(text, str): raise ActionError('Invalid revisionText')
        fingerprint = _json(dict(decision, revisionText=text))
        ns = self._ns(owner)
        with self._tx() as c:
            existing = c.execute('SELECT id,fingerprint FROM operations WHERE namespace=? AND session=? AND idem=?',
                                 (ns, owner['storedSessionId'], decision['idempotencyKey'])).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint: raise ActionError('Idempotency key payload conflict', 409)
                return self._operation(c, owner, existing['id'])
            r = self._get(c, owner, decision['requestId'])
            if r['revision'] != decision['expectedRevision'] or r['state'] not in ('open', 'needs_context'):
                raise ActionError('Stale or closed request', 409)
            choice = next((x for x in r['choices'] if x['id'] == decision['choiceId']), None)
            if not choice: raise ActionError('Unknown choice')
            effect = choice['effect']
            if effect in ('approve', 'answer') and not r['scope']['complete']: raise ActionError('Scope is incomplete')
            if effect == 'revise': _string(text)
            if effect == 'answer':
                if r['kind'] not in ('choice', 'input', 'external_step'): raise ActionError('Answers require a clarification or user step')
                if choice['id'] == 'answer-text': _string(text)
                elif text != choice['label']: raise ActionError('Answer must match the exact choice label')
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).isoformat()
            oid = str(uuid.uuid4())
            op = {'operationId': oid, 'owner': owner, 'requestId': r['requestId'], 'revision': r['revision'],
                  'idempotencyKey': decision['idempotencyKey'], 'decision': decision,
                  'recordedAt': stamp, 'dispatchState': 'pending', 'attempts': 0,
                  'intent': {'request': r, 'effect': effect, 'revisionText': text,
                             # A go answer to a run question authorizes exactly this frozen scope; free text never.
                             'executionAuthorized': effect == 'approve' or (
                                 effect == 'answer' and choice.get('authorizes') is True and text == choice['label']),
                             'permissionGrants': []}}
            validate_admission_text({'operationId': oid, **op['intent']})
            try:
                c.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)',
                          (oid, ns, owner['storedSessionId'], r['requestId'], r['revision'],
                           decision['idempotencyKey'], fingerprint, _json(op)))
            except sqlite3.IntegrityError: raise ActionError('A decision is already recorded for this revision', 409)
            resolution = {'choiceId': decision['choiceId'], 'operationId': oid,
                          'revision': r['revision'], 'recordedAt': stamp}
            c.execute('UPDATE requests SET resolution=? WHERE namespace=? AND id=?',
                      (_json(resolution), ns, r['requestId']))
            return self._operation(c, owner, oid)

    def dispatch(self, owner, oid, receiver, *, early_tool_call_id=None):
        # Commit the attempt then release the DB lock BEFORE external I/O. Other
        # requests stay usable; concurrent retries share the receiver's durable key.
        # early_tool_call_id: deliver through a blocked clarify's tool result (receiver.deliver_early) instead of
        # admitting a new turn; that is not a delivery attempt either.
        with self._tx() as c:
            result = self._operation(c, owner, oid); op = result['operation']
            if op['dispatchState'] in ('delivered', 'cancelled'): return result
            op['attempts'] += 1
            from datetime import datetime, timezone
            op['dispatchStartedAt'] = datetime.now(timezone.utc).isoformat()
            op['dispatchState'] = 'pending'
            c.execute('UPDATE operations SET body=? WHERE id=?', (_json(op), oid))
        receipt = None
        confirmed = False
        busy = False
        failure_class = 'unconfirmed_readback'
        try:
            if receiver is None: raise RuntimeError('No durable delivery receiver configured')
            receipt = (receiver.deliver_early(op, early_tool_call_id) if early_tool_call_id
                       else receiver.deliver(op))
            confirmed = bool(receiver.readback(op, receipt))
        except Exception as exc:
            # Never expose credentials, receiver URLs or raw transport bodies: only classify.
            busy = 'session is busy' in str(exc).lower()
            failure_class = 'session_busy' if busy else getattr(exc, 'reason', type(exc).__name__)
        if not confirmed:
            import logging
            logging.getLogger('user-steps').warning(
                'Decision delivery unconfirmed operation=%s classification=%s', oid, failure_class)
        with self._tx() as c:
            result = self._operation(c, owner, oid); op = result['operation']
            # A slow failed retry cannot downgrade a concurrently confirmed delivery.
            if op['dispatchState'] in ('delivered', 'cancelled'): return result
            if busy or early_tool_call_id:
                # Not a delivery failure: a busy owner refuses admission until its turn ends, and an early answer
                # never admits a turn. Neither may exhaust the retry budget.
                op['attempts'] = max(0, op['attempts'] - 1)
            if busy: op['busyRefusals'] = op.get('busyRefusals', 0) + 1
            if confirmed:
                cancelled = receipt.get('terminalCancelled') is True
                op.update(dispatchState='cancelled' if cancelled else 'delivered', receipt=receipt)
                op.pop('error', None)
                effect = op['intent']['effect']
                state = 'cancelled' if cancelled else {'revise': 'superseded', 'decline': 'cancelled'}.get(effect, 'resolved')
                resolution = {'choiceId': op['decision']['choiceId'], 'operationId': oid,
                              'revision': op['revision'], 'recordedAt': op['recordedAt']}
                c.execute('UPDATE requests SET state=?,resolution=? WHERE namespace=? AND id=? AND revision=?',
                          (state, _json(resolution), self._ns(owner), op['requestId'], op['revision']))
            else:
                op.update(dispatchState='failed', error=BUSY_ERROR if busy else 'Delivery unconfirmed; retry the same idempotency key.')
            c.execute('UPDATE operations SET body=? WHERE id=?', (_json(op), oid))
            return {'operation': op, 'request': self._get(c, owner, op['requestId'])}

    def pending_owners(self):
        with self._read() as c:
            owners = {_json(op['owner']): op['owner'] for row in c.execute('SELECT body FROM operations')
                      if (op := json.loads(row['body']))['dispatchState'] not in ('delivered', 'cancelled')}
            return list(owners.values())

    def undelivered(self, gateway_id, profile):
        """Recorded-but-undelivered operations (any effect) of this gateway/profile, oldest first."""
        with self._read() as c:
            return [op for row in c.execute('SELECT body FROM operations WHERE namespace=? ORDER BY rowid',
                                            (_json([gateway_id, profile]),))
                    if (op := json.loads(row['body']))['dispatchState'] not in ('delivered', 'cancelled')]

    def reconcile(self, owner, receiver, *, max_attempts=None):
        with self._tx() as c:
            ids = [row['id'] for row in c.execute(
                'SELECT id,body FROM operations WHERE namespace=? AND session=? ORDER BY rowid',
                (self._ns(owner), owner['storedSessionId']))
                if (op := json.loads(row['body']))['dispatchState'] not in ('delivered', 'cancelled')
                and (max_attempts is None or op.get('attempts', 0) < max_attempts)]
        return [self.dispatch(owner, oid, receiver) for oid in ids]

    # ---- lifecycle: withdrawal (a moot ask leaves the queue; NOT a decision) ----------------------------

    def _life(self, c, owner, rid):
        return c.execute('SELECT created_at, withdrawal, notified_at FROM lifecycle WHERE namespace=? AND id=?',
                         (self._ns(owner), rid)).fetchone()

    def _view(self, c, owner, rid):
        """Presentation copy for REST/hook readers. Internal callers keep _get's exact frozen body
        (card bindings compare it byte for byte), so lifecycle fields never enter decisions."""
        body = self._get(c, owner, rid)
        life = self._life(c, owner, rid)
        if life is not None and life['created_at'] is not None:
            body['createdAt'] = _iso(life['created_at'])
        if body['state'] == 'withdrawn' and life is not None and life['withdrawal']:
            body['withdrawal'] = json.loads(life['withdrawal'])
        return body

    def view(self, owner, rid):
        with self._read() as c: return self._view(c, owner, rid)

    def views(self, owner, *, include_withdrawn=False):
        with self._read() as c:
            rows = c.execute('SELECT id, state FROM requests WHERE namespace=? AND session=? ORDER BY rowid',
                             (self._ns(owner), owner['storedSessionId'])).fetchall()
            return [self._view(c, owner, row['id']) for row in rows
                    if include_withdrawn or row['state'] != 'withdrawn']

    def created_at(self, owner, rid):
        with self._read() as c:
            life = self._life(c, owner, rid)
            return life['created_at'] if life is not None else None

    def stamp_ask(self, owner, rid, ts, revision, *, fresh=False):
        """Record when ``revision`` was asked; return the stored ask time.

        Backfill (fresh=False) never moves a known time. fresh=True: a process that predates the stamp wrote
        this revision (an older producer re-asked), so it is a fresh ask. A request that moved on since it was
        read is left alone; the next pass re-reads it."""
        ns = self._ns(owner)
        with self._tx() as c:
            row = c.execute('SELECT state, revision FROM requests WHERE namespace=? AND id=?', (ns, rid)).fetchone()
            if row is not None and row['revision'] == revision and row['state'] in ('open', 'needs_context'):
                if fresh:
                    c.execute('INSERT INTO lifecycle(namespace,id,created_at,asked_revision) VALUES (?,?,?,?) '
                              'ON CONFLICT(namespace,id) DO UPDATE SET created_at=excluded.created_at, '
                              'asked_revision=excluded.asked_revision, withdrawal=NULL, notified_at=NULL',
                              (ns, rid, float(ts), revision))
                else:
                    c.execute('INSERT INTO lifecycle(namespace,id,created_at,asked_revision) VALUES (?,?,?,?) '
                              'ON CONFLICT(namespace,id) DO UPDATE SET '
                              'created_at=COALESCE(lifecycle.created_at, excluded.created_at), '
                              'asked_revision=excluded.asked_revision', (ns, rid, float(ts), revision))
            life = c.execute('SELECT created_at FROM lifecycle WHERE namespace=? AND id=?', (ns, rid)).fetchone()
            return life['created_at'] if life is not None and life['created_at'] is not None else float(ts)

    def moot_candidates(self, owner):
        """[(request, lifecycle row or None)] for this session's undecided requests with no bound decision card,
        in ONE deferred read with a 2 s busy timeout: the best-effort pass that runs on every queue read and
        agent turn takes no write lock and never waits long behind a writer."""
        c = sqlite3.connect(self.path, timeout=2)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN')
            ns = self._ns(owner)
            rows = c.execute("SELECT id FROM requests WHERE namespace=? AND session=? AND state IN ('open','needs_context') "
                             'AND resolution IS NULL ORDER BY rowid', (ns, owner['storedSessionId'])).fetchall()
            out = []
            for row in rows:
                r = self._get(c, owner, row['id'])
                if any(a.get('type') == 'kanban' for a in r.get('aliases', [])):
                    continue  # decided on its own decision card; withdrawing it would strand that card
                life = c.execute('SELECT created_at, asked_revision FROM lifecycle WHERE namespace=? AND id=?',
                                 (ns, row['id'])).fetchone()
                out.append((r, dict(life) if life is not None else None))
            c.commit()
            return out
        finally:
            c.close()

    def withdraw(self, owner, rid, *, by, reason, evidence=None, expected_revision=None, now=None,
                 closes_cards=False):
        """open/needs_context -> withdrawn. Grants nothing, admits no turn, delivers nothing.

        Refused once a decision is recorded for the current revision (the decision wins), for a closed
        request, or for a stale expected revision. Repeating it returns the first withdrawal unchanged.
        A request bound to a decision card is refused unless the caller closes that card afterwards
        (``closes_cards``, withdraw_request with a card closer), so no card is left asking David.
        """
        if by not in WITHDRAW_BY: raise ActionError('Invalid withdrawal actor')
        if not isinstance(reason, str) or not reason.strip(): raise ActionError('Withdrawal reason required')
        if expected_revision is not None and type(expected_revision) is not int:
            raise ActionError('Invalid expectedRevision')
        record = {'by': by, 'reason': re.sub(r'\s+', ' ', reason).strip()[:300], 'at': _iso(now or time.time())}
        if evidence: record['evidence'] = str(evidence)[:500]
        with self._tx() as c:
            r = self._get(c, owner, rid); ns = self._ns(owner)
            if r['state'] == 'withdrawn': return self._view(c, owner, rid)
            if r['state'] not in ('open', 'needs_context'): raise ActionError('Request is already closed', 409)
            if not closes_cards and any(a.get('type') == 'kanban' for a in r.get('aliases', [])):
                raise ActionError('This request is decided on its decision card', 409)
            if expected_revision is not None and r['revision'] != expected_revision:
                raise ActionError('Stale revision', 409)
            if r.get('resolution') or c.execute('SELECT 1 FROM operations WHERE namespace=? AND request=? AND revision=?',
                                                (ns, rid, r['revision'])).fetchone():
                raise ActionError('A decision is already recorded for this revision', 409)
            c.execute("UPDATE requests SET state='withdrawn' WHERE namespace=? AND id=? AND revision=?",
                      (ns, rid, r['revision']))
            c.execute('INSERT INTO lifecycle(namespace,id,withdrawal) VALUES (?,?,?) ON CONFLICT(namespace,id) '
                      'DO UPDATE SET withdrawal=excluded.withdrawal, notified_at=NULL', (ns, rid, _json(record)))
            return self._view(c, owner, rid)

    def unnotified_withdrawals(self, owner):
        """Withdrawals the owning chat did not make itself (system/user) and has not been told about."""
        with self._read() as c:
            rows = c.execute("SELECT r.id FROM requests r JOIN lifecycle l ON l.namespace=r.namespace AND l.id=r.id "
                             "WHERE r.namespace=? AND r.session=? AND r.state='withdrawn' AND l.notified_at IS NULL "
                             "ORDER BY r.rowid", (self._ns(owner), owner['storedSessionId'])).fetchall()
            views = [self._view(c, owner, row['id']) for row in rows]
            return [v for v in views if (v.get('withdrawal') or {}).get('by') in ('system', 'user')]

    def mark_notified(self, owner, rids, now=None):
        with self._tx() as c:
            for rid in rids:
                c.execute('UPDATE lifecycle SET notified_at=? WHERE namespace=? AND id=?',
                          (now or time.time(), self._ns(owner), rid))

    def migrate_step(self, owner, step):
        if step.get('session') != owner['storedSessionId']: raise ActionError('Legacy session association unproven', 403)
        _string(step.get('id')); _string(step.get('text'))
        ref = dict(gatewayId=owner['gatewayId'], profile=owner['profile'], type='user_step', id=step['id'])
        previous = None
        r = legacy_request(owner, step, ref)
        with self._tx() as c:
            old = c.execute('SELECT namespace,id FROM aliases WHERE ref=?', (_json(ref),)).fetchone()
            if old:
                if old['namespace'] != self._ns(owner): raise ActionError('Alias owner mismatch', 403)
                previous = self._get(c, owner, old['id'])
                if previous['state'] not in ('open', 'needs_context') or previous.get('resolution'):
                    return previous
                if previous['choices'] == LEGACY_FALLBACK and previous['revision'] == 1:
                    pass  # retired generic fallback: always re-render (any kind)
                else:
                    # A prior mechanical upgrade (server-recorded marker) may be refreshed when
                    # the derivation improves; agent/producer revisions are never touched.
                    marker = c.execute('SELECT body FROM clarification_upgrades WHERE namespace=? AND request=? '
                                       'AND revision=?', (self._ns(owner), previous['requestId'],
                                                          previous['revision'])).fetchone()
                    shape = ('choices', 'outcomes', 'state', 'scope')
                    if (not marker or json.loads(marker[0]) != previous
                            or all(previous[k] == r[k] for k in shape)):
                        return previous
        if previous:
            if (r['kind'] != previous['kind'] or r['scope']['markdown'] != previous['scope']['markdown']
                    or r['title'] != previous['title']):
                return previous
            r['requestId'] = previous['requestId']
            upgraded = self.revise(owner, previous['requestId'], previous['revision'], r, fresh=False)
            # This marker proves a mechanical, server-side re-render, not an agent scope
            # revision. Missing/racing markers fail closed in chat. Approvals never get one:
            # they stay agent-incomplete until the producer publishes the exact plan.
            if upgraded['kind'] != 'approval':
                with self._tx() as c:
                    if self._get(c, owner, upgraded['requestId']) == upgraded:
                        c.execute('INSERT INTO clarification_upgrades VALUES (?,?,?,?)',
                                  (self._ns(owner), upgraded['requestId'], upgraded['revision'], _json(upgraded)))
            return upgraded
        try: return self.create(owner, r, created_at=step.get('created'))
        except ActionError as exc:
            if exc.status != 409: raise
            with self._tx() as c:
                old = c.execute('SELECT id FROM aliases WHERE ref=?', (_json(ref),)).fetchone()
                if not old: raise
                return self._get(c, owner, old['id'])

    def backfill_legacy(self, ledger=None, *, gateway=None):
        """One-shot server-side re-render of every request still carrying the retired
        generic fallback. Uses only server-stored owners and their own session ledger
        entry (exact alias id + session); takes no caller input and grants nothing."""
        ledger = ledger or Ledger()
        with self._tx() as c:
            rows = c.execute('SELECT r.namespace,r.id,r.session,v.body FROM requests r JOIN revisions v '
                             'ON v.namespace=r.namespace AND v.id=r.id AND v.revision=r.revision '
                             "WHERE r.state IN ('open','needs_context') AND r.resolution IS NULL").fetchall()
        out = []
        for row in rows:
            body = json.loads(row['body'])
            owner = body.get('owner') or {}
            if body.get('choices') != LEGACY_FALLBACK or owner.get('storedSessionId') != row['session']:
                continue
            if gateway and owner.get('gatewayId') != gateway:
                continue
            try:
                if self._ns(owner) != row['namespace']: continue
                refs = [a for a in body['aliases'] if a['type'] == 'user_step'
                        and a['gatewayId'] == owner['gatewayId'] and a['profile'] == owner['profile']]
                if len(refs) != 1: continue
                step = next((s for s in ledger.read(row['session']).get('steps', [])
                             if s.get('id') == refs[0]['id'] and s.get('session') == row['session']), None)
                if step is None: continue
                after = self.migrate_step(owner, step)
                if after['revision'] != body['revision']:
                    out.append(after)
            except (ActionError, OSError, ValueError, KeyError, sqlite3.Error):
                continue
        return out


_ALT_LEAD = re.compile(r"^(?:do you (?:mean|want(?: me)? to|prefer(?: to)?)|would you (?:like|prefer)(?: me)? to|"
                       r"should (?:i|we|it)|shall (?:i|we)|can (?:i|we)|want me to|is it)\b\s*", re.I)
_ALT_OTHER = re.compile(r"^(?:something else|anything else|other|not|neither|none(?: of (?:these|them))?)$", re.I)
_WH = re.compile(r"^(?:which|what|who|whom|whose|where|when|how|why)\b", re.I)


def _ws(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _question_alternatives(span: str, source: str) -> list[str]:
    """Literal alternatives of ONE 'A, or B?' question in the source span. Nothing
    is paraphrased: each label is a verbatim slice of the question (case of the first
    letter aside); 'something else' tails are left to the free-text answer."""
    if not span or span.casefold() not in source.casefold():
        return []
    questions = [q for q in re.findall(r"[^.?!\n]*\?", span) if re.search(r"\bor\b", q, re.I)]
    if len(questions) != 1:
        return []
    # Markup and a leading 'Ask:'-style label are framing, not part of an option.
    q = re.sub(r"[*_`]+", "", questions[0]).strip()
    q = re.sub(r"^[A-Za-z][\w ]{0,20}:\s*", "", q)
    parts = re.split(r",?\s+or\s+", q.rstrip('?').strip(), flags=re.I)
    if len(parts) != 2:
        return []
    out = []
    for part in parts:
        part = _ALT_LEAD.sub('', part.strip(' ,;:')).strip(' ,;:')
        if _ALT_OTHER.match(part):
            continue
        if _WH.match(part) or len(part.split()) < 2 or len(part) > OPTION_MAX:
            return []  # open questions ('which model ID…') are not pickable options
        out.append(_cap(part))
    return out


_WHETHER = re.compile(r"\bwhether\s+to\s+(.+?)\s*,?\s+or\s+(?:to\s+)?(.+?)[\s.?!]*$", re.I)


def _whether_alternatives(text: str) -> list[str]:
    """The two alternatives of a 'Choose whether to A or B' step text (the title the card shows). Both are
    verbatim slices of that title; an 'or not' / 'something else' tail, a third 'or', or a clause break inside an
    option ('whether CI moved, then unblock X or archive it' is not two options) yields none."""
    m = _WHETHER.search(re.sub(r"[*_`]+", "", _ws(text)))
    if not m or re.search(r"\bor\b", m.group(1), re.I) or re.search(r"\bor\b", m.group(2), re.I):
        return []
    out = [part.strip(' ,;:') for part in m.groups()]
    if any(_ALT_OTHER.match(p) or re.fullmatch(r"not", p, re.I) or len(p.split()) < 2 or len(p) > OPTION_MAX
           or re.search(r"[;:]|,\s*(?:then|and|so|but)\b|\bthen\b", p, re.I)
           for p in out):
        return []
    return [_cap(p) for p in out]


def literal_options(step: dict) -> list[str]:
    """Answer buttons for a legacy clarification: only text literally in the source.

    The question's own 'A, or B?' alternatives come first, then any model option found
    verbatim. An 'other' tail ('something else', 'neither') is never a button: the
    free-text answer covers it, and a lone 'something else' button hid the real choice
    (desktop-1321064734)."""
    source = _ws(step.get('paragraph') or step.get('text'))
    low = source.casefold()
    found = []
    for o in step.get('options') or []:
        o = _ws(o)
        if o and o.casefold() in low and not _ALT_OTHER.match(o.strip(' .!?')):
            i = low.index(o.casefold())
            found.append(source[i:i + len(o)])
    if len(found) >= 2:
        out = found
    else:
        out = _question_alternatives(_ws(step.get('verbatim_span') or step.get('paragraph')), source)
        # The agent's own options were paraphrases (dropped above), but the card's title states both: use them.
        # Live repro (hermes-hub 2026-10-28, gen-3/gen-4 spot): only 'keep spot' survived and Approve had no button.
        if len(out) < 2 and len(whether := _whether_alternatives(step.get('text'))) == 2:
            out = whether
        # a model option that is part of a derived alternative is the same choice, not a second button
        out += [x for x in found if not any(x.casefold() in y.casefold() for y in out)]
    return list(dict.fromkeys(out))[:4]


ANSWER_TEXT = {'id': 'answer-text', 'label': 'Answer in your own words', 'effect': 'answer'}

# A question about WHEN or WHETHER to run/do something (not "which colour"): its go options authorize.
_RUN_Q = re.compile(r"\b(?:when|whether|should\s+I|shall\s+I|ok(?:ay)?\s+to|go\s+ahead|want\s+me\s+to|can\s+I)\b", re.I)
_RUN_VERB = re.compile(r"\b(?:run|start|do|restart|migrate|migration|ship|deploy|merge|apply|proceed|execute|kick\s+off|"
                       r"launch|install|update|upgrade|reboot|rollout|roll\s+out|cut\s?over|switch|push|release)\w*\b", re.I)
_GO = re.compile(r"^(?:now|right\s+now|yes|yes,?\s+\w.*|go(?:\s+ahead)?|do\s+it(?:\s+now)?|run\s+it(?:\s+now)?|proceed|asap|"
                 r"when\s+(?:it'?s\s+)?idle|(?:once|after)\s+(?:it'?s\s+)?idle|tonight|immediately|start\s+now)[.!]?$", re.I)
_NO_GO = re.compile(r"\b(?:no|not|don'?t|later|wait|hold|skip|never|cancel|stop|pause|defer|tomorrow)\b", re.I)


def run_question(*texts) -> bool:
    text = ' '.join(t for t in texts if isinstance(t, str))
    return bool(_RUN_Q.search(text) and _RUN_VERB.search(text))


def go_option(label: str) -> bool:
    """'now', 'when idle', 'yes' approve; 'later', 'wait', 'no' and anything hedged do not."""
    label = _ws(label).strip()
    return bool(_GO.match(label)) and not _NO_GO.search(label)


def legacy_request(owner: dict, step: dict, ref: dict) -> dict:
    """Canonical v2 shape for an extracted step. Never a generic 'request' button:
    clarifications get their literal options + free text, user steps get Done + free
    text, approvals stay agent-incomplete (no user button) until a producer publishes
    the exact plan. Nothing here grants execution permission."""
    kind = {'approve': 'approval', 'choose': 'choice', 'do': 'external_step'}.get(step.get('kind'), 'input')
    markdown = step.get('paragraph') or step['text']
    title = step['text']
    if step.get('payload'):
        title = payload_title(title, step['payload'])
        if step['payload'] not in markdown:
            markdown = markdown + '\n\n' + step['payload']
    r = {'schemaVersion': 2, 'owner': owner, 'requestId': str(uuid.uuid4()), 'revision': 1, 'kind': kind,
         'title': title, 'scope': {'markdown': markdown, 'complete': True, 'sourceRefs': [ref]},
         'limits': [], 'exclusions': [], 'aliases': [ref], 'state': 'open'}
    no_grant = 'This does not grant execution permission.'
    if kind in ('choice', 'input'):
        # Completeness here describes the QUESTION, never executable scope.
        r['choices'] = [{'id': f'answer-{i}', 'label': label, 'effect': 'answer'}
                        for i, label in enumerate(literal_options(step))] + [dict(ANSWER_TEXT)]
        go = run_question(markdown, step.get('text')) and [c for c in r['choices'] if go_option(c['label'])]
        for c in go or []:
            c['authorizes'] = True
        if go:
            # A "when/whether should I run X" question: picking "now"/"when idle"/"yes" IS the go-ahead for
            # exactly the frozen scope above (self-heal agent-aa8d66631c: "now" was read as timing only).
            r['outcomes'] = {'answer': 'Send this answer to the agent. Options marked ✓ approve running exactly '
                                       'the scope above; any other answer (and free text) grants no execution '
                                       'permission.',
                             'revise': f'Clarify the question; {no_grant[0].lower() + no_grant[1:]}'}
        else:
            r['outcomes'] = {'answer': f'Send this answer to the agent. {no_grant}',
                             'revise': f'Clarify the question; {no_grant[0].lower() + no_grant[1:]}'}
    elif kind == 'external_step':
        r['choices'] = [{'id': 'answer-done', 'label': 'Done', 'effect': 'answer'},
                        dict(ANSWER_TEXT, label="Can't do it / reply in your own words")]
        r['outcomes'] = {'answer': f'Tell the agent. {no_grant}'}
    else:
        r['scope']['complete'] = False
        r['state'] = 'needs_context'
        r['choices'] = [{'id': 'revise', 'label': 'Request changes', 'effect': 'revise'}]
        r['outcomes'] = {'revise': 'Waiting for the agent to publish the full exact plan. Nothing can be '
                                   'approved yet and no permission is granted.'}
    return r

# ---- moot-ask lifecycle: retire asks whose work settled, or that their chat/user withdrew -------------

def _retire_steps(ledger: "Ledger", session: str, ids: list[str], resolution: str, by: str) -> None:
    """Mark this session's legacy ledger steps behind a withdrawn request resolved, so the passive strip, steps
    payload and ask-gate see the ask as retired (status stays a known value). ``withdrawn`` records that no
    decision was made: asking the same thing again later is a fresh ask, not a repeat to hold."""
    root = ledger.root(session)
    def hit(s):
        return s.get("id") in ids and s.get("session") == session and s.get("status") == "open"
    if not any(hit(s) for s in ledger._read(root).get("steps", [])):
        return
    with ledger.edit(root) as d:
        for s in d.get("steps", []):
            if hit(s):
                s.update(status="resolved", resolution=resolution[:200], resolved_by=by, resolved_at=time.time(),
                         withdrawn=True)


def withdraw_request(store: "ActionStore", ledger: Optional["Ledger"], owner: dict, rid: str, *, by: str,
                     reason: str, evidence: Optional[str] = None, expected_revision: Optional[int] = None,
                     now: Optional[float] = None, cards=None) -> dict:
    """Withdraw one canonical request (ActionStore.withdraw), then retire its ledger step(s).

    ``cards`` (QualifiedCardDelivery) also lets a request bound to a decision card be withdrawn: the card
    is closed (archived) right after. Repeating the withdrawal retries a card close that failed."""
    view = store.withdraw(owner, rid, by=by, reason=reason, evidence=evidence,
                          expected_revision=expected_revision, now=now, closes_cards=cards is not None)
    if cards is not None and view.get("state") == "withdrawn" and any(
            a.get("type") == "kanban" for a in view.get("aliases", [])):
        w = view.get("withdrawal") or {}
        try:
            cards.close_withdrawn(view, w.get("reason") or reason)
        except Exception as exc:
            raise ActionError(f"Request withdrawn, but closing its decision card failed ({exc}); "
                              "repeat the withdrawal to retry", 503) from exc
    ids = [a["id"] for a in view.get("aliases", []) if a.get("type") == "user_step"
           and a.get("gatewayId") == owner["gatewayId"] and a.get("profile") == owner["profile"]]
    if ids and ledger is not None:
        w = view.get("withdrawal") or {}
        try:
            _retire_steps(ledger, owner["storedSessionId"], ids, f"{NO_LONGER_NEEDED}: " + (w.get("reason") or reason),
                          w.get("by") or by)
        except Exception:  # the canonical request is already withdrawn; the legacy mirror is best effort
            logging.getLogger("user-steps").exception("withdrawn request %s: ledger step not retired", rid)
    return view


_CARD_ID = re.compile(r"\bt_[0-9a-f]{8}(?:[0-9a-f]{8})?\b")
_TERMINAL = ("done", "archived")


def kanban_root() -> Path:
    """Boards are install-shared (core kanban_home), not per profile; fall back to this home."""
    try:
        from hermes_cli.kanban_db import kanban_home  # type: ignore
        return Path(kanban_home())
    except Exception:
        return home()


def kanban_boards(root: Optional[Path] = None) -> list[tuple[str, Path]]:
    """Live boards only: `kanban/boards/_archived/...` holds retired copies, whose duplicate ids would
    make every card look ambiguous (and ambiguous ids never retire anything)."""
    r = root or kanban_root()
    out = [("default", r / "kanban.db")] + [(p.parent.name, p) for p in sorted((r / "kanban" / "boards").glob("*/kanban.db"))
                                            if not p.parent.name.startswith("_")]
    return [(b, p) for b, p in out if p.is_file()]


def _ts(value) -> Optional[float]:
    """Kanban timestamps are mixed: epoch seconds from the local writer, milliseconds from synced holders."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v / 1000.0 if v > 1e11 else v


# A card the ask is about counts only if it was WAITING on someone when the ask was made: blocked, in review,
# or escalated to triage. The ask is moot once every such card is done or archived after the ask. Unblocking
# does NOT end the wait: agents unblock to retry, and the retry often hits the same wall. A card that was
# merely running when asked is not counted: the ask may be about what happens after it finishes.
_WAIT_KINDS = ("blocked", "review_requested", "block_loop_detected", "gave_up")
_RELEASE_KINDS = ("unblocked", "completed", "archived", "changes_requested", "claimed", "promoted_manual")
_EVENT_KINDS = _WAIT_KINDS + _RELEASE_KINDS
# Core's ``human_request.resolved_check`` (kanban_ask_resolved): the probe exited 0, so core appended
# ``ask_resolved`` {cmd, rc, output} and, in the same txn, ``unblocked`` {via: ask_resolved}. That is the one
# unblock that is not a retry: David's step happened outside Hermes. Read only as evidence for _ask_resolution.
_ASK_RESOLVED = "ask_resolved"
_FACT_KINDS = _EVENT_KINDS + (_ASK_RESOLVED,)


def _card_facts(task_id: str, boards: list[tuple[str, Path]], board: Optional[str] = None,
                conns: Optional[dict] = None) -> Optional[dict]:
    """{board, status, completed_at, events: [(ts, kind, payload)]} for a card on exactly one local board.

    None when the id is unknown or ambiguous, or when a board could not be read (it could hold a duplicate).
    ``conns`` caches one read-only connection per board for a whole pass (the caller closes them)."""
    found = []
    marks = ",".join("?" * len(_FACT_KINDS))
    for slug, path in boards:
        if board and slug != board:
            continue
        try:
            c = conns.get(path) if conns is not None else None
            if c is None:
                c = _ro_connect(path, timeout=2)
                if conns is not None:
                    conns[path] = c
            try:
                row = c.execute("SELECT status, completed_at FROM tasks WHERE id=?", (task_id,)).fetchone()
                events = c.execute(f"SELECT created_at, kind, payload FROM task_events WHERE task_id=? AND kind IN ({marks}) "
                                   "ORDER BY created_at, id", (task_id, *_FACT_KINDS)).fetchall() if row else []
            finally:
                if conns is None:
                    c.close()
        except sqlite3.Error:
            return None
        if not row:
            continue
        timed = sorted(((_ts(ts), kind, payload) for ts, kind, payload in events if _ts(ts) is not None),
                       key=lambda e: e[0])
        found.append({"board": slug, "status": row[0], "completed_at": _ts(row[1]), "events": timed})
    return found[0] if len(found) == 1 else None


def _releases(kind: str, payload) -> bool:
    if kind != "claimed":
        return True
    try:  # a reviewer claiming a card in review continues the review wait
        return (json.loads(payload or "{}") or {}).get("source_status") != "review"
    except (ValueError, AttributeError):
        return True


def _waiting_at(facts: dict, at: float) -> bool:
    state = False
    for ts, kind, payload in facts["events"]:
        if ts > at:
            break
        if kind not in _EVENT_KINDS:
            continue  # evidence rows (ask_resolved) neither start nor end a wait; its `unblocked` does
        if kind in _WAIT_KINDS:
            state = True
        elif _releases(kind, payload):
            state = False
    return state


def _payload(raw) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _ask_resolution(facts: dict, asked_at: float) -> Optional[dict]:
    """{request, cmd, at} when core's resolved_check retired exactly the human ask the card was blocked on when
    this request was asked; else None.

    All of, in board event order: the card's last wait at or before ``asked_at`` is a ``blocked`` event whose
    human_request has an action and a resolved_check (the pending ask; a metadata-only amendment is a ``blocked``
    event too, so the latest one wins); the FIRST relevant lifecycle/proof event after the ask is ``ask_resolved`` with rc 0 and that
    same command, immediately followed by ``unblocked`` with via=ask_resolved (an amendment, David's own unblock,
    or any other move in between means a different ask was resolved); and nothing after it waits again (re-block,
    review, triage, a second resolution), with the card's current status not a wait either."""
    pending = None
    after = []
    for ts, kind, raw in facts["events"]:
        if ts > asked_at:
            after.append((ts, kind, raw))
        elif kind == "blocked":
            pending = _payload(raw).get("human_request")
        elif kind in _WAIT_KINDS or kind == _ASK_RESOLVED or (kind in _RELEASE_KINDS and _releases(kind, raw)):
            pending = None
    if not isinstance(pending, dict) or not str(pending.get("action") or "").strip():
        return None
    cmd = pending.get("resolved_check")
    if not isinstance(cmd, str) or not cmd.strip() or len(after) < 2:
        return None
    (at, kind, raw), (_uts, ukind, uraw) = after[0], after[1]
    proof = _payload(raw)
    rc = proof.get("rc")
    if (kind != _ASK_RESOLVED or type(rc) is not int or rc != 0
            or not isinstance(proof.get("cmd"), str) or proof["cmd"].strip() != cmd.strip()):
        return None
    if ukind != "unblocked" or _payload(uraw).get("via") != _ASK_RESOLVED:
        return None
    if any(k in _WAIT_KINDS or k == _ASK_RESOLVED for _ts2, k, _p in after[2:]):
        return None
    if facts["status"] in ("blocked", "triage", "review"):
        return None
    return {"request": pending, "cmd": cmd.strip(), "at": at}


def _restates_card_ask(request: dict, owner: dict, steps: dict, board: str, task_id: str, human_request: dict) -> bool:
    """Identity, not context: the request's own ask (its title, or its same-owner extracted step's text/sentence)
    restates the card's human_request.action (``card_ask_for`` text rules). Menus are not compared: generic
    Approve/Decline labels match any approval. A request that merely names the card asks something else."""
    entry = {"id": f"kanban:{board}:{task_id}", "kind": KANBAN_ASK, "text": str(human_request.get("action") or ""),
             "board": board, "task_id": task_id, "labels": []}
    candidates = [{"text": request.get("title") or "", "verbatim_span": ""}]
    for a in request.get("aliases", []):
        if a.get("type") == "user_step" and a.get("gatewayId") == owner["gatewayId"] and a.get("profile") == owner["profile"]:
            s = steps.get(a.get("id")) or {}
            candidates.append({"text": s.get("text") or "", "verbatim_span": s.get("verbatim_span") or ""})
    return any(card_ask_for(c, [entry]) is not None for c in candidates)


def _card_verdict(facts: dict, asked_at: float, same_ask=None) -> Optional[str]:
    """None = not counted (not waiting when asked), 'open' = its wait has not ended, else why it ended.

    ``same_ask(human_request) -> bool``: when given, a card whose pending ask core's resolved_check retired
    (``_ask_resolution``) has ended its wait too, but only if the request IS that ask. Any other unblock stays
    'open': agents unblock to retry."""
    if not _waiting_at(facts, asked_at):
        return None
    status = facts["status"]
    if status in _TERMINAL and (any(kind in ("completed", "archived") for ts, kind, _ in facts["events"] if ts > asked_at)
                                or (facts["completed_at"] or 0) > asked_at):
        return f"is {status}"
    if same_ask is not None:
        resolved = _ask_resolution(facts, asked_at)
        if resolved is not None and same_ask(resolved["request"]):
            cmd = resolved["cmd"] if len(resolved["cmd"]) <= 80 else resolved["cmd"][:77] + "..."
            return f"had its ask resolved outside Hermes (resolved_check `{cmd}` exited 0)"
    return "open"


def _watched_cards(request: dict, owner: dict, steps: dict) -> list[tuple[Optional[str], str]]:
    """Cards the ask is about: ids in its title and its extracted step's text (the ask itself), plus explicit
    same-owner kanban sourceRefs. Never the evidence sentence or its paragraph, which name context too
    ("t_X is blocked on CI; meanwhile, pick the pricing tier")."""
    texts = [request.get("title") or ""]
    for a in request.get("aliases", []):
        if a.get("type") == "user_step" and a.get("gatewayId") == owner["gatewayId"] and a.get("profile") == owner["profile"]:
            texts.append((steps.get(a.get("id")) or {}).get("text") or "")
    out: list[tuple[Optional[str], str]] = []
    for t in texts:
        out += [(None, m) for m in _CARD_ID.findall(t)]
    for ref in (request.get("scope") or {}).get("sourceRefs", []):
        if ref.get("type") == "kanban" and ref.get("gatewayId") == owner["gatewayId"] and ref.get("profile") == owner["profile"]:
            out.append((ref.get("board"), ref.get("id")))
    seen, uniq = set(), []
    for board, tid in out:
        if tid and tid not in seen:
            seen.add(tid)
            uniq.append((board, tid))
    return uniq


def _asked_at(store: "ActionStore", owner: dict, request: dict, life: Optional[dict], steps: dict, now: float) -> float:
    """When the current revision was asked. A row from before the lifecycle table takes its ledger step's time;
    a revision nothing stamped (an older process re-asked) is a fresh ask as of now."""
    if life is None or life.get("created_at") is None:
        ts = next((steps[a["id"]].get("created") for a in request.get("aliases", [])
                   if a.get("type") == "user_step" and (steps.get(a.get("id")) or {}).get("created")), None)
        return store.stamp_ask(owner, request["requestId"], ts or now, request["revision"])
    if life.get("asked_revision") != request["revision"]:
        return store.stamp_ask(owner, request["requestId"], now, request["revision"], fresh=True)
    return life["created_at"]


def reconcile_moot(store: "ActionStore", ledger: "Ledger", owner: dict, *, boards=None,
                   now: Optional[float] = None) -> list[dict]:
    """Withdraw (by=system) each undecided ask whose linked work is over.

    Every card the ask names must resolve on exactly one readable local board; at least one of them was
    waiting on someone when the ask was made; every such card is done or archived after the ask, or (the request
    restating that card's own human ask) core's ``resolved_check`` retired exactly the ask it was blocked on
    (``_ask_resolution``); and no named card is still in flight. Anything else (unknown, ambiguous or unreadable ids, a card still waiting or
    running, a recorded decision, a bound decision card) leaves the ask alone: a stale tile is recoverable
    ("Not needed"), a wrongly retired ask is lost. Runs on every For-you read and agent turn.
    """
    now = now or time.time()
    out: list[dict] = []
    conns: dict = {}
    steps: Optional[dict] = None
    try:
        for r, life in store.moot_candidates(owner):
            if steps is None and any(a.get("type") == "user_step" for a in r.get("aliases", [])):
                try:
                    steps = {s.get("id"): s for s in ledger.read(owner["storedSessionId"]).get("steps", []) if s.get("id")}
                except Exception:
                    steps = {}
            watched = _watched_cards(r, owner, steps or {})
            if not watched:
                continue
            if boards is None:
                boards = kanban_boards()  # lazily: no board I/O when no ask names a card
            asked_at = _asked_at(store, owner, r, life, steps or {}, now)
            settled, keep = [], False
            for board, tid in watched:
                facts = _card_facts(tid, boards, board, conns)
                if facts is None:
                    keep = True  # unknown, ambiguous or unreadable: never retire on partial knowledge
                    break
                def same_ask(hr, _r=r, _board=facts["board"], _tid=tid):
                    return _restates_card_ask(_r, owner, steps or {}, _board, _tid, hr)
                verdict = _card_verdict(facts, asked_at, same_ask)
                if verdict is None:
                    if facts["status"] not in _TERMINAL:
                        keep = True  # a named card is still in flight
                        break
                    continue
                if verdict == "open":
                    keep = True
                    break
                settled.append(f"{tid} {verdict}")
            if keep or not settled:
                continue
            reason = ("Linked card " if len(settled) == 1 else "Linked cards: ") + "; ".join(settled)
            try:
                out.append(withdraw_request(store, ledger, owner, r["requestId"], by="system", reason=reason,
                                            expected_revision=r["revision"], now=now))
            except ActionError:
                continue  # raced with a decision or a new revision: the next pass re-evaluates
        return out
    finally:
        for c in conns.values():
            try:
                c.close()
            except sqlite3.Error:
                pass
