# Exact incremental dashboard reads and starvation indexing

Mirror entries: 4471, 4472, 4473, 4539, 4540.

## Behavior

- **4471** — Dashboard deltas compare narrow typed bookkeeping fields, fetch moved wide rows only, and reuse immutable episode payload decoding.
- **4472** — Board-switcher counts move per-task status buckets using the task-change cursor instead of recounting every changed board.
- **4473** — The boundary-lint baseline records the dashboard’s intended task-change and bookkeeping-column imports.
- **4539** — Run-start indexing is installed during fresh/additive initialization and legacy rebuild to bound starvation clock reads.
- **4540** — The run-start index becomes partial and opt-in to the starvation query, avoiding implicit plan/order changes for unrelated run readers.

## How it works

A trusted content cursor says unmarked rows can differ only in declared bookkeeping columns. The dashboard compares those columns with typed serialization, so values such as integer and floating representations are not silently equated. Missing retained state, shape changes or unverifiable inputs fall back to full exact reads. ID-only task fetches avoid dispatch-priority traversal; correlated event marks and a run-bookkeeping covering index reduce wide-row access. Episode JSON decoding is retained on the immutable event snapshot while clock-dependent decisions stay live.

Status-count entries retain the cursor and each task's prior status. In one read snapshot they obtain changed IDs and current statuses, then apply insert/move/delete bucket deltas under a per-board lock. A legacy board uses exact GROUP BY; first reads, cursor FULL, generation/schema change and more than 512 changes cause a whole-board read. Failed updates publish no stale entry.

The final starvation index uses the tautology `(started_at+0 IS NULL OR started_at+0 IS NOT NULL)` as a partial-index predicate. Only the starvation MAX reader spells that predicate. All storage classes remain admitted, but existing run readers cannot unexpectedly choose this new index. Migration replaces the earlier global experimental definition and is a no-op when settled.

## Code

- `hermes_cli/kanban_board_share.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_diagnostic_history.py`
- `hermes_cli/kanban_diagnostics.py`
- `plugins/kanban/dashboard/board_counts.py`
- `plugins/kanban/dashboard/board_incremental.py`
- `plugins/kanban/dashboard/board_read.py`
- `plugins/kanban/dashboard/board_run_history.py`
- `scripts/boundary_lint_baseline.json`

## Tests

Source tests compare incremental and cold payloads, typed bookkeeping changes, deletions/schema drift, live clock decisions, covering-index migration and query plans. Count tests cover status/null transitions, heartbeat-only commits, FULL/large deltas and recovery after failure. Starvation tests compare indexed MAX with scans across storage classes, fresh/additive/rebuilt schemas, missing-column fallback, ANALYZE plans and VM-work bounds.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_db_init.py`
- `tests/hermes_cli/test_kanban_starvation_index.py`
- `tests/hermes_cli/test_s1_planner_diagnostic.py`
- `tests/plugins/test_kanban_board_counts_delta.py`
- `tests/plugins/test_kanban_board_e7.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

These are exact-read optimizations, not permission to cache dynamic policy decisions or change polling. An earlier intermediate dashboard change documented a failed scale gate; do not treat its historical component timings as end-to-end acceptance. No fresh benchmarks or live CPU measurements were run in this documentation pass.
