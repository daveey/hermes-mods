# Exact incremental Kanban dashboard reads

Mirror batch 6; ordinals 4021, 4022, 4023.

## Behavior

Each board GET projects current committed rows and volatile bookkeeping rather than serving a stale TTL snapshot. Retained materialization avoids full parsing while preserving bounded done-history display and current counts.

## How it works

board_rows serializes per-observer refreshes and reads change cursor plus rows in one SQLite snapshot. The incremental materializer fetches displayed IDs union marked IDs by primary key, reuses unchanged parsed tuples, refreshes affected rollups/graphs and overlays current summaries/runs/wake state. It retains ranked done membership and falls back to full reads when the window or cursor cannot be trusted, the query changes, the cap is disabled, or changes exceed the bound. Count caching uses content revision so heartbeat-only commits do not invalidate switcher counts. Two explicit boundary-lint allowances document bundled-plugin internal imports, not a public extension API.

## Code

Source paths touched by this feature group (including regression fixtures):

- `plugins/kanban/dashboard/board_counts.py`
- `plugins/kanban/dashboard/board_incremental.py`
- `plugins/kanban/dashboard/board_read.py`
- `plugins/kanban/dashboard/board_rows.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `scripts/boundary_lint_baseline.json`
- `tests/plugins/test_kanban_board_delta.py`
- `tests/plugins/test_kanban_board_p2.py`
- `tests/plugins/test_kanban_board_read_cache.py`
- `tests/plugins/test_kanban_board_rows_cache.py`
- `tests/plugins/test_kanban_cold_start_reads.py`

Mail entries in the ordered series:

- 4021: [`1b2eb076a5269a688b53a24280c32b530f9024d4`](patches/4021-daveey-WIP-incremental-dashboard-P2-reads-and-bookkeeping-overlay.patch)
- 4022: [`1d7e1e391d08c48722fec89d834b1badd3482a05`](patches/4022-daveey-Refresh-only-changed-dashboard-neighbours-and-preserve-exact-GET-tests.patch)
- 4023: [`4d21b26801385f8b5f213c4f6bbcb63f2263831a`](patches/4023-daveey-Register-intended-incremental-dashboard-core-boundaries.patch)

## Tests

- `tests/plugins/test_kanban_board_delta.py`: delta projections equal cold full reads across mixed writes, child completion, priorities (including hidden waiters), links/deletions, tenant/archive changes, done-window entries/underflow and chains. Counters assert no unchanged parses, no hidden-history parses, changed-neighbour-only rollups/graphs, and retained done-set identity. Unmarked ready/done bookkeeping and heartbeat events/runs remain visible.
- `tests/plugins/test_kanban_board_p2.py`: real board GET sees heartbeat, progress, link and done-window changes without full materialization; missing triggers force fallback; a write between cursor and materialization is seen on the next snapshot; counts ignore heartbeat but refresh for status.
- `tests/plugins/test_kanban_board_read_cache.py`: exact next-GET writes, clock ages and heartbeat state, separate query caches, busy writes and prewarm overlap are asserted; a blocked exact read waits rather than returning stale output.
- `tests/plugins/test_kanban_board_rows_cache.py`: local/external writes and wake-state changes arrive without full rebuild; each GET reprojects time over retained rows.
- `tests/plugins/test_kanban_cold_start_reads.py`: warmup covers selected boards once, preserves query-only counts behavior and default warmup after discovery failure. Patch 4023 changes only the boundary baseline; its source report names the existing boundary-lint tests, not a new test file.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

4021 is explicitly a WIP source checkpoint; 4022 aligns consumers and 4023 records intended dependencies. Synthetic replay or focused tests are not live budget/display proof. Exact reads may wait; no constant-time or zero-SQL claim is implied.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

This modifies the bundled Kanban dashboard plugin and core adapters. It is not a standalone external plugin; the boundary baseline does not promote internal modules to the third-party contract.
