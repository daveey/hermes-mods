"""Weekly-quota forecast per subscription pool: how much is left and whether it lasts to the next resets.

Stdlib only; no usage request is made here. Inputs:

* the accounts the Desktop panel already holds (``model.options`` ``usage.accounts``), posted to ``POST /forecast``;
* a usage history: this plugin's own ``<home>/state/account-usage-history.jsonl`` (one weekly reading per account,
  at most every ``RECORD_EVERY_S`` per provider, ``KEEP_S`` retained) plus, read-only when present, core's Kanban
  pacing store ``<home>/llm_pacing_history.sqlite`` (same quantity, same account hash, so the two simply merge).

Model (weekly windows only; the 5-hour session window is ignored):

* ``left``: sum over ready accounts of ``100 - weekly used`` (in %-points; 100 = one untouched account-week).
* ``burn``: %-points/hour spent across the pool over the last ``RATE_WINDOW_S``: per account, the sum of increases
  between consecutive readings of the same weekly window (a reset starts a new window from 0). Fewer than
  ``MIN_SPAN_S`` of history -> ``learning`` (no forecast is guessed).
* projection, hourly for ``HORIZON_H``: fuel starts at ``left``, loses ``burn`` each hour, and gains each account
  back at its reset (a spent account +100 at its ``resets_at``; a ready one its used share at its weekly reset).
* ``verdict``: ``short`` when fuel reaches 0 inside the horizon (``gaps`` say when), ``tight`` when the burn is over
  90% of what the week refills or fuel dips under ``TIGHT_MARGIN_H`` hours of burn before a reset, else ``ok``.

Account labels (e-mail addresses) are hashed on arrival and never stored or returned.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

HORIZON_H = 168
RATE_WINDOW_S = 24 * 3600
MIN_SPAN_S = 2 * 3600
RECORD_EVERY_S = 300
KEEP_S = 8 * 24 * 3600
SAME_RESET_S = 600
TIGHT_PACE = 0.9
TIGHT_MARGIN_H = 3
FILE_NAME = "account-usage-history.jsonl"
PACING_DB = "llm_pacing_history.sqlite"
WEEK_KIND = "seven_day"

_lock = threading.Lock()
_last_record: dict[tuple[str, str], float] = {}
_last_prune: dict[str, float] = {}


def account_hash(label: Any) -> str:
    """Same id core's pacing history uses (``kanban_llm_pacing.account_id``), so both histories merge."""
    return hashlib.sha256(str(label).encode("utf-8")).hexdigest()[:16]


def _ts(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalize(accounts: Iterable[dict]) -> list[dict]:
    """Wire accounts -> ``{h, state, used, week_reset, back}``; labels are dropped here."""
    out = []
    for a in accounts or ():
        if not isinstance(a, dict) or a.get("disabled"):
            continue
        used = a.get("week_used")
        out.append({
            "h": account_hash(a.get("label") or a.get("id") or ""),
            "state": str(a.get("state") or "unknown"),
            "used": None if used is None else max(0.0, min(100.0, float(used))),
            "week_reset": _ts(a.get("week_resets_at")),
            "back": _ts(a.get("resets_at")),
        })
    return out


# ── history ──────────────────────────────────────────────────────────────────

def history_path(home: Path) -> Path:
    return Path(home) / "state" / FILE_NAME


def record(home: Path, provider: str, accounts: list[dict], now: float) -> bool:
    """Append one weekly reading per account (throttled per provider). Returns whether it wrote."""
    rows = [a for a in accounts if a["used"] is not None]
    if not rows:
        return False
    path = history_path(home)
    key = (str(path), provider)
    with _lock:
        if now - _last_record.get(key, 0.0) < RECORD_EVERY_S:
            return False
        _last_record[key] = now
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            for a in rows:
                fh.write(json.dumps({"ts": round(now, 1), "p": provider, "a": a["h"], "u": a["used"],
                                     "r": a["week_reset"]}) + "\n")
        if now - _last_prune.get(str(path), 0.0) > 3600:
            _last_prune[str(path)] = now
            _prune(path, now)
    return True


def _prune(path: Path, now: float) -> None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    keep = []
    for line in lines:
        try:
            if now - float(json.loads(line)["ts"]) <= KEEP_S:
                keep.append(line)
        except (ValueError, KeyError, TypeError):
            continue
    if len(keep) != len(lines):
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text("".join(f"{l}\n" for l in keep))
        os.replace(tmp, path)


def read_samples(home: Path, provider: str, since: float) -> list[tuple[float, str, float, Optional[float]]]:
    """``(ts, account_hash, used, reset_at)`` weekly readings since ``since``, from both histories."""
    out = []
    try:
        with open(history_path(home)) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    if r["p"] == provider and float(r["ts"]) >= since:
                        out.append((float(r["ts"]), r["a"], float(r["u"]), r.get("r")))
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    db = Path(home) / PACING_DB
    if db.exists():
        try:
            uri = f"file:{db}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as conn:  # noqa: kanban-lockguard (read-only history)
                out += [(float(t), a, float(u), r) for t, a, u, r in conn.execute(
                    "SELECT ts, account_hash, used, reset_at FROM samples WHERE provider=? AND kind=? AND ts>=?",
                    (provider, WEEK_KIND, since))]
        except sqlite3.Error:
            pass
    return out


# ── model ────────────────────────────────────────────────────────────────────

def burn_rate(samples: list[tuple], now: float) -> tuple[Optional[float], float]:
    """(%-points per hour across the pool, observed span in seconds); rate None below ``MIN_SPAN_S``."""
    if not samples:
        return None, 0.0
    span = now - min(s[0] for s in samples)
    if span < MIN_SPAN_S:
        return None, span
    spent = 0.0
    by_acct: dict[str, list] = {}
    for s in samples:
        by_acct.setdefault(s[1], []).append(s)
    for series in by_acct.values():
        series.sort(key=lambda s: s[0])
        for (_, _, u0, r0), (_, _, u1, r1) in zip(series, series[1:]):
            same = (r0 is None and r1 is None) or (r0 is not None and r1 is not None and abs(r1 - r0) < SAME_RESET_S)
            if same:
                spent += max(0.0, u1 - u0)
            elif r0 is not None and r1 is not None and r1 > r0:
                spent += u1  # a new weekly window started at 0
    return spent / (span / 3600.0), span


def project(accounts: list[dict], burn: float, now: float, horizon_h: int = HORIZON_H) -> dict:
    """Hourly fuel curve from now; refills at each account's reset."""
    ready = [a for a in accounts if a["state"] == "ready"]
    fuel = sum(100.0 - (a["used"] or 0.0) for a in ready)
    start = fuel
    events = []  # (hour, add)
    for a in accounts:
        if a["state"] == "ready":
            if a["week_reset"] is not None and a["used"]:
                events.append(((a["week_reset"] - now) / 3600.0, a["used"]))
        elif a["back"] is not None:
            wk, used = a["week_reset"], a["used"]
            if used is not None and used < 100 and wk is not None and a["back"] < wk - SAME_RESET_S:
                # Out on its 5-hour window only: back with its weekly remainder, the rest at the weekly reset.
                events += [((a["back"] - now) / 3600.0, 100.0 - used), ((wk - now) / 3600.0, used)]
            else:
                events.append(((a["back"] - now) / 3600.0, 100.0))
    events = sorted(e for e in events if e[0] <= horizon_h)
    curve, gaps, gap, ei = [], [], None, 0
    for h in range(horizon_h + 1):
        while ei < len(events) and events[ei][0] <= h:
            fuel += events[ei][1]
            ei += 1
        curve.append(round(fuel, 1))
        if fuel <= 0.01 and burn > 0:
            gap = gap or {"start_h": h}
        elif gap:
            gaps.append({**gap, "end_h": h})
            gap = None
        fuel = max(0.0, fuel - burn)
    if gap:
        gaps.append({**gap, "end_h": horizon_h})
    refills = sum(add for _, add in events)
    supply = start + refills
    pace = (burn * horizon_h / supply) if supply > 0 else (math.inf if burn > 0 else 0.0)
    return {"left": round(start, 1), "curve": curve, "gaps": gaps, "pace": None if math.isinf(pace) else round(pace, 3),
            "resets_h": [round(h, 2) for h, _ in events]}


def forecast(home: Path, provider: str, accounts: list[dict], now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    norm = normalize(accounts)
    record(home, provider, norm, now)
    burn, span = burn_rate(read_samples(home, provider, now - RATE_WINDOW_S), now)
    if burn is None:
        p = project(norm, 0.0, now)
        return {"verdict": "learning", "left": p["left"], "burn_per_h": None, "history_h": round(span / 3600, 2),
                "curve": p["curve"], "gaps": [], "runout_h": None, "pace": None, "resets_h": p["resets_h"]}
    p = project(norm, burn, now)
    # tight: over 90% of the week's supply, or the curve comes within TIGHT_MARGIN_H hours of empty before a reset.
    near = burn > 0 and min(p["curve"]) < burn * TIGHT_MARGIN_H
    verdict = "short" if p["gaps"] else ("tight" if near or (p["pace"] or 0) > TIGHT_PACE else "ok")
    return {"verdict": verdict, "left": p["left"], "burn_per_h": round(burn, 3), "history_h": round(span / 3600, 2),
            "curve": p["curve"], "gaps": p["gaps"], "runout_h": p["gaps"][0]["start_h"] if p["gaps"] else None,
            "pace": p["pace"], "resets_h": p["resets_h"]}
