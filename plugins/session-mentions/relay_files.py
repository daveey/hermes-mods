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


def root() -> Path:
    try:
        from hermes_constants import get_hermes_home
        base = Path(str(get_hermes_home()))
    except Exception:
        base = Path(os.path.expanduser("~/.hermes"))
    path = base / "session_mentions"
    for sub in ("outbox", "claimed", "results"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def safe_id(raw: Any) -> str:
    text = str(raw or "")
    return text if text and all(c.isalnum() or c in "-_" for c in text) and len(text) <= 64 else ""


def home_courier_alive(base: Path | None = None) -> bool:
    base = base or root()
    try:
        return time.time() - (base / "results" / f"{HOME_COURIER_ID}.json").stat().st_mtime < HOME_FRESH_SECONDS
    except OSError:
        return False


def claim_pending(base: Path | None = None, home: bool = True) -> list[dict]:
    """Claim every pending request. A non-home drainer (another Mac's Desktop) gets nothing
    while this gateway's own Desktop courier is alive."""
    base = base or root()
    if not home and home_courier_alive(base):
        sweep(base)
        return []
    out = []
    now = time.time()
    for path in sorted((base / "outbox").glob("*.json"), key=lambda p: p.stat().st_mtime):
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
    for sub in ("claimed", "results", "outbox"):
        for path in (base / sub).glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
