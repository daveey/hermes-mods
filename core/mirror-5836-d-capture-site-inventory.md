# Reviewed static pending-custody mutation inventory

Source patch ordinals: 5793–5800.

## Behavior

The final state is an explicit reviewed inventory of pending-custody mutation sites. It stamps, certifies and activates nothing. Historical/new rows do not become original captures because a site is listed as `ORIGINAL_CANDIDATE`.

## How it works

The sequence first added source/count pins, then explored a test-suite SQLite authorizer, and finally replaced that interception with a reviewed static snapshot. The final `kanban_sync_capture_sites` declarations classify exact qualified function, SQL verb and custody-table sets, including dynamic-table declarations. Tests compare exact rows/dispositions and verify that qualified functions exist. The runtime authorizer, fixture wrappers and global connection interception from intermediate commits are removed in the final follow-up.

## Code

- `hermes_cli/kanban_sync_capture_sites.py`
- `tests/hermes_cli/test_kanban_sync_capture_sites.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

The final `tests/hermes_cli/test_kanban_sync_capture_sites.py` checks exact reviewed sites, dynamic table sets, qualified function existence and disposition constraints. Intermediate commits contain runtime-authorizer tests for resolved writes, trigger/backup custody and fixture semantics; those tests and interception are not the final enforcement mechanism.

## Applying

Apply through ordinal 5800, not just an intermediate authorizer commit. Treat inventory edits as review work requiring semantic reassessment before later capture-certification implementation.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

The final snapshot does not discover new writers or prove source bodies still match it. It is not runtime authorization, coverage of unexecuted branches, writer drain, pre-I/O exposure proof or provenance activation. Do not advertise the removed regex/runtime gates as active guarantees.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
