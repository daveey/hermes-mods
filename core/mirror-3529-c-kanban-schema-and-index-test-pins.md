# Kanban schema-owner and remote-index test hardening

Patches: **3524, 3529**.

## Behavior
- Tests only; no production change in either patch.
- The schema-owner reference is pinned per mode: building the unsynced reference first must not stand in for the synced one (a synced board missing a sync column is refused), and the reverse order still opens an unsynced board at par.
- The CLI on a missing board prints exactly one `kanban: ` refusal line, exits 1 and creates no database or sidecar files.
- The remote-index republish test now waits for the publishing pass to finish before reading the publish counter, removing a CI race.

## How it works
- 3524 adds three tests that kill two surviving revert mutants (single shared reference cache; removed CLI refusal handler around `init_db`).
- 3529 changes the wait in the republish test: `publish()` commits the key before bumping the counter, so the test waits until the holder's pass ends before snapshotting the counter.

## Code
- `tests/hermes_cli/test_kanban_schema_owner.py`
- `tests/tui_gateway/test_kanban_remote_index.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_schema_owner.py`: `test_unsynced_reference_built_first_does_not_stand_in_for_the_synced_one`, `test_synced_reference_built_first_is_not_applied_to_an_unsynced_board`, `test_cli_on_a_missing_board_prints_the_refusal_line_and_creates_nothing`.
- `tests/tui_gateway/test_kanban_remote_index.py`: modifies the existing `test_holder_republishes_on_replica_change_only_while_leased`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Two equivalent mutants were deliberately left without tests (re-inlined sync DDL; dropped `refuse_missing_board`, because `connect()` re-checks).
