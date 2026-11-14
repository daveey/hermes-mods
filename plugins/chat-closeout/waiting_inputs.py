"""Change-proportional inputs of the sidebar waiting source and the close-out host pass (WAITING-INPUTS.md).

Design t_01a1d563324d7e8e §3 row C, review R11, condition C15. Read-only; keeps no state.db connection open
(IDLE-READS.md). It only decides WHEN ``pending_closeouts`` (or a host-pass row) must be redone:

* :class:`ReplicaWatch`: the hub replica's ``tasks`` rows of the batch cards as substantive digests (the liveness-only
  ``activity_at`` / ``activity_kind`` and the ``origin_observation.task_hash`` re-stamp they cause are left out), read
  incrementally by ``seq``; the owners' scope heartbeats; the sync meta. Every clock verdict the projection judges
  from them is judged again at ``now`` on each call.
* :class:`LineageWatch`: a per-lineage transcript watermark per state.db: the highest ``messages.id`` (any row and
  active rows), the sessions' ``transcript_revision``, ``end_reason`` and compression-child count. Index seeks, never
  the full ``transcript_key`` scan, and never the global state.db stat as a VALUE (R11: it moves in ~47% of 2 s
  windows); the stat only gates re-running the seeks in this process.
* :func:`row_key` / :class:`RowCache`: the inputs of ONE ledger row (its fields, its cards as read, its lineage
  watermark). A pass re-reconciles only rows whose key moved.

Every key VALUE is content-derived (no connection, token or object), so another process computing it over the same
stores gets the same value and ``register_waiting_source(..., inputs=)`` may let it adopt this host's annotation.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger("chat_closeout")

VERSION = 1
LIVENESS_FIELDS = ("activity_at", "activity_kind")
REPLICA_FRESH_S = 180.0      # core project(): replica fresh / owner observation recent within 180 s
HUB_STALE_AFTER_S = 180.0    # core fallback when kanban_session_activity is unavailable
FLOOR_S = 600.0              # every key carries this bucket: a missed input is recomputed within 10 min (C9)


class Unportable(Exception):
    """A key part only this process can compare (a stat fallback holding a connection): no cross-process key."""


class Unvouched(Exception):
    """A state.db whose triggers cannot vouch for in-place message rewrites: no transcript watermark."""


def digest(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def strict_digest(value: Any) -> str:
    """``digest`` of plain JSON data only: anything else raises (a cross-process key must not stringify objects)."""
    return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_stat(path: Optional[Path]) -> Optional[tuple]:
    """(dev, ino, size, mtime_ns) of the file and its -wal: a process-local GATE only, never a key value."""
    if path is None:
        return None
    out = []
    for p in (Path(path), Path(str(path) + "-wal")):
        try:
            st = p.stat()
            out.append((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def portable(value: Any) -> Any:
    """``value`` as plain JSON data, by core's annotation-host rule (``kanban_annotation_host._portable``): a board
    content key's stat fallback keeps its stat and drops the connection/version only this process can read; an
    unreadable board's key (``("stat", stat, object())``) raises :class:`Unportable`."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (tuple, list)):
        if value[:1] in (("stat",), ["stat"]):
            if len(value) != 4:
                raise Unportable("unreadable board")
            return ["stat", portable(value[1])]
        return [portable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): portable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, frozenset) or isinstance(value, set):
        return sorted(portable(v) for v in value)
    raise Unportable(type(value).__name__)


def portable_digest(value: Any, _memo: dict[int, tuple[Any, str]] = {}) -> str:  # noqa: B006 - process memo
    """``digest(portable(value))``, reused while ``value`` is the very same object (core's content keys are cached
    tuples: a board's key can be large and is rebuilt only when the board changed). Raises like ``portable``."""
    hit = _memo.get(id(value))
    if hit is not None and hit[0] is value:
        return hit[1]
    out = digest(portable(value))
    if len(_memo) > 256:
        _memo.clear()
    _memo[id(value)] = (value, out)
    return out


# ---- P1 accounting (core ``hermes_cli.loop_accounting``, card A); a no-op on older cores -------------------------
class _NoPass:
    def skip(self) -> None: ...
    def change(self, changed: bool = True) -> None: ...
    def fallback(self, fell_back: bool = True) -> None: ...
    def rows(self, n: int) -> None: ...


def accounting_pass(name: str):
    """``loop_accounting.pass_(name)`` when core has it, else a context yielding a no-op pass."""
    try:
        from hermes_cli import loop_accounting as la
        return la.pass_(name)
    except Exception:
        return contextlib.nullcontext(_NoPass())


# ---- hub replica ----------------------------------------------------------------------------------------------
def substantive(data: Any) -> Any:
    """A replica ``tasks`` record without its liveness-only fields (rf2: 67% of changed task rows): the activity
    stamps and the ``origin_observation.task_hash`` re-stamp they cause (design C5). Whether the observation still
    verifies against the record is kept, so a real attestation change still moves the digest."""
    if not isinstance(data, dict):
        return data
    payload = {k: v for k, v in data.items() if k not in LIVENESS_FIELDS and k != "origin_observation"}
    obs = data.get("origin_observation")
    if isinstance(obs, dict):
        valid = None
        try:
            from hermes_cli.kanban_origin_ledger import _owner_evidence
            valid = bool(_owner_evidence(data))
        except Exception:
            valid = None
        payload["origin_observation"] = {**{k: v for k, v in obs.items() if k != "task_hash"}, "_valid": valid}
    elif obs is not None:
        payload["origin_observation"] = obs
    return payload


def _scope_window(info: Any) -> float:
    try:
        from hermes_cli.kanban_scope_heartbeat import scope_window
        return float(scope_window(info))
    except Exception:
        return REPLICA_FRESH_S


def _fresh(at: Any, now: float, window: float) -> Optional[bool]:
    if not isinstance(at, (int, float)) or isinstance(at, bool) or not math.isfinite(at):
        return None
    return 0 <= now - at <= window


def _tail(key: Any) -> str:
    return str(key).rsplit("/", 1)[-1]


_MISSING = object()
_REPLICA_PROBE = ("SELECT gw, count(*), count(seq), max(seq), total(seq) FROM replica WHERE coll = 'tasks' "
                  "GROUP BY gw")  # index-only on replica_poll_metadata (coll, gw, seq): ~1 ms on a 18k-row hub


def file_ident(path: Optional[Path]) -> Optional[tuple]:
    """(st_dev, st_ino) of ``path``: a replaced or restored file is another file (core ``kanban_replica_rows``)."""
    if path is None:
        return None
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_dev, st.st_ino


class ReplicaWatch:
    """The hub replica rows the batch cards' remote reads depend on, read incrementally.

    ``refresh`` re-reads only when ``token`` (the replica's committed revision in this process) moved. Then, in one
    read transaction (core ``kanban_replica_rows._delta``'s checks, on what this key needs):

    * a per-gateway probe aggregate (``count``, ``count(seq)``, ``max(seq)``, ``total(seq)``; index-only);
    * the rows written since each gateway's cursor (an index range), merged into the ``(gw, key) -> seq`` map of
      every task row; the merged map must reproduce the probe, else the cause (a delete, a row rewritten at an older
      seq, a gateway whose seqs went back) is unknown and the rows are rebuilt;
    * every tracked batch row re-read by primary key, its raw text compared: a batch row rewritten in place at the
      same seq (a resync) still moves its digest.

    A NULL ``seq`` (no cursor can cover it), a replaced file (``file_ident``), new batch tails or a failed read
    rebuild."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clear()

    def clear(self) -> None:
        self.path: Optional[str] = None
        self.ident: Optional[tuple] = None
        self.token: Any = None
        self.cursor: dict[str, int] = {}
        self.seqs: dict[tuple[str, str], Optional[int]] = {}  # every task row: (gw, key) -> seq
        self.aggs: dict[str, list] = {}  # gw -> [rows, rows with a seq, total seq]: ``self.seqs`` summed
        self.tails: frozenset = frozenset()
        # (gw, key) -> (tail, digest, observed_at, raw text hash)
        self.rows: dict[tuple[str, str], tuple[str, str, Any, str]] = {}
        self.gateways: dict[str, list[tuple[str, Any, float]]] = {}  # gw -> [(scope digest, observed_at, window)]
        self.meta: Optional[dict] = None
        self.ok = False
        self.rebuilds = 0
        self.increments = 0

    @staticmethod
    def _raw_hash(raw: Any) -> str:
        return hashlib.sha1(raw.encode() if isinstance(raw, str) else repr(raw).encode()).hexdigest()

    @classmethod
    def _entry(cls, key: Any, raw: Any) -> tuple[str, str, Any, str]:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = raw
        observed = None
        if isinstance(data, dict) and isinstance(data.get("origin_observation"), dict):
            observed = data["origin_observation"].get("observed_at")
        return _tail(key), digest(substantive(data)), observed, cls._raw_hash(raw)

    @staticmethod
    def _probe(conn: sqlite3.Connection) -> dict[str, tuple]:
        return {str(gw): (int(n), int(ns), int(top or 0), float(tot or 0))
                for gw, n, ns, top, tot in conn.execute(_REPLICA_PROBE)}

    def _agrees(self, probe: dict[str, tuple]) -> bool:
        """The ``(gw, key) -> seq`` map (kept as per-gateway sums) reproduces the probe aggregate of every gateway."""
        mine = {gw: (a[0], a[1], self.cursor.get(gw, 0), float(a[2])) for gw, a in self.aggs.items() if a[0]}
        return mine == probe

    def _put_seq(self, gw: str, key: str, seq: Optional[int]) -> None:
        agg = self.aggs.setdefault(gw, [0, 0, 0])
        old = self.seqs.get((gw, key), _MISSING)
        if old is _MISSING:
            agg[0] += 1
        elif old is not None:
            agg[1] -= 1
            agg[2] -= old
        if seq is not None:
            agg[1] += 1
            agg[2] += seq
        self.seqs[(gw, key)] = seq

    @staticmethod
    def _scopes(raw: Any) -> list[tuple[str, Any, float]]:
        try:
            info = json.loads(raw)
        except (TypeError, ValueError):
            return [("unreadable", None, 0.0)]
        if not isinstance(info, dict):
            return [("unreadable", None, 0.0)]
        scopes = info.get("task_scopes")
        window = _scope_window(info)
        rest = digest({k: v for k, v in info.items() if k != "task_scopes"} if "task_scopes" in info else info)
        out = [("info:" + rest, None, window)]
        for s in scopes if isinstance(scopes, list) else []:
            if isinstance(s, dict):
                out.append((digest({k: v for k, v in s.items() if k != "observed_at"}), s.get("observed_at"), window))
            else:
                out.append((digest(s), None, window))
        return sorted(out, key=lambda t: (t[0], str(t[1])))

    _ROW_SQL = "SELECT seq, data FROM replica WHERE gw = ? AND coll = 'tasks' AND key = ?"

    def _read_gateways(self, conn: sqlite3.Connection) -> None:
        self.gateways = {str(gw): self._scopes(data) for gw, data in conn.execute(
            "SELECT gw, data FROM replica WHERE coll = 'gateway' AND key = 'info'")}

    def _rebuild(self, conn: sqlite3.Connection, tails: frozenset) -> None:
        self.seqs, self.aggs, self.cursor = {}, {}, {}
        want = []
        for gw, key, seq in conn.execute("SELECT gw, key, seq FROM replica WHERE coll = 'tasks'"):
            gw, key = str(gw), str(key)
            self._put_seq(gw, key, None if seq is None else int(seq))
            self.cursor[gw] = max(self.cursor.get(gw, 0), int(seq or 0))
            if _tail(key) in tails:
                want.append((gw, key))
        rows: dict[tuple[str, str], tuple[str, str, Any, str]] = {}
        for gw, key in want:
            r = conn.execute(self._ROW_SQL, (gw, key)).fetchone()
            if r is not None:
                rows[(gw, key)] = self._entry(key, r[1])
        self.rows = rows
        self._read_gateways(conn)
        self.rebuilds += 1

    def _increment(self, conn: sqlite3.Connection) -> bool:
        """Advance to ``conn``'s snapshot; False when only a rebuild can tell what changed."""
        probe = self._probe(conn)
        if any(n != ns for n, ns, _top, _tot in probe.values()):
            return False  # a NULL seq: no cursor covers that row (core refuses to memoize it too)
        for gw in sorted(set(probe) | set(self.cursor)):
            for key, seq in conn.execute("SELECT key, seq FROM replica WHERE coll = 'tasks' AND gw = ? AND seq > ?",
                                         (gw, self.cursor.get(gw, 0))):
                key, seq = str(key), int(seq)
                self._put_seq(gw, key, seq)
                self.cursor[gw] = max(self.cursor.get(gw, 0), seq)
                if _tail(key) in self.tails and (gw, key) not in self.rows:
                    self.rows[(gw, key)] = ("", "", None, "")  # a new batch row: read below
        if not self._agrees(probe):
            return False  # a delete, a rewrite at an older seq, a seq that went back: unknown which rows
        for (gw, key), old in list(self.rows.items()):
            r = conn.execute(self._ROW_SQL, (gw, key)).fetchone()
            if r is None:
                self.rows.pop((gw, key))
            elif self._raw_hash(r[1]) != old[3]:
                self.rows[(gw, key)] = self._entry(key, r[1])  # includes a rewrite in place at the same seq
        self._read_gateways(conn)
        self.increments += 1
        return True

    def refresh(self, path: Optional[Path], tails: Iterable[str], connect: Callable[[Path], Any],
                token: Any) -> None:
        tails = frozenset(_tail(t) for t in tails if t)
        with self.lock:
            if path is None or not Path(path).is_file():
                if self.path is not None or not self.ok:
                    self.clear()
                    self.ok = True
                return
            ident = file_ident(path)
            same = (self.ok and self.path == str(path) and token is not None and self.token is not None
                    and ident is not None and ident == self.ident)
            if same and token == self.token and tails <= self.tails:
                if tails != self.tails:
                    self.tails = tails
                    self.rows = {k: v for k, v in self.rows.items() if v[0] in tails}
                return
            self.ok = False
            try:
                conn = connect(Path(path))
            except Exception:
                logger.debug("chat-closeout: replica unreadable for waiting inputs", exc_info=True)
                self.clear()
                return
            try:
                conn.execute("BEGIN")  # one snapshot: rows, gateways and meta agree
                try:
                    if not (same and tails <= self.tails and self._increment(conn)):
                        self._rebuild(conn, tails)
                    self.meta = {str(k): v for k, v in conn.execute(
                        "SELECT k, v FROM meta WHERE k IN ('last_ok', 'offline_since')").fetchall()}
                finally:
                    conn.rollback()
                self.tails = tails
                self.rows = {k: v for k, v in self.rows.items() if v[0] in tails}
                self.path, self.ident, self.token, self.ok = str(path), ident, token, True
            except Exception:
                logger.debug("chat-closeout: replica read for waiting inputs failed", exc_info=True)
                self.clear()
            finally:
                conn.close()

    def key(self, now: float, stale_after: float = HUB_STALE_AFTER_S) -> Any:
        """The batch rows' substantive digests and every clock verdict at ``now``. Raises when unreadable."""
        with self.lock:
            if not self.ok:
                raise LookupError("replica unreadable")
            if self.path is None:
                return ["no-replica"]
            rows = sorted([gw, key, d] for (gw, key), (_t, d, _o, _r) in self.rows.items())
            owners = sorted([gw, key, _fresh(o, now, REPLICA_FRESH_S)]
                            for (gw, key), (_t, _d, o, _r) in self.rows.items() if o is not None)
            scopes = sorted([gw, s, _fresh(at, now, w)] for gw, lst in self.gateways.items() for s, at, w in lst)
            meta = dict(self.meta or {})
        raw = meta.get("last_ok")
        try:
            last_ok = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            last_ok = None
        lo = last_ok or 0.0
        verdict = [meta.get("offline_since") or None, last_ok is None, 0 <= now - lo, now - lo <= REPLICA_FRESH_S,
                   last_ok is not None and now - last_ok > stale_after]
        return [digest(rows), digest(owners), digest(scopes), verdict]


# ---- state.db transcripts ---------------------------------------------------------------------------------------
class LineageWatch:
    """Per-session transcript watermarks per state.db. A db is re-queried only when its stat moved (a process-local
    gate) or new ids are asked for, on one short-lived connection (never retained, IDLE-READS.md).

    ``vouch(conn)`` (the plugin passes core's ``TRANSCRIPT_TRIGGERS`` check): False when the db's triggers cannot
    vouch that ``transcript_revision`` moves on an in-place rewrite; then, like core ``transcript_key``, there is
    no watermark (no reuse)."""

    def __init__(self, vouch: Optional[Callable[[sqlite3.Connection], bool]] = None) -> None:
        self.lock = threading.Lock()
        self.vouch = vouch
        self.dbs: dict[str, tuple[Optional[tuple], frozenset, dict[str, tuple]]] = {}  # path -> (stat, ids, rows)
        self.queries = 0

    def clear(self) -> None:
        with self.lock:
            self.dbs.clear()

    def _query(self, path: Path, ids: list[str], connect: Callable[[Path], Any]) -> dict[str, tuple]:
        """``connect(path)``: a context manager yielding a read-only connection (the plugin's ``_ro``: the running
        pass's pooled one, else a fresh one closed after use)."""
        with connect(path) as conn:
            return self._read(conn, ids)

    def _read(self, conn: sqlite3.Connection, ids: list[str]) -> dict[str, tuple]:
        mcols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        scols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "id" not in scols:
            return {}
        rev = "s.transcript_revision" if "transcript_revision" in scols else "NULL"
        end = "s.end_reason" if "end_reason" in scols else "NULL"
        kids = ("(SELECT COUNT(*) FROM sessions c WHERE c.parent_session_id = s.id)"
                if "parent_session_id" in scols else "NULL")
        if "session_id" in mcols:
            top = "(SELECT MAX(m.id) FROM messages m WHERE m.session_id = s.id)"
            if "active" in mcols:
                # Core ``transcript_key``'s terms per session, index-only (idx_messages_session_active): an active
                # flip or delete below the max moves the count and id sum, a timestamp repair (no trigger covers
                # ``timestamp``) the timestamp sum.
                act = ("(SELECT json_array(MAX(m.id), COUNT(*), TOTAL(m.id), TOTAL(m.timestamp)) FROM messages m "
                       "WHERE m.session_id = s.id AND m.active = 1)")
            else:
                act = "NULL"
        else:
            top = act = "NULL"
        out: dict[str, tuple] = {}
        began = not conn.in_transaction
        if began:
            conn.execute("BEGIN")  # one snapshot for every id
        try:
            if self.vouch is not None and ("transcript_revision" not in scols or not self.vouch(conn)):
                raise Unvouched("state.db cannot vouch for in-place message rewrites")
            for i in range(0, len(ids), 300):
                chunk = ids[i:i + 300]
                for r in conn.execute(f"SELECT s.id, {top}, {act}, {rev}, {end}, {kids} FROM sessions s "
                                      f"WHERE s.id IN ({','.join('?' for _ in chunk)})", chunk):
                    out[str(r[0])] = tuple(r[1:])
        finally:
            if began:
                conn.rollback()  # read-only: no transaction outlives this read
        self.queries += 1
        return out

    def watermarks(self, paths: Iterable[Path], ids: Iterable[str],
                   connect: Callable[[Path], Any]) -> Optional[dict[str, tuple]]:
        """``{session id: ((db name, watermark), ...)}`` for the ids found; None when a state.db can't be read."""
        want = frozenset(str(i) for i in ids if i)
        paths = [Path(p) for p in paths]
        found: dict[str, list] = {}
        with self.lock:
            for path in paths:
                key = str(path)
                st = file_stat(path)
                hit = self.dbs.get(key)
                if hit is not None and hit[0] == st and want <= hit[1]:
                    rows = hit[2]
                else:
                    try:
                        rows = self._query(path, sorted(want), connect) if want else {}
                    except (sqlite3.Error, OSError, Unvouched):
                        logger.debug("chat-closeout: lineage watermark of %s unavailable", path, exc_info=True)
                        self.dbs.pop(key, None)
                        return None
                    if file_stat(path) == st:
                        self.dbs[key] = (st, want, rows)
                    else:
                        self.dbs.pop(key, None)  # moved while read: labels nothing, read again next time
                name = path.parent.name if path.parent.parent.name == "profiles" else "default"
                for sid in want:
                    if sid in rows:
                        found.setdefault(sid, []).append([name, list(rows[sid])])
            live = {str(p) for p in paths}
            for key in [k for k in self.dbs if k not in live]:
                self.dbs.pop(key, None)
        return {sid: tuple(tuple(x) for x in v) for sid, v in found.items()}


def lineage_key(marks: Optional[dict[str, tuple]], ids: Iterable[str]) -> Optional[list]:
    """The watermark of one lineage (its ids in order), None when unreadable."""
    if marks is None:
        return None
    return [[i, [list(x) for x in marks.get(i, ())]] for i in dict.fromkeys(i for i in ids if i)]


# ---- per-row cache --------------------------------------------------------------------------------------------
def card_view(cards: Any, keys: Iterable[str]) -> Any:
    """What a read says about ``keys``: each card as read plus the read's uncertainty about them."""
    keys = sorted(set(keys))
    return [[k, (cards or {}).get(k)] for k in keys] + [
        sorted(set(getattr(cards, "uncertain", ())) & set(keys)),
        sorted(set(getattr(cards, "absence_veto", ())) & set(keys)),
        bool(getattr(cards, "readable", True))]


def row_key(row: dict, cards: Any, lineage: Optional[list], fields: Optional[Iterable[str]] = None,
            extra: Any = None) -> Optional[str]:
    """Everything one row's settlement reads: its fields (``None``: the whole row), its batch and covered cards as
    read, and its lineage's transcript watermark. None (never reused) when the watermark is unknown."""
    if lineage is None:
        return None
    keys = set(row.get("batch") or []) | set(row.get("covered") or [])
    own = sorted(row.items()) if fields is None else [[f, row.get(f)] for f in fields]
    return digest([own, card_view(cards, keys), lineage, extra])


class RowCache:
    """``{session id: (row key, result)}``: the last outcome of each row and the inputs it was computed from."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rows: dict[str, tuple[str, Any]] = {}

    def clear(self) -> None:
        with self.lock:
            self.rows.clear()

    def get(self, sid: str, key: Optional[str]) -> Optional[tuple[str, Any]]:
        if key is None:
            return None
        with self.lock:
            hit = self.rows.get(sid)
        return hit if hit is not None and hit[0] == key else None

    def put(self, sid: str, key: Optional[str], result: Any) -> None:
        with self.lock:
            if key is None:
                self.rows.pop(sid, None)
            else:
                self.rows[sid] = (key, result)

    def keep(self, sids: Iterable[str]) -> None:
        keep = set(sids)
        with self.lock:
            for sid in [s for s in self.rows if s not in keep]:
                self.rows.pop(sid)
