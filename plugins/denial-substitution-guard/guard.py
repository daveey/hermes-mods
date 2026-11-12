"""Persistent denial mistake guard. Not a sandbox or authority verifier.

M1 containment: no time, identity, writable grant, or owner-terminal exemption.
Parser/tamper coverage remains bounded; see SPEC.md and the owner acceptance ledger.
"""
from __future__ import annotations

import contextlib
from collections import deque
import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Optional

MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_LEDGER_ROWS = 20000
MAX_OVERFLOW_EVIDENCE = 128
_PROD = False
_ROOT = None
_UNHEALTHY = {}
_OVERFLOW = {}
SHELL_TOOLS = {"terminal", "process_manage", "process", "tailnet_exec", "run_in_user_terminal"}
PY_TOOLS = {"execute_code"}
NESTED_TOOLS = {"tool_call", "mcp__tool_call"}
FILE_TOOLS = {"write_file", "mcp__write_file", "patch", "mcp__patch"}
PROTECTED = ("denial-ledger", "denial-grants")
GLOBAL_VALUE_OPTS = {"--profile", "--region", "--endpoint-url", "--output", "--query", "--cli-read-timeout",
                     "--cli-connect-timeout", "--ca-bundle", "--color"}
READERS = {"cat", "sed", "grep", "rg", "head", "tail", "less", "jq", "awk", "ls", "echo", "printf", "wc", "stat"}
WRITE_SHELL = re.compile(r"(>|\btee\b|\brm\b|\bmv\b|\bcp\b|\bsed\s+-i|\btruncate\b|\bdd\b|\binstall\b|\bpython3?\b|\bperl\b)")
WRITE_PY = re.compile(r"write|open\s*\(|unlink|remove|rename|replace|truncate", re.I)
DENIAL_START = re.compile(r"User:\s*(arn:\S+)\s+is not authorized to perform")
POSITIVE_READS = {("ssm", "getcommandinvocation"), ("sts", "getcalleridentity"),
                  ("ec2", "describeinstances"), ("ssm", "listcommands")}


def _account_root():
    try:
        from hermes_constants import account_default_hermes_home
        return account_default_hermes_home().resolve()
    except ImportError:
        import pwd
        return (Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes").resolve()


def resolve_root():
    global _ROOT
    if _ROOT is None:
        _ROOT = _account_root()
    return _ROOT


def _assert_writable_root(root):
    root, account = Path(root).resolve(), _account_root()
    if not _PROD and (root == account or account in root.parents):
        # Explicit synthetic scratch roots are the only exception, never profiles.
        scratch = account / "cache" / "scratch"
        if scratch not in root.parents:
            raise PermissionError("unregistered guard cannot write account ledger or descendants")


def _root(root):
    return Path(root).resolve() if root is not None else resolve_root()


def ledger_path(root=None):
    return _root(root) / "denial-ledger.jsonl"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _fingerprint(tool, args):
    texts = _call_texts(tool, args)
    return _digest(json.dumps(texts, ensure_ascii=True, separators=(",", ":")))


def iter_result_texts(value, depth=0):
    if depth > 5:
        return
    if isinstance(value, str):
        if len(value) > MAX_LEDGER_BYTES:
            raise ValueError("oversized denial result")
        try:
            decoded = json.loads(value)
        except ValueError:
            yield value
        else:
            if isinstance(decoded, (dict, str)):
                yield from iter_result_texts(decoded, depth + 1)
    elif isinstance(value, dict):
        for key in ("output", "stdout", "stderr", "error"):
            if value.get(key):
                yield from iter_result_texts(value[key], depth + 1)


def parse_denials(result):
    for stream in iter_result_texts(result):
        for line in stream.splitlines():
            starts = list(DENIAL_START.finditer(line))
            for i, match in enumerate(starts):
                text = line[match.end():starts[i + 1].start() if i + 1 < len(starts) else len(line)]
                action = re.match(r":\s*([A-Za-z0-9-]+:[A-Za-z0-9*]+)", text)
                resource = re.search(r"\bon resource:\s*(\S+)", text)
                explicit = True if "explicit deny" in text else False if "no identity-based policy allows" in text else None
                yield {"principal": match.group(1).rstrip(".,\"'\\"),
                       "action": action.group(1) if action else "UNKNOWN",
                       "resource": resource.group(1).rstrip(".,\"'\\") if resource else "UNKNOWN",
                       "explicit": explicit, "truncated": bool(re.search(r"…|truncated", text)),
                       "evidence_sha256": _digest(match.group(0) + text)}


def parse_denial(text):
    return next(parse_denials(text), None)


def invocation_identity(text):
    m = re.search(r"token-broker\s+exec\b.*?--scope[ =]['\"]?([\w./:-]+)", text or "", re.S)
    if m:
        return f"broker:{m.group(1)}"
    m = re.search(r"--profile[ =]['\"]?([\w./:@-]+)", text or "") or re.search(r"AWS_PROFILE=['\"]?([\w./:@-]+)", text or "")
    return f"profile:{m.group(1)}" if m else "ambient"


def _norm(op):
    return re.sub(r"[-_]", "", op).lower()


def _shell_calls(text, depth=0):
    out = []
    try:
        toks = shlex.split(text)
    except ValueError:
        toks = text.split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if os.path.basename(t) == "aws":
            j = i + 1
            while j < len(toks) and toks[j].startswith("-"):
                j += 2 if toks[j] in GLOBAL_VALUE_OPTS else 1
            if j + 1 < len(toks):
                out.append((toks[j].lower(), _norm(toks[j + 1])))
        elif depth < 2 and re.search(r"\baws\s", t) and " " in t:
            out += _shell_calls(t, depth + 1)
        i += 1
    return out


def _py_calls(code):
    out = []
    for lit in re.findall(r'''["']([^"'\n]*\baws\s[^"'\n]*)["']''', code):
        out += _shell_calls(lit)
    for svc in re.findall(r'''client\(\s*['"]([\w-]+)['"]''', code):
        for meth in re.findall(r"\.([a-z][a-z0-9_]*)\(", code):
            out.append((svc.lower(), _norm(meth)))
    return out


def _call_texts(tool_name, args):
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        return [("shell", " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data")))]
    if tool_name in PY_TOOLS:
        return [("python", str(args.get("code") or ""))]
    if tool_name in FILE_TOOLS:
        return [("file", str(args.get("path") or ""))]
    if tool_name in NESTED_TOOLS:
        return [t for call in args.get("calls") or [] if isinstance(call, dict)
                for t in _call_texts(str(call.get("name") or ""), call.get("arguments"))]
    return []


def _plausible_aws_call(kind, text):
    if kind == "shell" and os.path.basename((text.split() or [""])[0]) in READERS:
        return False
    return bool(re.search(r"\baws\b|token-broker\s+exec|boto3|botocore", text))


def _calls(kind, text):
    return _shell_calls(text) if kind == "shell" else _py_calls(text) if kind == "python" else []


def is_mutating(tool, args):
    return any(call not in POSITIVE_READS for kind, text in _call_texts(tool, args) for call in _calls(kind, text))


class LedgerState:
    def __init__(self, status, rows=None, reason=""):
        self.status, self.rows, self.reason = status, rows or [], reason


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate(row):
    if not isinstance(row, dict) or row.get("v", 0) not in (0, 1):
        raise ValueError("unsupported row schema")
    for name in ("action", "resource", "principal"):
        if not isinstance(row.get(name), str) or not row[name]:
            raise ValueError("invalid " + name)
    if not _finite(row.get("at")) or row.get("explicit") is not None and type(row["explicit"]) is not bool:
        raise ValueError("invalid timestamp or qualifier")
    if row["action"] == row["resource"] == "UNKNOWN" and not row.get("fingerprint"):
        raise ValueError("unknown legacy scope lacks invocation fingerprint")
    if row.get("v") == 1:
        if not _finite(row.get("observed_at")) or row["observed_at"] != row["at"]:
            raise ValueError("timestamp aliases disagree")
        for name in ("event_id", "execution_host", "fingerprint", "evidence_sha256"):
            if not isinstance(row.get(name), str) or not row[name]:
                raise ValueError("invalid " + name)
    return row


def _read_unlocked(root):
    try:
        if os.path.lexists(root / "denial-ledger.cap"):
            return LedgerState("UNHEALTHY", reason="persistent cap")
        if str(root) in _UNHEALTHY:
            return LedgerState("UNHEALTHY", reason=_UNHEALTHY[str(root)])
        path = root / "denial-ledger.jsonl"
        if not os.path.lexists(path):
            return LedgerState("MISSING")
        if path.stat().st_size >= MAX_LEDGER_BYTES:
            return LedgerState("UNHEALTHY", reason="byte cap")
        with path.open() as fh:
            data = fh.read(MAX_LEDGER_BYTES + 1)
        if len(data.encode()) >= MAX_LEDGER_BYTES:
            return LedgerState("UNHEALTHY", reason="byte cap")
        rows = [_validate(json.loads(line)) for line in data.splitlines() if line.strip()]
        if len(rows) >= MAX_LEDGER_ROWS:
            return LedgerState("UNHEALTHY", rows, "row cap")
        return LedgerState("VALID" if rows else "EMPTY", rows)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        return LedgerState("UNHEALTHY", reason=type(exc).__name__)


@contextlib.contextmanager
def _locked(root, exclusive=False):
    _assert_writable_root(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "denial-ledger.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_ledger(root=None):
    root = _root(root)
    try:
        with _locked(root):
            return _read_unlocked(root)
    except (OSError, ValueError) as exc:
        return LedgerState("UNHEALTHY", reason=type(exc).__name__)


def _dedupe(row):
    key = tuple(row.get(k) for k in ("action", "resource", "principal", "explicit", "execution_host"))
    return key + (row.get("fingerprint"),) if row["action"] == row["resource"] == "UNKNOWN" else key


def _cap(root):
    with open(root / "denial-ledger.cap", "a") as fh:
        fh.flush()
        os.fsync(fh.fileno())
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _retain_overflow(root, rows):
    # A bounded diagnostic sample, not authorization state. The persistent cap
    # or unhealthy latch holds mutations independently of retained evidence.
    _OVERFLOW.setdefault(str(root), deque(maxlen=MAX_OVERFLOW_EVIDENCE)).extend(rows)


def append_denials(rows, root=None):
    root = _root(root)
    _assert_writable_root(root)
    try:
        with _locked(root, True):
            state = _read_unlocked(root)
            if state.status == "UNHEALTHY":
                _retain_overflow(root, rows)
                if "cap" in state.reason:
                    _cap(root)
                return
            keys = {_dedupe(row) for row in state.rows}
            count = len(state.rows)
            size = (root / "denial-ledger.jsonl").stat().st_size if state.rows else 0
            for row in rows:
                _validate(row)
                key = _dedupe(row)
                if key in keys:
                    continue
                payload = (json.dumps(row, sort_keys=True) + "\n").encode()
                if count >= MAX_LEDGER_ROWS or size + len(payload) >= MAX_LEDGER_BYTES:
                    _retain_overflow(root, [row])
                    _cap(root)
                    continue
                new_file = not os.path.lexists(root / "denial-ledger.jsonl")
                fd = os.open(root / "denial-ledger.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    view = memoryview(payload)
                    while view:
                        n = os.write(fd, view)
                        if n <= 0:
                            raise OSError("short append")
                        view = view[n:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if new_file:
                    directory_fd = os.open(root, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                keys.add(key)
                count += 1
                size += len(payload)
    except Exception as exc:
        _UNHEALTHY[str(root)] = type(exc).__name__
        raise


def record_from_result(tool_name, args, result, now=None, *, root=None, **native_ids):
    texts = [(kind, text) for kind, text in _call_texts(tool_name, args) if _plausible_aws_call(kind, text)]
    if not texts:
        return
    when = time.time() if now is None else now
    fingerprint = _fingerprint(tool_name, args)
    identity = invocation_identity(texts[0][1]) if len(texts) == 1 else "unknown"
    host = str(native_ids.get("execution_host") or (args or {}).get("host") or "local")
    rows = []
    for index, denial in enumerate(parse_denials(result)):
        row = dict(denial, v=1, observed_at=when, at=when, identity=identity, identity_hint=identity,
                   execution_host=host, fingerprint=fingerprint, tool_name=tool_name,
                   session_id=str(native_ids.get("session_id") or ""), tool_call_id=str(native_ids.get("tool_call_id") or ""),
                   denial_index=index)
        row["event_id"] = "sha256:" + _digest(json.dumps(row, sort_keys=True))
        rows.append(row)
    if rows:
        append_denials(rows, root=root)


def verify_receipt(denial_row, invocation):
    return "UNAVAILABLE"


def _rid(resource):
    return re.split(r"[/:]", resource.rstrip("/"))[-1]


def _mentions(text, rid):
    return bool(rid) and rid != "*" and re.search(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])", text) is not None


def _tamper(kind, text):
    if any(name in text for name in PROTECTED) and (kind == "file" or kind == "shell" and WRITE_SHELL.search(text) or kind == "python" and WRITE_PY.search(text)):
        return "DENIAL GUARD: blocked apparent ledger/grant mutation. Agent-written state is not authority. No in-agent reset or grant route exists."
    return None


def classify(tool_name, args, now=None, *, root=None, **native_ids):
    texts = _call_texts(tool_name, args)
    for kind, text in texts:
        why = _tamper(kind, text)
        if why:
            return why
    invocations = [(kind, text, _calls(kind, text)) for kind, text in texts if kind != "file"]
    if not any(calls for _, _, calls in invocations):
        return None
    state = read_ledger(root=root)
    if state.status == "UNHEALTHY":
        return "DENIAL GUARD: recognized AWS mutation held: ledger unhealthy (" + state.reason + ")." if is_mutating(tool_name, args) else None
    fingerprint = _fingerprint(tool_name, args)
    for kind, text, calls in invocations:
        for row in state.rows:
            if row["action"] == row["resource"] == "UNKNOWN":
                matches = row.get("fingerprint") == fingerprint
            else:
                svc, _, op = row["action"].partition(":")
                action_match = (svc.lower(), _norm(op)) in calls if row["action"] != "UNKNOWN" else any(c not in POSITIVE_READS for c in calls)
                resource_match = row["resource"] == "UNKNOWN" or _mentions(text, _rid(row["resource"]))
                matches = action_match and resource_match
            if matches:
                return message(row, invocation_identity(text))
    return None


def message(row, identity):
    return (f"DENIAL GUARD: blocked repeat of denied {row['action']} on {row['resource']}, "
            f"recorded principal {row['principal']} (invocation hint {row.get('identity', 'unknown')}). "
            "Time, identity, transport, command success and agent-authored rationale do not establish subsequent authority. "
            "Independent exact-denial authority verification is UNAVAILABLE; this hold cannot be lifted by this plugin.")
