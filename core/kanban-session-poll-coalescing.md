# Overlapping Kanban session-list annotation reads

Patch 0661 (`b549dbc49c`); fix-up 0727 (`c826ad105b`): a cold poll behind another profile's or a failed read reads its own scope.

## Behavior

Overlapping session-list polls share one annotation refresh instead of occupying a worker for every identical read. A caller arriving during a refresh takes the last good snapshot; a caller arriving after completion reads again. Failed reads remain unknown, not stale success.

Since 0727: one server process serves several profiles, and results are kept per profile scope. A poll whose profile has no result yet, queued behind another profile's read or behind a read that failed, does its own read while it holds the flight lock instead of marking every row unknown. Same-profile overlapping polls still share one read. A failed read raises its real error (logged with traceback, rows `kanban_unknown`); a missing snapshot without an error logs one WARNING line and no traceback.

## How it works

A single refresher coalesces the annotation count, waiting-source and attention reads. It does not introduce a time-based cache that prevents a later non-overlapping poll from refreshing.

## Code

`hermes_cli/kanban_session_activity.py`.

## Tests

Canonical `scripts/run_tests.sh tests/hermes_cli/test_kanban_session_activity.py`: 51 passed on Linux during this mirror run. The source commit adds overlap and failure-path regression coverage. No live Desktop load measurement is claimed here.

0727 adds, in the same file (all fail on 0661 alone, pass with 0727; 55 passed): `test_cold_profile_poll_behind_another_profiles_read_gets_its_own_counts` (cross-profile, one read per scope), `test_cold_poll_behind_a_failed_read_retries_once`, `test_cold_poll_whose_retry_also_fails_is_unknown_with_the_real_error` (the waiter's own traceback is logged, not a synthetic RuntimeError), and `test_snapshot_none_logs_one_line_without_a_synthetic_traceback`. The 38 test files importing the module: 610 passed.

## Applying

Apply after 0660, before 0662, in curated series order.

## Gotchas

A cold profile's poll may do one extra board read (~130 ms) while holding the flight lock. A last-good snapshot is only for overlapping requests; a failed refresh must not be reported as a fresh successful read.

## plugin?

No; the shared core session-list annotation reader.
