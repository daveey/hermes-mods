"""coach drafts: the judged draft behind each reply_nudge / report_only_confirm event, so live precision can be audited.

log.jsonl keeps only rule / p / evidence, and the session's last_reply is overwritten by the next reply, so a nudge's
draft was lost one turn later (audit t_01a17f79ade9cbdd: 3 of 5 sampled nudges unrecoverable). Each judged draft is
stored once under a `draft_id` that the log events carry:

    <HERMES_HOME>/coach/drafts.jsonl   {"draft_id", "ts", "session_id", "platform", "ask", "draft"}

Privacy and size:
  * only the user's ask and the agent's final reply are kept, never tool calls or tool outputs;
  * both pass through core's secret redaction (`agent.redact.redact_sensitive_text(force=True)`), with a local
    pattern fallback when core isn't importable (tests, old cores), and are cut to ASK_CHARS / DRAFT_CHARS
    (the judge itself reads at most 1500 / 4000 chars of them);
  * the file is capped at MAX_BYTES and MAX_AGE_S: on overflow or an aged head record it is rewritten with aged
    records dropped and the oldest dropped until it is under 3/4 of MAX_BYTES. Reads skip aged records too.
Env overrides: COACH_DRAFTS_MAX_BYTES, COACH_DRAFTS_MAX_AGE_DAYS; COACH_DRAFTS=off disables the store.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

ASK_CHARS = 1500
DRAFT_CHARS = 6000
MAX_BYTES = 2_000_000
MAX_AGE_S = 30 * 86400
FILE = "drafts.jsonl"

# Fallback only (core's redactor is preferred): vendor token shapes, key=value secrets, URL credentials.
_FALLBACK = [(re.compile(p), r) for p, r in [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", "[REDACTED:private-key]"),
    (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[REDACTED]"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[REDACTED]"),
    (r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b", "[REDACTED]"),
    (r"\bsk-[A-Za-z0-9_-]{20,}", "[REDACTED]"),
    (r"\bAIza[0-9A-Za-z_-]{35}\b|\bhf_[A-Za-z]{30,}\b|\bnpm_[A-Za-z0-9]{36}\b", "[REDACTED]"),
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "[REDACTED]"),
    (r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{12,}", r"\1 [REDACTED]"),
    (r"(?i)(://[^/\s:@]+):[^@\s/]+@", r"\1:[REDACTED]@"),
    (r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|credential)[A-Za-z0-9_]*)"
     r"(\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}\3", r"\1\2\3[REDACTED]\3"),
]]


def redact(text: str) -> str:
    text = str(text or "")
    out = text
    try:
        from agent.redact import redact_sensitive_text  # type: ignore
        out = redact_sensitive_text(text, force=True)
    except Exception:
        out = text
    for pat, rep in _FALLBACK:  # belt and braces: also covers a core without force= or with redaction off
        out = pat.sub(rep, out)
    return out


def _clip(text: str, n: int) -> str:
    """Redact the whole text first, then cut, so a secret straddling the cut is still masked."""
    t = redact(text)
    return t if len(t) <= n else t[:n] + f" [...{len(t) - n} more chars]"


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except ValueError:
        return default


def new_id() -> str:
    return "d_" + uuid.uuid4().hex[:16]


class Drafts:
    def __init__(self, root: Path, clock: Callable[[], float] = time.time, max_bytes: Optional[int] = None,
                 max_age_s: Optional[float] = None):
        self.root = Path(root)
        self.clock = clock
        self.max_bytes = max_bytes if max_bytes is not None else _env_int("COACH_DRAFTS_MAX_BYTES", MAX_BYTES)
        self.max_age_s = (max_age_s if max_age_s is not None
                          else _env_int("COACH_DRAFTS_MAX_AGE_DAYS", MAX_AGE_S // 86400) * 86400)

    @property
    def path(self) -> Path:
        return self.root / FILE

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("COACH_DRAFTS", "on").strip().lower() not in ("0", "off", "false", "no")

    @contextlib.contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".drafts.lock", "a+") as fh:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self) -> list[dict]:
        out = []
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def put(self, draft_id: str, *, session_id: str, ask: str, draft: str, platform: str = "") -> Optional[str]:
        """Store one judged draft; returns draft_id, or None when disabled or on any error (never breaks a turn)."""
        if not self.enabled():
            return None
        try:
            rec = {"draft_id": draft_id, "ts": self.clock(), "session_id": session_id, "platform": platform or "",
                   "ask": _clip(ask, ASK_CHARS), "draft": _clip(draft, DRAFT_CHARS)}
            line = json.dumps(rec) + "\n"
            with self._lock():
                with self.path.open("a") as f:
                    f.write(line)
                self._compact_if_needed()
            return draft_id
        except Exception:
            return None

    def _compact_if_needed(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        cutoff = self.clock() - self.max_age_s
        head_aged = False
        if size <= self.max_bytes:
            try:
                with self.path.open() as f:
                    head_aged = json.loads(f.readline() or "{}").get("ts", cutoff) < cutoff
            except Exception:
                head_aged = True
            if not head_aged:
                return
        recs = [r for r in self._read() if r.get("ts", 0) >= cutoff]
        lines = [json.dumps(r) + "\n" for r in recs]
        budget = int(self.max_bytes * 0.75)
        total = sum(len(l.encode()) for l in lines)
        while lines and total > budget:
            total -= len(lines.pop(0).encode())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(lines))
        os.replace(tmp, self.path)

    def get(self, draft_id: str) -> Optional[dict]:
        if not draft_id:
            return None
        cutoff = self.clock() - self.max_age_s
        for r in reversed(self._read()):
            if r.get("draft_id") == draft_id:
                return r if r.get("ts", 0) >= cutoff else None
        return None

    def for_event(self, event: dict) -> Optional[dict]:
        """The judged draft behind a log.jsonl reply_nudge / report_only_confirm record."""
        return self.get((event or {}).get("draft_id") or "")
