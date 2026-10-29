# Kanban stale checks count awake time only

Patches:

- `patches/0991-fix-kanban-stale-checks-count-only-awake-time-from-t.patch`
- `patches/1002-fix-kanban-a-dry-run-dispatch-writes-no-suspend-ledg.patch`
- `patches/1005-fix-ci-dry-run-dispatch-writes-nothing-GET-api-sessi.patch`

A Mac lid-close longer than an hour made the first dispatcher pass after wake SIGTERM every live host-local worker: the heartbeat backstop (60 min), `detect_stale_running` and `enforce_max_runtime` measured wall-clock time, so sleep read as worker silence (44 kills, 8 cards gave up).

## Behavior

- Each dispatcher pass stamps `kanban.db`; a gap over `max(300, 3 x kanban.dispatch_interval_seconds)` is recorded as a suspend (pruned after 7 days). `awake_seconds()` subtracts recorded suspends.
- Heartbeat age is measured in awake time from `max(last_heartbeat_at, active run started_at)`, so a fresh run is never stale against its predecessor's heartbeat; max-runtime uses awake time from the run's own start.
- 1002: a dry-run dispatch writes no stamp (it created tables and hid real gaps). 1005 also keeps `GET /api/sessions` from opening `state.db` writable unless there are drafts to archive.

## How it works

`_run_reclaim_phase` calls `record_dispatch_pass(dry_run=...)` before any stale check; `kanban_db_stale_claims.py` holds the awake-time stale logic moved out of `kanban_db.py`.

## Code

`hermes_cli/kanban_suspend_ledger.py`, `hermes_cli/kanban_db_stale_claims.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db.py`, `hermes_state_maintenance.py`, `hermes_cli/web_server_sessions.py`.

## Tests

`tests/hermes_cli/test_kanban_suspend_ledger.py` (suspend recording, awake-time subtraction, dry-run no-op); `tests/hermes_state/test_empty_draft_archive.py` and `tests/hermes_cli/test_web_server_auto_archive_gateway_lock.py` for 1005.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
