"""The ``/v1/series`` payload: bucketed counts per metric and gateway.

``{now, since, bucket_s, range, local, gateways, series, recent, recent_minutes}``:
* ``series[metric][gateway]`` = counts per bucket over ``[since, now)`` (oldest first), or ``null`` when the
  gateway doesn't report that metric. Inside an array, ``null`` = no sample in that bucket (never 0).
* ``recent[metric][gateway]`` = the last ``recent_minutes`` per-minute counts, for the "rate now" numbers
  (the average over the last 5 min on 1h, 15 min otherwise) whatever the range's bucket size.
* ``sampled[metric][gateway]`` (sampled metrics only: tokens, sessions) = sampled minutes per bucket. A bucket
  sampled for 7 of its 60 minutes holds 7 minutes of counts; its rate is the sum over those 7 minutes.
Counts, not rates: the Desktop divides by the sampled seconds (cards: ``bucket_s``) and scales to /s, /m or /h.
"""
from __future__ import annotations

import time
from typing import Iterable, Mapping, Optional

from .cards import EPHEMERAL, UNATTRIBUTED, per_minute
from .store import Store, minute_of

RANGES = {"1h": (60, 60), "6h": (360, 300), "24h": (1440, 900), "7d": (10080, 3600)}  # minutes, bucket seconds
METRICS = ("tokens_io", "tokens_cache", "sess_in", "sess_out", "cards_in", "cards_out")
STORE_METRICS = METRICS[:4]
RECENT_MIN = 15
UNIT_S = {"s": 1, "m": 60, "h": 3600}


def bucket_counts(per_min: Mapping[int, Optional[float]], since: int, until: int, bucket_s: int,
                  missing_is_zero: bool) -> list[Optional[float]]:
    """Sum per-minute counts into buckets over ``[since, until)``. With ``missing_is_zero`` an absent minute is 0
    (cards: every event is in the replica); otherwise a bucket with no sampled minute is None."""
    n = (until - since) // bucket_s
    out: list[Optional[float]] = [0 if missing_is_zero else None] * n
    for m, v in per_min.items():
        if v is None or not since <= m < until:
            continue
        b = (m - since) // bucket_s
        out[b] = (out[b] or 0) + v
    return out


def rate(count: Optional[float], seconds: float, unit: str) -> Optional[float]:
    """A count over ``seconds`` as a rate per s/m/h (None stays None)."""
    if count is None:
        return None
    return count * UNIT_S[unit] / seconds


def order_gateways(names: Iterable[str]) -> list[str]:
    names = set(n for n in names if n)
    tail = [n for n in (EPHEMERAL, UNATTRIBUTED) if n in names]
    head = ["hub"] if "hub" in names else []
    rest = sorted(names - set(tail) - set(head))
    return head + rest + tail


def build(store: Store, card_events: dict, machines: Iterable[str], local: str, range_key: str,
          now: Optional[float] = None) -> dict:
    if range_key not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    minutes, bucket_s = RANGES[range_key]
    until = minute_of(time.time() if now is None else now)
    since = until - minutes * 60
    r_since = until - RECENT_MIN * 60

    reporting = store.reporters(until)
    stored, cover = store.bucketed_sampled(since, until, bucket_s)
    recent_stored = store.bucketed(r_since, until, 60)
    created = per_minute(card_events.get("created", []), min(since, r_since), until)
    done = per_minute(card_events.get("done", []), min(since, r_since), until)

    gateways = order_gateways([*machines, local, *(g for g, _ in reporting), *created, *done,
                               EPHEMERAL, UNATTRIBUTED])
    series: dict = {m: {} for m in METRICS}
    recent: dict = {m: {} for m in METRICS}
    sampled: dict = {m: {} for m in STORE_METRICS}
    nb = minutes * 60 // bucket_s
    for g in gateways:
        for m in STORE_METRICS:
            if (g, m) in reporting:
                series[m][g] = stored.get((g, m), [None] * nb)
                sampled[m][g] = cover.get((g, m), [0] * nb)
                recent[m][g] = recent_stored.get((g, m), [None] * RECENT_MIN)
            else:
                series[m][g] = recent[m][g] = sampled[m][g] = None
        for m, src in (("cards_in", created), ("cards_out", done)):
            series[m][g] = bucket_counts(src.get(g, {}), since, until, bucket_s, True)
            recent[m][g] = bucket_counts(src.get(g, {}), r_since, until, 60, True)
    return {"now": until, "since": since, "bucket_s": bucket_s, "range": range_key, "local": local,
            "gateways": gateways, "series": series, "sampled": sampled, "recent": recent,
            "recent_minutes": RECENT_MIN}
