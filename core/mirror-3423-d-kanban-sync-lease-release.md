# Kanban sync: retire moot lease_lost releases

Patches: **3131, 3148, 3149, 3161**.

## Behavior
- A crash release rejected as `lease_lost` because a reclaim already ended the run is retired by normal sync, so `sync diff` stops reporting the gateway dirty.
- Retirement happens only on fresh authoritative evidence; a live lease by that run, or a local change during the read, vetoes it.

## How it works
- 3131 classifies a rejected release as moot only if: the card row is live and neither leases nor runs that run id; the run row is live, belongs to the card and has ended; and locally there is no open copy of the run and no pending op or tombstone for the card or run.
- 3148, 3149 and 3161 make the fake board service mirror the real hub: the lease grant writes `lease_run_uid`, clears null it, a release after run end clears the mirror, and the release is fenced on the holder's run and gateway (`lease_lost` otherwise); an ended run retries a still-running card.

## Code
- `hermes_cli/kanban_sync_rejections.py`
- `tests/hermes_cli/kanban_sync_fake_board.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_rejections.py`: `test_lease_lost_release_of_ended_run_retired_by_normal_sync`, `test_release_retirement_needs_ended_run_off_the_lease`, `test_release_local_change_during_read_vetoes`, `test_live_lease_by_the_run_keeps_release_end_to_end`, `test_fake_lease_release_after_run_end_clears_mirror`, `test_fake_lease_release_after_review_run_end_retries_to_review`, `test_fake_lease_release_is_fenced_on_the_holders_run`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The end-to-end veto test still inserts the rejected local release op by hand.
