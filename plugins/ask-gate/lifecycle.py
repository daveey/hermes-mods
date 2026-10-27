"""Durable lifecycle suppression, never execution authorization.

Only trusted installed Python integrations may register adapters. This module is
not a model tool: do not expose registration or state writes through tool args.
No chat/text classification, fuzzy identity matching, or timestamp ordering.
"""
from __future__ import annotations

import copy
from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Callable, Literal, TypedDict, cast


class Identity(TypedDict):
    authority: str
    kind: Literal["account", "subscription", "project", "user_step", "native_action"]
    subject: str
    action: str


class Record(TypedDict):
    target: Identity
    state: Literal["active", "revoked", "cancelled", "completed", "impossible"]
    revision: int
    verified: bool
    observed_at: float
    explicit_reopen: bool


_KEYS = ("authority", "kind", "subject", "action")
_KINDS = {"account", "subscription", "project", "user_step", "native_action"}
_TERMINAL = {"revoked", "cancelled", "completed", "impossible"}
_LOCK = threading.RLock()
_ADAPTERS: dict[str, tuple[Callable, Callable]] = {}
_MAX_AGE = 30


def _identity(value: object) -> Identity:
    if not isinstance(value, dict) or set(value) != set(_KEYS):
        raise ValueError("Incomplete lifecycle identity")
    if not all(isinstance(v, str) and v.strip() and len(v) <= 1000 for v in value.values()):
        raise ValueError("Invalid lifecycle identity")
    if value["kind"] not in _KINDS:
        raise ValueError("Invalid lifecycle kind")
    return cast(Identity, dict(value))  # Never trim, case-fold, or normalize.


def register(authority: str, resolve: Callable, read: Callable) -> Callable:
    """Install trusted code for one exact authority; return an unload disposer.

    resolve(context) -> None (unmanaged) or {authority, kind, subject, action}.
    Resolve from authoritative workflow scope, not model-provided identity claims.
    read(identity) -> Record, freshly authenticated and verified for that scope.
    Readers must be bounded and read-only. revision is an authority-owned strictly
    increasing integer per identity (not a timestamp). Each changed record,
    including an explicit reopen event, requires a new revision. observed_at is
    the fresh read time, NOT the event time. explicit_reopen=True on active must
    prove a deliberate trusted reopen; generic 'active' snapshots cannot reopen.
    Neither verified nor explicit_reopen may be copied from tool/model arguments.
    """
    if not isinstance(authority, str) or not authority.strip() or len(authority) > 1000:
        raise ValueError("Invalid lifecycle authority")
    if not callable(resolve) or not callable(read):
        raise ValueError("Invalid lifecycle adapter")
    pair = (resolve, read)
    with _LOCK:
        if authority in _ADAPTERS:
            raise ValueError("Lifecycle adapter already registered")
        _ADAPTERS[authority] = pair

    def dispose():
        with _LOCK:
            if _ADAPTERS.get(authority) is pair:
                del _ADAPTERS[authority]
    return dispose


class Store:
    """SQLite current-state registry. Writes are internal to trusted preflight.

    ASK_GATE_LIFECYCLE_DB overrides the default HERMES_HOME/state location.
    The database retains terminal suppression indefinitely. No expiry or recency
    truncation applies. BEGIN IMMEDIATE serializes revision checks across workers.
    """

    def __init__(self, path: str | Path | None = None):
        try:
            from hermes_constants import get_hermes_home
        except ImportError:  # Standalone plugin tests/reporting.
            home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        else:
            home = get_hermes_home()
        self.path = Path(path or os.environ.get("ASK_GATE_LIFECYCLE_DB")
                         or home / "state" / "ask-lifecycle.sqlite").expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS lifecycle (
                authority TEXT NOT NULL, kind TEXT NOT NULL,
                subject TEXT NOT NULL, action TEXT NOT NULL,
                state TEXT NOT NULL, revision INTEGER NOT NULL,
                observed_state TEXT NOT NULL, explicit_reopen INTEGER NOT NULL,
                PRIMARY KEY (authority, kind, subject, action))""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def get(self, identity: Identity) -> dict | None:
        identity = _identity(identity)
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM lifecycle WHERE authority=? AND kind=? AND subject=? AND action=?",
                             tuple(identity[k] for k in _KEYS)).fetchone()
        return dict(row) if row is not None else None

    def _observe(self, identity: Identity, record: Record) -> str:
        """Already validated adapter evidence only; no public model-write API."""
        key = tuple(identity[k] for k in _KEYS)
        state, revision = record["state"], record["revision"]
        reopen = record.get("explicit_reopen", False)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM lifecycle WHERE authority=? AND kind=? AND subject=? AND action=?", key).fetchone()
            if old is not None:
                if revision < old["revision"]:
                    raise ValueError("Stale lifecycle revision")
                if revision == old["revision"]:
                    if state != old["observed_state"] or reopen != bool(old["explicit_reopen"]):
                        raise ValueError("Conflicting lifecycle revision")
                    return old["state"]
                if old["state"] in _TERMINAL and state == "active" and not reopen:
                    state = old["state"]
            db.execute("""INSERT INTO lifecycle VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(authority,kind,subject,action) DO UPDATE SET
                state=excluded.state, revision=excluded.revision,
                observed_state=excluded.observed_state,
                explicit_reopen=excluded.explicit_reopen""",
                       key + (state, revision, record["state"], int(reopen)))
        return state


def _record(value: object, identity: Identity) -> Record:
    if not isinstance(value, dict) or value.get("verified") is not True:
        raise ValueError("Unverified lifecycle evidence")
    if _identity(value.get("target")) != identity:
        raise ValueError("Mismatched lifecycle scope")
    stamp = value.get("observed_at")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        raise ValueError("Invalid lifecycle observation")
    if not math.isfinite(stamp) or not 0 <= time.time() - stamp <= _MAX_AGE:
        raise ValueError("Stale lifecycle observation")
    revision = value.get("revision")
    if type(revision) is not int or not 0 <= revision <= 9223372036854775807:
        raise ValueError("Invalid lifecycle revision")
    state = value.get("state")
    if not isinstance(state, str) or state not in _TERMINAL | {"active"}:
        raise ValueError("Invalid lifecycle state")
    reopen = value.get("explicit_reopen", False)
    if type(reopen) is not bool or (reopen and state != "active"):
        raise ValueError("Invalid lifecycle reopen")
    return cast(Record, dict(value))


def _hold(retired: bool) -> dict:
    reason = ("This exact action is retired (revoked, cancelled, completed, or impossible). Do not ask again."
              if retired else "Lifecycle state could not be reconciled. Use agent-owned recovery, not a new human ask.")
    return {"action": "block", "message": "ask-gate: " + reason
            + " This is not execution authorization and does not satisfy OS authentication."}


def preflight(context: dict) -> dict | None:
    """Re-read each matching authority and persist its terminal state on every ask.

    Returns block or None (continue ordinary validation, NEVER permission). A
    trusted explicit reopen releases only this suppression. Failed/stale reads
    block with operational recovery and cannot clear durable retirement. All
    adapters are visited even if another fails, so terminal evidence is retained.
    """
    with _LOCK:
        adapters = tuple(_ADAPTERS.items())
    failed = retired = False
    for authority, (resolve, read) in adapters:
        try:
            identity = resolve(copy.deepcopy(context))
            if identity is None:
                continue
            identity = _identity(identity)
            if identity["authority"] != authority:
                raise ValueError("Untrusted lifecycle authority")
            record = _record(read(dict(identity)), identity)
            state = Store()._observe(identity, record)
            retired = retired or state in _TERMINAL
        except Exception:
            # Never include private source identities or adapter exception text.
            failed = True
    return _hold(retired) if retired or failed else None
