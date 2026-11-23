# Kanban connect: integrity checks off the schema lock; keyed-create replay serialization (mirror 5505–5508)

## Behavior
- Per the 5505 comment, `connect()` ran a full integrity check (O(board size)) once per process under the cross-process init lock, so N concurrent short-lived CLI runs cost N serial scans. (Whether waiters gave up at the lock timeout is not shown by the diff.)
- Final behavior (5507): the full integrity probe runs outside the cross-process init turn (cold threads in one process share one probe through a per-path lock); schema/repair work stays under the init turn; a file replaced after the pre-probe is checked again under the lock. The pre-probe does no repair or quarantine (`_healthy_preprobe_identity` returns the file identity only for a clean result); suspicion and replacement go to the existing guard under the lock, and a test checks corruption is confirmed and backed up only under the lock. Per its docstring no result survives the connect call, so each fresh CLI still pays its own full check.
- Intermediate patches 5505 (a `<db>.integrity-ok` stamp valid 6 h per device/inode) and 5506 (binding the stamp to unchanged DB and WAL metadata generations, rejecting rollback journals and recovery/checkpoint changes) were both removed by 5507, which deletes `test_kanban_integrity_generation.py` and `test_kanban_connect_integrity_stamp.py` and removes the stamp helpers. Do not expect the stamp file in the final state.
- 5508: the replay recheck in `kanban_origin_admission` now runs for every creation with an idempotency key (it was skipped when the strict path was not active), inside the insertion lock, so racing legacy creators are intended to yield one task; the test is named `test_legacy_recheck_after_concurrent_early_miss`, and an archived legacy key still allows a new card.

## How it works
`hermes_cli/kanban_db_connect.py` (`_COLD_PATH_LOCKS`, pre-probe before the init turn); `hermes_cli/kanban_origin_admission.py` rechecks inside the write transaction.

## Code
`hermes_cli/kanban_db_connect.py`, `hermes_cli/kanban_origin_admission.py`.

## Tests
Source (final state): `tests/hermes_cli/test_kanban_integrity_preprobe.py` (full check outside the init turn then schema inside; cold threads share one probe; replaced file (parametrized) rechecked under lock; corruption confirmed and backed up only under lock), `test_enrollment_cli_convoy.py` (a stalled scan does not block create with a live WAL writer), `test_enrollment_cli_create.py` (modified by 5507; the diff does not list its final test names), `test_kanban_creation_key_race.py` (legacy recheck after a concurrent early miss; archived legacy key allows a new card), `test_kanban_receipt_artifacts.py` (receipt preserved when the process scan defers cleanup). Not executed in this mirror pass.

## Applying
Net result requires 5505–5507 in order (or squash to the 5507 end state); 5508 is independent.

## Gotchas
Reviewers mirroring selectively should skip the stamp design: it is superseded.

## plugin?
No.
