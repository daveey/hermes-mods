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
}


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


def _float(v):
    try:
        return None if v in (None, "", "None") else float(v)
    except (TypeError, ValueError):
        return None


class View:
    """One read of hub.db: everything a request needs, then closed."""

    def __init__(self, conn: sqlite3.Connection, now: Optional[float] = None):
        self.conn = conn
        self.now = _now() if now is None else now
        self.gateway = _meta(conn, "gateway")
        self.last_ok = _float(_meta(conn, "last_ok"))
        self.offline_since = _float(_meta(conn, "offline_since"))
        self._seen = None
        self._pending = None

    # when did each owner last publish anything we pulled (seconds)
    def owner_seen(self) -> Dict[str, float]:
        if self._seen is None:
            self._seen = {r[0]: (r[1] or 0) / 1000.0 for r in
                          self.conn.execute("SELECT gw, MAX(updated_at) FROM replica GROUP BY gw")} \
                if _has(self.conn, "replica") else {}
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

    def rows(self, coll: str) -> List[dict]:
        """Own records from `published`, other gateways' from `replica` (never own rows twice)."""
        out = []
        pend = self.pending()
        if _has(self.conn, "published"):
            for r in self.conn.execute("SELECT key, data, version FROM published WHERE coll = ?", (coll,)):
                d = _loads(r["data"])
                if d is None:
                    continue
                d.update(gateway=self.gateway, key=r["key"], version=r["version"], local=True, stale=False,
                         synced_at=self.now, pending=pend.get((self.gateway, coll, r["key"]), []))
                out.append(d)
        if _has(self.conn, "replica"):
            stale = self.hub_stale()
            for r in self.conn.execute("SELECT gw, key, data, version, updated_at FROM replica WHERE coll = ? AND gw IS NOT ?",
                                       (coll, self.gateway)):
                d = _loads(r["data"])
                if d is None:
                    continue
                d.update(gateway=r["gw"], key=r["key"], version=r["version"], local=False, stale=stale,
                         synced_at=self.synced_at(r["gw"]), updated_at_hub=(r["updated_at"] or 0) / 1000.0,
                         pending=pend.get((r["gw"], coll, r["key"]), []))
                out.append(d)
        return out

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
    return View(conn, now)


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


def session_links(session_ids: List[str], include_closed: bool = False) -> dict:
    """Cards on any gateway that one of these (lineage) session ids created or subscribed to."""
    wanted = [s for s in dict.fromkeys(session_ids) if s][:50]

    def fn(v: View):
        out = []
        for t in v.rows("tasks"):
            if not include_closed and t.get("status") not in LINK_STATUSES:
                continue
            links = [l for l in t.get("sessions") or [] if isinstance(l, dict) and l.get("session") in wanted]
            if not links:
                continue
            via = "created" if any(l.get("rel") == "created" for l in links) else "subscribed"
            run = t.get("run") or {}
            out.append({"id": t.get("id"), "title": t.get("title"), "status": t.get("status"),
                        "assignee": t.get("assignee"), "board": t.get("board") or "default",
                        "block_kind": t.get("block_kind"), "human_request": t.get("human_request"),
                        "session_id": links[0].get("session"), "via": via, "created_at": t.get("created_at"),
                        "started_at": t.get("started_at"),
                        "current_run_started_at": run.get("started_at") if run.get("status") == "running" else None,
                        "gateway": t["gateway"], "local": t["local"], "stale": t["stale"],
                        "synced_at": t["synced_at"], "version": t["version"], "pending": t["pending"]})
        out.sort(key=lambda x: (x["local"], -(x.get("created_at") or 0)))
        return {"session_ids": wanted, "tasks": out}
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
    if coll == "tasks" and (action == "create") != (b == "new"):
        raise RequestError(400, "tasks create uses key <board>/new; other task actions need a task id")
    clean = {}
    for k, req in spec.items():
        v = args.get(k)
        if v is None or (isinstance(v, str) and not v.strip() and k != "input"):
            if req:
                raise RequestError(400, "%s %s needs %s" % (coll, action, k))
            continue
        clean[k] = v if k == "input" else str(v)
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
    finally:
        v.conn.close()
    db = client.open_db(path)
    try:
        rid = client.queue_request(db, req["gateway"], req["coll"], req["key"], req["action"], req["args"],
                                   req["base_version"])
    finally:
        db.close()
    sent = _send_now(client, path, wait_s)
    state = "queued" if sent else "local"
    return {"id": rid, "queued_locally": not sent, "state": state, "gateway": req["gateway"], "coll": req["coll"],
            "key": req["key"], "action": req["action"]}
