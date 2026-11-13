# Scratch custody pressure API for worker disk reclaim

Patches: **3417**.

## Behavior
- Workspace trees of cards already evicted from the local replica (a custody receipt but no task row) were invisible to the worker disk-pressure reclaim, and the regular custody GC only removed them after retention. Core now exposes a small public API so a disk-pressure adapter can see and safely reclaim them.
- `pressure_candidates(conn, limit)` / `pressure_candidate(conn, task_id)` list receipts whose tree is a directory and whose recorded runs all ended, oldest latest-run-end first, from local reads only; listing grants nothing.
- `terminal_owner(hub, board, receipt)` applies the GC's rule (runs ended, then fresh terminal Board evidence).
- `removal_guard(conn, receipt)` is the guard for removing an archived tree for exactly this receipt (receipt unchanged, no local row, no active children, no overlapping receipt, no sharer, idle sessions, no owned job). A race keeps the tree.

## How it works
- 3417 adds these functions to `hermes_cli/kanban_scratch_custody.py`; archiving and complete-archive verification stay with the adapter, core keeps the guards and the receipt.

## Code
- `hermes_cli/kanban_scratch_custody.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_custody.py`: `test_pressure_candidates_are_local_oldest_first_and_bounded`, `test_pressure_candidates_without_custody_table`, `test_pressure_reclaim_inside_retention_archives_then_releases_receipt`, `test_pressure_terminal_owner_refuses_unverified_owner`, `test_pressure_terminal_owner_requires_ended_runs_before_board`, `test_pressure_guard_and_release_keep_tree_on_race`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- This patch is the core side only; the adapter that consumes it lives elsewhere.
