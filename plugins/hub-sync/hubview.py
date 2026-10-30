"""Read-only views over the local hermes-hub replica (hub.db), plus queuing owner requests.

hermes-hub (github.com/daveey/hermes-hub, client ``hermes_hub.py``) keeps a local SQLite file on every gateway:
``published`` = this gateway's own records, ``replica`` = every gateway's records as last pulled from the hub,
``sent`` = owner requests this gateway made. This module turns that file into what the Desktop needs:

- merged rows (own records from ``published``, everyone else's from ``replica``), each tagged with ``gateway``,
  ``local``, ``version``, ``key``, ``stale`` / ``synced_at`` (replica rows) and the requests still ``pending`` on it;
- ``where`` (session -> owning gateway/profile) and ``session_links`` (Kanban cards on any gateway linked to a
  session lineage);
- ``request``: validate an owner request, queue it in ``sent`` (works offline) and try to send it.

Nothing here needs the hub to be up: every read is local. Without the client or the db, every view answers
``available: false`` with empty lists, so callers never have to special-case an install without hermes-hub.

Paths: client ``$HERMES_HUB_CLIENT`` or ``~/.local/share/hermes-mail/hermes_hub.py``; db ``$HERMES_HUB_STATE/hub.db``
(default ``~/.local/state/hermes-hub/hub.db``), the same resolution the client uses.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_CLIENT = Path.home() / ".local" / "share" / "hermes-mail" / "hermes_hub.py"
# The poller syncs every 60 s; a pull older than this means the replica may be behind the hub.
STALE_AFTER_S = 180
SEND_WAIT_S = 4.0  # how long POST /request waits for the hub before answering "queued locally"
PENDING_STATES = ("local", "queued")

# coll -> action -> {arg: required?}. Mirrors hermes_hub.apply_request (the owner refuses anything else anyway).
ACTIONS: Dict[str, Dict[str, Dict[str, bool]]] = {
    "sessions": {"rename": {"title": True}, "archive": {}, "unarchive": {}, "pin": {}, "unpin": {}},
    "inbox": {"act": {"action": True, "input": False}, "archive": {}},
    "tasks": {"create": {"title": True, "body": False, "assignee": False}, "comment": {"text": True},
              "complete": {"summary": False}, "block": {"reason": False}, "unblock": {"reason": False},
              "archive": {}, "assign": {"profile": True}},
    # Phase 6b: a project changes from another gateway only as a shared field (primary); "" clears icon/color.
    "projects": {"rename": {"name": True}, "icon": {"icon": False}, "color": {"color": False}},
}
# Args kept verbatim (an empty string is a value, not a missing arg).
RAW_ARGS = ("input", "icon", "color")


def _now() -> float:
    return time.time()


class RequestError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------- client + db

def client_path() -> Path:
    return Path(os.environ.get("HERMES_HUB_CLIENT") or DEFAULT_CLIENT).expanduser()


def load_client():
    """The hermes_hub module, or None when it isn't installed (or fails to import)."""
    p = client_path()
    if not p.is_file():
        return None
    try:
        key = "_hermes_hub_client_%s" % int(p.stat().st_mtime_ns)
    except OSError:
        return None
    mod = sys.modules.get(key)
    if mod is not None:
        return mod
    try:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001 - a broken client must not break the dashboard
        return None
    return sys.modules.setdefault(key, mod)  # register only after exec_module (threadpool readers)


def db_path() -> Path:
    base = os.environ.get("HERMES_HUB_STATE") or str(Path.home() / ".local" / "state" / "hermes-hub")
    return Path(base).expanduser() / "hub.db"


def ro_connect(path: Path) -> Optional[sqlite3.Connection]:
    """Open hub.db for reading without ever creating it (rw + query_only works with a WAL db missing -wal/-shm)."""
    if not path.is_file():
        return None
    try:
        c = sqlite3.connect("file:%s?mode=rw" % urllib.parse.quote(str(path)), uri=True, timeout=5)
        c.execute("PRAGMA query_only = ON")
        c.row_factory = sqlite3.Row
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return c
    except sqlite3.Error:
        return None


def _has(conn, table) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _meta(conn, k, d=None):
    if not _has(conn, "meta"):
        return d
    r = conn.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return d if not r or r[0] in (None, "None") else r[0]


# Phase 6 shared fields (hermes-hub client >= 1.5.0, config "shared_fields"): fields any gateway sets directly on the
# hub, merged per field by HLC. Mirrors hermes_hub.SHARED / overlay(); read here so an older client keeps working.
SHARED = {"sessions": ("title", "pinned", "archived", "hidden"), "inbox": ("archived",),
          "projects": ("name", "icon", "color")}


def shared_mode(conn, coll) -> str:
    try:
        raw = json.loads(_meta(conn, "shared_fields") or '"off"')
    except ValueError:
        return "off"
    v = raw.get(coll, "off") if isinstance(raw, dict) else raw
    return v if v in ("off", "shadow", "primary") else "off"


def _float(v):
    try:
        return None if v in (None, "", "None") else float(v)
    except (TypeError, ValueError):
        return None


# Decoded `published`/`replica` rows per collection, reused while hub.db and its WAL are unchanged. Desktop polls
# sessions/tasks every few seconds and the replica holds ~16k rows (~40 MB of JSON): decoding all of it per request
# kept the Desktop backend busy. Keyed by file identity (mtime_ns, size) of hub.db and hub.db-wal; every commit moves
# one of them. Rows handed out are shallow copies, and nothing here mutates a cached row's nested values.
_DECODED: Dict[tuple, tuple] = {}
_DECODED_LOCK = threading.Lock()
_SEEN: Dict[str, tuple] = {}  # hub.db path -> (file signature, owner_seen map); see View.owner_seen


def _file_sig(path: Path) -> tuple:
    out = []
    for p in (path, Path(str(path) + "-wal")):
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size, st.st_ino))
        except OSError:
            out.append(None)
    return tuple(out)


def _has_seq(conn) -> bool:
    try:
        return any(r[1] == "seq" for r in conn.execute("PRAGMA table_info(replica)"))
    except sqlite3.Error:
        return False


class View:
    """One read of hub.db: everything a request needs, then closed."""

    def __init__(self, conn: sqlite3.Connection, now: Optional[float] = None, path: Optional[Path] = None):
        self.conn = conn
        self.path = path  # set by open_view: enables the decoded-row cache (a bare conn reads fresh every time)
        self.now = _now() if now is None else now
        self.gateway = _meta(conn, "gateway")
        self.last_ok = _float(_meta(conn, "last_ok"))
        self.offline_since = _float(_meta(conn, "offline_since"))
        self._seen = None
        self._pending = None
        self._overrides = {}

    # when did each owner last publish anything we pulled (seconds)
    def owner_seen(self) -> Dict[str, float]:
        """Memoized per hub.db file signature: the GROUP BY reads every replica row's overflow chain (``updated_at``
        follows the large ``data`` column), ~20 ms a call, and each request opened a new View and paid it. [daveey]"""
        if self._seen is None:
            sig = _file_sig(self.path) if self.path is not None else None
            with _DECODED_LOCK:
                hit = _SEEN.get(str(self.path)) if sig is not None else None
            if hit is not None and hit[0] == sig:
                self._seen = hit[1]
            else:
                self._seen = {r[0]: (r[1] or 0) / 1000.0 for r in
                              self.conn.execute("SELECT gw, MAX(updated_at) FROM replica GROUP BY gw")} \
                    if _has(self.conn, "replica") else {}
                if sig is not None:
                    with _DECODED_LOCK:
                        _SEEN[str(self.path)] = (sig, self._seen)
        return self._seen

    def hub_stale(self) -> bool:
        return self.last_ok is None or self.now - self.last_ok > STALE_AFTER_S

    def synced_at(self, gw: str) -> Optional[float]:
        """Newest moment we know gw's state for: its last publish we pulled, capped by our last successful pull."""
        seen = self.owner_seen().get(gw)
        if seen is None:
            return self.last_ok
        return min(seen, self.last_ok) if self.last_ok else seen

    def pending(self) -> Dict[tuple, List[dict]]:
        if self._pending is None:
            out: Dict[tuple, List[dict]] = {}
            if _has(self.conn, "sent"):
                q = "SELECT id, body, state, created_at FROM sent WHERE state IN (%s) ORDER BY created_at" % \
                    ",".join("?" * len(PENDING_STATES))
                for r in self.conn.execute(q, PENDING_STATES):
                    try:
                        b = json.loads(r["body"])
                    except (TypeError, ValueError):
                        continue
                    out.setdefault((b.get("to_gw"), b.get("coll"), b.get("key")), []).append(
                        {"id": r["id"], "action": b.get("action"), "args": b.get("args") or {}, "state": r["state"],
                         "created_at": r["created_at"]})
            self._pending = out
        return self._pending

    def overrides(self, coll: str) -> Dict[tuple, Dict[str, dict]]:
        """(gw, key) -> field -> {value, by, hlc} for this collection, when its shared fields are in primary mode."""
        if coll not in self._overrides:
            out: Dict[tuple, Dict[str, dict]] = {}
            if coll in SHARED and shared_mode(self.conn, coll) == "primary" and _has(self.conn, "overrides"):
                for r in self.conn.execute("SELECT gw, key, field, value, hlc, by_gw FROM overrides WHERE coll = ? AND value IS NOT NULL",
                                           (coll,)):
                    if r["field"] in SHARED[coll] or r["field"] == "pending_action":
                        out.setdefault((r["gw"], r["key"]), {})[r["field"]] = {"value": _json(r["value"]), "by": r["by_gw"], "hlc": r["hlc"]}
            self._overrides[coll] = out
        return self._overrides[coll]

    def _overlay(self, coll: str, d: dict) -> dict:
        ovr = self.overrides(coll).get((d["gateway"], d["key"]))
        if not ovr:
            return d
        for field, o in ovr.items():
            if field == "pending_action":
                d["pending_action"] = o["value"]
                continue
            if d.get(field) != o["value"]:
                # set on the hub, not yet applied by the owner (asleep or behind): shown, and marked as such
                d["shared"] = {**(d.get("shared") or {}), field: {"by": o["by"], "owner_value": d.get(field)}}
            d[field] = o["value"]
        if coll == "inbox" and d.get("archived") is True and d.get("state") in ("open", "queuing"):
            d["state"] = "archived"
        return d

    def _decoded(self, table: str, coll: str) -> list:
        """``[(row, data dict)]`` for one table and collection; data dicts are shared with the cache (copy them)."""
        if table == "published":
            sql, args = "SELECT key, data, version FROM published WHERE coll = ?", (coll,)
        else:
            sql, args = ("SELECT gw, key, data, version, updated_at FROM replica WHERE coll = ? AND gw IS NOT ?",
                         (coll, self.gateway))
        key = (str(self.path), table, coll, self.gateway)
        sig = _file_sig(self.path) if self.path is not None else None
        hit = None
        if sig is not None:
            with _DECODED_LOCK:
                hit = _DECODED.get(key)
            if hit is not None and hit[0] == sig:
                return hit[1]
            if table == "replica" and _has_seq(self.conn):
                return self._decoded_replica_incremental(key, sig, coll, hit)
        # Local staging can rewrite published rows without changing version/seq.
        # Reuse only byte-identical JSON, not a high-water mark. Unrelated replica
        # commits used to re-decode every local task (including large handoffs).
        previous = {r["key"]: (r["data"], d) for r, d in hit[1]} if table == "published" and hit else {}
        out = []
        for r in self.conn.execute(sql, args):
            old = previous.get(r["key"])
            d = old[1] if old is not None and old[0] == r["data"] else _loads(r["data"])
            if d is not None:
                out.append((dict(r), d))
        if sig is not None:
            with _DECODED_LOCK:
                _DECODED[key] = (sig, out)
        return out

    def _decoded_replica_incremental(self, key: tuple, sig: tuple, coll: str, hit: Optional[tuple]) -> list:
        """``_decoded("replica", coll)`` decoding only rows written since the cached copy. Any hub.db commit moves the
        file signature, and hub-sync commits several times a minute (mostly other collections), so the plain cache
        re-decoded ~10k session rows of JSON on nearly every Desktop poll. ``seq`` is the hub's change-feed position
        and an update rewrites it, so rows past the cached high-water mark are exactly the new and changed ones;
        COUNT and TOTAL(seq) against the cached rows catch deletions and resyncs, which reload in full. Contract: a
        writer that changes a row's data also moves its seq, as every hermes_hub.py upsert does. [daveey]"""
        where, args = "coll = ? AND gw IS NOT ?", (coll, self.gateway)
        count, total = self.conn.execute(
            "SELECT COUNT(*), TOTAL(seq) FROM replica WHERE " + where, args).fetchone()
        state = hit[2] if hit is not None and len(hit) > 2 else None
        if state is None:
            by_key, high, seqs = {}, None, {}
            new = self.conn.execute(
                "SELECT gw, key, data, version, updated_at, seq FROM replica WHERE " + where, args)
        else:
            by_key, high, seqs = dict(state[0]), state[1], dict(state[2])
            new = self.conn.execute(
                "SELECT gw, key, data, version, updated_at, seq FROM replica WHERE " + where + " AND seq > ?",
                args + (high if high is not None else -1,))
        for r in new:
            row = {k: r[k] for k in ("gw", "key", "data", "version", "updated_at")}
            ident = (r["gw"], r["key"])
            seqs[ident] = r["seq"] or 0
            d = _loads(r["data"])
            if d is None:
                by_key.pop(ident, None)
            else:
                by_key[ident] = (row, d)
            high = r["seq"] if high is None or (r["seq"] or 0) > high else high
        if len(seqs) != count or abs(sum(seqs.values()) - (total or 0)) > 0.5:
            if state is None:  # a full read that still disagrees: the table moved under us; serve it uncached
                return list(by_key.values())
            return self._decoded_replica_incremental(key, sig, coll, None)
        out = list(by_key.values())
        with _DECODED_LOCK:
            _DECODED[key] = (sig, out, (by_key, high, seqs))
        return out

    def rows(self, coll: str) -> List[dict]:
        """Own records from `published`, other gateways' from `replica` (never own rows twice), with shared-field
        overrides on top when the collection is in primary mode."""
        out = []
        pend = self.pending()
        if _has(self.conn, "published"):
            for r, data in self._decoded("published", coll):
                d = dict(data)
                d.update(gateway=self.gateway, key=r["key"], version=r["version"], local=True, stale=False,
                         synced_at=self.now, pending=pend.get((self.gateway, coll, r["key"]), []))
                out.append(d)
        if _has(self.conn, "replica"):
            stale = self.hub_stale()
            synced = {}  # per gateway, not per row (~16k rows, a handful of gateways)
            for r, data in self._decoded("replica", coll):
                d = dict(data)
                gw = r["gw"]
                if gw not in synced:
                    synced[gw] = self.synced_at(gw)
                d.update(gateway=gw, key=r["key"], version=r["version"], local=False, stale=stale,
                         synced_at=synced[gw], updated_at_hub=(r["updated_at"] or 0) / 1000.0,
                         pending=pend.get((r["gw"], coll, r["key"]), []))
                out.append(d)
        return [self._overlay(coll, d) for d in out]

    def gateways(self) -> List[dict]:
        names = set(self.owner_seen())
        if self.gateway:
            names.add(self.gateway)
        info = {r["gateway"]: r for r in self.rows("gateway")}
        out = []
        for g in sorted(names):
            i = info.get(g) or {}
            out.append({"gateway": g, "local": g == self.gateway, "host": i.get("host"), "os": i.get("os"),
                        "profiles": i.get("profiles") or [], "boards": i.get("boards") or [],
                        "seen_at": self.now if g == self.gateway else self.owner_seen().get(g),
                        "synced_at": self.now if g == self.gateway else self.synced_at(g)})
        return out


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _loads(text):
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def open_view(now: Optional[float] = None) -> Optional[View]:
    if load_client() is None:
        return None
    conn = ro_connect(db_path())
    if conn is None:
        return None
    return View(conn, now, path=db_path())


def _unavailable(**empty) -> dict:
    out = {"available": False, "gateway": None, "client": str(client_path()),
           "installed": client_path().is_file(), "db": str(db_path())}
    out.update(empty)
    return out


def _with_view(fn, **empty):
    v = open_view()
    if v is None:
        return _unavailable(**empty)
    try:
        res = fn(v)
    except sqlite3.Error as e:
        return dict(_unavailable(**empty), error="hub.db: %s" % e)
    finally:
        v.conn.close()
    base = {"available": True, "gateway": v.gateway, "synced_at": v.last_ok, "stale": v.hub_stale(),
            "offline_since": v.offline_since}
    base.update(res)
    return base


# ---------------------------------------------------------------- views

def status() -> dict:
    def fn(v: View):
        c = v.conn
        cnt = (lambda q: c.execute(q).fetchone()[0])
        return {"version": getattr(load_client(), "VERSION", None), "last_ok": v.last_ok,
                "last_error": _meta(c, "last_error"), "cursor": int(_float(_meta(c, "cursor")) or 0),
                "head": _meta(c, "head"), "stale_after_s": STALE_AFTER_S,
                "outbox": cnt("SELECT COUNT(*) FROM outbox") if _has(c, "outbox") else 0,
                "requests_waiting_to_send": cnt("SELECT COUNT(*) FROM sent WHERE state = 'local'") if _has(c, "sent") else 0,
                "requests_pending": sum(len(x) for x in v.pending().values()),
                "gateways": v.gateways()}
    return _with_view(fn, gateways=[])


def _sort_activity(rows):
    rows.sort(key=lambda r: -(r.get("activity") or r.get("started_at") or 0))
    return rows


def _gw_filter(rows, gateway: Optional[str], exclude: Iterable[str]):
    ex = {g for g in exclude if g}
    return [r for r in rows if (not gateway or r["gateway"] == gateway) and r["gateway"] not in ex]


def sessions(gateway: Optional[str] = None, exclude: Iterable[str] = (), include_archived: bool = False,
             limit: int = 500) -> dict:
    def fn(v: View):
        rows = [r for r in _gw_filter(v.rows("sessions"), gateway, exclude)
                if include_archived or not r.get("archived")]
        return {"sessions": _sort_activity(rows)[:max(1, min(limit, 5000))]}
    return _with_view(fn, sessions=[])


def inbox(gateway: Optional[str] = None, exclude: Iterable[str] = (), include_closed: bool = False) -> dict:
    def fn(v: View):
        rows = [r for r in _gw_filter(v.rows("inbox"), gateway, exclude)
                if include_closed or r.get("state") in ("open", "queuing")]
        rows.sort(key=lambda r: -(r.get("updated_at") or r.get("created_at") or 0))
        return {"items": rows}
    return _with_view(fn, items=[])


def tasks(gateway: Optional[str] = None, exclude: Iterable[str] = (), board: Optional[str] = None,
          include_closed: bool = False) -> dict:
    def fn(v: View):
        rows = [r for r in _gw_filter(v.rows("tasks"), gateway, exclude)
                if (not board or r.get("board") == board)
                and (include_closed or r.get("status") not in ("done", "archived"))]
        rows.sort(key=lambda r: (-(r.get("priority") or 0), r.get("created_at") or 0))
        return {"tasks": rows}
    return _with_view(fn, tasks=[])


def _project_path_key(node: dict) -> str:
    """Folder identity for merging one gateway's profiles (as the REST route's ``_merge_profile_tree`` does)."""
    p = str(node.get("path") or node.get("id") or "").replace("\\", "/").rstrip("/")
    return p.lower() if len(p) > 1 and p[1] == ":" else p


def _project_wire(n: dict, previews: List[dict], ids: List[str]) -> dict:
    explicit = n.get("kind") == "explicit"
    node = {"id": n.get("id"), "label": (n.get("name") if explicit else None) or n.get("label") or n.get("id"),
            "path": n.get("path"), "color": n.get("color") or None, "icon": n.get("icon") or None,
            "isAuto": bool(n.get("isAuto")), "isNoProject": bool(n.get("isNoProject")),
            "sessionCount": n.get("sessionCount") or 0, "lastActive": n.get("lastActive") or 0,
            "totalTokens": n.get("totalTokens") or 0, "totalCostUsd": n.get("totalCostUsd") or 0,
            "repos": [dict(r, groups=[dict(g, sessions=[]) for g in r.get("groups") or []]) for r in n.get("repos") or []],
            "previewSessions": previews, "sessionIds": ids,
            "members": [{"gateway": n["gateway"], "profile": n.get("profile"), "id": n.get("id"), "key": n["key"],
                         "kind": n.get("kind")}]}
    if n.get("shared"):
        node["shared"] = n["shared"]
    return node


def _merge_project(into: dict, other: dict, preview_limit: int) -> dict:
    if into["isAuto"] and not other["isAuto"]:  # a declared project's label/color/icon win the identity
        into, other = other, into
    repos = {r["id"]: r for r in into["repos"]}
    for r in other["repos"]:
        if r["id"] in repos:
            groups = {g["id"]: g for g in repos[r["id"]].get("groups") or []}
            for g in r.get("groups") or []:
                groups.setdefault(g["id"], g)
            repos[r["id"]] = dict(repos[r["id"]], groups=list(groups.values()),
                                  sessionCount=(repos[r["id"]].get("sessionCount") or 0) + (r.get("sessionCount") or 0))
        else:
            repos[r["id"]] = r
    into["repos"] = list(repos.values())
    for k in ("sessionCount", "totalTokens", "totalCostUsd"):
        into[k] = (into.get(k) or 0) + (other.get(k) or 0)
    into["lastActive"] = max(into.get("lastActive") or 0, other.get("lastActive") or 0)
    into["previewSessions"] = _sort_activity(into["previewSessions"] + other["previewSessions"])[:preview_limit]
    into["sessionIds"] = into["sessionIds"] + other["sessionIds"]
    into["members"] = into["members"] + other["members"]
    return into


def projects(gateway: Optional[str] = None, exclude: Iterable[str] = (), preview_limit: int = 3) -> dict:
    """Each gateway's project tree from the replica (hermes-hub >= 1.9.0): its published tree nodes with shared
    name/icon/color on top, previews and sessionIds from its session records (stamped ``project``), profiles merged
    by folder. ``{"gateways": {gw: {"projects": [...], "scoped_session_ids": [...], "synced_at"}}}``; a node's
    ``members`` name every (gateway, profile, id) it stands for, so a rename can target each explicit one."""
    def fn(v: View):
        nodes = _gw_filter(v.rows("projects"), gateway, exclude)
        gws = {n["gateway"] for n in nodes}
        by_node: Dict[tuple, List[dict]] = {}
        rows: Dict[tuple, dict] = {}
        for s in v.rows("sessions"):
            if s["gateway"] not in gws or s.get("archived") or s.get("hidden"):
                continue
            rows[(s["gateway"], s.get("profile"), s.get("id"))] = s
            if s.get("project"):
                by_node.setdefault((s["gateway"], s.get("profile"), s["project"]), []).append(s)
        out: Dict[str, dict] = {}
        for n in nodes:
            gw, profile = n["gateway"], n.get("profile")
            node_key = n["key"].split("/", 1)[1]
            claimed = by_node.get((gw, profile, node_key), [])
            previews = [rows[(gw, profile, i)] for i in n.get("previewIds") or [] if (gw, profile, i) in rows]
            wire = _project_wire(n, previews[:preview_limit], [s["id"] for s in claimed])
            merged = out.setdefault(gw, {"by_path": {}, "synced_at": v.synced_at(gw) if gw != v.gateway else v.now})
            k = _project_path_key(wire)
            merged["by_path"][k] = _merge_project(merged["by_path"][k], wire, preview_limit) if k in merged["by_path"] else wire
        result = {}
        for gw, m in out.items():
            tree = sorted(m["by_path"].values(), key=lambda p: -(p.get("lastActive") or 0))
            result[gw] = {"projects": tree, "scoped_session_ids": [i for p in tree for i in p["sessionIds"]],
                          "synced_at": m["synced_at"]}
        return {"gateways": result}
    return _with_view(fn, gateways={})


def where(session_id: str) -> dict:
    def fn(v: View):
        hits = []
        for s in v.rows("sessions"):
            if s.get("id") == session_id or str(s.get("key") or "").endswith("/" + session_id):
                hits.append({"gateway": s["gateway"], "profile": s.get("profile"), "session": s.get("id"),
                             "ref": "@session:%s@%s/%s" % (s.get("profile"), s["gateway"], s.get("id")),
                             "title": s.get("title"), "local": s["local"], "stale": s["stale"],
                             "synced_at": s["synced_at"], "archived": bool(s.get("archived"))})
        hits.sort(key=lambda h: (not h["local"], h["gateway"] or ""))
        return {"session_id": session_id, "hits": hits}
    return _with_view(fn, session_id=session_id, hits=[])


LINK_STATUSES = ("todo", "ready", "running", "scheduled", "blocked", "review", "triage")


def session_links(session_ids: List[str], include_closed: bool = False,
                  origin_gateway: Optional[str] = None, origin_profile: Optional[str] = None) -> dict:
    """Cards on any gateway that one of these (lineage) session ids created or subscribed to."""
    wanted = [s for s in dict.fromkeys(session_ids) if s][:50]

    def fn(v: View):
        out = []
        for t in v.rows("tasks"):
            if not include_closed and t.get("status") not in LINK_STATUSES:
                continue
            candidates = list(t.get("sessions") or [])
            for binding in t.get("origin_bindings") or []:
                origin = binding.get("origin") if isinstance(binding, dict) else None
                if isinstance(origin, dict):
                    candidates.append(dict(origin, gw=origin.get("gateway"), rel="created"))
            links = []
            qualified_origin = False
            for link in candidates:
                if not isinstance(link, dict) or link.get("session") not in wanted:
                    continue
                gw, profile = link.get("gw") or link.get("gateway"), link.get("profile")
                if origin_profile:
                    if (gw and gw != (origin_gateway or v.gateway)) or (profile and profile != origin_profile):
                        continue
                    qualified_origin |= bool(gw and profile)
                links.append(link)
            if not links:
                continue
            parts = str(t.get("key") or "").split("/")
            owner_profile = parts[0] if len(parts) == 3 and all(parts) else None
            qualified = bool(owner_profile and qualified_origin)
            via = "created" if any(l.get("rel") == "created" for l in links) else "subscribed"
            run = t.get("run") or {}
            out.append({"id": t.get("id"), "title": t.get("title"),
                        "status": t.get("status") if qualified else "unknown",
                        "reported_status": t.get("status"), "profile": owner_profile if qualified else None,
                        "ownership_state": "qualified" if qualified else "unknown",
                        "assignee": t.get("assignee"), "board": t.get("board") or "default",
                        "block_kind": t.get("block_kind"), "human_request": t.get("human_request"),
                        "session_id": links[0].get("session"), "via": via, "created_at": t.get("created_at"),
                        "started_at": t.get("started_at"),
                        "current_run_started_at": run.get("started_at") if run.get("status") == "running" else None,
                        "gateway": t["gateway"], "local": t["local"], "stale": t["stale"],
                        "synced_at": t["synced_at"], "version": t["version"], "pending": t["pending"]})
        out.sort(key=lambda x: (x["local"], -(x.get("created_at") or 0)))
        return {"session_ids": wanted, "origin_gateway": origin_gateway or v.gateway,
                "origin_profile": origin_profile, "qualified_origins": True, "tasks": out}
    return _with_view(fn, session_ids=wanted, tasks=[])


def requests(limit: int = 50) -> dict:
    def fn(v: View):
        rows = []
        if _has(v.conn, "sent"):
            for r in v.conn.execute("SELECT * FROM sent ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)):
                b = _loads(r["body"]) or {}
                rows.append({"id": r["id"], "gateway": b.get("to_gw"), "coll": b.get("coll"), "key": b.get("key"),
                             "action": b.get("action"), "args": b.get("args") or {}, "state": r["state"],
                             "result": _loads(r["result"]) if r["result"] else None,
                             "created_at": r["created_at"], "done_at": r["done_at"]})
        return {"requests": rows}
    return _with_view(fn, requests=[])


# ---------------------------------------------------------------- requests (the only write)

def validate(body: Dict[str, Any], own_gateway: Optional[str], known: Iterable[str]) -> dict:
    gw = str(body.get("gateway") or body.get("to_gw") or "").strip()
    coll = str(body.get("coll") or "").strip()
    key = str(body.get("key") or "").strip()
    action = str(body.get("action") or "").strip()
    args = body.get("args") or {}
    if not isinstance(args, dict):
        raise RequestError(400, "args must be an object")
    if not gw:
        raise RequestError(400, "gateway required")
    if own_gateway and gw == own_gateway:
        raise RequestError(400, "%s is this gateway; change its records directly, not through the hub" % gw)
    if gw not in set(known):
        raise RequestError(404, "unknown gateway %r (not in the hub replica)" % gw)
    if coll not in ACTIONS:
        raise RequestError(400, "coll must be one of %s" % ", ".join(ACTIONS))
    spec = ACTIONS[coll].get(action)
    if spec is None:
        raise RequestError(400, "%s supports %s, not %r" % (coll, ", ".join(ACTIONS[coll]), action))
    a, _, b = key.partition("/")
    if not a or not b or ".." in key:
        raise RequestError(400, "key must be <profile-or-board>/<id>")
    if coll == "tasks" and action == "create":
        parts = key.split("/")
        if parts[-1] != "new" or len(parts) not in (2, 3) or not all(parts):
            raise RequestError(400, "tasks create uses key <profile>/<board>/new (or <board>/new); "
                                    "other task actions need a task id")
        if len(parts) == 2:  # the owner gateway decodes <profile>/<board>/new only
            key = "default/" + key
    elif coll == "tasks" and b == "new":
        raise RequestError(400, "tasks create uses key <profile>/<board>/new (or <board>/new); "
                                "other task actions need a task id")
    clean = {}
    for k, req in spec.items():
        v = args.get(k)
        if v is None or (isinstance(v, str) and not v.strip() and k not in RAW_ARGS):
            if req:
                raise RequestError(400, "%s %s needs %s" % (coll, action, k))
            continue
        clean[k] = v if k in RAW_ARGS else str(v)
    extra = set(args) - set(spec)
    if extra:
        raise RequestError(400, "unexpected args: %s" % ", ".join(sorted(extra)))
    base = body.get("base_version")
    if base is not None:
        try:
            base = int(base)
        except (TypeError, ValueError):
            raise RequestError(400, "base_version must be an integer")
    return {"gateway": gw, "coll": coll, "key": key, "action": action, "args": clean, "base_version": base}


def _send_now(client, path: Path, wait_s: float) -> Optional[bool]:
    """Try to hand queued requests to the hub. True sent, False hub unreachable/no key, None still trying."""
    result: Dict[str, Any] = {}

    def run():
        db = None
        try:
            cfg = client.load_config()
            api = client.Api(cfg["hub_url"], client.read_key())
            db = client.open_db(path)
            client.send_queued(db, api)
            if hasattr(client, "push_shared") and client.shared_any_on(db) and client.shared_supported(db, api, time.time()):
                client.push_shared(db, api)
            result["ok"] = True
        except BaseException as e:  # noqa: BLE001 - Offline, SystemExit (no key), anything: stays queued
            result["ok"] = False
            result["error"] = str(e)
        finally:
            if db is not None:
                db.close()

    t = threading.Thread(target=run, name="hub-sync-send", daemon=True)
    t.start()
    t.join(wait_s)
    return result.get("ok") if "ok" in result else None


def request(body: Dict[str, Any], wait_s: float = SEND_WAIT_S) -> dict:
    client = load_client()
    if client is None:
        raise RequestError(503, "hermes-hub is not installed on this gateway")
    path = db_path()
    if not path.is_file():
        raise RequestError(503, "hermes-hub has no local state yet (run `hermes-hub sync`)")
    v = open_view()
    if v is None:
        raise RequestError(503, "hub.db unreadable")
    try:
        own = v.gateway
        known = [g["gateway"] for g in v.gateways()]
        if not own:
            raise RequestError(503, "this gateway has no hub name yet (run `hermes-hub sync`)")
        req = validate(body, own, known)  # inbox: callers pass the version they displayed as base_version
        if req["coll"] == "projects" and shared_mode(v.conn, "projects") != "primary":
            raise RequestError(409, "projects on other gateways change only with shared_fields.projects primary")
    finally:
        v.conn.close()
    db = client.open_db(path)
    try:
        rid = client.queue_request(db, req["gateway"], req["coll"], req["key"], req["action"], req["args"],
                                   req["base_version"])
        row = db.execute("SELECT state, result FROM sent WHERE id = ?", (rid,)).fetchone()
        shared = bool(row) and row["state"] == "done" and "shared_field" in (row["result"] or "")
    finally:
        db.close()
    sent = _send_now(client, path, wait_s)
    if shared:
        # Primary shared field: already applied on this gateway's view; the owner applies it when it is next awake.
        return {"id": rid, "queued_locally": not sent, "state": "applied", "via": "shared_field", "gateway": req["gateway"],
                "coll": req["coll"], "key": req["key"], "action": req["action"]}
    state = "queued" if sent else "local"
    return {"id": rid, "queued_locally": not sent, "state": state, "gateway": req["gateway"], "coll": req["coll"],
            "key": req["key"], "action": req["action"]}
