# Runtime follow-ups 1470–1473

## Behavior

- **1470** serializes aggregate profile-session scans within a backend without caching their results; each request retains its own pagination parameters.
- **1471** makes isolated-serve test teardown tolerate processes that have already exited. This is test-harness work, not a new process lifecycle guarantee.
- **1472** refreshes refresh-only notices once per backend and candidate/branch rather than on every sweeper pass. Failed refreshes retry; new notice rows and superseding card changes still act.
- **1473** skips idle Board-sync writes and unchanged repair scans, and reuses outbox read connections while reopening replaced database files.

## How it works

A scan lock serializes the uncached aggregate route. Serve teardown catches process disappearance. Notice refresh memoization records only successful refreshes and prunes to current candidates. Sync skips an empty unchanged-cursor apply, gates repair work on input state captured before scans, and retains file-identity-aware read connections. No TTL-based result cache is introduced.

## Code

- `hermes_cli/web_routers/profiles.py`
- `tests/hermes_cli/test_isolated_serve_ledger_marker.py`
- `tui_gateway/notice_guarantee.py`
- `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_sync_partition_repair.py`

## Tests

Inspected source assertions (not rerun by this mirror):

- `test_profiles_sessions_scan_serialization.py`: concurrent REST requests never overlap scans, identical requests still scan, limits remain distinct, exceptions release admission.
- `test_isolated_serve_ledger_marker.py`: teardown regressions for disappeared processes and the existing ledger-marker integration.
- `test_notice_guarantee.py`: repeat human/budget passes, new rows, failed-refresh retry and supersede after refresh.
- `test_kanban_sync_idle_round.py`: shadow/primary idle rounds commit nothing; repair scans re-arm on local/remote changes; capture gaps still heal; outbox polls reuse a connection, see new work and reopen replaced files.

The mirror's executed inventory, replay and tree checks are recorded in [mirror-1474-verification.md](mirror-1474-verification.md). No live CPU-reduction or deployment claim is made.

## Applying

Apply the contiguous series using the bridge-aware [README recipe](README.md); these patches depend on preceding core changes.

## Gotchas / plugin?

These are core and test-harness changes, not plugin replacements. Scan serialization is backend-local, not a distributed lock. State-keyed skips must re-arm when inputs change; source tests cover those transitions rather than only the first successful pass.
