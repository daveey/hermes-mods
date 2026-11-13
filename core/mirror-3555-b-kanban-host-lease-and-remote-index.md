# Kanban host lease and remote notice index hardening

Patches: **3542, 3544**.

## Behavior
- Remote notice index holder: the retry backoff now belongs to one holding and one replica state. Re-acquiring the lease, or seeing a replica stamp not yet tried, rebuilds immediately instead of waiting up to 60 s. A repeated failure on the same stamp still backs off with doubling.
- A serve process that holds the index lease hands it to a gateway claim in the same process; the gateway lease becomes holder and publishes the next replica state (pinning existing behavior).
- Unloaded-host pass: the lease heartbeat is beaten even when a pass raises.
- Wake slot sizing uses a new `HostLoopLease.exclusive()`, true only while the process really holds the lock (false under the kill switch or when the lease failed open), so a failed-open lease no longer gives every process the holder's 8 wake slots instead of 2.
- Without a real lease, killed-subagent notices are retried only in the process's own store; the real holder retries every store.
- The notice fanout state keeps one connection per (path, pid), opened once; a failed statement drops it so the next call reopens (12 connects became 1 in the new test).

## How it works
- 3542 changes `_hold` in the remote index module to reset `retry_at`/`delay` on re-acquire or new stamp, and adds a real-thread test of the serve-to-gateway hand-off.
- 3544 makes `run_pass` beat in a `finally`, adds `exclusive()`, caches the fanout state connection, and selects stores in `host_pass` based on lease ownership.

## Code
- `tui_gateway/kanban_remote_index.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/notice_fanout_state.py`
- `hermes_cli/host_loop_lease.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_kanban_remote_index.py`: `test_holder_backoff_restarts_when_the_lease_is_re_acquired`, `test_holder_backoff_restarts_on_a_new_replica_stamp`, `test_serve_holder_hands_the_lease_to_a_gateway_claim_in_the_same_process`.
- `tests/tui_gateway/test_kanban_unloaded_host_lease.py`: `test_the_lease_holder_retries_killed_notices_in_every_store`, `test_kill_switch_retries_killed_notices_only_in_its_own_store`, `test_a_fail_open_lease_retries_killed_notices_only_in_its_own_store`, `test_a_host_pass_that_raises_still_beats`, `test_a_fail_open_lease_keeps_the_base_wake_slots`, `test_a_real_lease_holder_gets_the_holder_wake_slots`, `test_notice_fanout_state_opens_its_store_once_per_process`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The hand-off test passes without the fix; it pins existing behavior.
- The kill switch (`HERMES_HOST_LOOP_LEASE=0`) is meant to be an exact rollback to pre-lease behavior.
