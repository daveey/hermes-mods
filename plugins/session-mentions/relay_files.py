"""File protocol shared by the cross_session tool and the dashboard API.

<HERMES_HOME>/session_mentions/
  outbox/<id>.json    request written by the tool (or forwarded by another Desktop's courier)
  claimed/<id>.json   moved here by the Desktop courier's drain (GET /outbox)
  results/<id>.json   written by POST /result; the tool polls for it
  results/home-courier.json
                      heartbeat from THIS gateway's own Desktop (the "home" courier)

Why a home courier: every Desktop with the session-mentions plugin drains every gateway it is
connected to. Another Mac's Desktop reaches this gateway through a separate (SSH-isolated) serve
process, and core refuses a submit from there into a chat this Mac's Desktop holds open ("This
chat is open in another Hermes window/terminal"). So while the home courier is alive, remote
drainers get nothing; the home courier sends through the socket that owns the open chat.

Targeted requests. Every Desktop connected to a gateway drains its outbox, so a request has to be
able to name the Mac it is for. The tool stamps ``route`` = the gateway part of its target
(``@session:p@<gateway>/<id>`` -> ``<gateway>``). A courier drains with ``courier=<its device
name>`` and ``reach=<every gateway name it can resolve>``, and is registered under
``couriers/<device>.json`` with its aliases. Claim rule (``may_claim``):
  - no route: anyone;
  - ``this-device`` / ``local``: only this gateway's own (home) courier — from anywhere else the
    name is ambiguous (each Desktop calls its own Mac that);
  - a route naming a live courier's device: only that courier (the Mac answers for itself even
    when another Desktop can reach it through a registry connection);
  - otherwise: a courier whose ``reach`` has it. A legacy drain (no ``reach``) keeps the old
    claim-everything behaviour except for the two cases above.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

STALE_SECONDS = 3600
HOME_COURIER_ID = "home-courier"
HOME_FRESH_SECONDS = 20.0  # the home Desktop beats every ~5s (from a worker, so hidden windows keep it)
ACTIONS = ("list", "read", "send")
LOCAL_ALIASES = ("this-device", "local")
COURIER_FRESH_SECONDS = 20.0  # couriers drain every ~3s


def norm_name(raw: Any) -> str:
    """Gateway / device names compare as slugs (`MacBook Pro` == `macbook-pro`)."""
    out, dash = [], False
    for ch in str(raw or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    return "".join(out).strip("-")


def names(raw: Any) -> list[str]:
    items = raw.split(",") if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple, set)) else [])
    return sorted({n for n in (norm_name(x) for x in items) if n})


def route_of_target(target: Any) -> str:
    """The gateway a session/task ref names: `@session:p@gw/id`, `@task:b@gw/t_..`, `gw:id`. '' = origin."""
    text = str(target or "").strip().strip("`\"'")
    for prefix in ("@session:", "@task:"):
        while text.startswith(prefix):
            text = text[len(prefix):].strip("`\"'")
    if "/" in text:
        head = text.split("/", 1)[0]
        return norm_name(head.rsplit("@", 1)[1]) if "@" in head else ""
    if ":" in text:
        return norm_name(text.split(":", 1)[0])
    return ""


def root() -> Path:
    try:
        from hermes_constants import get_hermes_home
        base = Path(str(get_hermes_home()))
    except Exception:
        base = Path(os.path.expanduser("~/.hermes"))
    # A named profile (<root>/profiles/<name>) shares the gateway root's outbox: the Desktop
    # courier drains only the root dashboard, so a profile-local outbox would never be picked up.
    if base.parent.name == "profiles":
        base = base.parent.parent
    path = base / "session_mentions"
    for sub in ("outbox", "claimed", "results"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def device_info() -> dict:
    """This machine's courier name: config `session_mentions.device_name`, else $HERMES_DEVICE_NAME,
    else the short hostname. Aliases always include the hostname so either name reaches it."""
    import socket
    configured = os.environ.get("HERMES_DEVICE_NAME", "")
    try:
        from hermes_cli.config import load_config  # type: ignore
        configured = str(((load_config() or {}).get("session_mentions") or {}).get("device_name") or configured)
    except Exception:
        pass
    host = norm_name(socket.gethostname().split(".")[0])
    device = norm_name(configured) or host or "this-device"
    # `gateway` rides here (not only in /ping) because /ping is mounted once per backend while this
    # file hot-reloads: a running backend's old ping spreads device_info() and so reports it too.
    return {"device": device, "aliases": sorted({device, host} - {""}), "gateway": gateway_name()}


def gateway_name() -> str:
    """This gateway's hermes-hub registry name (``self_gateway()``), slugged; '' when unknown.
    Hermes writes cross-gateway links with it (``@task:default@hub/t_..``), and a Desktop may know
    the gateway under another connection label (``hub-ec2``), so /ping reports it for the courier."""
    try:
        from hermes_cli.kanban_capability_registry import self_gateway  # type: ignore
        return norm_name(self_gateway())
    except Exception:
        return ""


def safe_id(raw: Any) -> str:
    text = str(raw or "")
    return text if text and all(c.isalnum() or c in "-_" for c in text) and len(text) <= 64 else ""


def home_courier_alive(base: Path | None = None) -> bool:
    base = base or root()
    try:
        return time.time() - (base / "results" / f"{HOME_COURIER_ID}.json").stat().st_mtime < HOME_FRESH_SECONDS
    except OSError:
        return False


def register_courier(base: Path, courier: Any, aliases: Any = (), reach: Any = (), home: bool = False) -> str:
    """Record a draining Desktop courier (its device name + aliases). Returns the device name."""
    device = norm_name(courier)
    if not device or not safe_id(device):
        return ""
    (base / "couriers").mkdir(exist_ok=True)
    atomic_write(base / "couriers" / f"{device}.json", {
        "device": device, "aliases": sorted(set(names(aliases)) | {device}), "reach": names(reach),
        "home": bool(home), "at": time.time()})
    return device


def live_couriers(base: Path | None = None) -> list[dict]:
    base = base or root()
    out = []
    for path in sorted((base / "couriers").glob("*.json")) if (base / "couriers").is_dir() else ():
        try:
            if time.time() - path.stat().st_mtime < COURIER_FRESH_SECONDS:
                out.append(json.loads(path.read_text()))
        except Exception:
            continue
    return out


def may_claim(req: dict, *, home: bool, courier: str = "", reach: list[str] | None = None,
              couriers: list[dict] | None = None) -> bool:
    route = norm_name(req.get("route"))
    if not route:
        return True
    if route in LOCAL_ALIASES:
        return home
    for c in couriers or ():
        if route in (c.get("aliases") or [c.get("device")]):
            return c.get("device") == courier  # that Mac answers for itself
    if reach is None:
        return True  # legacy courier: no reach list
    return route in reach


def claim_pending(base: Path | None = None, home: bool = True, courier: Any = "", reach: Any = None,
                  aliases: Any = ()) -> list[dict]:
    """Claim every pending request this courier may serve (see ``may_claim``). A non-home drainer
    (another Mac's Desktop) gets nothing while this gateway's own Desktop courier is alive."""
    base = base or root()
    device = register_courier(base, courier, aliases, reach or (), home) if courier else ""
    home_alive = (not home) and home_courier_alive(base)
    if home_alive and not device:
        sweep(base)
        return []
    reach_list = None if reach is None else sorted(set(names(reach)) | set(names(aliases)) | ({device} - {""}))
    couriers = live_couriers(base)
    # While this gateway's own Desktop courier is alive, another Mac's Desktop may still claim a request
    # that ONLY it can serve: addressed to that Mac itself, or to a gateway the home courier cannot reach.
    # Otherwise the home courier skips it ("that Mac answers for itself") and the request is stranded.
    home_names = set()
    if home_alive:
        for c in couriers:
            if c.get("home"):
                home_names |= set(names(c.get("reach") or ())) | set(names(c.get("aliases") or ())) | {norm_name(c.get("device"))}
        home_names.discard("")
    out = []
    now = time.time()
    for path in sorted((base / "outbox").glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            peek = json.loads(path.read_text())
        except Exception:
            peek = {}
        if isinstance(peek, dict) and not may_claim(peek, home=home, courier=device, reach=reach_list, couriers=couriers):
            continue
        if home_alive:
            route = norm_name(peek.get("route")) if isinstance(peek, dict) else ""
            mine = set(names(aliases)) | ({device} - {""})
            if not route or route in LOCAL_ALIASES or not (route in mine or route not in home_names):
                continue
        target = base / "claimed" / path.name
        try:
            os.replace(path, target)  # atomic claim: a second drainer loses the race
        except OSError:
            continue
        try:
            req = json.loads(target.read_text())
        except Exception:
            continue
        if now - float(req.get("created_at") or now) > float(req.get("ttl") or 600):
            write_result(req.get("id"), {"ok": False, "error": "request expired before the Desktop picked it up"}, base)
            continue
        out.append(req)
    sweep(base)
    return out


def write_result(req_id: Any, result: dict, base: Path | None = None) -> bool:
    base = base or root()
    rid = safe_id(req_id)
    if not rid:
        return False
    atomic_write(base / "results" / f"{rid}.json", result)
    return True


# Human chats a Kanban worker must not inject a turn into. Worker/kanban sessions (and anything
# else we can identify) stay sendable; an unknown target fails closed so a missed lookup cannot
# land in the owner's open conversation.
HUMAN_CHAT_SOURCES = frozenset({"desktop", "cli"})


def session_id_of(target: Any) -> str:
    """Session id from `@session:profile@gw/id`, `@session:profile/id`, `gw:id`, or a bare id."""
    text = str(target or "").strip().strip("`\"'")
    for prefix in ("@session:", "@task:"):
        while text.startswith(prefix):
            text = text[len(prefix):].strip("`\"'")
    if "/" in text:
        text = text.split("/", 1)[1]
    elif ":" in text:
        text = text.split(":", 1)[1]
    return text.strip()


def _ro_connect(path: Path, timeout: float = 2.0):
    """Read-only open. mode=ro fails on a WAL db whose -wal/-shm were deleted, so fall back to
    mode=rw + query_only (reads the same data, never writes, never creates a missing db)."""
    uri = path.resolve().as_posix()
    try:
        con = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=timeout)
        con.execute("select 1 from sqlite_master limit 1")
        return con
    except sqlite3.OperationalError:
        con = sqlite3.connect(f"file:{uri}?mode=rw", uri=True, timeout=timeout)
        con.execute("PRAGMA query_only=ON")
        return con


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(str(get_hermes_home()))
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def replica_db() -> Path:
    return Path.home() / ".local/state/hermes-hub/hub.db"


def _query_source(db: Path, sql: str, params: tuple) -> str | None:
    if not db.is_file():
        return None
    try:
        con = _ro_connect(db)
    except sqlite3.OperationalError:
        return None
    try:
        row = con.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not row or row[0] is None:
        return None
    text = str(row[0]).strip().lower()
    return text or None


def session_source(target: Any) -> str | None:
    """Source of the target session: this gateway's state.db, else the hermes-hub replica."""
    sid = session_id_of(target)
    if not sid:
        return None
    local = _query_source(
        _hermes_home() / "state.db",
        "SELECT source FROM sessions WHERE id = ?",
        (sid,),
    )
    if local:
        return local
    gw = route_of_target(target)
    if gw:
        return _query_source(
            replica_db(),
            "SELECT json_extract(data, '$.source') FROM replica "
            "WHERE coll = 'sessions' AND gw = ? AND key GLOB ?",
            (gw, f"*/{sid}"),
        )
    return _query_source(
        replica_db(),
        "SELECT json_extract(data, '$.source') FROM replica "
        "WHERE coll = 'sessions' AND key GLOB ?",
        (f"*/{sid}",),
    )


def worker_task_from_env() -> str:
    return os.environ.get("HERMES_KANBAN_TASK", "").strip()


def worker_send_refusal(target: Any, *, kanban_task: str | None = None, source_lookup=None) -> str | None:
    """Refusal text when a Kanban worker would inject into a human chat; None when the send may proceed.

    ``kanban_task=None`` reads HERMES_KANBAN_TASK (the tool). An explicit string, including ``""``,
    is the caller's identity and does not consult the process env (the REST route, which runs in
    the dashboard, not the worker).
    """
    task = worker_task_from_env() if kanban_task is None else str(kanban_task).strip()
    if not task:
        return None
    lookup = source_lookup or session_source
    try:
        source = lookup(target)
    except Exception:
        source = None
    source_l = str(source or "").strip().lower()
    if source_l and source_l not in HUMAN_CHAT_SOURCES:
        return None
    gw = route_of_target(target) or "the target gateway"
    where = (
        f"{target} (source={source_l})"
        if source_l
        else f"{target} (source could not be confirmed as a worker session)"
    )
    head = f"Refused: a Kanban worker (HERMES_KANBAN_TASK={task}) cannot send into a human chat {where}. "
    sub = subscribe_refused_chat(target, task) if source_l else None
    if sub:
        return (
            head
            + f"That injects a turn into the owner's conversation. This chat is now subscribed ({sub}) to YOUR card "
            f"{task} in notify mode, so it receives the standard done notice when you complete. Put the message in "
            f"your kanban_complete summary instead of sending it; to deliver exact text, add a line "
            f"`FINAL SEND for {_ref_of(target)}: <text>` and that text replaces the summary in the notice. "
            f"Do NOT create a relay card for this: a worker on it would be refused the same way. "
            f"If the work itself must run on {gw}, place a card there (hermes-hub create-task {gw} or kanban_create "
            f"with placement on that gateway); if that gateway's executor is down, kanban_block with blocker_id "
            f"for the outage (MacBook: hermes-desktop-gateway-unavailable)."
        )
    return (
        head
        + f"That injects a turn into the owner's conversation. Place a card on {gw} instead "
        f"(hermes-hub create-task {gw} or kanban_create with placement on that gateway). "
        f"If that gateway's executor is down, kanban_block with blocker_id for the outage "
        f"(MacBook: hermes-desktop-gateway-unavailable) — do not message the chat."
    )


_REF = re.compile(
    r"^(?:@session:)+(?:(?P<profile>[A-Za-z0-9_.-]+)(?:@(?P<gw>[A-Za-z0-9_.-]+))?/)?(?P<sid>[A-Za-z0-9_.-]+)$"
)


def parse_session_ref(target: Any) -> tuple[str, str, str] | None:
    """(profile, gateway or '', session id) from `@session:p@gw/id`, `@session:p/id` or `gw:id`; else None."""
    text = str(target or "").strip().strip("`\"'")
    m = _REF.match(text)
    if m:
        if m.group("profile") is None:
            return ("default", "", m.group("sid"))
        return (m.group("profile"), norm_name(m.group("gw") or ""), m.group("sid"))
    m = re.match(r"^(?P<gw>[A-Za-z0-9_.-]+):(?P<sid>[A-Za-z0-9_.-]+)$", text)
    if m:
        return ("default", norm_name(m.group("gw")), m.group("sid"))
    return None


def _ref_of(target: Any) -> str:
    parsed = parse_session_ref(target)
    if not parsed:
        return str(target)
    profile, gw, sid = parsed
    return f"@session:{profile}" + (f"@{gw}" if gw else "") + f"/{sid}"


def _this_gateway() -> str:
    try:
        from hermes_cli import kanban_origin

        return norm_name(kanban_origin.gateway_names()[0])
    except Exception:
        return ""


def _default_add_sub(**kw: Any) -> None:
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn

    with kbc.connect_closing() as conn:
        # A forwarded request (enqueue) names a card owned by another gateway; never write an
        # orphan row for a task this board does not have.
        if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (kw["task_id"],)).fetchone() is None:
            raise LookupError(f"task {kw['task_id']} is not on this board")
        kbn.add_notify_sub(conn, **kw)


def subscribe_refused_chat(target: Any, task: str, *, add_sub=None, this_gateway: str | None = None) -> str | None:
    """Subscribe the chat a worker was refused into to the worker's OWN card, notify mode. Idempotent
    (add_notify_sub keys on task+platform+chat). Same-gateway chat -> ``tui:<sid>``; chat on another gateway
    -> the cross-gateway row hub cards use, ``hub:<gw>/<profile>/<sid>``. Returns a short description, or
    None when the target is not a session ref or the card DB could not be written (never raises)."""
    parsed = parse_session_ref(target)
    if not parsed or not task:
        return None
    profile, gw, sid = parsed
    me = norm_name(this_gateway if this_gateway is not None else _this_gateway())
    add = add_sub or _default_add_sub
    try:
        if gw and gw != me:
            add(task_id=task, platform="hub", chat_id=f"{gw}/{profile}/{sid}", notifier_profile=profile)
            return f"hub:{gw}/{profile}/{sid}"
        add(task_id=task, platform="tui", chat_id=sid, notifier_profile=profile,
            delivery_metadata={"reason": "send_refused"})
        return f"tui:{sid}"
    except Exception:
        return None


def enqueue(req: Any, base: Path | None = None) -> bool:
    """Queue a request forwarded by another Desktop's courier for this gateway's home courier."""
    base = base or root()
    if not isinstance(req, dict):
        return False
    if req.get("action") == "send":
        sender = req.get("sender")
        args = req.get("args")
        sender_task = sender.get("kanban_task") if isinstance(sender, dict) else ""
        target = args.get("target") if isinstance(args, dict) else None
        if worker_send_refusal(target, kanban_task=str(sender_task or "")):
            return False
    rid = safe_id(req.get("id"))
    if not rid or rid == HOME_COURIER_ID or req.get("action") not in ACTIONS:
        return False
    if any((base / sub / f"{rid}.json").exists() for sub in ("outbox", "claimed", "results")):
        return False
    try:
        ttl = min(float(req.get("ttl") or 600), 600.0)
    except (TypeError, ValueError):
        ttl = 600.0
    atomic_write(base / "outbox" / f"{rid}.json", {**req, "id": rid, "created_at": time.time(), "ttl": ttl})
    return True


def request_status(req_id: Any, base: Path | None = None) -> dict:
    """pending | claimed | done (the result is popped exactly once) | unknown."""
    base = base or root()
    rid = safe_id(req_id)
    if not rid or rid == HOME_COURIER_ID:
        return {"state": "unknown"}
    result = base / "results" / f"{rid}.json"
    if result.exists():
        try:
            data = json.loads(result.read_text())
        except Exception:
            return {"state": "claimed"}  # mid-write; poll again
        for path in (result, base / "claimed" / f"{rid}.json"):
            try:
                path.unlink()
            except OSError:
                pass
        return {"state": "done", "result": data}
    if (base / "claimed" / f"{rid}.json").exists():
        return {"state": "claimed"}
    if (base / "outbox" / f"{rid}.json").exists():
        return {"state": "pending"}
    return {"state": "unknown"}


def cancel(req_id: Any, base: Path | None = None) -> bool:
    """Withdraw a still-unclaimed request. False once any courier has claimed it."""
    base = base or root()
    rid = safe_id(req_id)
    if not rid:
        return False
    try:
        (base / "outbox" / f"{rid}.json").unlink()
        return True
    except OSError:
        return False


def sweep(base: Path) -> None:
    cutoff = time.time() - STALE_SECONDS
    for sub in ("claimed", "results", "outbox", "couriers"):
        for path in (base / sub).glob("*.json") if (base / sub).is_dir() else ():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
