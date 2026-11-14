# Kept-reader eviction and stale-generation tests

Mirror snapshot 4059; covered ordinals: 3903, 3949.

## Behavior

Borrowed Kanban SQLite connections are not closed under their borrowers or left for garbage collection after cap eviction. Additional tests pin reopening after database/WAL-generation changes, externally closed connections, open transactions, and registry shutdown.

## How it works

`hermes_cli/kanban_kept_reader.py:_enforce_cap` first evicts and explicitly closes the oldest idle entry. If all candidates are borrowed, it detaches the oldest; the borrow's `finally` notices it is no longer the map entry and closes it on exit. Patch 3949 changes tests only, checking already-existing identity and shutdown contracts for Kanban and session-state readers.

## Code

- `hermes_cli/kanban_kept_reader.py`

### Mirrored commits

- 3903: `84c7510975cd5de047ea84489bb83d87a959e8ab` — Explicitly closes cap-evicted kept readers without closing borrowers early.
- 3949: `9619532248cd6ed295d766c61bff2a601cec6070` — Pins kept-reader stale generations, transactions, and shutdown.

## Tests

- `tests/hermes_cli/test_kanban_kept_reader.py`: nested borrows across the cap preserve the borrowed connection, close the idle eviction victim, and eventually close an all-borrowed detached entry. Later tests remove `-shm`, close a handle without retiring its map entry, or leave a transaction open, then assert a fresh readable connection and explicit closure where applicable.
- `tests/hermes_state/test_state_kept_reader.py`: sidecar deletion or replacement of a rollback-journal database reopens the state handle; shutdown `close_all` retires kept readers and a subsequent read gets a new handle.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

A stable main-file inode does not imply a stable WAL generation. A transaction left open pins a snapshot and must not be kept. Cap enforcement may temporarily leave detached borrowed handles alive until their borrowers exit; that is not permission to close them early.

## Plugin?

No. Connection generation and lifetime management are core database responsibilities.
