# Test-suite stabilization: backup and backfill fixtures (mirror 5485, 5486, 5509, 5519)

## Behavior
No production behavior changes. Test-only changes:
- 5485: the SIGTERM mid-copy test now runs its child with `HERMES_PREFLIGHT_CLONE=0`, because it tests the locked page copy and the short-lived `.partial-probe` from clone detection is not readiness for the real staging copy; the rest of that test is unchanged in the diff. A new posix-only test, `test_sigterm_readiness_does_not_accept_a_transient_clone_probe`, re-runs it with clone enabled, an owned child and a socket barrier so the probe is observed and removed before the readiness assertion; per its docstring, removing the opt-out must fail it.
- 5486: the progress ordering assertion moves into `_assert_copy_progress` and checks `events[-2] is final` (identity) instead of `events.index(final)`, so equal duplicate final events no longer confuse it; a new test builds two equal-but-distinct final events and checks that misordered streams still fail.
- Kanban backfill fixtures stop re-migrating cold schemas per case: 5509 builds a module-scoped empty session-schema template (written with the SQLite backup API) that each case copies before constructing its real `SessionDB`; 5519 replaces that with a module-scoped snapshot of a real synced home (each `*.db` copied via SQLite backup, `-wal`/`-shm` sidecars removed) that is copied per case with the home/hub/environment isolation and monkeypatches reapplied.

## How it works
Fixture and helper changes plus new control tests; 5486 moves existing assertions into a helper and swaps one index-based check for an identity check.

## Code
Tests: `tests/hermes_cli/test_backup_preflight_staging.py`, `test_backup_preflight_progress.py`, `tests/tools/test_kanban_backfill_empty_events.py`.

## Tests
These are the tests. Added tests: the clone-probe SIGTERM regression, equal-but-distinct final progress events with misordered-stream controls, per-case session-template isolation (parametrized `first`/`second`), and a home-template test checking schema parity with a fresh `make_home`, separate inodes, and that mutating one copy leaves the template and another copy untouched. Not executed in this mirror pass.

## Applying
Apply in order; 5519 supersedes the approach of 5509 while keeping its checks.

## Gotchas
The isolation test asserts copied databases have different inodes from the template and that writes to one copy do not appear in the template or another copy.

## plugin?
No.
