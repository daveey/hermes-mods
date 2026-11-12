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


# --- local per-session token snapshot ---------------------------------------------------------------

def session_snapshot(dbs: list[Path]) -> tuple[dict[tuple[str, str], tuple[int, int]], set[str]]:
    """``({(db, session_id): (io_tokens, cache_tokens)}, {readable db paths})``."""
    snap: dict[tuple[str, str], tuple[int, int]] = {}
    readable: set[str] = set()
    for db in dbs:
        try:
            c = ro_connect(db)
            try:
                rows = c.execute(
                    "SELECT id, COALESCE(input_tokens,0)+COALESCE(output_tokens,0), "
                    "COALESCE(cache_read_tokens,0)+COALESCE(cache_write_tokens,0) FROM sessions").fetchall()
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
                cols = {r[1] for r in c.execute("PRAGMA table_info(session_model_usage)")}
                if not {"session_id", "model", "input_tokens", "output_tokens", "cache_read_tokens",
                        "cache_write_tokens"} <= cols:
                    continue
                rows = c.execute(
                    "SELECT session_id, COALESCE(NULLIF(TRIM(model),''),'unknown'), "
                    "SUM(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)), "
                    "SUM(COALESCE(cache_read_tokens,0)+COALESCE(cache_write_tokens,0)) "
                    "FROM session_model_usage GROUP BY 1, 2").fetchall()
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
        self._snap: Optional[dict] = None
        self._snap_minute: Optional[int] = None
        self._snap_dbs: set[str] = set()
        self._msnap: Optional[dict] = None
        self._msnap_dbs: set[str] = set()
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
        snap, readable = session_snapshot(state_dbs(self.root))
        if not readable:
            return  # nothing readable: no sample (the next one spreads the gap or stores it as missing)
        prev, prev_dbs = self._snap, self._snap_dbs
        if prev is not None and self._snap_minute is not None and minute <= self._snap_minute:
            return  # same minute sampled twice: keep the older baseline
        # A db unreadable this minute keeps its previous totals, so its sessions never come back as "new";
        # a db seen for the first time (a new profile) is only a baseline.
        for key, v in (prev or {}).items():
            if key[0] not in readable:
                snap[key] = v
        msnap, mcapable = model_snapshot([Path(d) for d in readable])
        prev_m, prev_mdbs = self._msnap, self._msnap_dbs
        for key, v in (prev_m or {}).items():
            if key[0] not in mcapable:
                msnap[key] = v
        if prev is not None and self._snap_minute is not None:
            io, cache = snapshot_delta(prev, {k: v for k, v in snap.items() if k[0] in prev_dbs})
            self.store.record_delta(me, "tokens_io", self._snap_minute, minute, io)
            self.store.record_delta(me, "tokens_cache", self._snap_minute, minute, cache)
            # Only consecutive model-readable samples can explain this scalar interval. On recovery,
            # establish a fresh baseline: an older model delta also includes tokens already unreported.
            m_io, m_cache = model_deltas(
                prev_m or {}, {k: v for k, v in msnap.items() if k[0] in prev_mdbs and k[0] in prev_dbs})
            self.store.record_models(me, "tokens_io", self._snap_minute, minute, io, m_io)
            self.store.record_models(me, "tokens_cache", self._snap_minute, minute, cache, m_cache)
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
