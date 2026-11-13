"""Per-minute throughput series: ``<hermes root>/cache/throughput/series.sqlite``.

One row per (gateway, metric, minute). ``value`` is the count in that minute; NULL means the minute was
not sampled (gateway offline, sampler down, a gap too long to spread). A gateway "reports" a metric when it
has any row for it inside the retention window; one that never reported has no rows, and the API returns
``null`` for its whole series rather than zeros.

Counter baselines (``counters``) survive a restart so a short sampler outage is spread over the minutes it
covered instead of being lost.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Iterator, Optional

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


RESERVED_MODELS = ("other", "unreported")
MODEL_KINDS = ("tokens_io", "tokens_cache")
MAX_MODELS = 64  # per reading: a hostile or buggy registry map cannot create unbounded series



def model_name(model) -> str:
    """Normalize blank names; retain the wire's reserved overflow bucket ``other``.

    ``unreported`` is also reserved by the API. Normalization is idempotent because registry
    baselines and stored deltas pass through this helper independently.
    """
    return str(model).strip()[:128] or "unknown"


def apportion(scalar: float, models: Optional[dict]) -> tuple[dict, float]:
    """Split one scalar delta into per-model deltas plus ``unreported``, always summing to ``scalar``.

    The scalar counter is the authority. Models short of it leave the rest as ``unreported``; models above it
    (per-model cumulative churn: a deleted session removes more model tokens than scalar tokens, a reset in
    one counter) are scaled down proportionally, ``unreported`` 0. Missing/empty ``models`` -> all unreported."""
    scalar = max(0.0, float(scalar))
    parts = {m: float(v) for m, v in (models or {}).items() if v and v > 0}
    total = sum(parts.values())
    if total > scalar:
        f = scalar / total if total else 0.0
        return {m: v * f for m, v in parts.items()}, 0.0
    return parts, scalar - total


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
            # WAL is persistent in the db file: set it once, and read the row it returns. An unread row leaves the
            # statement active until its cursor is freed, and a COMMIT on that connection then fails with
            # "SQL statements in progress".
            c.execute("PRAGMA journal_mode=WAL").fetchone()
            c.executescript(SCHEMA)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """A fresh connection for one transaction: committed (or rolled back) and closed on exit."""
        c = sqlite3.connect(str(self.path), timeout=10)
        try:
            with c:
                yield c
        finally:
            c.close()

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

    def record_models(self, gateway: str, kind: str, m_prev: int, m_now: int, scalar: Optional[float],
                      models: Optional[dict]):
        """Per-model rows for ``[m_prev, m_now)`` next to a scalar delta: ``<kind>:<model>`` rows (non-zero only)
        plus ``<kind>:unreported``, written for every sampled minute (0 included) as the sampled-minute marker of
        the model view. ``scalar`` None (long gap): only missing ``unreported`` rows, never 0. Minutes before
        deployment get no rows: model history starts at deployment."""
        if scalar is None:
            self.record_delta(gateway, f"{kind}:unreported", m_prev, m_now, None)
            return
        normalized = {}
        for key, value in (models or {}).items():
            name = model_name(key)
            normalized[name] = normalized.get(name, 0.0) + value
        parts, unreported = apportion(scalar, normalized)
        unreported += parts.pop("unreported", 0.0)
        for model, v in parts.items():
            self.record_delta(gateway, f"{kind}:{model}", m_prev, m_now, v)
        self.record_delta(gateway, f"{kind}:unreported", m_prev, m_now, unreported)

    def observe_token_counter(self, gateway: str, kind: str, minute: int, value: float, at: Optional[float],
                              models: Optional[dict]):
        """``observe_counter`` for a token scalar plus its per-model cumulative counters (``models`` {model: value};
        None/empty when the machine reports no model map). A model baseline is used only when taken at the scalar's
        previous baseline minute, so a model that is new, came back after being absent, or follows a period without
        a map starts from a baseline (its tokens stay in ``unreported``) instead of a stale cumulative."""
        normalized = {}
        for key, cumulative in list((models or {}).items())[:MAX_MODELS]:
            name = model_name(key)
            normalized[name] = normalized.get(name, 0.0) + float(cumulative)
        models = normalized
        marker = f"model_sampling:{gateway}:{kind}"
        with self._lock, self._conn() as c:
            model_started = c.execute("SELECT 1 FROM meta WHERE key=?", (marker,)).fetchone() is not None
            row = c.execute("SELECT minute, value, at FROM counters WHERE gateway=? AND metric=?",
                            (gateway, kind)).fetchone()
            if row is not None and at is not None and row[2] is not None and float(at) == float(row[2]):
                return
            if row is not None and minute <= row[0]:
                return
            base = {}
            if row is not None and models:
                prefix = kind + ":"
                for metric, bmin, bval in c.execute(
                        "SELECT metric, minute, value FROM counters WHERE gateway=? AND substr(metric,1,?)=?",
                        (gateway, len(prefix), prefix)):
                    if int(bmin) == int(row[0]):
                        base[metric[len(prefix):]] = float(bval)
        if row is not None:
            deltas = {m: counter_delta(base[m], v) for m, v in models.items() if m in base}
            scalar = counter_delta(float(row[1]), float(value))
            self.record_delta(gateway, kind, int(row[0]), int(minute), scalar)
            if model_started:
                self.record_models(gateway, kind, int(row[0]), int(minute), scalar, deltas)
            # An existing scalar baseline may predate this feature. Preserve its scalar gap, but never
            # create model (even unreported) rows before our first model-aware observation.
        upsert = ("INSERT INTO counters(gateway, metric, minute, value, at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(gateway, metric) DO UPDATE SET minute=excluded.minute, value=excluded.value, "
                  "at=excluded.at")
        with self._lock, self._conn() as c:
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, '1')", (marker,))
            c.execute(upsert, (gateway, kind, int(minute), float(value), at))
            c.executemany(upsert, [(gateway, f"{kind}:{m}", int(minute), v, at) for m, v in models.items()])
            c.execute("DELETE FROM counters WHERE gateway=? AND metric GLOB ? AND minute < ?",
                      (gateway, kind + ":*", int(minute) - RETENTION_S))

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
        return self.bucketed_sampled(since, until, bucket_s)[0]

    def bucketed_sampled(self, since: int, until: int, bucket_s: int):
        """``(sums, sampled)``: ``bucketed`` plus ``{(gateway, metric): [sampled minutes per bucket]}``, so a
        partly sampled bucket's rate can be its sum over the sampled minutes, not over the whole bucket."""
        n = (until - since) // bucket_s
        out: dict[tuple[str, str], list[Optional[float]]] = {}
        cover: dict[tuple[str, str], list[int]] = {}
        with self._conn() as c:
            rows = c.execute(
                "SELECT gateway, metric, (minute - ?) / ?, SUM(value), COUNT(value) FROM minutes "
                "WHERE minute >= ? AND minute < ? AND metric NOT GLOB '*:*' GROUP BY 1, 2, 3", (since, bucket_s, since, until)).fetchall()
        for g, m, b, total, sampled in rows:
            arr = out.setdefault((g, m), [None] * n)
            cov = cover.setdefault((g, m), [0] * n)
            if 0 <= b < n and sampled:
                arr[b] = float(total)
                cov[b] = int(sampled)
        return out, cover

    def model_minutes(self, since: int, until: int) -> dict:
        """``{kind: {model: {minute: tokens summed over gateways}}}`` over ``[since, until)`` for the model rows
        (``unreported`` included: it marks the sampled minutes). Minutes where every gateway's row is NULL are
        absent."""
        out: dict = {k: {} for k in MODEL_KINDS}
        with self._conn() as c:
            for kind in MODEL_KINDS:
                rows = c.execute(
                    "SELECT metric, minute, SUM(value) FROM minutes WHERE metric GLOB ? AND minute >= ? AND minute < ? "
                    "GROUP BY 1, 2 HAVING COUNT(value) > 0", (kind + ":*", since, until)).fetchall()
                for metric, minute, total in rows:
                    out[kind].setdefault(metric[len(kind) + 1:], {})[int(minute)] = float(total)
        return out
