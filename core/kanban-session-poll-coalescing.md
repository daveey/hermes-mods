# Overlapping Kanban session-list annotation reads

Patch 0671 (`b549dbc49c`); fix-up 0869 (`c826ad105b`): a cold poll behind another profile's or a failed read reads its own scope.

## Behavior

Overlapping session-list polls share one annotation refresh instead of occupying a worker for every identical read. A caller arriving during a refresh takes the last good snapshot; a caller arriving after completion reads again. Failed reads remain unknown, not stale success.

Since 0869: one server process serves several profiles, and results are kept per profile scope. A poll whose profile has no result yet, queued behind another profile's read or behind a read that failed, does its own read while it holds the flight lock instead of marking every row unknown. Same-profile overlapping polls still share one read. A failed read raises its real error (logged with traceback, rows `kanban_unknown`); a missing snapshot without an error logs one WARNING line and no traceback.

## How it works

A single refresher coalesces the annotation count, waiting-source and attention reads. It does not introduce a time-based cache that prevents a later non-overlapping poll from refreshing.

## Code

`hermes_cli/kanban_session_activity.py`.

## Tests

Canonical `scripts/run_tests.sh tests/hermes_cli/test_kanban_session_activity.py`: 51 passed on Linux during this mirror run. The source commit adds overlap and failure-path regression coverage. No live Desktop load measurement is claimed here.

0869 adds, in the same file (all fail on 0671 alone, pass with 0869; 55 passed): `test_cold_profile_poll_behind_another_profiles_read_gets_its_own_counts` (cross-profile, one read per scope), `test_cold_poll_behind_a_failed_read_retries_once`, `test_cold_poll_whose_retry_also_fails_is_unknown_with_the_real_error` (the waiter's own traceback is logged, not a synthetic RuntimeError), and `test_snapshot_none_logs_one_line_without_a_synthetic_traceback`. The 38 test files importing the module: 610 passed.

## Applying

Apply after 0670, before 0672, in curated series order.

## Gotchas

A cold profile's poll may do one extra board read (~130 ms) while holding the flight lock. Since 1520, a last-good snapshot also bounds the initiating refresh caller's wait; it keeps its original read timestamp and is not reported as a fresh successful read.

## plugin?

No; the shared core session-list annotation reader.

## Bounded initiating refresh (1520)

### Behavior

With a previous snapshot for the scope, even the caller that wins the refresh lock waits only
`_REFRESH_WAIT_S = 1.0` for the new read. A fast read returns fresh data; a slow read returns the
previous tuple while the helper publishes its result for the next poll. Previous data retains its
read timestamp. A cold scope without previous data still reads inline; this is not a universal
one-second REST deadline.

### How it works and Code

`hermes_cli/kanban_annotation_refresh.py::refresh_bounded` transfers the already-held lock to a
context-propagating daemon thread and waits on an event. The helper releases the lock on success or
failure. Fast errors reach the caller; a late background error keeps the previous read and logs it.
`kanban_session_activity.py` records tuple identity/read time and uses that date when serving stale
parts. `kanban_session_activity_release.py` receives the unchanged owner-terminal attestation rule
as a file-size extraction; no release authority is broadened.

### Tests

Source `tests/hermes_cli/test_kanban_annotation_refresh_bounded.py` covers slow/fast refresh,
fast/late errors and lock release, cold inline reads, real helper threads despite a stubbed
`threading.Thread`, and `test_session_detail_route_answers_within_bound_during_slow_refresh` for
the session-detail route. These tests were not run in this documentation pass; no live Desktop
burst benchmark is claimed.

### Applying and Gotchas

Apply 1520 in the complete ordered mirror via the [README replay recipe](README.md#applying).
The bounded wait depends on an existing same-scope snapshot. Late failures are not fresh success,
and the underlying refresh is not cancelled when the caller stops waiting. Historical test results
above describe earlier publication runs, not this follow-up.
