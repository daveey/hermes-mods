"""Session-panel feed: the gateway precomputes each watched chat's panel data and pushes what changed.

[daveey 2026-11-20] "precompute the status panel data on the hub ... use a websocket or some form of event
streaming so that the desktop is always kept in sync without polling." The Desktop registers the chats it
cares about (`changes`); a background tick (1 s) checks the board/session data stamps, finds the cards that
changed (new `task_events` ids), recomputes only the bundles that reference them, and hands every changed
bundle to `emit` in one batch (the gateway pushes it as `plugin.session-panel.changed`). A full refresh of every
watched chat runs every FULL_EVERY_S as a safety net. With nothing to push, a heartbeat batch goes out every
HEARTBEAT_S: clients count the feed live only from pushes, so a feed whose pushes do not reach them (no socket,
another backend, a stuck thread) is never trusted. `changes()` is the catch-up for a client that (re)connected,
saw a revision gap, or got a batch by name only.

A bundle holds exactly what the Desktop panel would otherwise fetch itself, in the same shapes:
- `lineage`: the chat and its compression ancestors (session-tasks `lineageIds`);
- `tasks`: the rows of `/api/session-kanban-tasks` for that lineage;
- `done`: the rows of `/api/plugins/user-steps/session-cards` (finished cards included);
- `cards`: a slim `/api/plugins/kanban/tasks/<id>` record (`{detail, board}`) for every card on a linked card's
  dependency chain: only the fields session-panel's chain and wake logic read.
Rendering (sections, chains, "10m left", evidence freshness) stays on the Desktop, computed at render time.

Concurrency: bundles are computed outside any lock (the readers give each thread its own connections); storing a
bundle, taking its revision and emitting the batch happen under `compute_lock`, so batches go out in revision order
and each batch's `prev` is the revision right before it. Pure: every read goes through `readers`.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, Iterable, Optional

FULL_EVERY_S = 60.0          # a watched chat with open cards is recomputed at least this often (run evidence)
QUIET_FULL_EVERY_S = 300.0   # one with none (it changes only through card events, which recompute it at once)
HEARTBEAT_S = 20.0           # with nothing to push, a heartbeat at least this often (clients' liveness)
INTEREST_TTL_S = 15 * 60.0   # a chat nobody re-registers stops being computed
MAX_WATCHED = 200            # interest cap across all clients
CHAIN_DEPTH = 6              # hops per chain (session-panel walks at most six cards)
CHAIN_CARDS = 80             # cards per bundle
CATCHUP_WAIT_S = 8.0         # a catch-up waits at most this long for chats another thread is computing (< 15 s client timeout)
TERMINAL = {'done', 'archived', 'cancelled'}
CARD_ID = re.compile(r'\bt_[0-9a-f]{6,}\b')
PROCESS_FEED = None          # the one feed of this process (plugin_api.feed())
PROCESS_LOCK = threading.Lock()
log = logging.getLogger('session-panel')


def _hash(bundle: dict) -> str:
    body = {k: v for k, v in bundle.items() if k not in ('computed_at', 'computed_seq')}
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
    """One feed per gateway process (one profile: the process's own)."""

    def __init__(self, readers: Any, *, emit: Callable[[dict], None], clock: Callable[[], float] = time.time,
                 full_every_s: float = FULL_EVERY_S, interest_ttl_s: float = INTEREST_TTL_S,
                 heartbeat_s: float = HEARTBEAT_S):
        self.readers = readers
        self.emit = emit
        self.clock = clock
        self.full_every_s = full_every_s
        self.interest_ttl_s = interest_ttl_s
        self.heartbeat_s = heartbeat_s
        self.epoch = uuid.uuid4().hex[:12]   # a new process starts a new revision space
        self.rev = 0
        self.bundles: dict[str, dict] = {}   # session -> {rev, hash, bundle, refs, lineage}
        self.interest: dict[str, float] = {} # session -> expires at
        self.failures: dict[str, dict] = {}  # session -> {count, error, at}: shown by /status
        self.lock = threading.RLock()        # interest / bundles / failures
        self.compute_lock = threading.Lock() # store + revision + emit, in order
        self.stopped = threading.Event()
        self.last_emit = clock()          # the heartbeat clock starts with the feed
        self.last_tick_s = 0.0               # duration of the last tick that recomputed (shown by /status)
        self._seq = itertools.count(1)       # compute order: a bundle computed earlier never replaces a later one
        self._stamps: Any = None
        self._cursor: Any = None
        self._last_full = 0.0
        self._inflight: dict[str, threading.Event] = {}  # session -> set once its running compute is stored

    # ── interest ────────────────────────────────────────────────────────────
    def watch(self, sessions: Iterable[str]) -> list[str]:
        now = self.clock()
        wanted = [s for s in dict.fromkeys(str(s) for s in sessions if s)][:MAX_WATCHED]
        with self.lock:
            for s in wanted:
                self.interest[s] = now + self.interest_ttl_s
            self._expire(now)
            while len(self.interest) > MAX_WATCHED:  # oldest registration first
                s = min(self.interest, key=self.interest.get)
                self._forget(s)
        return wanted

    def _expire(self, now: float) -> None:
        for s, until in list(self.interest.items()):
            if until < now:
                self._forget(s)

    def _forget(self, s: str) -> None:
        self.interest.pop(s, None)
        self.bundles.pop(s, None)
        self.failures.pop(s, None)

    # ── computing ───────────────────────────────────────────────────────────
    def compute(self, session: str) -> dict:
        seq = next(self._seq)  # taken before any read: a slower compute that started earlier loses to this one
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
                'computed_at': self.clock(), 'computed_seq': seq}

    def _compute_all(self, sessions: Iterable[str]) -> dict[str, dict]:
        """Compute outside any lock. A chat that fails keeps its last bundle; its failure is counted and logged."""
        out = {}
        for s in sessions:
            # A long pass must not starve clients of heartbeats (their trust lapses after three missed ones).
            self._publish({}, heartbeat=True)
            try:
                out[s] = self.compute(s)
                with self.lock:
                    self.failures.pop(s, None)
            except Exception as e:
                with self.lock:
                    f = self.failures.setdefault(s, {'count': 0})
                    f.update(count=f['count'] + 1, error=f'{type(e).__name__}: {e}'[:300], at=self.clock())
                    first = f['count'] == 1
                (log.warning if first else log.debug)('session-panel feed: %s failed', s, exc_info=True)
        return out

    def _compute_publish(self, sessions: Iterable[str], *, always: bool = False
                         ) -> tuple[dict[str, dict], list[threading.Event]]:
        """Single-flight compute + store + push: `(pushed, waits)`, where `waits` are the events of the chats another
        thread was already computing (skipped here; that thread stores and pushes them, then sets the event).
        `always` (the tick) computes every chat anyway: a change can land after a running catch-up read its data,
        and only the newer compute wins (`computed_seq`). Catch-ups may still wait on the tick's compute.

        Without this every catch-up that found a chat missing recomputed it in parallel; under load each took
        longer than the Desktop's 15 s timeout, whose retries added still more computes until no request reached
        the backend in time and the panel showed no cards (hub serve, 2026-11-21). [daveey]"""
        wanted = list(dict.fromkeys(sessions))
        with self.lock:
            waits = [] if always else [self._inflight[s] for s in wanted if s in self._inflight]
            owned = [s for s in wanted if s not in self._inflight]
            for s in owned:
                self._inflight[s] = threading.Event()
        todo = wanted if always else owned
        try:
            return (self._publish(self._compute_all(todo)) if todo else {}), waits
        finally:
            with self.lock:  # after the store: a woken waiter finds the bundle (or, on failure, recomputes later)
                for s in owned:
                    self._inflight.pop(s).set()

    def _store(self, session: str, bundle: dict) -> bool:
        """Keep `bundle`; True when it differs from what clients last got (it took a new revision)."""
        h = _hash(bundle)
        prev = self.bundles.get(session)
        if prev and prev['bundle'].get('computed_seq', 0) > bundle.get('computed_seq', 0):
            return False  # computed before what we hold (a slow catch-up racing the tick): never goes back
        refs = set(bundle['cards']) | {str(t.get('id')) for t in bundle['tasks'] + bundle['done'] if t.get('id')}
        if prev and prev['hash'] == h:
            prev.update(bundle=bundle, refs=refs, lineage=set(bundle['lineage']))
            return False
        self.rev += 1
        self.bundles[session] = {'rev': self.rev, 'hash': h, 'bundle': bundle, 'refs': refs,
                                 'lineage': set(bundle['lineage'])}
        return True

    def _publish(self, computed: dict[str, dict], *, heartbeat: bool = False) -> dict[str, dict]:
        """Store what was computed and push one batch (or a heartbeat when nothing changed and one is due)."""
        with self.compute_lock:
            prev = self.rev
            with self.lock:
                changed = {s: b for s, b in computed.items() if s in self.interest and self._store(s, b)}
            now = self.clock()
            if changed or (heartbeat and now - self.last_emit >= self.heartbeat_s):
                with self.lock:
                    # The chats this push vouches for: held, watched, and last computed without a failure. Clients
                    # re-stamp only these (a dropped or failing chat is never confirmed by a heartbeat).
                    fresh = sorted(s for s in self.interest if s in self.bundles and s not in self.failures)
                try:
                    self.emit({'epoch': self.epoch, 'prev': prev, 'rev': self.rev, 'bundles': changed, 'fresh': fresh,
                               **({} if changed else {'heartbeat': True})})
                    self.last_emit = now
                except Exception:  # no client / transport down: clients catch up, and see no heartbeat meanwhile
                    log.debug('session-panel feed: push failed', exc_info=True)
            return changed

    def tick(self) -> int:
        """One pass: recompute what changed and push one batch, else a heartbeat when due. Returns bundles pushed."""
        now = self.clock()
        with self.lock:
            self._expire(now)
            watched = list(self.interest)
        if not watched:
            return 0
        stamps = self.readers.stamps()
        full = now - self._last_full >= self.full_every_s
        if stamps == self._stamps and not full:
            self._publish({}, heartbeat=True)
            return 0
        changed_cards, self._cursor = self.readers.changed_cards(self._cursor)
        self._stamps = stamps
        if full:
            self._last_full = now
            # Open cards carry run evidence that expires: those chats every FULL_EVERY_S. A chat with none changes
            # only through card events (handled below) or a link adopted with no event: every QUIET_FULL_EVERY_S.
            with self.lock:
                dirty = {s for s in watched if s not in self.bundles or self.bundles[s]['bundle'].get('tasks')
                         or now - self.bundles[s]['bundle'].get('computed_at', 0) >= QUIET_FULL_EVERY_S}
            touched = {v for v in changed_cards.values() if v}
            with self.lock:
                for s in watched:
                    b = self.bundles.get(s)
                    if b and (b['refs'] & set(changed_cards) or b['lineage'] & touched):
                        dirty.add(s)
        else:
            touched = {v for v in changed_cards.values() if v}
            with self.lock:
                dirty = {s for s in watched if s not in self.bundles}
                for s in watched:
                    b = self.bundles.get(s)
                    if b and (b['refs'] & set(changed_cards) or b['lineage'] & touched):
                        dirty.add(s)
        started = time.monotonic()
        pushed, _ = self._compute_publish(sorted(dirty), always=True) if dirty else ({}, [])
        if dirty:
            self.last_tick_s = time.monotonic() - started
        if not pushed:
            self._publish({}, heartbeat=True)
        return len(pushed)

    # ── catch-up ────────────────────────────────────────────────────────────
    def changes(self, sessions: Iterable[str], *, since: int = 0, epoch: Optional[str] = None) -> dict:
        """Watch `sessions` and return every bundle of theirs newer than `since` (all of them when `epoch` is not
        this process's: the client's revisions mean nothing here). Missing bundles are computed now (and pushed, so
        every other client's revision chain stays unbroken)."""
        wanted = self.watch(sessions)
        reset = epoch != self.epoch
        with self.lock:
            missing = [s for s in wanted if s not in self.bundles]
        if missing:
            try:
                _, waits = self._compute_publish(missing)
            finally:
                release = getattr(self.readers, 'release', None)
                if callable(release):
                    release()  # request threads keep no connections between catch-ups
            # Another thread's compute of a chat we also miss: wait for it, bounded below the Desktop's request
            # timeout. Whatever is still computing is left out; its push reaches this client anyway.
            deadline = time.monotonic() + CATCHUP_WAIT_S
            for ev in waits:
                ev.wait(max(0.0, deadline - time.monotonic()))
        with self.compute_lock, self.lock:
            out = {s: self.bundles[s]['bundle'] for s in wanted
                   if s in self.bundles and (reset or self.bundles[s]['rev'] > since)}
            return {'epoch': self.epoch, 'rev': self.rev, 'reset': reset, 'bundles': out}

    def status(self) -> dict:
        with self.lock:
            return {'epoch': self.epoch, 'rev': self.rev, 'watched': len(self.interest), 'bundles': len(self.bundles),
                    'failures': {s: dict(f) for s, f in self.failures.items()}, 'last_tick_s': round(self.last_tick_s, 3),
                    'stopped': self.stopped.is_set()}


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
