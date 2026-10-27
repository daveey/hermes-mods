"""Trusted approval-store adapters; suppress stale asks, NEVER authorize execution.

Only installed Python integrations register adapters. Tool arguments, transcripts,
model verdicts and other-agent messages are not approval evidence. Adapters resolve
the current exact target from their own authority, then read its current store.
No cache: every attempted escalation re-reads the store. No decision writes here.
"""
from __future__ import annotations

import copy
import math
import threading
import time
from typing import Callable

_LOCK = threading.RLock()
_ADAPTERS: dict[str, tuple[Callable, Callable]] = {}
_TARGET_KEYS = {"authority", "subject_kind", "subject", "revision"}
_MAX_AGE = 30


def register(name: str, resolve: Callable, read: Callable) -> Callable:
    """Register trusted code and return its unload disposer.

    resolve(context) -> None (unmanaged) or exact target mapping. It MUST obtain
    current scope/revision from authority, not trust a head claimed in the ask.
    Selection must classify the actual requested action, not trust interaction
    labels: an approval labeled 'reply' still needs reconciliation. Genuine OS
    authentication/physical actions are distinct and must resolve to None.
    read(target) -> {target, state, verified: bool, observed_at: epoch seconds}.
    verified means source authentication and proof checks succeeded, including
    negative/pending reads. new_scope means an authoritative no-matching-record
    result, NOT an old or mismatched record. Readers must be bounded/read-only.
    No user identities or raw decision evidence should be returned to this module.
    """
    if not isinstance(name, str) or not name.strip() or not callable(resolve) or not callable(read):
        raise ValueError("Invalid approval adapter")
    pair = (resolve, read)
    with _LOCK:
        if name in _ADAPTERS:
            raise ValueError("Approval adapter already registered")
        _ADAPTERS[name] = pair

    def dispose():
        with _LOCK:
            if _ADAPTERS.get(name) is pair:
                del _ADAPTERS[name]
    return dispose


def _target(value):
    if not isinstance(value, dict) or set(value) != _TARGET_KEYS:
        raise ValueError("Incomplete approval target")
    if not all(isinstance(v, str) and v.strip() and len(v) <= 1000 for v in value.values()):
        raise ValueError("Invalid approval target")
    return dict(value)


def _hold(code: str) -> dict:
    messages = {
        "approved": "This exact scope is already approved. Reuse the recorded approval through the supported execution path; do not ask again.",
        "denied": "This exact scope is denied. Do not execute or turn the denial into a repeat approval prompt.",
        "recovery": "Approval state could not be reconciled. Keep this with agent-owned operational recovery, not a new human approval ask.",
    }
    return {"action": "block", "message": "ask-gate: " + messages[code]
            + " This is not execution authorization, does not complete work, and does not satisfy OS authentication."}


def preflight(context: dict) -> dict | None:
    """Return a blocking directive for an invalid/repeated approval escalation.

    None means only 'continue normal ask validation'; it never approves execution.
    Unmanaged requests retain the existing gate. Deployments must install an
    adapter for each managed approval domain; no inference from chat is used.
    """
    with _LOCK:
        adapters = tuple(_ADAPTERS.values())
    decisions = []
    try:
        for resolve, read in adapters:
            target = resolve(copy.deepcopy(context))
            if target is None:
                continue
            target = _target(target)
            result = read(dict(target))
            if not isinstance(result, dict) or result.get("verified") is not True:
                return _hold("recovery")
            stamp = result.get("observed_at")
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                return _hold("recovery")
            if not math.isfinite(stamp) or not 0 <= time.time() - stamp <= _MAX_AGE:
                return _hold("recovery")
            if _target(result.get("target")) != target:
                return _hold("recovery")
            state = result.get("state")
            if state not in {"approved", "vetoed", "pending", "new_scope"}:
                return _hold("recovery")
            decisions.append(state)
    except Exception:
        # Do not leak adapter errors (which may include private store identifiers).
        return _hold("recovery")
    if "vetoed" in decisions:
        return _hold("denied")
    if "approved" in decisions:
        # Conflicting authorities are not resolved by choosing the permissive one.
        return _hold("approved" if all(s == "approved" for s in decisions) else "recovery")
    return None
