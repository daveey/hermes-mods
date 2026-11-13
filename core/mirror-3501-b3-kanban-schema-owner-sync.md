# Kanban schema owner: sync DDL in the reference, no phantom boards

Patches: **3499–3500**.

## Behavior
- A board registered as synced is checked against a reference schema that includes the sync tables and columns. A synced board missing a sync column this core declares opens read-only and refuses writes with `BoardSchemaNotOwned` naming the column.
- A non-installed checkout connecting in a home whose installed core is a different one no longer creates an empty board file (or sidecars) when the board is missing; it raises `BoardSchemaNotOwned`.
- `hermes kanban` prints the refusal as a one-line message on stderr and exits 1 instead of a traceback.
- Unsynced boards ignore the sync DDL.
- 3500 changes comments and docstrings only.

## How it works
- 3499 makes `kanban_db_sync.apply_sync_schema_steps` the single list of sync DDL steps (also run by `install_or_remove`), and adds it to the at-par reference when the board would be registered synced. `connect` and `init_db` call `refuse_missing_board` before creating directories or opening the file. `kanban_command` catches the refusal for both init and action handling.
- 3500 clarifies that `_links_unchanged` callers re-record the done marker only when inputs were readable and reconcile succeeded, and rewords the `LINKS_SLOW_LOG_S` comment.

## Code
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_schema_owner.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_schema_owner.py`: `test_reference_schema_with_sync_matches_what_a_synced_connect_creates`, `test_synced_board_missing_a_sync_column_this_core_declares_is_not_at_par`, `test_unsynced_board_ignores_sync_ddl`, `test_non_installed_checkout_does_not_create_a_missing_board`, `test_cli_prints_the_refusal_line_not_a_traceback`.
- 3500 has no test changes (docstring/comment only).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only a home's installed core migrates its board; other checkouts open read-only or refuse.
