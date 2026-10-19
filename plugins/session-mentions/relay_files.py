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
    return {"device": device, "aliases": sorted({device, host} - {""})}


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
    if not home and home_courier_alive(base):
        sweep(base)
        return []
    reach_list = None if reach is None else sorted(set(names(reach)) | set(names(aliases)) | ({device} - {""}))
    couriers = live_couriers(base)
    out = []
    now = time.time()
    for path in sorted((base / "outbox").glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            peek = json.loads(path.read_text())
        except Exception:
            peek = {}
        if isinstance(peek, dict) and not may_claim(peek, home=home, courier=device, reach=reach_list, couriers=couriers):
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


def enqueue(req: Any, base: Path | None = None) -> bool:
    """Queue a request forwarded by another Desktop's courier for this gateway's home courier."""
    base = base or root()
    if not isinstance(req, dict):
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
