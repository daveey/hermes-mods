"""Budget view: core's capacity model (``llm_pacing.json`` ``v2``) next to the spend the series measured, and what a
burst would cost.

* ``project(v2, factor, duration_s, now)``: per pool, what running at ``factor`` x the planned rate for
  ``duration_s`` from ``now`` costs (pure; see its docstring).
* ``history(series_db, now)``: the fleet's actual in+out tokens per provider, hourly, over the last 7 days, from the
  series' per-model rows (``tokens_io:<model>``, every gateway) attributed with ``throttle.provider_of_model`` (the
  start gate's attribution). ``rate`` is tokens/s per bucket (None for a bucket no gateway sampled), ``cumulative``
  the running sum from the window's start through each bucket's end, so the chart can continue it with core's
  forward curve.
* ``chart_v2(v2)``: the parts of ``v2`` the chart draws, without account readings.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .throttle import _number, provider_name, provider_of_model

HISTORY_S = 7 * 86400
BUCKET_S = 3600
EVENT_TYPES = frozenset({"account_added", "account_removed", "reset", "reset_early"})
POOL_FIELDS = ("tpp_7d", "tpp_5h", "tpp_source", "remaining_tokens", "exhaust_plan_at", "rate_tokens_per_s",
               "rate_cap_5h", "at_reserve", "until", "ratio", "read_at")


def _pools(v2) -> Optional[dict]:
    """``{provider: entry}`` of a well-formed ``v2`` (a finite ``computed_at`` and a ``pools`` map); None otherwise."""
    if not isinstance(v2, dict) or _number(v2.get("computed_at")) is None or not isinstance(v2.get("pools"), dict):
        return None
    return {p: e for p, e in v2["pools"].items() if provider_name(p) and isinstance(e, dict)}


def _nulls() -> dict:
    return {"rate_now": None, "extra_tokens": None, "exhausts_during_burst_at": None, "earlier_s": None,
            "rate_after": None, "rate_cap_5h": None, "exceeds_5h_cap": None}


def project(v2, factor: float, duration_s: float, now: float) -> dict:
    """``{pool: {rate_now, extra_tokens, exhausts_during_burst_at, earlier_s, rate_after, rate_cap_5h,
    exceeds_5h_cap}}`` for a burst of ``factor`` x the planned rate (``rate_tokens_per_s``) for ``duration_s`` starting
    ``now``:

    * ``extra_tokens``: the burst's incremental spend over the plan's in the same time, ``min(remaining, factor x
      rate_now x duration_s) - min(remaining, rate_now x duration_s)``.
    * ``exhausts_during_burst_at``: when the remaining tokens run out at the burst rate, if before the burst ends.
    * ``earlier_s``: how much earlier the pool runs out if spend returns to the old plan afterwards without
      re-planning: ``extra_tokens / rate_now``, at most the time left to ``exhaust_plan_at``. When the pool runs out
      during the burst it is exactly how much sooner that is than the plan's end, ``exhaust_plan_at -
      exhausts_during_burst_at`` (``now + remaining / rate_now`` standing in for an unknown ``exhaust_plan_at``).
    * ``rate_after``: the rate the planner slows to after the burst, ``rate_now x (remaining - burst spend) /
      (remaining - plan spend over the same span)``: today's rate scaled by what the burst leaves against what the
      plan would leave, so never above ``rate_now`` (0 when the burst or the plan uses everything up).
    * ``exceeds_5h_cap``: whether the burst rate is above the pool's ``rate_cap_5h`` (core: the summed rate at which
      each account under reserve would spend its 5-hour window down to its reserve by that window's reset), i.e. a
      sustained burst would push accounts into their 5-hour reserve; None when core has no 5-hour cap for the pool.

    A pool at reserve: zeros (no exhaustion time, ``exceeds_5h_cap`` false). A pool whose rate or remaining is
    unknown: nulls. A missing or malformed ``v2``: ``{}``."""
    pools = _pools(v2)
    if pools is None:
        return {}
    end = now + duration_s
    out = {}
    for provider, entry in sorted(pools.items()):
        if entry.get("at_reserve") is True:
            out[provider] = {"rate_now": 0.0, "extra_tokens": 0.0, "exhausts_during_burst_at": None,
                             "earlier_s": 0.0, "rate_after": 0.0, "rate_cap_5h": _number(entry.get("rate_cap_5h")),
                             "exceeds_5h_cap": False}
            continue
        rate, remaining = _number(entry.get("rate_tokens_per_s")), _number(entry.get("remaining_tokens"))
        if rate is None or remaining is None or rate < 0:
            out[provider] = _nulls()
            continue
        remaining = max(0.0, remaining)
        exhaust_at = _number(entry.get("exhaust_plan_at"))
        burst_rate = factor * rate
        burst_spend = min(remaining, burst_rate * duration_s)
        extra = burst_spend - min(remaining, rate * duration_s)
        runs_out = now + remaining / burst_rate if burst_rate > 0 and remaining / burst_rate < duration_s else None
        earlier = extra / rate if rate > 0 else 0.0
        if exhaust_at is not None:
            earlier = min(earlier, max(0.0, exhaust_at - now))
        if runs_out is not None:  # rate > 0 here
            plan_end = exhaust_at if exhaust_at is not None else now + remaining / rate
            earlier = max(0.0, plan_end - runs_out)
        cap5 = _number(entry.get("rate_cap_5h"))
        # The plan's rate already folds in the 5-hour caps and per-account margins, so re-deriving it from
        # remaining / time-left can come out above today's rate. Scale today's rate instead by how much the burst
        # leaves compared with what the plan alone would leave after the same span: never above rate_now.
        plan_left = remaining - min(remaining, rate * duration_s)
        if exhaust_at is not None and exhaust_at <= end:
            after = 0.0
        else:
            after = rate * (remaining - burst_spend) / plan_left if plan_left > 0 else 0.0
        out[provider] = {"rate_now": rate, "extra_tokens": extra, "exhausts_during_burst_at": runs_out,
                         "earlier_s": earlier, "rate_after": after, "rate_cap_5h": cap5,
                         "exceeds_5h_cap": None if cap5 is None else burst_rate > cap5}
    return out


def chart_v2(v2) -> dict:
    """``{computed_at, margin_s, global_rate_tokens_per_s, pools, events, curve}`` from ``v2`` for the chart: pools
    without their account readings, events of the known types only, curves as ``[[t, capacity, spend]]`` with
    malformed points dropped. Empty fields for a missing or malformed ``v2``."""
    pools = _pools(v2)
    if pools is None:
        return {"computed_at": None, "margin_s": None, "global_rate_tokens_per_s": None, "pools": {}, "events": [],
                "curve": {}}
    events = []
    for e in v2.get("events") if isinstance(v2.get("events"), list) else []:
        if isinstance(e, dict) and e.get("type") in EVENT_TYPES and _number(e.get("ts")) is not None \
                and provider_name(e.get("provider")):
            detail = e.get("detail") if isinstance(e.get("detail"), dict) else {}
            events.append({"ts": float(e["ts"]), "provider": e["provider"], "type": e["type"],
                           "kind": detail.get("kind") if isinstance(detail.get("kind"), str) else None})
    curve = {}
    raw = v2.get("curve") if isinstance(v2.get("curve"), dict) else {}
    for provider, points in raw.items():
        if provider not in pools or not isinstance(points, list):
            continue
        good = [[float(t), float(c), float(s)] for t, c, s in
                (pt for pt in points if isinstance(pt, list) and len(pt) == 3)
                if _number(t) is not None and _number(c) is not None and _number(s) is not None]
        curve[provider] = sorted(good)[:64]
    return {"computed_at": float(v2["computed_at"]), "margin_s": _number(v2.get("margin_s")),
            "global_rate_tokens_per_s": _number(v2.get("global_rate_tokens_per_s")),
            "pools": {p: {k: e.get(k) for k in POOL_FIELDS} | {"accounts": len(e.get("accounts") or [])}
                      for p, e in sorted(pools.items())},
            "events": events[-50:], "curve": curve}


def history(series_db: Path, now: float, span_s: int = HISTORY_S) -> dict:
    """``{bucket_s, since, until, t, providers: {provider: {rate, cumulative}}}``: hourly buckets starting at ``t``
    (the last one ends at ``until``, the current minute). ``rate`` per bucket is tokens/s over the bucket's length,
    None where no gateway sampled a minute in it; ``cumulative`` is tokens from ``since`` through the bucket's end
    (unsampled buckets add 0). ``unreported`` and ``other`` tokens belong to no provider. Read-only; one query that
    walks the primary key per gateway, so it costs one 7-day slice of the token rows."""
    until = int(now) // 60 * 60
    since = (until - span_s) // BUCKET_S * BUCKET_S
    starts = list(range(since, until, BUCKET_S))
    out = {"bucket_s": BUCKET_S, "since": since, "until": until, "t": starts, "providers": {}}
    if not starts or not Path(series_db).is_file():
        return out
    from .sampler import ro_connect
    try:
        c = ro_connect(series_db)
    except sqlite3.Error:
        return out
    try:
        rows = c.execute(
            "SELECT (minute - ?) / ?, substr(metric, 11), SUM(value) FROM minutes "
            "WHERE gateway IN (SELECT DISTINCT gateway FROM minutes) AND metric >= 'tokens_io:' AND metric < "
            "'tokens_io;' AND minute >= ? AND minute < ? AND value IS NOT NULL GROUP BY 1, 2",
            (since, BUCKET_S, since, until)).fetchall()
    except sqlite3.Error:
        return out
    finally:
        c.close()
    sampled = [False] * len(starts)
    totals: dict[str, list[float]] = {}
    providers: dict[str, str] = {}
    for index, model, value in rows:
        i = int(index)
        if not 0 <= i < len(starts):
            continue
        sampled[i] = True  # ``tokens_io:unreported`` is written for every sampled minute
        if model in ("unreported", "other"):
            continue
        if model not in providers:
            providers[model] = provider_of_model(model)
        totals.setdefault(providers[model], [0.0] * len(starts))[i] += float(value or 0.0)
    for provider, tokens in sorted(totals.items()):
        rate, cumulative, running = [], [], 0.0
        for i, start in enumerate(starts):
            length = min(BUCKET_S, until - start)
            rate.append(tokens[i] / length if sampled[i] else None)
            running += tokens[i]
            cumulative.append(running)
        out["providers"][provider] = {"rate": rate, "cumulative": cumulative}
    return out
