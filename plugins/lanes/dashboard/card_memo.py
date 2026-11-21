"""Bounded decoded-history reuse; counts and derived values stay request-local.

Validate ordered raw event content on the actual request connection, not a path
stat or count/max(id). This detects edits, same-ID replacements and rebuilt DBs,
including an old connection retained across atomic pathname replacement. Only
JSON decoding is shared; all mutable objects handed to consumers are private.
"""
from __future__ import annotations

import copy
import threading
from collections import OrderedDict


class CardMemo:
    def __init__(self, limit=1024):
        self.limit = limit
        self.lock = threading.Lock()
        self.entries = OrderedDict()  # (source identity, card id) -> (raw content, decoded events)
        self.hits = self.misses = 0

    def _entry(self, key, stamp, build):
        with self.lock:
            old = self.entries.get(key)
            if old is not None and old[0] == stamp:
                self.entries.move_to_end(key)
                self.hits += 1
                return old
        built = (stamp, copy.deepcopy(build()))
        with self.lock:
            self.misses += 1
            self.entries[key] = built
            self.entries.move_to_end(key)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return built

    def bind(self, evidence):
        """Memoize decoded events, never rows, counts or mutable derived values."""
        identity = getattr(evidence, 'identity', None)
        if identity is None or identity('')[0] == 'memory':
            return evidence  # no stable source identity: never share across requests
        original_events, original_pr_url = evidence.events, evidence.pr_url
        local, computed = {}, {}

        def events(task_id):
            if task_id not in local:
                db = evidence.connection(task_id)
                stamp = tuple(tuple(row) for row in db.execute(
                    'SELECT id, kind, payload FROM task_events WHERE task_id=? ORDER BY id', (task_id,)))
                shared = self._entry((evidence.identity(task_id), task_id), stamp,
                                     lambda: original_events(task_id))
                local[task_id] = copy.deepcopy(shared[1])
            return local[task_id]

        def derived(task_id, name, compute):
            key = (task_id, name)
            if key not in computed:
                computed[key] = compute(events(task_id))
            return computed[key]

        def pr_url(task):
            keys = task.keys()
            name = ('url', task['status'], task['completion_contract'] if 'completion_contract' in keys else None)
            return derived(task['id'], name, lambda _events: original_pr_url(task))

        evidence.events, evidence.pr_url, evidence.derived = events, pr_url, derived
        return evidence
