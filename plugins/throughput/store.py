"""Per-minute throughput series: ``<hermes root>/cache/throughput/series.sqlite``.

One row per (gateway, metric, minute). ``value`` is the count in that minute; NULL means the minute was
not sampled (gateway offline, sampler down, a gap too long to spread). A gateway "reports" a metric when it
has any row for it inside the retention window; one that never reported has no rows, and the API returns
``null`` for its whole series rather than zeros.

Counter baselines (``counters``) survive a restart so a short sampler outage is spread over the minutes it
covered instead of being lost.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Optional

RETENTION_S = 8 * 24 * 3600
SPREAD_MAX_MIN = 5  # a gap of up to this many minutes is spread evenly; longer gaps are stored as missing

SCHEMA = """
CREATE TABLE IF NOT EXISTS minutes (
    gateway TEXT NOT NULL, metric TEXT NOT NULL, minute INTEGER NOT NULL, value REAL,
    PRIMARY KEY (gateway, metric, minute)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS minutes_by_time ON minutes(minute);
CREATE TABLE IF NOT EXISTS counters (
    gateway TEXT NOT NULL, metric TEXT NOT NULL, minute INTEGER NOT NULL, value REAL NOT NULL, at REAL,
    PRIMARY KEY (gateway, metric)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def minute_of(t: float) -> int:
    return int(t // 60) * 60


def spread(m_prev: int, m_now: int, delta: Optional[float]) -> list[tuple[int, Optional[float]]]:
    """Rows for the minutes ``[m_prev, m_now)`` that a delta measured between two samples covers.

    One minute: the whole delta. Up to SPREAD_MAX_MIN minutes: split evenly. Longer, or no delta: every
    minute is missing (None), never 0."""
    n = (m_now - m_prev) // 60
    if n <= 0:
        return []
    if delta is None or n > SPREAD_MAX_MIN:
        first = max(m_prev, m_now - RETENTION_S)  # never write gap rows older than the retention window
        return [(m, None) for m in range(first, m_now, 60)]
    return [(m_prev + 60 * i, delta / n) for i in range(n)]


def counter_delta(prev: float, cur: float) -> float:
    """A monotonic counter's increase; a decrease means the counter was reset (or rows were deleted): 0."""
    d = cur - prev
    return d if d > 0 else 0.0


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # --- writes -------------------------------------------------------------------------------------
    def put(self, gateway: str, metric: str, rows: Iterable[tuple[int, Optional[float]]], *, replace: bool = True):
        """Write minute rows. ``replace=False`` keeps an existing non-NULL value (a gap fill never erases data)."""
        rows = list(rows)
        if not rows:
            return
        sql = ("INSERT INTO minutes(gateway, metric, minute, value) VALUES (?,?,?,?) "
               "ON CONFLICT(gateway, metric, minute) DO UPDATE SET value=excluded.value"
               + ("" if replace else " WHERE minutes.value IS NULL"))
        with self._lock, self._conn() as c:
            c.executemany(sql, [(gateway, metric, int(m), v) for m, v in rows])

    def record_delta(self, gateway: str, metric: str, m_prev: int, m_now: int, delta: Optional[float]):
        rows = spread(m_prev, m_now, delta)
        self.put(gateway, metric, [r for r in rows if r[1] is not None])
        self.put(gateway, metric, [r for r in rows if r[1] is None], replace=False)

    def observe_counter(self, gateway: str, metric: str, minute: int, value: float, at: Optional[float] = None):
        """Feed one cumulative counter reading. The first reading is only a baseline (no row). A reading whose
        report time ``at`` equals the baseline's is stale (the machine stopped reporting): nothing is written now,
        and the gap logic decides on the next fresh reading."""
        with self._lock, self._conn() as c:
            row = c.execute("SELECT minute, value, at FROM counters WHERE gateway=? AND metric=?",
                            (gateway, metric)).fetchone()
        if row is not None and at is not None and row[2] is not None and float(at) == float(row[2]):
            return
        if row is not None and minute <= row[0]:
            return
        if row is not None:
            self.record_delta(gateway, metric, int(row[0]), int(minute), counter_delta(float(row[1]), float(value)))
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO counters(gateway, metric, minute, value, at) VALUES (?,?,?,?,?) "
                      "ON CONFLICT(gateway, metric) DO UPDATE SET minute=excluded.minute, value=excluded.value, "
                      "at=excluded.at", (gateway, metric, int(minute), float(value), at))

    def get_meta(self, key: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, str(value)))

    def prune(self, now: float):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM minutes WHERE minute < ?", (int(now) - RETENTION_S,))

    # --- reads --------------------------------------------------------------------------------------
    def reporters(self, now: float) -> set[tuple[str, str]]:
        """(gateway, metric) pairs with any row in the retention window."""
        with self._conn() as c:
            return {(g, m) for g, m in c.execute(
                "SELECT DISTINCT gateway, metric FROM minutes WHERE minute >= ?", (int(now) - RETENTION_S,))}

    def bucketed(self, since: int, until: int, bucket_s: int) -> dict[tuple[str, str], list[Optional[float]]]:
        """``{(gateway, metric): [sum per bucket]}`` over ``[since, until)``; a bucket with no sampled minute is
        None (missing), a bucket with some sampled minutes sums those."""
        n = (until - since) // bucket_s
        out: dict[tuple[str, str], list[Optional[float]]] = {}
        with self._conn() as c:
            rows = c.execute(
                "SELECT gateway, metric, (minute - ?) / ?, SUM(value), COUNT(value) FROM minutes "
                "WHERE minute >= ? AND minute < ? GROUP BY 1, 2, 3", (since, bucket_s, since, until)).fetchall()
        for g, m, b, total, sampled in rows:
            arr = out.setdefault((g, m), [None] * n)
            if 0 <= b < n and sampled:
                arr[b] = float(total)
        return out
