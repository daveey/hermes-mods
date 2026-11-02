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

## Follow-up patches (1641, 1642): a busy Board waits 30 s between cursor-only repair passes

**1641** (`1d513947fc`, `fix(kanban-sync): a busy Board no longer re-runs the full repair pass every round`) and **1642**
(`03d35b4082`, `a rebuilt replica never defers its repair pass, even when the inode is reused`).

- **Behavior.** The replica's feed cursor is one of the repair inputs and moves on every change any gateway makes to the
  board, so on a busy Board the sync loop re-ran the full repair pass (partition links, divergence, missing children,
  placeholder links) nearly every round (measured: 29 rounds in 90 s with no local work, about 13.5% of a core on the
  sync thread). Now a change that only moved the replica cursor (same replica file `(dev, ino)`, live revision, schema,
  mode, gateway and placement) waits until `REPLICA_REPAIR_MIN_S` (30 s) after the last completed pass. Local writes, a
  rebuilt replica, and mode/gateway/placement changes still repair at once. The cheap foreign-parent mirror still runs
  every changed round, so a finished foreign parent releases its child without delay. `SyncResult.repair_deferred`
  reports a deferral. A deferred pass runs when due even if nothing else changes. 1642: a snapshot rebuild deletes and
  recreates the replica file and ext4 often reuses the inode, so `_pulled_replica` now yields a `rebuilt` flag and a
  rebuilt round never defers.
- **How it works.** `hermes_cli/kanban_sync_client.py`: `_defer_repair(key, inputs)` compares the inputs with
  `_REPAIRED[key]` ignoring only the cursor; when within the window it records the due time in `_REPAIR_DEFERRED`.
  `_REPAIRED_AT` stamps completed passes with `_repair_clock()` (monotonic). Each round first drops its board's stale
  deferral, so a failing board cannot make the loop spin. The sync loop selects boards from `deferred_repairs_due` and
  caps its wait at `next_deferred_repair`.
- **Code.** `hermes_cli/kanban_sync_client.py`.
- **Tests** (not rerun by this mirror pass). New `tests/hermes_cli/test_kanban_sync_repair_window.py`:
  `test_remote_changes_right_after_a_pass_do_not_rerun_it`, `test_a_burst_of_remote_changes_runs_one_repair_pass_per_window`,
  `test_a_local_write_inside_the_window_still_repairs_at_once`, `test_an_uncaptured_local_write_inside_the_window_is_healed`,
  `test_a_rebuilt_replica_repairs_at_once` (1642: a real `svc.compact` rebuild with the inode forced equal),
  `test_a_foreign_parent_finishing_inside_the_window_releases_the_child_now`, `test_a_failed_round_drops_its_deferral`,
  `test_the_sync_loop_runs_a_deferred_pass_when_it_is_due`, and 1642's `test_a_mode_change_inside_the_window_repairs_at_once`
  (shadow↔primary). `test_kanban_sync_idle_round.py`'s remote-change step now advances the repair clock past the window.
  The CPU figures come from the commit message's live replay, not from these tests.
- **Applying.** Apply 1641 then 1642 after 1483. `tests/hermes_cli/test_kanban_sync_idle_round.py` carries a reviewed
  mirror normalization (see Applying above); 1641 edits that file, so the mirror verification may need to re-pin it.
- **Gotchas.** Repairs of remote-only drift can now lag up to 30 s. A gap the repair heals that is not a local write
  and not a rebuild (only the cursor moved) waits for the window.
