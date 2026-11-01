# Sync repair scans invalidate on input changes, not dispatch clocks

## Behavior

The Board sync client skips its repair scans (partition links, remote parents, divergence, missing children, placeholder retirement) when their inputs have not changed. Before this change, the cache key was the live file's `PRAGMA data_version`. That value moves on every commit, including the dispatcher's own clock and bookkeeping writes, so idle rounds kept repeating the scans. Now only writes to the scans' input tables, or a schema change, invalidate the key.

## How it works

- `hermes_cli/kanban_sync_repair_revision.py` (new) keeps a one-row `kanban_repair_revision` counter. `AFTER INSERT/UPDATE/DELETE` triggers (`kanban_repair_*`) bump it on `tasks`, `task_runs`, `task_links`, `task_chat_links`, `kanban_sync_outbox`, `kanban_sync_tombstones` and `kanban_sync_remote_parents`. The triggers are independent of the sync capture triggers, because repairing writes made during a capture gap is why the scans exist.
- `ensure_revision` takes no write transaction when the table, row and every trigger definition are intact. Otherwise, in one transaction, it creates whatever is missing, bumps the counter (a gap or replaced definition must invalidate) and replaces only the drifted triggers. Extra unrelated triggers do not cause reinstallation.
- `sync_board` calls `ensure_revision` each round. `_repair_inputs` reads `(revision, schema_version)` before the scans, so a relevant commit during a scan re-arms the next round. A replaced file (new inode) is reopened with a new generation, so a restored counter never matches an old key. `_BoardWatch.version` was removed.

## Code

- Patch **1483** (`c11f90d244`), PR #494: `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_sync_repair_revision.py`.
- Follows [link-projection scope](links-fingerprint-scope.md) (1478), the same idle-CPU line of work.

## Tests

- `tests/hermes_cli/test_kanban_sync_repair_revision.py`: each input-table mutation bumps the revision but a rolled-back one does not; install is read-only when intact and repairs a missing trigger; additional triggers do not cause perpetual reinstallation; a removed revision table is recovered.
- `tests/hermes_cli/test_kanban_sync_idle_round.py` (new cases): dispatch-clock commits do not repeat unchanged repairs; the revision rolls back and tracks writes from another connection; a write during a repair is not cached as already repaired.
- Exact PR head `c11f90d244d480a7fc42cedc28609e20b7226d0f` passed CI run `41294239072`.

These tests show invalidation behavior. They do not measure a production CPU reduction.

## Applying

Full-series recipe in [README.md](README.md), snapshot 1484. The mirror policy's existing reviewed fingerprint for `tests/hermes_cli/test_kanban_sync_idle_round.py` moved to the post-1483 blob pair. Its public normalization is unchanged: one internal investigation id in the module docstring becomes `example-investigation`. See [mirror-1484-verification.md](mirror-1484-verification.md).

## Gotchas

- A new table that the repair scans read must be added to `TABLES`, or writes to it will not invalidate the cache.
- Don't fold these triggers into the capture triggers: disabling capture would then hide exactly the writes repair must see.

## Plugin?

No. This is the core sync client's internal cache key.
