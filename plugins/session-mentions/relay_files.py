"""File protocol shared by the cross_session tool and the dashboard API.

<HERMES_HOME>/session_mentions/
  outbox/<id>.json    request written by the tool
  claimed/<id>.json   moved here by the Desktop courier's drain (GET /outbox)
  results/<id>.json   written by POST /result; the tool polls for it
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

STALE_SECONDS = 3600


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


def claim_pending(base: Path | None = None) -> list[dict]:
    base = base or root()
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


def sweep(base: Path) -> None:
    cutoff = time.time() - STALE_SECONDS
    for sub in ("claimed", "results", "outbox"):
        for path in (base / sub).glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
