"""Cards created / done per minute and gateway, from the Board replicas ``kanban/sync-shadow/*.db``.

* created: ``tasks.created_at``, attributed to ``tasks.created_on``.
* done: ``tasks.completed_at`` with status done/archived, attributed to the holder of the task's last
  ``claimed`` event: payload ``holder`` (a Board lease, e.g. ``worker-4``) or ``lock`` (``hermes-worker-4:<pid>``
  → ``worker-4``).
* ``hwp-*`` / ``rec-*`` (short-lived pool/recovery workers) → ``ephemeral``.
* ``boot-<uuid>:<pid>`` locks are macOS claims (core ``kanban_host_identity``: the host's boot-session UUID,
  stable across hostname changes). Nothing on the Board records which machine a boot UUID belongs to, so it is
  learned from the replicas: a Mac only runs its own cards (pinned_only, no pickup), so the ``created_on`` of the
  cards claimed under one boot UUID names its machine. A UUID whose cards don't agree (< 80 %) or that has no
  known creator stays ``unattributed``.
* anything else (no claim, a hostname that isn't a known machine) → ``unattributed``.

The replica ``tasks`` tables have no index on created_at/completed_at; one scan of ~10k rows is ~20 ms and the
result is cached for 30 s. The per-card last-claim lookup uses ``idx_events_task (task_id, created_at)`` and is
memoized per completed card (a completed card's last claim never changes).
"""
from __future__ import annotations

import glob
import json
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable, Optional

from .sampler import ro_connect
from .store import RETENTION_S, minute_of

EPHEMERAL = "ephemeral"
UNATTRIBUTED = "unattributed"
CACHE_S = 30
BOOT_AGREEMENT = 0.8


def replica_paths(root: Path) -> list[Path]:
    try:
        from hermes_cli import kanban_db
        base = Path(kanban_db.kanban_home()) / "kanban"
    except Exception:
        base = Path(root) / "kanban"
    return [Path(p) for p in sorted(glob.glob(str(base / "sync-shadow" / "*.db")))]


def parse_claim(payload: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``(holder, lock)`` from a ``claimed`` event payload."""
    if not payload:
        return None, None
    try:
        j = json.loads(payload)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(j, dict):
        return None, None
    h, lk = j.get("holder"), j.get("lock")
    return (h if isinstance(h, str) and h else None), (lk if isinstance(lk, str) and lk else None)


def boot_uuid(lock: Optional[str]) -> Optional[str]:
    if lock and lock.startswith("boot-"):
        return lock.split(":", 1)[0][len("boot-"):] or None
    return None


def classify(name: Optional[str], known: Optional[set[str]] = None) -> str:
    """A machine-ish name → gateway label."""
    if not name:
        return UNATTRIBUTED
    if name.startswith(("hwp-", "rec-")):
        return EPHEMERAL
    if name.startswith("boot-"):
        return UNATTRIBUTED
    if known is not None and name not in known:
        return UNATTRIBUTED
    return name


def attribute(holder: Optional[str], lock: Optional[str], boot_map: dict[str, str],
              known: Optional[set[str]] = None) -> str:
    """Gateway that completed a card, from its last claim."""
    if holder:
        return classify(holder)  # Board leases name the machine; trust them even if it left the registry
    if not lock:
        return UNATTRIBUTED
    uid = boot_uuid(lock)
    if uid is not None:
        return boot_map.get(uid, UNATTRIBUTED)
    host = lock.split(":", 1)[0]
    if host.startswith(("hwp-", "rec-")):
        return EPHEMERAL
    if host.startswith("hermes-"):
        host = host[len("hermes-"):]
    return classify(host, known)


def learn_boot_map(votes: Iterable[tuple[str, Optional[str]]], known: Optional[set[str]] = None) -> dict[str, str]:
    """``{boot_uuid: machine}`` from (boot_uuid, created_on) pairs of cards claimed under that UUID."""
    by: dict[str, Counter] = defaultdict(Counter)
    for uid, creator in votes:
        if uid and creator and not creator.startswith(("hwp-", "rec-", "boot-")) and (known is None or creator in known):
            by[uid][creator] += 1
    out = {}
    for uid, c in by.items():
        name, n = c.most_common(1)[0]
        if n / sum(c.values()) >= BOOT_AGREEMENT:
            out[uid] = name
    return out


def _last_claim(conn: sqlite3.Connection, task_id: str) -> Optional[str]:
    row = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='claimed' "
                       "ORDER BY created_at DESC, id DESC LIMIT 1", (task_id,)).fetchone()
    return row[0] if row else None


class Cards:
    """Per-minute created/done events for the retention window, cached for CACHE_S."""

    def __init__(self, root: Path, known: Optional[Callable[[], Optional[set[str]]]] = None, clock=time.time,
                 replica_dir: Optional[Path] = None):
        self.root = Path(root)
        self.replica_dir = Path(replica_dir) if replica_dir else None
        self.known = known or (lambda: None)
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: Optional[tuple[float, dict]] = None
        self._claims: dict[tuple[str, str, float], tuple[Optional[str], Optional[str]]] = {}

    def events(self) -> dict:
        """``{"created": [(t, gw)], "done": [(t, gw)], "boot_map": {...}}`` for the last RETENTION_S."""
        with self._lock:
            now = self.clock()
            if self._cache and now - self._cache[0] < CACHE_S:
                return self._cache[1]
            out = self._build(now)
            self._cache = (now, out)
            return out

    def _build(self, now: float) -> dict:
        known = self.known()
        since = now - RETENTION_S
        created, done_raw, votes = [], [], []
        dbs = sorted(self.replica_dir.glob("*.db")) if self.replica_dir else replica_paths(self.root)
        for db in dbs:
            try:
                c = ro_connect(db)
            except sqlite3.Error:
                continue
            try:
                for t, on in c.execute("SELECT created_at, created_on FROM tasks WHERE created_at >= ?", (since,)):
                    created.append((t, on))
                for tid, t, on in c.execute(
                        "SELECT id, completed_at, created_on FROM tasks WHERE completed_at >= ? "
                        "AND status IN ('done','archived')", (since,)):
                    key = (str(db), tid, float(t))
                    claim = self._claims.get(key)
                    if claim is None:
                        claim = self._claims[key] = parse_claim(_last_claim(c, tid))
                    done_raw.append((t, claim))
                    if boot_uuid(claim[1]) and not claim[0]:
                        votes.append((boot_uuid(claim[1]), on))
            except sqlite3.Error:
                pass
            finally:
                c.close()
        self._claims = {k: v for k, v in self._claims.items() if k[2] >= since}  # drop cards out of the window
        bmap = learn_boot_map(votes, known)
        return {
            "created": [(t, classify(on)) for t, on in created],
            "done": [(t, attribute(h, lk, bmap, known)) for t, (h, lk) in done_raw],
            "boot_map": bmap,
        }


def per_minute(events: Iterable[tuple[float, str]], since: int, until: int) -> dict[str, dict[int, int]]:
    out: dict[str, dict[int, int]] = defaultdict(dict)
    for t, gw in events:
        if since <= t < until:
            m = minute_of(t)
            out[gw][m] = out[gw].get(m, 0) + 1
    return out
