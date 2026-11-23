"""Panel adapter for the native keyed hints shipped by loaded notifications.

Hints are discovery only. Canonical panel readers/writers still own hydration.
Unsupported cores/sources retain legacy admission. One watcher owns the 30s
recovery clock; hints never postpone that deadline.
"""
from __future__ import annotations

import time


class KeyedHints:
    def __init__(self, *, clock=time.monotonic):
        from tui_gateway.notification_loaded_f import Scheduler
        from tui_gateway.kanban_remote_notices import hub_db_path
        self.reader = Scheduler()  # passive hint cursor; no notification actions/thread
        self.reader.source_dirty = False
        self.path = hub_db_path()
        self.clock = clock
        self.next_reconcile = clock() + 30.0
        self.known = {}

    def due(self):
        now = self.clock()
        if now < self.next_reconcile:
            return False
        self.next_reconcile = now + 30.0
        return True

    def sessions(self, held, profile):
        from contextlib import closing
        from tui_gateway.kanban_remote_notices import _ro
        from tui_gateway.kanban_remote_notices import _linked_chats
        from tui_gateway.kanban_remote_notices_keyed import read_rows
        with closing(_ro(self.path)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='replica_poll_metadata'").fetchone() is None:
                raise RuntimeError('native keyed index unavailable; retain legacy source')
        prior = self.reader.last_seq, self.reader.source_identity
        self.reader.source_dirty = False
        keys = self.reader._changed_keys()
        if not keys:
            return set()
        try:
            me, _ready, rows = read_rows(self.path, keys)
            if not _ready:
                raise RuntimeError('native keyed source incomplete; retain legacy source')
        except Exception:
            self.reader.last_seq, self.reader.source_identity = prior
            raise
        links = { (gw, key): {sid for p, sid in _linked_chats(task, me) if p == profile}
                  for gw, key, task in rows }
        affected = set()
        for gw, key in keys:
            chats = self.known.get((gw, key), set()) | links.get((gw, key), set())
            # The canonical key is gateway/profile/board/task; a held dependency
            # may not itself be linked to this chat. Conservative legacy refs
            # lacking owner coordinates retain invalidation, never false freshness.
            card_id = key.rsplit('/', 1)[-1]
            for session, value in held.items():
                if chats.intersection(value['lineage']) or card_id in value['refs']:
                    affected.add(session)
            self.known[(gw, key)] = links.get((gw, key), set())
        # Membership cache only covers watched chats; no unbounded source history.
        lineages = set().union(*(v['lineage'] for v in held.values())) if held else set()
        self.known = {k: v for k, v in self.known.items() if v & lineages}
        return affected
