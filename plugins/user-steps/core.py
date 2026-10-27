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
        if _ASK.search(s) and not _NOT_ASK.search(s) and all(o["verbatim_span"] != s for o in out):
            kind = "approve" if _APPROVE.search(s) else "do" if _DO.search(s) else "input"
            out.append({"text": s[:300], "kind": kind, "verbatim_span": s, "options": []})
    return out


# ---- the aux-model extractor ---------------------------------------------------------------------------

SYSTEM = """You read an AI assistant's final chat reply to its user and list what the reply asks the USER to do \
or decide right now. Also say which previously-open steps are now resolved.

Return ONLY JSON:
{"steps":[{"text":"...","kind":"approve|do|choose|input","verbatim_span":"...","options":["..."]}],
 "resolved":["<open step id>", ...]}

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
- "resolved": ids from OPEN STEPS that the USER'S LAST MESSAGE answered, did, declined or made moot, or that the
  reply says are now done. Leave a step out when unsure.
- No asks: {"steps":[],"resolved":[...]}."""


def build_prompt(reply: str, open_steps: list[dict], user_msg: str) -> str:
    opened = "\n".join(f"- {s['id']} ({s.get('kind')}): {s.get('text')}" for s in open_steps) or "(none)"
    return (f"OPEN STEPS:\n{opened}\n\nUSER'S LAST MESSAGE:\n{(user_msg or '(none)')[:1500]}\n\n"
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


def locate(reply: str, span: str) -> Optional[dict]:
    """The prose block (not code, not quote) whose text contains ``span`` (markdown/whitespace-insensitive)."""
    ns = _norm(span)
    if len(ns) < 4:
        return None
    for b in _blocks(reply):
        if b["code"] or b["quote"]:
            continue
        nb = _norm(re.sub(r"`[^`\n]*`", lambda m: m.group(0).strip("`"), b["text"]))
        if ns in nb:
            return b
    return None


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


def validate(raw_steps: Any, reply: str, open_steps: list[dict], dropped: Optional[list] = None,
             *, continuity: bool = False) -> list[dict]:
    out: list[dict] = []
    # The creation boundary needs repeats too, including those resolved while the
    # model runs. Ordinary extractor callers retain their old deduplication.
    seen: set[Any] = set() if continuity else {_norm(s.get("text", "")) for s in open_steps}
    for s in raw_steps if isinstance(raw_steps, list) else []:
        if not isinstance(s, dict):
            continue
        text = re.sub(r"\s+", " ", str(s.get("text") or "")).strip()[:300]
        span = str(s.get("verbatim_span") or "").strip()
        kind = str(s.get("kind") or "").strip().lower()
        opts = [re.sub(r"\s+", " ", str(o)).strip()[:60] for o in (s.get("options") or []) if str(o).strip()][:4]
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
        block = locate(reply, span)
        if block is None or _norm(span) not in _norm(block["text"]):
            continue  # full evidence required; locate's framing prefix is not validation
        if recommendation_without_request(span, block["text"]):
            continue  # a recommendation is not permission to manufacture a choice
        candidate = {"text": text, "kind": kind, "verbatim_span": span, "options": opts}
        key = action_identity(candidate) if continuity else _norm(text)
        if key in seen:
            continue
        if is_terminal_handoff(text, span, reply):
            if dropped is not None:
                dropped.append(text)
            continue  # terminal-doable: the agent runs it via run_in_user_terminal, no For-you card
        seen.add(key)
        out.append(candidate)
    # Suppression/reuse must not be truncated by a *new* action budget.
    return out if continuity else out[:MAX_NEW_PER_REPLY]


LLM = Callable[[str, str], str]


def extract(reply: str, open_steps: list[dict], user_msg: str, llm: Optional[LLM],
            *, continuity: bool = False) -> dict:
    """{steps, resolved, source: 'model'|'regex', error?}."""
    ids = {s["id"] for s in open_steps}
    dropped: list[str] = []
    if llm is not None:
        try:
            d = _parse_json(llm(SYSTEM, build_prompt(reply, open_steps, user_msg)))
            resolved = [str(x) for x in (d.get("resolved") or []) if str(x) in ids]
            return {"steps": validate(d.get("steps"), reply, open_steps, dropped, continuity=continuity), "resolved": resolved,
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


def process_reply(reply: str, session_id: str, *, user_msg: str = "", llm: Optional[LLM] = None,
                  ledger: Ledger, cards: Optional[Cards] = None, framed: bool = True,
                  session_ref: str = "", now: Optional[float] = None,
                  action_store=None, action_owner=None) -> dict:
    """Run one final reply through the pipeline. Returns {text (None = unchanged), steps, resolved, source}."""
    # A positive retirement finding must survive later persistence/card failures.
    # Local state is not model-supplied and grants no reopen/execution authority.
    retired = []
    try:
        return _process_reply(reply, session_id, user_msg=user_msg, llm=llm, ledger=ledger,
                              cards=cards, framed=framed, session_ref=session_ref, now=now,
                              retired=retired, action_store=action_store, action_owner=action_owner)
    except Exception:
        if not retired:
            raise
        logging.getLogger("user-steps").exception("publication failed after retirement; preserving hold")
        return {"text": HELD_REPLY, "steps": [], "resolved": [], "source": "retirement-hold"}


def _process_reply(reply: str, session_id: str, *, user_msg: str, llm: Optional[LLM],
                   ledger: Ledger, cards: Optional[Cards], framed: bool,
                   session_ref: str, now: Optional[float], retired: list,
                   action_store=None, action_owner=None) -> dict:
    """Reconcile exact actions under the source lock before creation/publication.

    Resolution is suppression-only. Neither model metadata nor a later repeated
    extraction can reopen a source action. This is not semantic deduplication.
    """
    now = now or time.time()
    root = ledger.root(session_id)
    opened = ledger.open_steps(session_id)
    if not opened and not _worth_asking(reply):
        return {"text": None, "steps": [], "resolved": [], "source": "skip"}
    res = extract(reply, opened, user_msg, llm, continuity=True)
    published, resolved = [], []
    text = reply
    changed = False
    if res["steps"] or res["resolved"]:
        # Re-read after extraction under Ledger.edit's flock. Resolve, match,
        # budget, and append in one transaction; never decide from opened above.
        with ledger.edit(root) as d:
            seen = {_norm(s.get("text", "")) for s in d["steps"] if s.get("status") == "open"}
            for step in d["steps"]:
                if step.get("id") in res["resolved"] and step.get("status") == "open" and not step.get("requestId"):
                    step.update(status="resolved", resolution="answered in chat", resolved_by="model",
                                resolved_at=now)
                    resolved.append(step["id"])
            matches = [(s, [old for old in d["steps"] if action_identity(old) == action_identity(s)])
                       for s in res["steps"]]
            suppressed = [s for s, old in matches if any(x.get("status") == "resolved" for x in old)]
            held = False
            if suppressed:
                # Evidence does not delimit dependent instructions in other blocks.
                # Hold the whole draft rather than leave an orphaned approval ask.
                retired.extend(suppressed)
                text, held = HELD_REPLY, True
            recent = sum(1 for s in d["steps"] if now - s.get("created", 0) < 3600)
            budget = min(MAX_NEW_PER_REPLY, max(0, MAX_NEW_PER_HOUR - recent))
            # Retain old label dedup only for genuinely new actions, never use it
            # as an authority binding or to bypass exact retirement/reuse.
            for candidate, prior in matches:
                block = locate(text, candidate["verbatim_span"])
                if held or candidate in suppressed or block is None:
                    continue
                existing = next((s for s in prior if s.get("status") == "open"), None)
                if existing is not None:
                    published.append(dict(existing))
                    continue
                if budget <= 0 or (action_store is None and _norm(candidate["text"]) in seen):
                    continue
                step = {**candidate, "id": new_step_id(), "status": "open", "created": now,
                        "session": session_id, "paragraph": block["text"]}
                if action_store is not None:
                    canonical = action_store.migrate_step(action_owner, step)
                    step.update(requestId=canonical["requestId"], requestOwner=canonical["owner"],
                                requestRevision=canonical["revision"])
                d["steps"].append(step)
                published.append(dict(step))
                seen.add(_norm(candidate["text"]))
                budget -= 1
                changed = True
    if published and framed:
        text = frame(text, published, session_id)
    if changed or resolved:
        sync_card(ledger, root, session_id, session_ref or f"@session:default/{session_id}", cards)
    return {"text": text if text != reply else None, "steps": published, "resolved": resolved, "source": res["source"],
            "dropped": res.get("dropped") or [], **({"error": res["error"]} if res.get("error") else {})}


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


# ---- ActionRequest v2: immutable scope versions + durable decision outbox -----

class ActionError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


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
        for text in limit.values(): _string(text)
    for text in value['exclusions']: _string(text)
    _object(value['outcomes'], ('revise',), ('approve', 'decline'))
    for text in value['outcomes'].values(): _string(text)
    ids = set()
    for choice in value['choices']:
        _object(choice, ('id', 'label', 'effect'))
        _string(choice['id']); _string(choice['label'])
        if choice['id'] in ids or choice['effect'] not in ('approve', 'revise', 'decline', 'answer'):
            raise ActionError('Invalid or duplicate choice')
        ids.add(choice['id'])
        if choice['effect'] in ('approve', 'decline') and choice['effect'] not in value['outcomes']:
            raise ActionError('Choice requires a stated outcome')
    if not ids: raise ActionError('At least one choice is required')
    if value['state'] not in ('needs_context', 'open', 'resolved', 'superseded', 'cancelled'):
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


class ActionStore:
    """SQLite FULL-sync transaction commits decision + frozen delivery intent before dispatch.

    No fuzzy identity, cross-session search, or automatic compression/branch inheritance.
    Immutable revision documents are separate from resolution and delivery projections.
    """
    def __init__(self, path=None):
        self.path = Path(path) if path else home() / 'user-steps' / 'actions.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            ''')

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA synchronous=FULL')
        return c

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
        with self._tx() as c: return self._get(c, owner, rid)

    def requests(self, owner):
        with self._tx() as c:
            ids = c.execute('SELECT id FROM requests WHERE namespace=? AND session=? ORDER BY rowid',
                            (self._ns(owner), owner['storedSessionId'])).fetchall()
            return [self._get(c, owner, row[0]) for row in ids]

    def _aliases(self, c, owner, r):
        ns = self._ns(owner)
        for ref in r['aliases']:
            # Remote aliases are provenance only; never resolve/mutate another gateway's card.
            old = c.execute('SELECT namespace,id FROM aliases WHERE ref=?', (_json(ref),)).fetchone()
            if old and tuple(old) != (ns, r['requestId']):
                raise ActionError('Alias already belongs to another request', 409)
            c.execute('INSERT OR IGNORE INTO aliases VALUES (?,?,?)', (_json(ref), ns, r['requestId']))

    def create(self, owner, value, *, bind=None):
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
                if bind is not None: bind(r, 0)
        except sqlite3.IntegrityError: raise ActionError('Request already exists', 409)
        return r

    def history(self, owner, rid):
        with self._tx() as c:
            self._get(c, owner, rid)
            return [json.loads(row[0]) for row in c.execute(
                'SELECT body FROM revisions WHERE namespace=? AND id=? ORDER BY revision', (self._ns(owner), rid))]

    def revise(self, owner, rid, expected, replacement, *, bind=None):
        with self._tx() as c:
            old = self._get(c, owner, rid); ns = self._ns(owner)
            if type(expected) is not int or old['revision'] != expected: raise ActionError('Stale revision', 409)
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
        with self._tx() as c: return self._operation(c, owner, oid, key)

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
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).isoformat()
            oid = str(uuid.uuid4())
            op = {'operationId': oid, 'owner': owner, 'requestId': r['requestId'], 'revision': r['revision'],
                  'idempotencyKey': decision['idempotencyKey'], 'decision': decision,
                  'recordedAt': stamp, 'dispatchState': 'pending', 'attempts': 0,
                  'intent': {'request': r, 'effect': effect, 'revisionText': text,
                             'executionAuthorized': effect == 'approve',
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

    def dispatch(self, owner, oid, receiver):
        # Commit the attempt then release the DB lock BEFORE external I/O. Other
        # requests stay usable; concurrent retries share the receiver's durable key.
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
        try:
            if receiver is None: raise RuntimeError('No durable delivery receiver configured')
            receipt = receiver.deliver(op)
            confirmed = bool(receiver.readback(op, receipt))
        except Exception:
            # Never expose credentials, receiver URLs or raw transport bodies.
            pass
        with self._tx() as c:
            result = self._operation(c, owner, oid); op = result['operation']
            # A slow failed retry cannot downgrade a concurrently confirmed delivery.
            if op['dispatchState'] in ('delivered', 'cancelled'): return result
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
                op.update(dispatchState='failed', error='Delivery unconfirmed; retry the same idempotency key.')
            c.execute('UPDATE operations SET body=? WHERE id=?', (_json(op), oid))
            return {'operation': op, 'request': self._get(c, owner, op['requestId'])}

    def pending_owners(self):
        with self._tx() as c:
            owners = {_json(op['owner']): op['owner'] for row in c.execute('SELECT body FROM operations')
                      if (op := json.loads(row['body']))['dispatchState'] not in ('delivered', 'cancelled')}
            return list(owners.values())

    def reconcile(self, owner, receiver):
        with self._tx() as c:
            ids = [row['id'] for row in c.execute(
                'SELECT id,body FROM operations WHERE namespace=? AND session=? ORDER BY rowid',
                (self._ns(owner), owner['storedSessionId']))
                if json.loads(row['body'])['dispatchState'] not in ('delivered', 'cancelled')]
        return [self.dispatch(owner, oid, receiver) for oid in ids]

    def migrate_step(self, owner, step):
        if step.get('session') != owner['storedSessionId']: raise ActionError('Legacy session association unproven', 403)
        _string(step.get('id')); _string(step.get('text'))
        ref = dict(gatewayId=owner['gatewayId'], profile=owner['profile'], type='user_step', id=step['id'])
        with self._tx() as c:
            old = c.execute('SELECT namespace,id FROM aliases WHERE ref=?', (_json(ref),)).fetchone()
            if old:
                if old['namespace'] != self._ns(owner): raise ActionError('Alias owner mismatch', 403)
                return self._get(c, owner, old['id'])
        r = {'schemaVersion': 2, 'owner': owner, 'requestId': str(uuid.uuid4()), 'revision': 1,
             'kind': {'approve': 'approval', 'choose': 'choice', 'do': 'external_step'}.get(step.get('kind'), 'input'),
             'title': step['text'], 'scope': {'markdown': step.get('paragraph') or step['text'],
                                             'complete': False, 'sourceRefs': [ref]},
             'limits': [], 'exclusions': [], 'outcomes': {'revise': 'Request complete action; keep work blocked.'},
             'choices': [{'id': 'request-context', 'label': 'Request complete action', 'effect': 'revise'}],
             'aliases': [ref], 'state': 'needs_context'}
        try: return self.create(owner, r)
        except ActionError as exc:
            if exc.status != 409: raise
            with self._tx() as c:
                old = c.execute('SELECT id FROM aliases WHERE ref=?', (_json(ref),)).fetchone()
                if not old: raise
                return self._get(c, owner, old['id'])
