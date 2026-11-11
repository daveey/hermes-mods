"""Decision logic for denial-substitution-guard (see SPEC.md). Pure stdlib; every entry point fails open."""
from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Optional

TTL = 24 * 3600
SHELL_TOOLS = {"terminal", "process_manage", "process", "tailnet_exec"}
PY_TOOLS = {"execute_code"}
NESTED_TOOLS = {"tool_call", "mcp__tool_call"}
FILE_TOOLS = {"write_file", "mcp__write_file", "patch", "mcp__patch"}
PROTECTED = ("denial-ledger.jsonl", "denial-grants.json")
VERIFIERS = {"owner", "broker"}

DENIAL_RE = re.compile(
    r"User:\s*(?P<principal>arn:\S+)\s+is not authorized to perform:\s*(?P<action>[A-Za-z0-9-]+:[A-Za-z0-9*]+)"
    r"\s+on resource:\s*(?P<resource>\S+)(?P<tail>[^\n]*)")
TRUNC_RE = re.compile(r"…\s*\(\+\d+ chars\)|…\s*$")
GLOBAL_VALUE_OPTS = {"--profile", "--region", "--endpoint-url", "--output", "--query", "--cli-read-timeout",
                     "--cli-connect-timeout", "--ca-bundle", "--color"}
READERS = {"cat", "sed", "grep", "rg", "head", "tail", "less", "jq", "awk", "ls", "echo", "printf", "wc", "stat"}
WRITE_SHELL = re.compile(r"(>|\btee\b|\brm\b|\bmv\b|\bcp\b|\bsed\s+-i|\btruncate\b|\bdd\b|\binstall\b|\bpython3?\b|\bperl\b)")
WRITE_PY = re.compile(r"write|open\s*\(|unlink|remove|rename|replace|truncate", re.I)


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def ledger_path() -> Path:
    return hermes_home() / "denial-ledger.jsonl"


def grants_path() -> Path:
    return hermes_home() / "denial-grants.json"


# --- parsing -----------------------------------------------------------------------------------------------------

def parse_denial(text: Any) -> Optional[dict]:
    if not isinstance(text, str) or not text:
        return None
    m = DENIAL_RE.search(text)
    if not m:
        return None
    tail = m.group("tail")
    resource = m.group("resource").rstrip(".,\"'\\")
    if "explicit deny" in tail:
        explicit: Optional[bool] = True
    elif "no identity-based policy allows" in tail:
        explicit = False
    else:
        explicit = None  # text cut (or not stated) before the qualifier: never overstate
    return {"principal": m.group("principal").rstrip(".,\"'\\"), "action": m.group("action"), "resource": resource,
            "explicit": explicit, "truncated": bool(TRUNC_RE.search(text[m.start():]))}


def invocation_identity(text: str) -> str:
    text = text or ""
    m = re.search(r"token-broker\s+exec\b.*?--scope[ =]['\"]?([\w./:-]+)", text, re.S)
    if m:
        return f"broker:{m.group(1)}"
    m = re.search(r"--profile[ =]['\"]?([\w./:@-]+)", text) or re.search(r"AWS_PROFILE=['\"]?([\w./:@-]+)", text) \
        or re.search(r"profile_name\s*=\s*['\"]([\w./:@-]+)['\"]", text)
    return f"profile:{m.group(1)}" if m else "ambient"


def _norm(op: str) -> str:
    return re.sub(r"[-_]", "", op).lower()


def _shell_calls(text: str, depth: int = 0) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
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


def _py_calls(code: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for lit in re.findall(r"""["']([^"'\n]*\baws\s[^"'\n]*)["']""", code):
        out += _shell_calls(lit)
    services = re.findall(r"""client\(\s*['"]([\w-]+)['"]""", code)
    for svc in services:
        for meth in re.findall(r"\.([a-z][a-z0-9_]*)\(", code):
            out.append((svc.lower(), _norm(meth)))
    return out


def _call_texts(tool_name: str, args: Any) -> list[tuple[str, str]]:
    """(kind, text) per call: kind is 'shell' | 'python' | 'file'."""
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        return [("shell", " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data")))]
    if tool_name in PY_TOOLS:
        return [("python", str(args.get("code") or ""))]
    if tool_name in FILE_TOOLS:
        return [("file", str(args.get("path") or ""))]
    if tool_name in NESTED_TOOLS:
        out: list[tuple[str, str]] = []
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                out += _call_texts(str(call.get("name") or ""), call.get("arguments"))
        return out
    return []


# --- ledger ------------------------------------------------------------------------------------------------------

def _plausible_aws_call(kind: str, text: str) -> bool:
    if kind == "shell":
        first = os.path.basename((text.split() or [""])[0])
        if first in READERS:
            return False
    return bool(re.search(r"\baws\b|token-broker\s+exec|boto3|botocore", text))


def record_from_result(tool_name: str, args: Any, result: Any, now: Optional[float] = None) -> None:
    denial = parse_denial(result if isinstance(result, str) else None)
    if not denial:
        return
    for kind, text in _call_texts(tool_name, args):
        if kind == "file" or not _plausible_aws_call(kind, text):
            continue
        row = dict(denial, at=now if now is not None else time.time(), identity=invocation_identity(text))
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a") as fh:
            fh.write(json.dumps(row) + "\n")
        return


def _read_json_lines(path: Path) -> list[dict]:
    rows = []
    try:
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("action") and r.get("resource") and isinstance(r.get("at"), (int, float)):
                rows.append(r)
    except OSError:
        pass
    return rows


def _granted(row: dict) -> bool:
    try:
        grants = json.loads(grants_path().read_text())
    except (OSError, ValueError):
        return False
    for g in grants if isinstance(grants, list) else []:
        if (isinstance(g, dict) and g.get("action") == row["action"] and g.get("resource") == row["resource"]
                and g.get("verified_by") in VERIFIERS and g.get("source")
                and isinstance(g.get("granted_at"), (int, float)) and g["granted_at"] > row["at"]):
            return True
    return False


def _rid(resource: str) -> str:
    return re.split(r"[/:]", resource.rstrip("/"))[-1]


def _mentions(text: str, rid: str) -> bool:
    return bool(rid) and rid != "*" and re.search(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])", text) is not None


# --- decision ----------------------------------------------------------------------------------------------------

def _tamper(kind: str, text: str) -> Optional[str]:
    if not any(name in text for name in PROTECTED):
        return None
    if kind == "file" or (kind == "shell" and WRITE_SHELL.search(text)) or (kind == "python" and WRITE_PY.search(text)):
        return ("DENIAL GUARD: blocked: the denial ledger and grants file are owner-controlled. An agent cannot write "
                "a grant for a denied operation; ask the owner (run_in_user_terminal / approved broker request).")
    return None


def classify(tool_name: str, args: Any, now: Optional[float] = None) -> Optional[str]:
    if tool_name == "run_in_user_terminal":
        return None  # supported owner/human boundary
    texts = _call_texts(tool_name, args)
    for kind, text in texts:
        why = _tamper(kind, text)
        if why:
            return why
    now = time.time() if now is None else now
    rows = [r for r in _read_json_lines(ledger_path()) if now - r["at"] <= TTL]
    if not rows:
        return None
    for kind, text in texts:
        if kind == "file":
            continue
        calls = _shell_calls(text) if kind == "shell" else _py_calls(text)
        if not calls:
            continue
        identity = invocation_identity(text)
        for row in rows:
            svc, _, op = row["action"].partition(":")
            if (svc.lower(), _norm(op)) not in calls or not _mentions(text, _rid(row["resource"])):
                continue
            if identity == row.get("identity") or _granted(row):
                continue
            return message(row, identity)
    return None


def message(row: dict, identity: str) -> str:
    unk = "" if row.get("explicit") else " (the recorded denial text does not say whether it was an explicit deny)"
    return (f"DENIAL GUARD: blocked: {row['action']} on {row['resource']} was denied to {row['principal']} "
            f"(invoked as {row.get('identity')}){unk}. This call repeats the same operation on the same resource as "
            f"{identity}, which substitutes identity/transport instead of resolving the denial. No independently verified "
            f"grant is recorded. Ask the owner for the grant (run_in_user_terminal or an owner-approved broker request) "
            f"or use a different, unrelated operation; other actions and resources are not affected.")
