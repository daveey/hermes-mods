# Update-pause requeue settle and remote-owner tick cleanup

Patches: **3632, 3633**.

## Behavior
- If cancelling a stopped head during update-pause requeue commits but then raises (unknown commit), the handler reads the row back and settles it as cancelled: the waiter is answered and media released, instead of hanging. If the cancel did not commit, the row stays queued, an ERROR is logged, and the notice runs once after the hold.
- The one remaining Stop window (between the write's commit and the coroutine resuming) is documented: `interrupt_update` reads the row queued and returns False.
- Remote-owner tick: the dead `_next_at`/`force` throttle is removed; no behavior change, the watcher sets cadence itself.

## How it works
- 3632 changes `gateway/session_update_pause.py` with a read-back in the cancel-failure path and a `requeue_unstarted` docstring.
- 3633 drops the throttle parameter and global from `kanban_remote_owner.tick` and updates its caller.

## Code
- `gateway/session_update_pause.py`
- `gateway/kanban_remote_owner.py`
- `gateway/kanban_watchers.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_update_requeue_stop_race.py`: `test_a_failed_cancel_of_the_stopped_head_runs_it_once_after_the_hold_or_settles_a_committed_one`.
- `tests/gateway/test_kanban_remote_owner_delivery.py`: modified `test_remote_owner_watcher_honours_notify_in_gateway` (zero start delay, async tick stub ending the loop after one pass).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- No retry loop on a failed cancel by design; a non-committed failure leaves the row queued.
