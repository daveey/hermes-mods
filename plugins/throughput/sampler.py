"""Background sampler: per-minute token and session counts for every gateway.

Two sources, one store (see store.py):

* **Board registry** (``hermes_cli.kanban_worker_node.registry()``): each machine's ``metrics`` may carry the
  cumulative counters ``tokens_io_total``, ``tokens_cache_total``, ``sessions_started_total`` and
  ``sessions_ended_total``. Each minute the sampler stores their deltas. A machine without them stores nothing,
  so the API reports its series as ``null`` (not 0).
* **This gateway's own state.db files** (root + ``profiles/*``), with the same definitions as those counters
  (io = input + output tokens, cache = cache read + cache write), so the local gateway has data before every
  machine reports them. Tokens are diffed per session: a session deleted between two samples contributes
  nothing (it never makes the total go negative), a new one contributes everything it has used.
  Sessions started/ended per minute are counted straight from ``sessions.started_at/ended_at``, which also
  backfills the retention window on first start.

One sampler per Hermes root: a non-blocking ``flock`` on ``cache/throughput/sampler.lock`` lets whichever
long-lived process (gateway, Desktop serve, dashboard) grabs it first do the work.
"""
from __future__ import annotations

import fcntl
import glob
import logging
import math
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

from .store import RETENTION_S, Store, minute_of

log = logging.getLogger("hermes.plugins.throughput")

INTERVAL_S = 60
SESSION_RECOUNT_S = 15 * 60  # recount the last 15 minutes of session starts/ends each pass (late ended_at writes)
REGISTRY_COUNTERS = {
    "tokens_io_total": "tokens_io",
    "tokens_cache_total": "tokens_cache",
    "sessions_started_total": "sess_in",
    "sessions_ended_total": "sess_out",
}
THREAD_NAME = "throughput-sampler"


def ro_connect(path: str | Path) -> sqlite3.Connection:
    """Read-only open that works on a WAL db whose -wal/-shm are gone: mode=rw + query_only (never creates)."""
    c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=rw", uri=True, timeout=1)
    c.execute("PRAGMA query_only=ON")
    return c


def state_dbs(root: Path) -> list[Path]:
    out = [root / "state.db"] + [Path(p) for p in sorted(glob.glob(str(root / "profiles" / "*" / "state.db")))]
    return [p for p in out if p.is_file()]


def hermes_root() -> Path:
    try:
        from hermes_constants import get_default_hermes_root
        return Path(get_default_hermes_root())
    except Exception:  # outside a Hermes runtime (tests pass a root explicitly)
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def series_path(root: Path) -> Path:
    return root / "cache" / "throughput" / "series.sqlite"


class _NoAccounting:
    """Stand-in for core's ``loop_accounting.Pass`` outside a Hermes runtime (or on an older core)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def skip(self):
        pass

    def change(self, changed=True):
        pass

    def fallback(self, fell_back=True):
        pass

    def rows(self, n):
        pass


def accounted_pass(name: str):
    """One P1 loop-accounting pass (core ``hermes_cli.loop_accounting``), or a no-op without it."""
    try:
        from hermes_cli.loop_accounting import pass_
    except Exception:  # outside a Hermes runtime, or a core without P1
        return _NoAccounting()
    return pass_(name)


# --- local per-session token snapshot ---------------------------------------------------------------

_TOKENS_SQL = ("SELECT id, COALESCE(input_tokens,0)+COALESCE(output_tokens,0), "
               "COALESCE(cache_read_tokens,0)+COALESCE(cache_write_tokens,0) FROM sessions")
_MODEL_COLUMNS = {"session_id", "model", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
_MODELS_SQL = ("SELECT session_id, COALESCE(NULLIF(TRIM(model),''),'unknown'), "
               "SUM(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)), "
               "SUM(COALESCE(cache_read_tokens,0)+COALESCE(cache_write_tokens,0)) "
               "FROM session_model_usage")


def model_capable(c: sqlite3.Connection) -> bool:
    return _MODEL_COLUMNS <= {r[1] for r in c.execute("PRAGMA table_info(session_model_usage)")}


def session_snapshot(dbs: list[Path]) -> tuple[dict[tuple[str, str], tuple[int, int]], set[str]]:
    """``({(db, session_id): (io_tokens, cache_tokens)}, {readable db paths})``."""
    snap: dict[tuple[str, str], tuple[int, int]] = {}
    readable: set[str] = set()
    for db in dbs:
        try:
            c = ro_connect(db)
            try:
                rows = c.execute(_TOKENS_SQL).fetchall()
            finally:
                c.close()
        except sqlite3.Error as exc:
            log.debug("throughput: %s unreadable: %s", db, exc)
            continue
        for sid, io, cache in rows:
            snap[(str(db), sid)] = (int(io or 0), int(cache or 0))
        readable.add(str(db))
    return snap, readable


def snapshot_delta(prev: Mapping, cur: Mapping) -> tuple[int, int]:
    """Tokens used between two snapshots. New sessions count in full; deleted sessions count 0; a session whose
    totals went down (rewritten) counts 0 rather than negative."""
    io = cache = 0
    for key, (cio, ccache) in cur.items():
        pio, pcache = prev.get(key, (0, 0))
        io += max(0, cio - pio)
        cache += max(0, ccache - pcache)
    return io, cache


def model_snapshot(dbs: list[Path]) -> tuple[dict[tuple[str, str, str], tuple[int, int]], set[str]]:
    """``({(db, session_id, model): (io, cache)}, {model-capable db paths})`` from ``session_model_usage``.

    Every row is summed (the registry's ``tokens_*_total`` counters sum the same ledger), over provider /
    base-url / mode / task for one (session, model). The local scalar comes from ``sessions``, which leaves out
    some auxiliary-call rows, so the models can exceed it: ``store.apportion`` then scales them down to the
    scalar (the scalar stays the authority). A db without the table (older schema), or whose table cannot be
    read, is not model-capable: it is absent from the set, so its tokens stay ``unreported``."""
    snap: dict[tuple[str, str, str], tuple[int, int]] = {}
    capable: set[str] = set()
    for db in dbs:
        try:
            c = ro_connect(db)
            try:
                if not model_capable(c):
                    continue
                rows = c.execute(_MODELS_SQL + " GROUP BY 1, 2").fetchall()
            finally:
                c.close()
        except sqlite3.Error as exc:
            log.debug("throughput: %s model usage unreadable: %s", db, exc)
            continue
        for sid, model, io, cache in rows:
            snap[(str(db), sid, str(model))] = (int(io or 0), int(cache or 0))
        capable.add(str(db))
    return snap, capable


def model_deltas(prev: Mapping, cur: Mapping) -> tuple[dict[str, int], dict[str, int]]:
    """``({model: io delta}, {model: cache delta})`` between two model snapshots, with ``snapshot_delta``'s rules
    per (session, model): new counts in full, vanished 0, lower 0."""
    io: dict[str, int] = {}
    cache: dict[str, int] = {}
    for (db, sid, model), (cio, ccache) in cur.items():
        pio, pcache = prev.get((db, sid, model), (0, 0))
        if cio > pio:
            io[model] = io.get(model, 0) + cio - pio
        if ccache > pcache:
            cache[model] = cache.get(model, 0) + ccache - pcache
    return io, cache


def registry_models(raw) -> tuple[dict[str, float], dict[str, float]]:
    """``tokens_by_model`` ``{model: [io, cache]}`` -> ``({model: io}, {model: cache})``. Anything malformed (not a
    mapping, bad pair, negative/non-finite/bool numbers) is skipped per model and per kind, never raised."""
    io: dict[str, float] = {}
    cache: dict[str, float] = {}
    if not isinstance(raw, Mapping):
        return io, cache
    for model, pair in raw.items():
        if not isinstance(model, str) or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        for out, v in ((io, pair[0]), (cache, pair[1])):
            if not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
                out[model] = out.get(model, 0.0) + float(v)
    return io, cache


def session_minutes(dbs: list[Path], since: int, until: int) -> Optional[tuple[dict[int, int], dict[int, int]]]:
    """Per-minute session starts and ends in ``[since, until)`` (every minute present, 0 when none). None when any
    state.db can't be read: a partial count would overwrite stored minutes with too-low numbers."""
    starts = {m: 0 for m in range(since, until, 60)}
    ends = dict(starts)
    if not dbs:
        return None
    for db in dbs:
        try:
            c = ro_connect(db)
            try:
                st = c.execute("SELECT started_at FROM sessions WHERE started_at >= ? AND started_at < ?",
                               (since, until)).fetchall()
                en = c.execute("SELECT ended_at FROM sessions WHERE ended_at >= ? AND ended_at < ?",
                               (since, until)).fetchall()
            finally:
                c.close()
        except sqlite3.Error as exc:
            log.debug("throughput: %s unreadable: %s", db, exc)
            return None
        for (t,) in st:
            starts[minute_of(t)] = starts.get(minute_of(t), 0) + 1
        for (t,) in en:
            ends[minute_of(t)] = ends.get(minute_of(t), 0) + 1
    return starts, ends


# --- incremental local reads (state.db ``messages.id`` watermark) -----------------------------------------
#
# A full read of every session and every model-usage row of every state.db each minute is O(state) (23k
# sessions in 11 files on the hub). Tokens move when a turn runs, and a turn appends messages, so a pass reads
# only the sessions of the messages handed out since the last pass (``messages`` AUTOINCREMENT watermark), the
# sessions created since (``sessions.rowid``), and every session that had either within HOT_S (the usage write
# can land after the message it accounts for). A file is read in full, exactly as before, when the watermark
# can't vouch for it:
#   * a message handed out since the last pass is gone (a rewind or delete: the watermark went back, or the new
#     id range has fewer rows than ids), the sessions row count is not the last count plus the new rows (a
#     session was deleted), or the file was replaced or its schema moved;
#   * no ``messages`` table, an unreadable file or model table, more than MAX_GAP_ROWS new messages, no
#     previous read;
#   * every FULL_EVERY_S (the P1 fallback ceiling): a usage write with no message (an auxiliary call on an idle
#     session) is counted at most that late. Deltas are per session against its last read value, so a late
#     read moves the minute a token is counted in, never the total.

FULL_EVERY_S = 600
HOT_S = 600
HOT_SEED_ROWS = 500  # a full read marks the sessions of the newest messages hot ...
HOT_SEED_SESSIONS = 50  # ... and the newest sessions (a usage write can precede a session's first message)
MAX_GAP_ROWS = 200_000
_IN_CHUNK = 500
# The sessions of a message-id range, deduplicated by the caller: with DISTINCT the planner walks a session_id
# index over the whole table (66 ms on the hub's 1.27M messages) instead of the rowid range (well under 1 ms).
_RANGE_SQL = "SELECT session_id FROM messages WHERE id > ? AND id <= ?"


class DbCursor:
    """What the last read of one state.db vouches for."""

    __slots__ = ("ident", "schema", "msg_wm", "srow", "scount", "hot", "full_at", "models")

    def __init__(self, ident, schema, msg_wm, srow, scount, hot, full_at, models):
        self.ident, self.schema, self.msg_wm, self.srow, self.scount = ident, schema, msg_wm, srow, scount
        self.hot: dict[str, float] = hot
        self.full_at = full_at
        self.models: bool = models  # session_model_usage readable (model_capable) when the cursor was taken


def _file_ident(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_dev, st.st_ino


def _header(c: sqlite3.Connection) -> Optional[tuple[int, int, int, int]]:
    """``(schema_version, messages watermark, max sessions.rowid, sessions count)``; None without a messages
    table. The watermark is the AUTOINCREMENT sequence (the highest id ever handed out, which a delete never
    lowers), or MAX(id) for a table without one."""
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'").fetchone() is None:
        return None
    schema = int(c.execute("PRAGMA schema_version").fetchone()[0])
    seq = None
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone():
        seq = c.execute("SELECT seq FROM sqlite_sequence WHERE name='messages'").fetchone()
    msg_wm = int(seq[0] if seq else c.execute("SELECT MAX(id) FROM messages").fetchone()[0] or 0)
    srow = int(c.execute("SELECT MAX(rowid) FROM sessions").fetchone()[0] or 0)
    scount = int(c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] or 0)
    return schema, msg_wm, srow, scount


def read_cursor(db: Path, now: float) -> Optional[DbCursor]:
    """The watermarks a full read starts from, taken BEFORE it (a write in between is re-read by the next pass),
    with the sessions of the newest HOT_SEED_ROWS messages as the hot set; None when the file can't be read
    incrementally (it is then read in full every pass)."""
    try:
        ident = _file_ident(db)
        c = ro_connect(db)
        try:
            c.execute("BEGIN")
            head = _header(c)
            if head is None:
                return None
            hot = {r[0]: now for r in c.execute(_RANGE_SQL, (head[1] - HOT_SEED_ROWS, head[1]))}
            hot.update((r[0], now) for r in c.execute("SELECT id FROM sessions WHERE rowid > ?",
                                                      (head[2] - HOT_SEED_SESSIONS,)))
            models = model_capable(c)
        finally:
            c.close()
    except (OSError, sqlite3.Error):
        return None
    return DbCursor(ident, *head, hot, now, models)


def read_incremental(db: Path, cur: DbCursor, now: float, models: bool):
    """``({sid: (io, cache) or None}, {sid: {model: (io, cache)}} or None)`` for every session the watermark says
    may have moved since ``cur`` (None value: gone); ``cur`` advances. None when only a full read is exact
    (``cur`` is then untouched)."""
    if not 0 <= now - cur.full_at < FULL_EVERY_S:
        return None
    try:
        if _file_ident(db) != cur.ident:
            return None  # replaced (a restore, a copy over it)
        c = ro_connect(db)
        try:
            c.execute("BEGIN")  # one read snapshot for the watermarks and the rows they vouch for
            head = _header(c)
            if head is None or head[0] != cur.schema:
                return None
            _, msg_wm, srow, scount = head
            if msg_wm < cur.msg_wm or msg_wm - cur.msg_wm > MAX_GAP_ROWS or srow < cur.srow:
                return None  # the watermark went back, or too far behind to scan
            new_sessions = [r[0] for r in c.execute("SELECT id FROM sessions WHERE rowid > ?", (cur.srow,))]
            if scount != cur.scount + len(new_sessions):
                return None  # a session was deleted
            appended = [r[0] for r in c.execute(_RANGE_SQL, (cur.msg_wm, msg_wm))]
            if len(appended) != msg_wm - cur.msg_wm:
                return None  # a message handed out since the last pass is gone (a rewind): whose tokens moved?
            hot = {sid: t for sid, t in cur.hot.items() if now - t < HOT_S}
            for sid in appended:
                hot[sid] = now
            for sid in new_sessions:
                hot[sid] = now
            ids = sorted(hot)
            tokens: dict[str, Optional[tuple[int, int]]] = dict.fromkeys(ids)
            by_model: Optional[dict[str, dict[str, tuple[int, int]]]] = {sid: {} for sid in ids} if models else None
            for i in range(0, len(ids), _IN_CHUNK):
                part = ids[i:i + _IN_CHUNK]
                marks = ",".join("?" * len(part))
                for sid, io, cache in c.execute(_TOKENS_SQL + f" WHERE id IN ({marks})", part):
                    tokens[sid] = (int(io or 0), int(cache or 0))
                if by_model is not None:
                    for sid, model, io, cache in c.execute(
                            _MODELS_SQL + f" WHERE session_id IN ({marks}) GROUP BY 1, 2", part):
                        by_model[sid][str(model)] = (int(io or 0), int(cache or 0))
        finally:
            c.close()
    except (OSError, sqlite3.Error):
        return None  # the full read decides whether the file is readable
    cur.msg_wm, cur.srow, cur.scount, cur.hot = msg_wm, srow, scount, hot
    return tokens, by_model


# --- sampler -------------------------------------------------------------------------------------------

def default_registry() -> Optional[Mapping]:
    from hermes_cli import kanban_worker_node
    return kanban_worker_node.registry()


class Sampler:
    def __init__(self, root: Path, store: Optional[Store] = None, *,
                 registry: Callable[[], Optional[Mapping]] = default_registry,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.store = store or Store(series_path(self.root))
        self.registry = registry
        self.clock = clock
        self._snap: Optional[dict[str, dict[str, tuple[int, int]]]] = None  # {db: {sid: (io, cache)}}
        self._snap_minute: Optional[int] = None
        self._snap_dbs: set[str] = set()
        self._msnap: dict[str, dict[str, dict[str, tuple[int, int]]]] = {}  # {db: {sid: {model: (io, cache)}}}
        self._msnap_dbs: set[str] = set()
        self._cursors: dict[str, DbCursor] = {}
        self._last_prune = 0.0

    def me(self, reg: Optional[Mapping]) -> str:
        name = (reg or {}).get("me")
        if name:
            self.store.set_meta("me", name)
            return str(name)
        name = self.store.get_meta("me")
        if name:
            return name
        host = socket.gethostname().split(".")[0]
        return host[len("hermes-"):] if host.startswith("hermes-") else host

    def sample_local_tokens(self, me: str, minute: int) -> None:
        """Per-session token deltas since the last sample, read incrementally per state.db when its watermark
        vouches for it (``read_incremental``), else in full (``session_snapshot`` / ``model_snapshot``)."""
        if self._snap is not None and self._snap_minute is not None and minute <= self._snap_minute:
            return  # same minute sampled twice: keep the older baseline
        with accounted_pass("throughput.local_tokens") as acct:
            self._sample_local_tokens(me, minute, acct)

    def _sample_local_tokens(self, me: str, minute: int, acct) -> None:
        now = self.clock()
        prev, prev_dbs = self._snap, self._snap_dbs
        prev_m, prev_mdbs = self._msnap, self._msnap_dbs
        changed: dict[str, dict[str, Optional[tuple[int, int]]]] = {}  # db -> re-read sessions
        mchanged: dict[str, dict[str, dict[str, tuple[int, int]]]] = {}
        full: list[Path] = []
        for db in state_dbs(self.root):
            key = str(db)
            cur = self._cursors.get(key) if prev is not None else None
            got = read_incremental(db, cur, now, key in prev_mdbs) if cur is not None else None
            if got is None:
                full.append(db)
                continue
            changed[key] = got[0]
            if got[1] is not None:
                mchanged[key] = got[1]
        acct.fallback(bool(full) and prev is not None)
        cursors = {str(db): read_cursor(db, now) for db in full}  # watermarks BEFORE the rows they vouch for
        snap_f, readable_f = session_snapshot(full)
        msnap_f, mcap_f = model_snapshot([Path(d) for d in sorted(readable_f)])
        readable = readable_f | set(changed)
        if not readable:
            return  # nothing readable: no sample (the next one spreads the gap or stores it as missing)
        mcapable = mcap_f | set(mchanged)
        new: dict[str, dict[str, tuple[int, int]]] = {d: {} for d in readable_f}
        for (d, sid), v in snap_f.items():
            new[d][sid] = v
        mnew: dict[str, dict[str, dict[str, tuple[int, int]]]] = {d: {} for d in mcap_f}
        for (d, sid, model), v in msnap_f.items():
            mnew[d].setdefault(sid, {})[model] = v
        io = cache = 0
        m_io: dict[str, int] = {}
        m_cache: dict[str, int] = {}

        def add_models(old: Mapping, cur_m: Mapping) -> None:
            for model, (cio, ccache) in cur_m.items():
                pio, pcache = old.get(model, (0, 0))
                if cio > pio:
                    m_io[model] = m_io.get(model, 0) + cio - pio
                if ccache > pcache:
                    m_cache[model] = m_cache.get(model, 0) + ccache - pcache

        # Only a db read last time has a baseline (a db seen for the first time, a new profile, is a baseline);
        # a db unreadable this minute keeps its previous totals, so its sessions never come back as "new". Only
        # consecutive model-readable samples explain a scalar interval: a model recovery is a fresh baseline.
        last = self._snap_minute if prev is not None else None
        prev_s = prev or {}
        for d, sessions in new.items():
            if last is not None and d in prev_dbs:
                d_io, d_cache = snapshot_delta(prev_s.get(d, {}), sessions)
                io, cache = io + d_io, cache + d_cache
        for d, sessions in mnew.items():
            if last is not None and d in prev_mdbs and d in prev_dbs:
                old_d = prev_m.get(d, {})
                for sid, cur_m in sessions.items():
                    add_models(old_d.get(sid, {}), cur_m)
        snap = dict(prev_s)
        snap.update(new)
        msnap = dict(prev_m)
        msnap.update(mnew)
        rows = len(snap_f)
        for d, reread in changed.items():  # in place: an incremental db's dict is not shared with anything else
            base = snap.setdefault(d, {})
            for sid, v in reread.items():
                if v is None:
                    base.pop(sid, None)
                    continue
                pio, pcache = base.get(sid, (0, 0))
                io, cache = io + max(0, v[0] - pio), cache + max(0, v[1] - pcache)
                base[sid] = v
            rows += len(reread)
        for d, reread in mchanged.items():
            mbase = msnap.setdefault(d, {})
            for sid, cur_m in reread.items():
                add_models(mbase.get(sid, {}), cur_m)
                if cur_m:
                    mbase[sid] = cur_m
                else:
                    mbase.pop(sid, None)
        acct.rows(rows)
        acct.change(bool(full) or any(changed.values()))
        if last is not None:
            self.store.record_delta(me, "tokens_io", last, minute, io)
            self.store.record_delta(me, "tokens_cache", last, minute, cache)
            self.store.record_models(me, "tokens_io", last, minute, io, m_io)
            self.store.record_models(me, "tokens_cache", last, minute, cache, m_cache)
        # An incremental read needs a full read's baseline for both maps: a db whose sessions or model usage
        # couldn't be read in full this minute is read in full again next minute.
        for d, cur in cursors.items():
            if cur is not None and d in readable_f and (d in mcap_f or not cur.models):
                # Sessions that moved since the last read stay hot, as do the old cursor's: a rewound message
                # no longer marks its session, but the usage write that follows it still lands.
                old = self._cursors.get(d)
                if old is not None:
                    cur.hot.update({sid: t for sid, t in old.hot.items() if now - t < HOT_S and sid not in cur.hot})
                if d in prev_dbs:
                    before = prev_s.get(d, {})
                    cur.hot.update((sid, now) for sid, v in new[d].items() if before.get(sid) != v)
                self._cursors[d] = cur
            else:
                self._cursors.pop(d, None)
        self._snap, self._snap_minute, self._snap_dbs = snap, minute, readable | prev_dbs
        self._msnap, self._msnap_dbs = msnap, mcapable

    def sample_local_sessions(self, me: str, minute: int) -> None:
        done = int(self.store.get_meta("sessions_until:" + me) or 0)
        since = max(minute - RETENTION_S, min(done, minute) - SESSION_RECOUNT_S) if done else minute - RETENTION_S
        counted = session_minutes(state_dbs(self.root), since, minute)
        if counted is None:
            return
        starts, ends = counted
        self.store.put(me, "sess_in", sorted(starts.items()))
        self.store.put(me, "sess_out", sorted(ends.items()))
        self.store.set_meta("sessions_until:" + me, str(minute))

    def sample_registry(self, reg: Optional[Mapping], me: str, minute: int) -> None:
        for name, entry in ((reg or {}).get("machines") or {}).items():
            if name == me:
                continue  # the local gateway is sampled directly
            metrics = (entry or {}).get("metrics") or {}
            at = metrics.get("at")
            by_model = dict(zip(("tokens_io", "tokens_cache"), registry_models(metrics.get("tokens_by_model"))))
            for key, metric in REGISTRY_COUNTERS.items():
                value = metrics.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    continue
                at_f = float(at) if isinstance(at, (int, float)) else None
                if metric in by_model:  # a missing/invalid map just means everything is unreported
                    self.store.observe_token_counter(name, metric, minute, float(value), at_f, by_model[metric])
                    continue
                self.store.observe_counter(name, metric, minute, float(value), at_f)

    def tick(self) -> None:
        now = self.clock()
        minute = minute_of(now)
        try:
            reg = self.registry()
        except Exception as exc:  # registry unreachable: local sampling still runs
            log.info("throughput: registry unavailable: %s", exc)
            reg = None
        me = self.me(reg)
        for step in (lambda: self.sample_local_tokens(me, minute), lambda: self.sample_local_sessions(me, minute),
                     lambda: self.sample_registry(reg, me, minute)):
            try:
                step()
            except Exception:
                log.exception("throughput: sample step failed")
        if now - self._last_prune > 3600:
            self.store.prune(now)
            self._last_prune = now


# --- process lifecycle ---------------------------------------------------------------------------------

_start_lock = threading.Lock()
_state: dict = {}


def eligible_process(argv: Optional[list[str]] = None, env: Optional[Mapping[str, str]] = None) -> bool:
    """Long-lived server processes only: ``hermes gateway …``, ``hermes serve …`` (Desktop), ``hermes dashboard``.
    Never a CLI chat, a Kanban worker or a one-shot command."""
    import sys
    argv = list(sys.argv if argv is None else argv)
    env = os.environ if env is None else env
    if env.get("HERMES_KANBAN_TASK") or env.get("HERMES_KANBAN_WORKER_TASK"):
        return False
    role = next((a for a in argv[1:] if not a.startswith("-")), "")
    return role in ("gateway", "serve", "dashboard")


def _stop_stale_threads() -> None:
    """A plugin reload re-runs this module; stop the previous module's sampler so it releases the lock."""
    for t in threading.enumerate():
        if t.name == THREAD_NAME and t is not _state.get("thread") and t.is_alive():
            ev = (getattr(t, "_args", None) or (None,))[0]
            if isinstance(ev, threading.Event):
                ev.set()


def _loop(stop: threading.Event, root: Path) -> None:
    lock_path = series_path(root).parent / "sampler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while not stop.is_set():
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if stop.wait(30):  # another process samples this root; take over if it exits
                    return
        sampler = Sampler(root)
        log.info("throughput: sampling %s every %ss (pid %s)", series_path(root), INTERVAL_S, os.getpid())
        while not stop.is_set():
            try:
                sampler.tick()
            except Exception:
                log.exception("throughput: tick failed")
            now = time.time()
            if stop.wait(INTERVAL_S - (now % INTERVAL_S) + 2):  # just after each minute boundary
                return
    finally:
        os.close(fd)  # releases the flock


def start(root: Optional[Path] = None, *, replace_stale: bool = True) -> bool:
    """Start the sampler thread once per process. True when a thread was started now.

    ``replace_stale`` (plugin load/reload): stop a sampler a previous load of this module left running.
    Without it (the REST API, a second import of this file under another name) any live sampler thread in
    the process counts as running."""
    with _start_lock:
        t = _state.get("thread")
        if t is not None and t.is_alive():
            return False
        if replace_stale:
            _stop_stale_threads()
        elif any(x.name == THREAD_NAME and x.is_alive() for x in threading.enumerate()):
            return False
        stop = threading.Event()
        t = threading.Thread(target=_loop, args=(stop, Path(root) if root else hermes_root()),
                             name=THREAD_NAME, daemon=True)
        _state.update(thread=t, stop=stop)
        t.start()
        return True


def stop() -> None:
    ev = _state.get("stop")
    if ev is not None:
        ev.set()
