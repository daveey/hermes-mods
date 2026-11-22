"""Bounded, snapshot-consistent task-event transport (not yet wired to Feed).

An empty page certifies only the event log at `heads`, never source freshness:
unevented writes, restored databases and membership changes need semantic guards.
Cursors are process-local. Consumers must process resets and drain `more` without
waiting for another filesystem event before adopting a source stamp. Process reset
flags before committing the returned cursor; a reset is emitted on its first page.

Callers own the stable absolute source inventory (including alias deduplication).
`touched_sessions` means current creator sessions, NOT prior/subscriber membership;
retain old card dependencies and compare the authoritative membership projection.
Same-inode restores, inode reuse and event pruning/regrowth require an independent
semantic/writer-generation guard. This helper must never authorize freshness alone.
"""
from contextlib import closing
from pathlib import Path
from typing import Any
import sqlite3


def read_event_delta(paths, cursor=None, *, page_size=20000):
    """Read at most page_size events per source using owned, short transactions.

    cards retains source coordinates; IDs from different boards never overwrite.
    A bootstrap replays from zero and explicitly requests source reconstruction.
    Failed sources preserve their last cursor, but have no successful head/page.
    """
    if type(page_size) is not int or not 1 <= page_size <= 20000:
        raise ValueError('page_size must be an integer from 1 to 20000')
    previous = cursor or {}
    sources = {str(Path(raw)): Path(raw) for raw in paths}
    removed = set(previous) - set(sources)
    result: dict[str, Any] = dict(cards={}, touched_sessions=set(), reset_sources=set(removed),
                                 removed_sources=removed, unknown_sources={}, cursor={}, heads={}, more=set())
    for key, path in sources.items():
        prior = previous.get(key)
        if not (isinstance(prior, dict) and type(prior.get('id')) is int
                and prior['id'] >= 0 and isinstance(prior.get('identity'), (tuple, list))
                and len(prior['identity']) == 2
                and all(type(n) is int for n in prior['identity'])):
            prior = None
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            start = prior['id'] if prior else 0
            # mode=rw never creates the main file; query_only prohibits SQL writes.
            # SQLite may need WAL/SHM sidecars after last-close checkpoint, so a
            # read-only URI is not reliable here. No persistent connection is kept.
            with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=rw', uri=True, timeout=2)) as conn:
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                head = conn.execute('SELECT coalesce(max(id), 0) FROM task_events').fetchone()[0]
                reset = prior is None or tuple(prior['identity']) != identity or head < start
                if reset:
                    start = 0
                rows = conn.execute(
                    'SELECT e.id, e.task_id, t.session_id FROM task_events e '
                    'LEFT JOIN tasks t ON t.id=e.task_id '
                    'WHERE e.id > ? AND e.id <= ? ORDER BY e.id LIMIT ?',
                    (start, head, page_size)).fetchall()
                if any(not isinstance(row[1], str) or not row[1] for row in rows):
                    raise sqlite3.DatabaseError('event has no task identity')
                after = path.stat()
                if (after.st_dev, after.st_ino) != identity:
                    raise OSError('source identity changed during read')
        except (OSError, sqlite3.Error) as exc:
            result['unknown_sources'][key] = type(exc).__name__
            if prior is not None:
                result['cursor'][key] = dict(prior)
            continue
        if reset:
            result['reset_sources'].add(key)
        if rows:
            result['cards'][key] = {str(row[1]): row[2] for row in rows}
            result['touched_sessions'].update(row[2] for row in rows if row[2])
        end = rows[-1][0] if rows else start
        result['cursor'][key] = dict(identity=identity, id=end)
        result['heads'][key] = head
        if end < head:
            result['more'].add(key)
    return result
