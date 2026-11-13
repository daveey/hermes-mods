# Kanban CI progress: pool-scan robustness

Patches: **3656, 3662**.

## Behavior
- With runner lists unreadable, the scan for a job a runner took on a queued run's labels no longer stops at the first failed job read; a later batch or a recently completed run can still match, avoiding a false `not_starting` (runners-offline) wake.
- If nothing matched and some read failed, the result is not cached, so an unreadable scan is not remembered.
- The in-progress run-list page size is pinned: more than ten in-progress runs are all scanned.

## How it works
3656 makes a failed job read (or run-list read) get noted while the scan continues through remaining batches and the completed-runs list; the first match wins and is remembered; over-budget/deadline handling is unchanged and non-I/O exceptions still propagate. 3662 is test-only: the fake `_scan_api` now honours `per_page` (default 30 when absent), so a mutant using a page size of 10 is caught.

## Code
- `hermes_cli/kanban_ci_progress.py`
- `tests/hermes_cli/test_kanban_ci_progress.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_unreadable_job_list_in_an_earlier_batch_does_not_end_the_scan`, `test_unreadable_job_list_does_not_skip_the_completed_runs`, `test_scan_lists_more_than_ten_in_progress_runs`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
