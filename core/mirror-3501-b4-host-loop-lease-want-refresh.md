# Host loop lease: hand-off waiter refreshes its own .want

Patches: **3501**.

## Behavior
- A higher-priority standby waiting for a hand-off keeps its `.want` record live on its own timer, so the holder and standbys keep deferring to it even if its caller rarely calls `held()`.
- If the refresher thread cannot start, a WARNING is logged, the wait continues, and `held()` refreshes `.want` as the fallback.

## How it works
3501: while waiting on the exclusive lock, `_want()` starts a second daemon thread, `_refresh_want_while_waiting`, that calls `_keep_want()` once per interval under the mutex. It stops when the wait thread wins or fails, on `release()`, or when the fd changed, and is reset after fork. Previously the refresh happened only from `held()` after `_next_probe`, so the record could pass its 3 x interval expiry.

## Code
- `hermes_cli/host_loop_lease.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_host_loop_lease.py`: `test_waiter_keeps_its_want_live_without_held_calls`, `test_refresher_spawn_failure_keeps_waiting_and_held_refreshes`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- `held()` still refreshes as a fallback, so both paths can write the record.
