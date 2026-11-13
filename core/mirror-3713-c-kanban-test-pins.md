# Kanban test pins: reopen reclaim and CI-progress scan

Patches: **3708, 3713**.

## Behavior
- Tests only; no production change.
- Reopen: a stale copy in blocked, scheduled or triage status with a live run pointer has its dangling run reclaimed (ended, current run pointer cleared); the reopen refusal text is `None` for a done-held copy.
- CI-progress pool scan: an unreadable in-progress run list does not skip the completed runs; a non-I/O error during job reads propagates out of the scan with nothing remembered; the in-progress page size must cover the full run cap.

## How it works
3708 parametrizes the stale-copy reopen test over three statuses and adds a direct assertion on `unblock_refusal(verb="reopen")` for a done-held copy. 3713 adds tests killing three surviving mutants of `_recent_job_on`: `continue` turned into `break` after an unreadable run list, removal of the non-I/O re-raise in `batch()`, and a smaller in-progress page size; the existing many-runs test now places the match at the last index of the cap.

## Code
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_ci_progress.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_reopen_of_a_stale_copy_with_a_live_run_pointer_ends_that_run`, `test_reopen_refusal_text_is_none_for_a_done_held_copy`.
- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_unreadable_in_progress_run_list_does_not_skip_the_completed_runs`, `test_unexpected_job_read_error_propagates_out_of_the_scan`, `test_scan_lists_more_than_ten_in_progress_runs` (modified).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The done-held refusal case is unreachable from the CLI reopen command, which accepts the done hold first, so it is pinned only by a direct unit assertion.
