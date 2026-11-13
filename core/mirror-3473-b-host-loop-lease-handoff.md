# Host loop lease: want-record republish, expiry and hand-off release

Patches: **3441**.

## Behavior
- A waiter for the host loop lease keeps its `.want` record alive: it rewrites it once per interval, refreshing `at` or republishing it if another wanter's record replaced it. A lower-priority non-blocking poller can no longer keep the lease while a higher-priority waiter waits silently.
- `.want` carries the wanter's `interval`; a record older than 3 x interval is ignored by the holder and standbys (one WARNING per wanter), so a live but stopped (SIGSTOPped) wanter does not stall the pass.
- A failed spawn of the hand-off thread clears the waiting state and the `.want` instead of raising out of `held()`.
- `release()` during a pending hand-off wait clears the waiting state; the blocked fd is unlocked and closed by its thread on wake-up, and a later `held()` on the same object competes again.
- Holder metadata is padded to a fixed 1024-byte block so a shorter rewrite never leaves a stale tail; lock-free readers retry once on a record that does not parse.

## How it works
3441 changes `hermes_cli/host_loop_lease.py`: the waiting loop republishes from `held()` once per interval, `.want` gains an `interval` field with an age check on read, `release()` hands the blocked fd to its thread, and metadata writes are space-padded with one retry on torn reads.

## Code
- `hermes_cli/host_loop_lease.py`
- `tests/hermes_cli/test_host_loop_lease.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_host_loop_lease.py`: `test_overwritten_want_is_republished_by_its_waiter`, `test_stale_want_expires_with_one_warning`, `test_fresh_want_from_a_live_wanter_still_defers`, `test_want_spawn_failure_clears_waiting`, `test_release_during_pending_handoff_lets_held_compete_again`, `test_meta_is_fixed_length_so_rewrites_never_leave_a_tail`, `test_torn_meta_read_is_retried`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Builds on an earlier lease implementation in the series.
