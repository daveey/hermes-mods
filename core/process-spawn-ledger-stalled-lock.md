# Spawn ledger: no rows lost past a stalled lock holder

Patches: **1713** (`8c0f63fe9b`, `fix(process): a spawn-ledger row written past a stalled lock holder is never lost [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A spawn-ledger writer that cannot get the cross-process lock within its wait no longer does an unlocked read-modify-write that drops rows another writer landed.

## How it works

`hermes_cli/process_identity.py` and `data_cleanup.py`: the lock wait is no longer a flat 2 s give-up followed by an unlocked atomic replace.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1713: CI (daveey/hermes run 41470490138, job 125362788105) lost 3 of 45 rows (retry: 6) in test_concurrent_processes_never_drop_each_others_ledger_rows, logging "spawn ledger lock unavailable".

## Code

`hermes_cli/data_cleanup.py`, `hermes_cli/process_identity.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_process_identity.py`: `test_a_row_written_past_a_stalled_holder_survives_its_late_write`, `test_concurrent_processes_never_drop_each_others_ledger_rows`, `test_register_child_waits_behind_writers_that_keep_landing`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
