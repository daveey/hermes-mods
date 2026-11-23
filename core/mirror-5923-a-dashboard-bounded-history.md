# Bounded dashboard histories and nonblocking count polls

Patch coverage: **5843, 5845–5848, 5856**.

## Behavior

- Board refreshes reuse unchanged event-history heads while rereading the recent tail; older edits force an exact reload.
- Board count/version polls no longer hold behind an entire materialization build.
- Shadow boards resolve ownership collisions and the combined done window before loading full cards and diagnostics, while preserving exact totals and fresh projections.

## How it works

Within a read transaction, SQL ordered aggregates build typed signatures and event seals, returning one row per task rather than all history rows to Python. A seal retains a stable head and rereads the newest 16 events to catch late stamps. Typed serialization distinguishes storage classes and embedded NULs; real zero intentionally prevents reuse. SQLite before 3.44 uses the previous full-load/marshal paths. A separate refresh lock serializes builds while the observer connection lock covers only short commit-clock reads.

Shadow selection first inventories narrow id/status/completion fields, excludes every live-owned id, ranks done cards with the live window, then hydrates only selected raw SQL keys. Display fields follow task parsing, including legacy blob values. A missing or `FULL`/otherwise untrusted cursor discards previous diagnostic history, still within the bounded selection. Cached rows are projected anew each poll. Patch 5856 records the two intentional dashboard/storage imports in the boundary-lint baseline.

## Code

- `hermes_cli/kanban_diagnostic_history.py`: immutable history extension.
- `plugins/kanban/dashboard/{board_event_history,board_run_history,board_rows,board_read,board_shadow}.py`: seals, signatures, locks, keyed hydration and merging.
- `scripts/boundary_lint_baseline.json`: explicit import-boundary allowances.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

- `tests/plugins/test_kanban_board_hot_rows.py`: tail-only reads, old edits/deletes/backfills, late stamps, typed signatures, transaction precondition and nonblocking count polls.
- `tests/plugins/{test_kanban_event_marks,test_kanban_projection_cost,test_kanban_run_history}.py`: event/run correctness and SQL stepping shape.
- `tests/plugins/{test_kanban_shadow_cost,test_kanban_shadow_refresh}.py` with `_kanban_shadow_oracle.py`: legacy payload parity, display-bound reads, total counts, collisions, filter/rank/type parity, fresh diagnostics, concurrent readers and cache clearing. Follow-ups cover same-id edits with missing triggers, full marks, revision gaps and schema changes.
- Patch 5856 is a lint-baseline-only acknowledgement; it adds no regression test.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

A seal proves equality only inside its read snapshot. A count/max mark cannot detect same-id rewrites. Selection bounds expensive hydration, not every inventory scan. Boundary allowances are not permission to bypass storage ownership. Source cost tests exist; no speedup was measured in this pass.

## plugin?

The existing dashboard plugin consumes these changes, but the implementation also changes core history/storage interfaces; it is not an independent external plugin.
