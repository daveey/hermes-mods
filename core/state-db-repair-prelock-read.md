# state.db repair: no pre-lock read when the repair budget is not exhausted

Patch: **1628** (`79929d70df`, `state.db repair: skip the pre-lock fingerprint read when the ledger has no exhausted
budget, so a queued repairer is not seen as a live writer by its peer`).

## Behavior

- Two processes that start repairing a malformed `state.db` at the same time again perform schema surgery exactly once.
  Before, the second repairer, waiting for the cross-process repair lock, could still hold `state.db` open while it
  read the file's fingerprint for the persistent-budget check. The lock winner's foreign-holder preflight
  (`hermes_state_holders`) then correctly saw a live holder and skipped surgery as "a live writer still holds state.db".
  This showed up as a flake in `test_two_processes_repairing_at_once_perform_surgery_once`.
- The persistent repair budget is unchanged: once the ledger records `_MAX_PERSISTENT_REPAIR_ATTEMPTS` failed attempts
  for the same fingerprint (or the same recorded size when the fingerprint is unavailable), repair still stops.

## How it works

`_persistent_repair_attempts_exhausted(db_path)` in `hermes_state_repair.py` runs before the cross-process lock. It now
reads the ledger's `failed_attempts` first and returns `False` (not exhausted) when the count is below the maximum or
unparseable. Only when the count could exhaust the budget does it call `_db_fingerprint` (a raw read of the database)
and compare it with the recorded fingerprint.

## Code

`hermes_state_repair.py`.

## Tests

Not rerun by this mirror pass.

- `tests/hermes_state/test_state_db_malformed_repair.py::test_repair_does_not_read_state_db_before_taking_the_repair_lock`:
  spies on `_db_fingerprint` and `_cross_process_repair_lock` during `repair_state_db_schema` on a corrupted database
  and asserts no fingerprint read happens before the lock is taken, and the repair succeeds.
- The existing two-process test is the regression it deflakes.

Untested: the exhausted-budget path after this reorder (a ledger at the maximum still fingerprints before the lock, so
a queued peer in that state can still hold the file briefly).

## Applying

Apply in series order; it edits one function in `hermes_state_repair.py`. No config.

## Gotchas

When the budget is exhausted the pre-lock fingerprint read still happens; that is rare (repeated failed repairs) and
then surgery is refused anyway.

## Plugin?

No. Startup database repair runs before plugins load.
