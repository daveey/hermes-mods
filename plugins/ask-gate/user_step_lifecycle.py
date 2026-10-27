"""Conservative retirement suppression for exact, source-owned user-step IDs.

Integration: call preflight with the runtime-owned session_id and complete draft
before any ask is delivered. None means continue ordinary gates, NOT permission.
bootstrap() is optional at plugin registration; preflight bootstraps lazily and
holds on failure. No model/tool argument may supply session_id or profile scope.

Source emits ::user-step{id="us_…,..." s="…" t="escaped paragraph"}.
The s and t attributes are untrusted display data, never ownership/provenance.
Plain prose and newly extracted IDs have no binding to old retired actions: this
adapter deliberately does not pretend to solve that source-contract gap.
"""
from __future__ import annotations

from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

_MARKER = re.compile(r"::user-step(?=\{|\s|$)")
# Match the source emitter, without trimming, repairing or interpreting IDs.
_DIRECTIVE = re.compile(
    r'::user-step\{id="([^"{}\n]*)" s="[A-Za-z0-9_.-]*"(?: t="[^"{}]*")?\}')
_ID = re.compile(r"us_[0-9a-f]{6,32}\Z")
_MAX_DRAFT = 256 * 1024
_MAX_IDS = 128


def _load_file(key, path):
    """Installed-code-only fallback, matching the plugins' sibling loader pattern."""
    path = path.resolve(strict=True)
    existing = sys.modules.get(key)
    if existing is not None and Path(existing.__file__).resolve() == path:
        return existing
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ImportError("Source module unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Publish only fully initialized modules; never accept a same-key other path.
    sys.modules[key] = mod
    return mod


def bootstrap():
    """Load real adjacent installed source; never search profiles or model paths.

    This repo's plugins are separately discovered hyphenated packages. There is
    no public cross-plugin import API. Resolve the sibling source from this
    installed file (including its deployment symlink), as user-steps does for
    ask-gate/handoff.py. No source ledger is read during bootstrap.
    """
    source = _load_file("ask_gate_user_steps_source", Path(__file__).resolve().parent.parent / "user-steps" / "core.py")
    if not callable(getattr(source.Ledger, "read_exact", None)):
        raise ImportError("Exact source reader unavailable")
    return source


def _lifecycle():
    if __package__:
        from . import lifecycle
        return lifecycle
    return _load_file("ask_gate_lifecycle", Path(__file__).with_name("lifecycle.py"))


def _home():
    try:
        from hermes_constants import get_hermes_home
    except ImportError:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().resolve()
    return Path(get_hermes_home()).expanduser().resolve()


def _root(source, home, session_id):
    """Source compression semantics, but fail closed instead of _row's fallback.

    Only exact primary-key lookups in the current profile's session DB. One read
    transaction keeps the bounded lineage walk consistent; never global scans.
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Missing authoritative session")
    with closing(source._ro_connect(home / "state.db", timeout=2)) as db:
        db.execute("BEGIN")

        def row(sid):
            rows = db.execute("SELECT id, parent_session_id, end_reason FROM sessions WHERE id=?", (sid,)).fetchmany(2)
            if len(rows) != 1:
                raise ValueError("Missing or ambiguous session")
            return rows[0]

        current = row(session_id)
        seen = {session_id}
        for _ in range(25):
            parent = current[1]
            if not parent:
                return current[0]
            ancestor = row(parent)
            if "compress" not in str(ancestor[2] or "").lower():
                return current[0]
            if parent in seen or len(seen) > 24:
                raise ValueError("Invalid or excessive compression lineage")
            seen.add(parent)
            current = ancestor
    raise ValueError("Excessive compression lineage")


def _hold(retired):
    reason = "user_step_retired" if retired else "user_step_unavailable"
    text = ("This exact user step is retired. Do not ask again or recreate it under a new ID."
            if retired else "User-step state could not be reconciled. Use agent-owned recovery, not a new human ask.")
    message = "ask-gate: " + text + " This is not execution authorization and does not satisfy OS authentication."
    return {"action": "block", "reason": reason, "message": message, "retry_message": message}


def preflight(context: dict) -> dict | None:
    """Read every exact ID in the full draft and durably retain observed retirement.

    A source resolved status is sufficient only for conservative suppression.
    resolved_by, resolution, verified and explicit_reopen are NEVER consulted.
    Store._observe is used solely as its monotonic suppression latch; no verified
    Record or trusted reopen proof is manufactured from this model-writable source.
    Missing/malformed/unknown sources hold operationally. No ledger is modified.
    """
    draft = context.get("draft")
    if not isinstance(draft, str) or not _MARKER.search(draft):
        return None
    if len(draft) > _MAX_DRAFT:
        return _hold(False)
    ids = []
    failed = False
    consumed = 0
    for marker in _MARKER.finditer(draft):
        if marker.start() < consumed:  # a marker inside escaped t is not another directive
            continue
        match = _DIRECTIVE.match(draft, marker.start())
        if match is None:
            failed = True
            continue
        consumed = match.end()
        directive_ids = match[1].split(',')
        if any(not _ID.fullmatch(step_id) for step_id in directive_ids):
            failed = True
            continue  # Never salvage a valid prefix from an invalid ID list.
        for step_id in directive_ids:
            if step_id not in ids:
                ids.append(step_id)
        if len(ids) > _MAX_IDS:
            return _hold(False)
    if not ids:
        return _hold(False)
    try:
        source = bootstrap()
        home = _home()  # Per invocation: gateway profile context may change.
        root = _root(source, home, context.get("session_id"))
        ledger = source.Ledger(home / "user-steps", home / "state.db")
        lifecycle = _lifecycle()
        store = lifecycle.Store(os.environ.get("ASK_GATE_LIFECYCLE_DB") or home / "state" / "ask-lifecycle.sqlite")
        # Encode tuple components losslessly; even shared override DBs keep profiles separate.
        authority = json.dumps(["user-steps-exact-v1", str(home)], separators=(',', ':'))
    except Exception:
        return _hold(False)
    retired = False
    for step_id in ids:
        try:
            snapshot = ledger.read_exact(root, step_id)
            status = snapshot['step'].get('status')
            if status not in ("open", "resolved"):
                raise ValueError("Unknown source state")
            identity = lifecycle._identity(dict(authority=authority, kind="user_step",
                subject=json.dumps([root, step_id], separators=(',', ':')), action="ask"))
            # Snapshot validation/revision ownership is provided by read_exact.
            # This private store method needs no invented verified provenance.
            state = store._observe(identity, dict(state="completed" if status == "resolved" else "active",
                revision=snapshot['revision'], explicit_reopen=False))
            retired = retired or state == "completed"
        except Exception:
            # Keep visiting IDs: an unknown sibling must not hide terminal evidence.
            failed = True
    return _hold(retired) if retired or failed else None
