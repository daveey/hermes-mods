"""Transport-neutral panel consumer of the published facts-v1 subscription.

The owner has already run the canonical panel reducer. This adapter only bridges
its revision domain into Feed's existing revision/push domain. It never captures
sources, reconciles requests or constructs a new interpretation of panel inputs.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
import copy

import threading


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def mutable(value):
    """Detach immutable contract values before handing them to the legacy writer."""
    if isinstance(value, Mapping):
        return {k: mutable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [mutable(v) for v in value]
    return copy.deepcopy(value)


class PanelSubscription:
    """One bounded inbox; callbacks only enqueue, publication runs on its owner.

    ``key`` is the published FactKey constructor, not an import of core internals.
    Changing watch membership establishes a fresh atomic snapshot subscription.
    The subscription generation fences callbacks that were in flight at close.
    """
    MAX_ENVELOPES = 64
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self, feed, store, scope, key, *, demand=None):
        self.feed, self.store, self.scope, self.key = feed, store, scope, key
        self.demand = demand
        self.subscription = None
        self.members = None
        self.cursor = None
        self.lock = threading.Lock()
        self.pending = deque()
        self.pending_bytes = 0
        self.reset_needed = False
        self.closed = False
        self.generation = 0
        self.feed._facts_mode = True
        self.feed._watching = True
        self.deadlines = {}
        self._status()

    def _status(self):
        self.status_wire = self.feed._encode({k: v for k, v in self.feed.status().items() if k != 'stopped'})

    def _enqueue(self, generation, delta):
        # Contract values are immutable. Account conservatively for the full
        # envelope, not only changed-key count (one panel can be large).
        size = len(repr(delta).encode('utf-8'))
        with self.lock:
            if self.closed or generation != self.generation:
                return
            if (self.reset_needed or len(self.pending) >= self.MAX_ENVELOPES
                    or self.pending_bytes + size > self.MAX_BYTES):
                self.pending.clear()
                self.pending_bytes = 0
                self.reset_needed = True
            else:
                self.pending.append((delta, size))
                self.pending_bytes += size
        self.feed._wake.set()

    def _subscribe(self, members):
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.pending.clear()
            self.pending_bytes = 0
            self.reset_needed = False
        old, self.subscription = self.subscription, None
        if old is not None:
            old.close()
        self.members = members
        self.cursor = None
        if members:
            if self.demand is not None:
                for session in members:
                    self.demand('panel_bundle', {'session': session})
            subscription = self.store.subscribe(
                self.scope, kinds=('ChatWaitingFact',),
                keys=tuple(self.key(self.scope, 'ChatWaitingFact', s) for s in members),
                after=None, callback=lambda delta: self._enqueue(generation, delta))
            with self.lock:
                accepted = not self.closed and generation == self.generation
                if accepted:
                    self.subscription = subscription
            if not accepted:
                subscription.close()

    def _unavailable(self, sessions, code):
        # Missing/unavailable is NOT deletion and does not mint empty panels.
        with self.feed.lock:
            for session in sessions:
                self.feed._retry.add(session)
                previous = self.feed.failures.get(session, {})
                self.feed.failures[session] = {
                    'count': previous.get('count', 0) + 1,
                    'error': code, 'at': self.feed.clock()}

    def _apply(self, delta):
        epoch, rev = field(delta, 'epoch'), field(delta, 'rev')
        reset = field(delta, 'reset', False)
        if not isinstance(epoch, str) or not isinstance(rev, int):
            return False
        if not reset:
            if self.cursor is None or epoch != self.cursor[0]:
                return False
            if rev <= self.cursor[1]:
                return True  # duplicate/delayed delivery cannot rewind a panel
            if field(delta, 'prev') != self.cursor[1]:
                return False
        elif self.cursor is not None and epoch == self.cursor[0] and rev < self.cursor[1]:
            return True
        computed, seen = {}, set()
        for fact in field(delta, 'upserts', ()):
            key = field(fact, 'key')
            session = field(key, 'id')
            if (field(key, 'scope') != self.scope or field(key, 'kind') != 'ChatWaitingFact'
                    or session not in self.members):
                continue
            seen.add(session)

            until = field(fact, 'valid_until')
            if field(fact, 'health') != 'fresh' or (until is not None and until <= self.feed.clock()):
                self._unavailable((session,), field(fact, 'error_code') or 'fact_source_unavailable')
                continue
            # Producer owns semantic time reductions; expiry here only withdraws
            # freshness if IPC stopped delivering before the published deadline.
            bundle = mutable(field(field(fact, 'value'), 'panel'))
            if not isinstance(bundle, dict) or not all(k in bundle for k in ('tasks', 'done', 'cards', 'lineage', 'computed_at')):
                self._unavailable((session,), 'fact_panel_invalid')
                continue

            computed[session] = bundle
            self.deadlines[session] = until
        if reset:
            self._unavailable(set(self.members) - seen, 'fact_panel_pending')
        for key in field(delta, 'deletes', ()):
            if field(key, 'scope') == self.scope and field(key, 'kind') == 'ChatWaitingFact':
                session = field(key, 'id')
                if session in self.members:
                    self._unavailable((session,), 'fact_panel_removed')
        with self.feed.lock:
            for session in computed:
                self.feed.failures.pop(session, None)
                self.feed._retry.discard(session)
                self.feed._decision_failures.discard(session)
                self.feed._board_failures.discard(session)
        self.feed._publish(computed, authoritative=True)
        self.cursor = (epoch, rev)
        return True

    def step(self):
        """Drain interest and queued deltas; no source reads, including cold keys."""
        if self.closed or self.feed.stopped.is_set():
            return
        self.feed._wake.clear()
        self.feed._drain()
        with self.feed.lock:
            self.feed._expire(self.feed.clock())
            members = tuple(sorted(self.feed.interest))
        for session, until in tuple(self.deadlines.items()):
            if session not in members:
                self.deadlines.pop(session, None)
            elif until is not None and until <= self.feed.clock():
                self._unavailable((session,), 'fact_expired')
                self.deadlines.pop(session, None)
        with self.lock:
            reset = self.reset_needed
        if reset or members != self.members:
            if reset:
                self._unavailable(members, 'fact_subscription_overflow')
            self._subscribe(members)
        while True:
            with self.lock:
                if not self.pending:
                    break
                delta, size = self.pending.popleft()
                self.pending_bytes -= size
            if not self._apply(delta):
                self._unavailable(members, 'fact_subscription_gap')
                self._subscribe(members)
                # Avoid an unbounded loop if a broken source repeats a gap.
                break
        # Heartbeat confirms transport only; _publish excludes failed/carried
        # bundles. No beat() here: the producer owns deadline reductions.
        self.feed._publish({}, heartbeat=True)
        self._status()

    def close(self):
        with self.lock:
            self.closed = True
            self.generation += 1
            self.pending.clear()
            self.pending_bytes = 0
        subscription, self.subscription = self.subscription, None
        if subscription is not None:
            subscription.close()
