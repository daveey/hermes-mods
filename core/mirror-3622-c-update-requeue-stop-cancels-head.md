# Update-pause requeue: a Stop latched during the write cancels the head

Patches: **3608**.

## Behavior
- When a core-update pause hands an unstarted notifier wake back to its FIFO place, a Stop (`interrupt_update`) that latches while that requeue write is running now cancels the re-queued row. Previously the caller was told the turn stops, but the latch was discarded with the claim and the notice ran after the update hold.
- The waiter of the cancelled row is answered (with `None`) and the Stop latch state is cleared, so a later claim of the session inherits no Stop. Admission media is released.
- If the cancel write fails, the failure is logged loudly and the row stays queued; it then runs once after the hold, as before.
- A Stop that arrives after the requeue returns finds the row queued (not started) and is refused, as for any queued row.

## How it works
- 3608 makes `requeue_unstarted` (`gateway/session_update_pause.py`) read `stopped(authority, row)` after the requeue write and before the claim is cleared. If a Stop latched, it returns `_stopped_after_requeue`, a new coroutine that discards the admission from `update_stops`, clears the matching `pending_stops` / `pending_stop_causes` entries, calls `cancel_session_input` through `tracked_write`, and on a `terminal`/`cancelled` result settles the row with `_settle_cancelled` and releases media, then republishes pending state and persists the work count. The commit leaves the bound-steer handling unchanged.

## Code
- `gateway/session_update_pause.py`
- `tests/gateway/test_update_requeue_stop_race.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_update_requeue_stop_race.py`: `test_stop_latched_during_the_requeue_write_cancels_the_head_and_clears_the_latch` (the Stop is accepted, the row ends `terminal`/`cancelled`, the waiter is answered, latches are empty, and the notice does not run after the hold is lifted).
- Untested: the cancel-write-failure path (row stays queued).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A failed cancel is not retried; the row runs once after the hold.
