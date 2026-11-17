"""Runway: how long each subscription pool's weekly allowance lasts at its current burn.

Source: the usage samples core's Kanban pacing governor stores in ``<kanban home>/llm_pacing.json``
(``{provider: {"read_at": ts, "samples": [[ts, account, kind, used_percent, reset_at], ...]}}``). This module only
reads that file; it makes no usage request. A host whose dispatcher does not run the governor has no file and
reports no pools, and so does a provider whose newest sample is over 30 minutes old.

Per provider, over the weekly (``seven_day``) windows of its accounts:

* ``remaining_pct``: sum of ``100 - used`` (200 = two untouched account-weeks).
* ``burn_pct_per_h``: sum of each account's slope between its newest sample and its oldest one 10-60 minutes
  earlier in the same window. An account without such a pair counts 0, so a pool that has just been read for the
  first time shows no burn rather than a guess.
* ``runway_s``: ``remaining / burn``; None when nothing is burning. It ignores resets: compare it with
  ``next_reset_at`` (the earliest weekly reset of any account) to see which comes first.

* ``pacing``: the governor's own verdict for the pool (``open``, ``paced``, ``held``; None when it stored none).
  ``paced`` and ``held`` mean Kanban is starting fewer (or no) cards on that pool whatever the throttle says, so
  the panel can show why the fleet is idle with the throttle off. ``pacing_until`` is a hold's known end.

Account names never leave this module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

WEEKLY = "seven_day"
FRESH_S = 1200  # a provider's "current" readings are those within 20 minutes of its newest sample
STALE_S = 1800  # a provider whose newest sample is older than this (the governor stopped reading) is not reported
RATE_WINDOW_S = 3600
MIN_SPAN_S = 600
SAME_RESET_S = 600
EXHAUSTED_PCT = 99.0
PACING_STATES = ("open", "paced", "held")


def pacing_path(root: Path) -> Path:
    try:
        from hermes_cli import kanban_db
        return Path(kanban_db.kanban_home()) / "llm_pacing.json"
    except Exception:  # outside a Hermes runtime, or an older core
        return Path(root) / "llm_pacing.json"


def _rows(entry) -> list[tuple[float, str, str, float, Optional[float]]]:
    out = []
    for row in (entry.get("samples") if isinstance(entry, dict) else None) or []:
        try:
            ts, account, kind, used, reset_at = row
            out.append((float(ts), str(account), str(kind), float(used), None if reset_at is None else float(reset_at)))
        except (TypeError, ValueError):
            continue
    return out


def _burn(latest, history) -> float:
    """Percent per second: the slope from the oldest same-window sample 10-60 minutes before ``latest``."""
    ts, account, kind, used, reset_at = latest
    same = [h for h in history
            if h[1] == account and h[2] == kind and ts - RATE_WINDOW_S <= h[0] <= ts - MIN_SPAN_S
            and (h[4] is None) == (reset_at is None) and abs((h[4] or 0) - (reset_at or 0)) <= SAME_RESET_S]
    if not same:
        return 0.0
    oldest = min(same, key=lambda h: h[0])
    return max(0.0, (used - oldest[3]) / (ts - oldest[0]))


def _pacing(entry) -> dict:
    pace = entry.get("pace") if isinstance(entry, dict) else None
    state = pace.get("state") if isinstance(pace, dict) else None
    until = pace.get("until") if isinstance(pace, dict) else None
    known = isinstance(until, (int, float)) and not isinstance(until, bool)
    return {"pacing": state if state in PACING_STATES else None, "pacing_until": float(until) if known else None}


def provider_runway(provider: str, entry, now: float) -> Optional[dict]:
    rows = [r for r in _rows(entry) if r[2] == WEEKLY]
    if not rows:
        return None
    newest = max(r[0] for r in rows)
    if now - newest > STALE_S:
        return None  # an old burn rate is not "this rate"
    latest: dict[str, tuple] = {}
    for r in rows:
        if newest - r[0] <= FRESH_S and (r[1] not in latest or r[0] > latest[r[1]][0]):
            latest[r[1]] = r
    remaining = sum(max(0.0, 100.0 - r[3]) for r in latest.values())
    burn = sum(_burn(r, rows) for r in latest.values())
    resets = [r[4] for r in latest.values() if r[4] is not None and r[4] > now]
    return {
        "provider": provider,
        "accounts": len(latest),
        "exhausted": sum(1 for r in latest.values() if r[3] >= EXHAUSTED_PCT),
        "remaining_pct": round(remaining, 2),
        "burn_pct_per_h": round(burn * 3600, 4),
        "runway_s": round(remaining / burn) if burn > 0 else None,
        "next_reset_at": min(resets) if resets else None,
        "read_at": newest,
        **_pacing(entry),
    }


def pools(path: Path, now: float) -> list[dict]:
    """One entry per provider with weekly samples, sorted by name; ``[]`` when the file is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out = [provider_runway(str(name), entry, now) for name, entry in sorted(data.items())]
    return [p for p in out if p is not None]
