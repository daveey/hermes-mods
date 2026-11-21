"""Session-panel feed: the hub precomputes each watched chat's panel data and pushes what changed.

[daveey 2026-11-20] "precompute the status panel data on the hub ... use a websocket or some form of event
streaming so that the desktop is always kept in sync without polling." The Desktop registers the chats it
cares about (`interest`); a background tick (1 s) checks the board/session data stamps, finds the cards that
changed (new `task_events` ids), recomputes only the bundles that reference them, and hands every changed
bundle to `emit` in one batch (the gateway pushes it as `plugin.session-panel-feed.changed`). A full refresh of
every watched chat runs every FULL_EVERY_S as a safety net. `changes()` is the catch-up for a client that
(re)connected or saw a revision gap.

A bundle holds exactly what the Desktop panel would otherwise fetch itself, in the same shapes:
- `lineage`: the chat and its compression ancestors (session-tasks `lineageIds`);
- `tasks`: the rows of `/api/session-kanban-tasks` for that lineage;
- `done`: the rows of `/api/plugins/user-steps/session-cards` (finished cards included);
- `cards`: a slim `/api/plugins/kanban/tasks/<id>` record (`{detail, board}`) for every card on a linked card's
  dependency chain: only the fields session-panel's chain and wake logic read.
Rendering (sections, chains, "10m left", evidence freshness) stays on the Desktop, computed at render time.

Pure: every read goes through `readers`, so tests drive it without a hub.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from typing import Any, Callable, Iterable, Optional

FULL_EVERY_S = 60.0          # every watched chat is recomputed at least this often
INTEREST_TTL_S = 15 * 60.0   # a chat nobody re-registers stops being computed
MAX_WATCHED = 200            # interest cap across all clients
CHAIN_DEPTH = 6              # hops per chain (session-panel walks at most six cards)
CHAIN_CARDS = 80             # cards per bundle
TERMINAL = {'done', 'archived', 'cancelled'}
PROCESS_FEED = None            # the one feed of this process (dashboard/plugin_api.py `feed()`)
PROCESS_LOCK = threading.Lock()
CARD_ID = re.compile(r'\bt_[0-9a-f]{6,}\b')


def _hash(bundle: dict) -> str:
    body = {k: v for k, v in bundle.items() if k != 'computed_at'}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def chain_refs(row: Optional[dict], detail: Optional[dict]) -> list[str]:
    """The cards a chain can step to from this card: its open parents and every card id its wait names
    (the row's wait/block text, the newest park's `awaiting` ref, its human request)."""
    out: list[str] = []
    task = (detail or {}).get('task') or {}
    status = {t.get('id'): t.get('status') for t in (detail or {}).get('link_tasks') or [] if isinstance(t, dict)}
    for pid in ((detail or {}).get('links') or {}).get('parents') or []:
        if status.get(pid) not in TERMINAL:
            out.append(pid)
    texts: list[Any] = [row or {}, task.get('block_reason'), task.get('human_request')]
    for e in ((detail or {}).get('events') or [])[-10:]:
        if isinstance(e, dict) and e.get('kind') in ('continued', 'scheduled', 'blocked'):
            texts.append(e.get('payload'))
    blob = json.dumps(texts, default=str)
    own = task.get('id') or (row or {}).get('id')
    out += [m for m in CARD_ID.findall(blob) if m != own]
    return list(dict.fromkeys(out))


class Feed:
    """One feed per gateway process. Thread-safe: `tick` runs on the feed thread, the rest on request threads."""

    def __init__(self, readers: Any, *, emit: Callable[[dict], None], clock: Callable[[], float] = time.time,
                 full_every_s: float = FULL_EVERY_S, interest_ttl_s: float = INTEREST_TTL_S):
        self.readers = readers
        self.emit = emit
        self.clock = clock
        self.full_every_s = full_every_s
        self.interest_ttl_s = interest_ttl_s
        self.epoch = uuid.uuid4().hex[:12]   # a new process starts a new revision space
        self.rev = 0
        self.bundles: dict[str, dict] = {}   # session -> {rev, hash, bundle, refs, lineage}
        self.interest: dict[str, float] = {} # session -> expires at
        self.lock = threading.RLock()
        # Recompute + emit are serialized (tick thread and catch-up requests): each pushed batch's `prev` is the
        # revision right before it, so a client that applied every batch has no gap and never needs a catch-up.
        self.compute_lock = threading.Lock()
        self._stamps: Any = None
        self._cursor: Any = None
        self._last_full = 0.0

    # ── interest ────────────────────────────────────────────────────────────
    def watch(self, sessions: Iterable[str]) -> list[str]:
        now = self.clock()
        wanted = [s for s in dict.fromkeys(str(s) for s in sessions if s)][:MAX_WATCHED]
        with self.lock:
            for s in wanted:
                self.interest[s] = now + self.interest_ttl_s
            for s, until in list(self.interest.items()):
                if until < now:
                    del self.interest[s]
                    self.bundles.pop(s, None)
            while len(self.interest) > MAX_WATCHED:  # oldest registration first
                s = min(self.interest, key=self.interest.get)
                del self.interest[s]
                self.bundles.pop(s, None)
        return wanted

    # ── computing ───────────────────────────────────────────────────────────
    def compute(self, session: str) -> dict:
        r = self.readers
        lineage = list(r.lineage(session)) or [session]
        tasks = list(r.linked(lineage))
        done = list(r.done(lineage))
        cards: dict[str, dict] = {}
        queue = [(str(t.get('id')), str(t.get('board') or 'default'), t, 0) for t in tasks if t.get('id')]
        seen: set[str] = set()
        while queue and len(cards) < CHAIN_CARDS:
            cid, board, row, depth = queue.pop(0)
            if cid in seen:
                continue
            seen.add(cid)
            rec = r.card(cid, board)
            if rec is None:
                continue
            cards[cid] = rec
            status = ((rec.get('detail') or {}).get('task') or {}).get('status')
            if depth >= CHAIN_DEPTH or status in TERMINAL or status == 'running':
                continue
            for ref in chain_refs(row, rec.get('detail')):
                if ref not in seen:
                    queue.append((ref, rec.get('board') or board, None, depth + 1))
        return {'session': session, 'lineage': lineage, 'tasks': tasks, 'done': done, 'cards': cards,
                'computed_at': self.clock()}

    def _store(self, session: str, bundle: dict) -> bool:
        """Keep `bundle`; True when it differs from what clients last got (and it took a new revision)."""
        h = _hash(bundle)
        prev = self.bundles.get(session)
        refs = set(bundle['cards']) | {str(t.get('id')) for t in bundle['tasks'] + bundle['done'] if t.get('id')}
        if prev and prev['hash'] == h:
            prev.update(bundle=bundle, refs=refs, lineage=set(bundle['lineage']))
            return False
        self.rev += 1
        self.bundles[session] = {'rev': self.rev, 'hash': h, 'bundle': bundle, 'refs': refs,
                                 'lineage': set(bundle['lineage'])}
        return True

    def _recompute(self, sessions: Iterable[str]) -> dict[str, dict]:
        changed = {}
        for s in sessions:
            try:
                b = self.compute(s)
            except Exception:  # one chat's failure never stops the others; it keeps its last bundle
                continue
            with self.lock:
                if s in self.interest and self._store(s, b):
                    changed[s] = b
        return changed

    def tick(self) -> int:
        """One pass: recompute what changed, emit one batch. Returns how many bundles were pushed."""
        now = self.clock()
        stamps = self.readers.stamps()
        full = now - self._last_full >= self.full_every_s
        if stamps == self._stamps and not full:
            return 0
        with self.lock:
            watched = [s for s, until in self.interest.items() if until >= now]
        changed_cards, self._cursor = self.readers.changed_cards(self._cursor)
        self._stamps = stamps
        if full:
            self._last_full = now
            dirty = set(watched)
        else:
            with self.lock:
                dirty = {s for s in watched if s not in self.bundles}
                for s in watched:
                    b = self.bundles.get(s)
                    if b and (b['refs'] & set(changed_cards) or b['lineage'] & {v for v in changed_cards.values() if v}):
                        dirty.add(s)
        if not dirty:
            return 0
        return len(self._recompute_and_emit(sorted(dirty)))

    def _recompute_and_emit(self, sessions) -> dict[str, dict]:
        with self.compute_lock:
            prev = self.rev
            changed = self._recompute(sessions)
            if changed:
                try:
                    self.emit({'epoch': self.epoch, 'prev': prev, 'rev': self.rev, 'bundles': changed})
                except Exception:  # no client connected / transport down: catch-up covers it
                    pass
            return changed

    # ── catch-up ────────────────────────────────────────────────────────────
    def changes(self, sessions: Iterable[str], *, since: int = 0, epoch: Optional[str] = None) -> dict:
        """Watch `sessions` and return every bundle of theirs newer than `since` (all of them when `epoch` is not
        this process's: the client's revisions mean nothing here). Missing bundles are computed now."""
        wanted = self.watch(sessions)
        reset = epoch != self.epoch
        with self.lock:
            missing = [s for s in wanted if s not in self.bundles]
        if missing:  # pushed too, so every other client's revision chain stays unbroken
            self._recompute_and_emit(missing)
        with self.lock:
            out = {s: self.bundles[s]['bundle'] for s in wanted
                   if s in self.bundles and (reset or self.bundles[s]['rev'] > since)}
            return {'epoch': self.epoch, 'rev': self.rev, 'reset': reset, 'bundles': out}


def with_terminal_history(tasks: list[dict], history: list[dict], ids: Iterable[str]) -> list[dict]:
    """user-steps' `/session-cards` union with core's receipt projection (plugins/user-steps/dashboard/plugin_api.py
    `_with_terminal_history`, same rules), so `done` is exactly that route's answer."""
    def identity(row):
        if all(row.get(k) for k in ('gateway', 'profile', 'board', 'id')):
            return '/'.join(str(row[k]) for k in ('gateway', 'profile', 'board', 'id'))
        return row.get('key') or None

    out = list(tasks)
    seen = {identity(row) for row in tasks} - {None}
    wanted = set(ids)
    for row in history:
        if row.get('status') not in ('done', 'archived') or row.get('unfinished') is not False:
            continue
        if not wanted.intersection((row.get('session'), row.get('origin_session'))):
            continue
        if not all(row.get(k) for k in ('gateway', 'profile', 'board', 'id', 'origin_gateway', 'origin_profile')):
            continue
        key = identity(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row, session_id=row['session'], source='origin-history'))
    return out
