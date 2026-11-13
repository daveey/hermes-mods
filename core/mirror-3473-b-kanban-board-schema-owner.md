# Kanban: only the installed core migrates a board; SchemaAhead alert

Patches: **3449, 3450, 3451, 3452, 3453, 3454**.

## Behavior
- A checkout that is not the home's installed core (worktree, scratch clone, other-HEAD env) opening a board whose schema it would change opens it read-only: reads work, writes raise `BoardSchemaNotOwned` naming the installed core and commit, the missing columns, and a scratch-home hint. The installed core, a tree at its commit, and a home with no install migrate as before; a board already at this core's schema opens normally.
- Boot home maintenance from a non-installed checkout logs and records "skipping board migrations".
- When Board sync gives up over SchemaAhead (at start or mid-run), one hub card is filed per host incident; while it is open a new incident comments on it. A card the replica shows closed (even inside the fresh window) gets a new card. A give-up during the host's own core update files nothing.
- A non-installed checkout opening a synced board at par keeps its sync mode (registered without install), so it mints Board ids and claims under the lease rather than reading as sync off.
- The at-par reference schema replays connect's own schema steps, so a board missing a table the installed core predates is not judged at par.

## How it works
3449 adds `hermes_cli/kanban_schema_owner.py` (`decide`), consulted by `kanban_db_connect.connect`; a `kanban_board_schema` step in `post_update.py`; and `kanban_sync_schema_alert.py` called from the sync client's give-up. 3450 adds `kanban_db_sync.register_without_install` for the at-par branch and reads the replica with `--all` for closed cards. 3451 routes the schema-owner module to the upgrade CI lane, pins UTF-8 on the alert's git probe and marks the in-memory reference schema connect. 3452 and 3454 move give-up recording and (board, refusal) formatting into the alert module to keep the sync client under its line cap. 3453 factors `apply_schema_steps` in connect and uses it for the reference schema.

## Code
- `hermes_cli/kanban_schema_owner.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_schema_alert.py`
- `hermes_cli/post_update.py`
- `scripts/ci/classify_changes.py`
- `tests/hermes_cli/test_kanban_schema_owner.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_schema_owner.py`: `test_non_installed_checkout_leaves_the_schema_and_refuses_writes`, `test_non_installed_checkout_opens_a_current_board_normally`, `test_installed_core_migrates_as_before`, `test_a_tree_at_the_installed_commit_migrates`, `test_home_without_an_installed_core_migrates`, `test_boot_home_maintenance_from_a_non_installed_checkout_skips_board_migration`, `test_boot_home_maintenance_of_the_installed_core_leaves_migration_to_first_connect`, `test_schema_ahead_give_up_opens_one_card_per_incident_and_comments_while_open`, `test_another_hosts_open_card_does_not_absorb_this_hosts_incident`, `test_run_forever_give_up_opens_the_hub_card`, `test_a_card_filed_and_closed_within_the_fresh_window_gets_a_new_card_not_a_comment`, `test_non_installed_checkout_on_a_synced_board_at_par_keeps_its_sync_mode`, `test_reference_schema_matches_what_connect_creates`, `test_board_missing_a_table_the_installed_core_predates_is_not_at_par`.
- 3451, 3452 and 3454 add no tests (CI routing and line-cap refactors).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only the home's installed core (`<home>/hermes-agent`) migrates; use a scratch `HERMES_HOME` for experiments from other checkouts.
- The hub card is filed through the hub courier; hosts without it are untested.
