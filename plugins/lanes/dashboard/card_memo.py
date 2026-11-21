"""Cross-request memo of per-card decoded history, validated on every use.

Lanes counts are recomputed by core on every change. The expensive part of a
card projection is re-reading and re-decoding each open card's whole event
history, although a status/create/archive change touches one card. Each entry is
bound to the card's own ``(count, max id)`` in the source it is read from,
fetched fresh with every request, so any appended or removed event rebuilds
exactly that card. Nothing here holds a count: the projection still runs in
core against the same rows and gives the same totals as a cold recompute.
"""
from __future__ import annotations

import threading
from collections import OrderedDict


class CardMemo:
    def __init__(self, limit=1024):
        self.limit = limit
        self.lock = threading.Lock()
        self.entries = OrderedDict()  # (source identity, card id) -> (stamp, events, derived)
        self.hits = self.misses = 0

    def _entry(self, key, stamp, build):
        with self.lock:
            old = self.entries.get(key)
            if old is not None and old[0] == stamp:
                self.entries.move_to_end(key)
                self.hits += 1
                return old
        built = (stamp, build(), {})
        with self.lock:
            self.misses += 1
            self.entries[key] = built
            self.entries.move_to_end(key)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return built

    def bind(self, evidence):
        """Route one request-local evidence object's history reads through the memo.

        The memo is consulted only after a stamp read on the request's own
        snapshot, so a card changed before this request began is rebuilt.
        """
        identity = getattr(evidence, 'identity', None)
        if identity is None or identity('')[0] == 'memory':
            return evidence  # no stable source identity (in-memory/legacy): never share across requests
        original_events, original_pr_url = evidence.events, evidence.pr_url
        local = {}

        def entry(task_id):
            if task_id not in local:
                db = evidence.connection(task_id)
                stamp = tuple(db.execute('SELECT count(*), coalesce(max(id),0) FROM task_events WHERE task_id=?',
                                         (task_id,)).fetchone())
                local[task_id] = self._entry((evidence.identity(task_id), task_id), stamp,
                                             lambda: original_events(task_id))
            return local[task_id]

        def events(task_id):
            return entry(task_id)[1]

        def derived(task_id, name, compute):
            memo = entry(task_id)[2]
            if name not in memo:
                memo[name] = compute(entry(task_id)[1])
            return memo[name]

        def pr_url(task):
            # The URL also depends on row fields outside the history.
            keys = task.keys()
            name = ('url', task['status'], task['completion_contract'] if 'completion_contract' in keys else None)
            return derived(task['id'], name, lambda _events: original_pr_url(task))

        evidence.events, evidence.pr_url, evidence.derived = events, pr_url, derived
        return evidence
