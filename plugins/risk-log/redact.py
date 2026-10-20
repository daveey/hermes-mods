"""Secret redaction for stored command prefixes (patterns follow gitleaks' default rules)."""

from __future__ import annotations

import hashlib
import re

# (pattern, replacement). Order matters: specific vendor formats first, generic assignment last.
_RULES = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "[REDACTED:private-key]"),
    (r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b", "[REDACTED:aws]"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", "[REDACTED:github]"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[REDACTED:github]"),
    (r"\bglpat-[A-Za-z0-9_-]{20}\b", "[REDACTED:gitlab]"),
    (r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b", "[REDACTED:slack]"),
    (r"\bxapp-\d-[A-Za-z0-9-]{10,}\b", "[REDACTED:slack]"),
    (r"https://hooks\.slack\.com/[A-Za-z0-9/_-]+", "[REDACTED:slack-webhook]"),
    (r"\bsk-ant-[A-Za-z0-9_-]{20,}", "[REDACTED:anthropic]"),
    (r"\bsk-(?:proj-|or-v1-|svcacct-)?[A-Za-z0-9_-]{20,}", "[REDACTED:sk]"),
    (r"\bAIza[0-9A-Za-z_-]{35}\b", "[REDACTED:gcp]"),
    (r"\bya29\.[0-9A-Za-z_-]{20,}", "[REDACTED:google-oauth]"),
    (r"\bnpm_[A-Za-z0-9]{36}\b", "[REDACTED:npm]"),
    (r"\bhf_[A-Za-z]{30,}\b", "[REDACTED:hf]"),
    (r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b", "[REDACTED:stripe]"),
    (r"\btbk_[A-Za-z0-9._-]{8,}", "[REDACTED:broker]"),
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "[REDACTED:jwt]"),
    (r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{12,}", r"\1 [REDACTED]"),
    (r"(?i)(://[^/\s:@]+):[^@\s/]+@", r"\1:[REDACTED]@"),
    (r"(?i)(\s--?[A-Za-z-]*(?:token|api-?key|secret|password|passwd))(\s+|=)(['\"]?)[^\s'\"]{6,}\3",
     r"\1\2\3[REDACTED]\3"),
    (r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|auth|credential)[A-Za-z0-9_]*)"
     r"(\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}\3", r"\1\2\3[REDACTED]\3"),
    (r"(?<![\w/.-])[A-Za-z0-9+_=-]{32,}", lambda m: _long(m.group(0))),
]


def _long(token: str) -> str:
    """Mixed-case + digit run of 32+ chars = random key material. Lower-case hex (git shas) and path or word
    runs (``software-development``) stay readable."""
    mixed = any(c.islower() for c in token) and any(c.isupper() for c in token) and any(c.isdigit() for c in token)
    return "[REDACTED:long]" if mixed else token


_COMPILED = [(re.compile(p), r) for p, r in _RULES]

PREFIX_CHARS = 80


def redact(text: str) -> str:
    out = str(text or "")
    for pattern, repl in _COMPILED:
        out = pattern.sub(repl, out)
    return out


def prefix(text: str, limit: int = PREFIX_CHARS) -> str:
    """Redact the WHOLE text first, then cut, so a secret straddling the cut is still masked."""
    one_line = " ".join(redact(text).split())
    return one_line[:limit]


def sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest()
