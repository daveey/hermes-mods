"""Spend approvals a Kanban worker inherits from its card's origin chat, on this gateway or another one.

A card created for a chat on another gateway carries that chat as a ``platform='hub'`` notify subscription with
``{"reason": "origin"}`` (core ``hermes_cli/kanban_origin.py``: ``<gateway>/<profile>/<session>``). David answers
the card's priced ask in that chat ("approved"), so the hub worker's own session never holds the pair, and the
compute-authority check held its spend lines (card t_01a193e011b0a776, runs 17295 and 25359).

For a would-be hold only, this reads the origin chat's display transcript and returns the figures the user
approved there with a bare yes to a priced ask (``compute_envelope.answered_asks``, typed user rows only). The
card body, comments and any agent relay never count: only the origin transcript's own user rows do.

- Same gateway: ``goal_scope.display_transcript`` (read-only SessionDB).
- Another gateway: the session-mentions relay (``authority_read`` action), served by a Desktop courier that can
  reach that gateway, the same path ``cross_session`` uses. No courier, a stale Desktop (unknown action) or a
  timeout credits nothing.
Results are cached per origin (OK_TTL_S / FAIL_TTL_S) so repeated holds in one run do not re-read.

The origin is often a laptop that sleeps (the MacBook's courier goes stale, the mini's SSH hop times out), and each
Kanban run is a fresh process, so the in-memory cache alone would re-hold every run started while it sleeps. A
successful read is therefore also recorded in ``<coach root>/origin_authority.json`` (figures + read time, per
origin, DURABLE_TTL_S). A later failed read credits the figures that record holds: an approval a user typed stays
given, and the record only ever holds figures a courier read returned (never card text). A successful read
replaces the record, so a figure no longer in the transcript stops counting.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

PICKUP_S = 15.0  # couriers drain every ~3 s; no claim by then = no Desktop serves that gateway now
TOTAL_S = 40.0
OK_TTL_S = 600.0
FAIL_TTL_S = 120.0
COURIER_FRESH_S = 20.0
DURABLE_TTL_S = 30 * 86400.0

_NAME = r"[A-Za-z0-9_.-]{1,128}"
_CHAT_ID = re.compile(rf"^(?P<gw>{_NAME})/(?P<profile>{_NAME})/(?P<session>{_NAME})$")

_cache: dict = {}  # origin ref -> (at, ok, figures)


def norm_name(raw) -> str:
    """Gateway names compare as slugs (session-mentions ``relay_files.norm_name``)."""
    out, dash = [], False
    for ch in str(raw or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    return "".join(out).strip("-")


def _kanban_db(path=None) -> Optional[Path]:
    if path:
        return Path(path)
    env = os.environ.get("HERMES_KANBAN_DB")
    if env:
        return Path(env)
    try:
        from hermes_cli.kanban_db import kanban_db_path
        return Path(kanban_db_path(os.environ.get("HERMES_KANBAN_BOARD") or None))
    except Exception:
        return None


def card_origins(task_id: str, db_path=None) -> list[tuple[str, str, str]]:
    """``(gateway, profile, session)`` of each origin chat bound to ``task_id`` (hub subs with reason origin)."""
    path = _kanban_db(db_path)
    if not task_id or path is None or not path.is_file():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
    try:
        rows = conn.execute("SELECT chat_id, delivery_metadata FROM kanban_notify_subs "
                            "WHERE task_id = ? AND platform = 'hub'", (task_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for chat_id, meta in rows:
        try:
            reason = (json.loads(meta) if isinstance(meta, str) and meta else meta or {}).get("reason")
        except (ValueError, AttributeError):
            reason = None
        m = _CHAT_ID.match(str(chat_id or ""))
        if reason == "origin" and m:
            out.append((m["gw"], m["profile"], m["session"]))
    return sorted(set(out))


def this_gateway() -> str:
    try:
        from hermes_cli.kanban_origin import gateway_names
        return gateway_names()[0] or ""
    except Exception:
        return ""


def _relay_root() -> Path:
    try:
        from hermes_constants import get_hermes_home
        base = Path(str(get_hermes_home()))
    except Exception:
        base = Path(os.path.expanduser("~/.hermes"))
    if base.parent.name == "profiles":  # profiles share the gateway root's outbox (relay_files.root)
        base = base.parent.parent
    return base / "session_mentions"


def _courier_reaches(base: Path, route: str) -> bool:
    for path in (base / "couriers").glob("*.json") if (base / "couriers").is_dir() else ():
        try:
            if time.time() - path.stat().st_mtime >= COURIER_FRESH_S:
                continue
            c = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if route in {norm_name(x) for x in [c.get("device"), *(c.get("aliases") or ()), *(c.get("reach") or ())]}:
            return True
    return False


def relay_read(gw: str, profile: str, session: str, *, base: Optional[Path] = None, pickup_s: Optional[float] = None,
               total_s: Optional[float] = None, sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """The origin chat's slimmed display transcript through a Desktop courier. Raises on any failure."""
    pickup_s = PICKUP_S if pickup_s is None else pickup_s
    total_s = TOTAL_S if total_s is None else total_s
    base = base or _relay_root()
    route = norm_name(gw)
    if not _courier_reaches(base, route):
        raise LookupError(f"no live Desktop courier reaches gateway {gw!r}")
    for sub in ("outbox", "results"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    rid = uuid.uuid4().hex
    req = {"id": rid, "action": "authority_read", "route": route,
           "args": {"target": f"@session:{profile}@{gw}/{session}"}, "wait_for_reply": 0,
           "sender": {"session_id": os.environ.get("HERMES_SESSION_ID", ""), "profile": profile,
                      "kanban_task": os.environ.get("HERMES_KANBAN_TASK", ""), "purpose": "coach compute_authority"},
           "created_at": time.time(), "ttl": int(total_s) + 5}
    outbox, result = base / "outbox" / f"{rid}.json", base / "results" / f"{rid}.json"
    tmp = outbox.with_name(f".{outbox.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(req))
    os.replace(tmp, outbox)
    start = time.time()
    try:
        while time.time() - start < total_s:
            if result.exists():
                try:
                    data = json.loads(result.read_text())
                except ValueError:
                    sleep(0.3)
                    continue
                result.unlink(missing_ok=True)
                if not isinstance(data, dict) or data.get("ok") is not True:
                    raise RuntimeError(str((data or {}).get("error") or "courier returned no transcript")[:200])
                rows = data.get("messages")
                if not isinstance(rows, list):
                    raise RuntimeError("courier result has no messages (Desktop plugin without authority_read?)")
                return [r for r in rows if isinstance(r, dict)]
            if outbox.exists() and time.time() - start > pickup_s:
                raise TimeoutError(f"no Desktop courier claimed the read of {gw!r} within {pickup_s:.0f}s")
            sleep(0.5)
        raise TimeoutError(f"origin transcript read from {gw!r} timed out after {total_s:.0f}s")
    finally:
        outbox.unlink(missing_ok=True)


def origin_rows(origin: tuple[str, str, str], scope, me: str, relay=relay_read) -> list[dict]:
    gw, profile, session = origin
    if me and norm_name(gw) == norm_name(me):
        return scope.display_transcript(session)
    return relay(gw, profile, session)


def _durable_load(path: Optional[Path]) -> dict:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _durable_get(path: Optional[Path], ref: str, now: float) -> Optional[tuple[float, list[float]]]:
    rec = _durable_load(path).get(ref)
    if not isinstance(rec, dict):
        return None
    try:
        at = float(rec["at"])
        figures = [float(v) for v in rec["usd"]]
    except (TypeError, KeyError, ValueError):
        return None
    return (at, figures) if now - at < DURABLE_TTL_S else None


def _durable_put(path: Optional[Path], ref: str, figures: list[float], now: float) -> None:
    if not path:
        return
    path = Path(path)
    data = {k: v for k, v in _durable_load(path).items()
            if isinstance(v, dict) and now - float(v.get("at") or 0) < DURABLE_TTL_S}
    data[ref] = {"at": now, "usd": figures[:50]}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def origin_asks(scope, ce, *, task_id: Optional[str] = None, db_path=None, relay=relay_read, me: Optional[str] = None,
                log: Optional[Callable[[dict], None]] = None, now: Callable[[], float] = time.time,
                durable: Optional[Path] = None) -> list[float]:
    """Figures the user approved, by a typed bare yes to a priced ask, in the origin chat(s) of this worker's card.

    [] outside a Kanban worker, for a card with no origin chat, or when every read failed (each logged) and no
    earlier successful read of that origin is on record in ``durable``."""
    task_id = task_id if task_id is not None else os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if not task_id:
        return []
    try:
        origins = card_origins(task_id, db_path)
    except Exception as exc:
        if log:
            log({"event": "authority_origin_read", "task": task_id, "ok": False,
                 "error": f"card origin lookup: {type(exc).__name__}: {exc}"[:200]})
        return []
    me = this_gateway() if me is None else me
    out: list[float] = []
    for origin in origins:
        ref = f"@session:{origin[1]}@{origin[0]}/{origin[2]}"
        hit = _cache.get(ref)
        if hit and now() - hit[0] < (OK_TTL_S if hit[1] else FAIL_TTL_S):
            out.extend(hit[2])
            continue
        try:
            figures = [float(v) for v in ce.answered_asks(origin_rows(origin, scope, me, relay), None,
                                                          is_user_evidence=scope.is_evidence_row)]
            _cache[ref] = (now(), True, figures)
            try:
                _durable_put(durable, ref, figures, now())
            except OSError:
                pass
            if log:
                log({"event": "authority_origin_read", "task": task_id, "origin": ref, "ok": True,
                     "usd": figures[:20]})
        except Exception as exc:
            rec = _durable_get(durable, ref, now())
            figures = rec[1] if rec else []
            _cache[ref] = (now(), False, figures)
            if log:
                entry = {"event": "authority_origin_read", "task": task_id, "origin": ref, "ok": False,
                         "error": f"{type(exc).__name__}: {exc}"[:200]}
                if rec:
                    entry.update({"recorded_usd": figures[:20], "recorded_at": rec[0]})
                log(entry)
        out.extend(figures)
    return out
