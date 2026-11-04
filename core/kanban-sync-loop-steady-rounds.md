# Kanban sync loop: skip unchanged chat-link passes

Patches: **1716** (`d2fdacd98a`, `[daveey] Skip unchanged sync-loop chat-link passes and pin placement config per round`); **1717** (`3b6cd20eab`, `[daveey] Clarify session-source cache refresh bound`); **1721** (`364d9f4109`, `[daveey] Test sync mode invalidation and config snapshot boundaries`); **1723** (`685020a940`, `[daveey] Desktop poll cost: drain timeout reads the read-only raw config; board_paths reads kanban.sync once`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- `reconcile_links(skip_unchanged=True)` (used by `run_forever`) skips a board whose inputs equal its last completed pass, and the placement config is loaded once per round instead of per placeholder check (1716/1717).
- Sync-mode changes and config snapshots invalidate the skip (1721).
- `board_paths()` reads `kanban.sync` once, and the restart drain timeout reads the read-only raw config instead of deep-copying it on every `/api/status` poll (1723).

## How it works

`hermes_cli/kanban_sync_client.py` keys the skip on live file identity plus the pass's inputs; `hermes_cli/kanban_placement.py` takes a per-round config snapshot.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1716: reconcile_links ran every 60 s unconditionally: each pass re-ran kanban_chat_links.reconcile and its creator-chats backfill over every task, subscription, ledger row and replica link, and every placeholder check reloaded the placement config (5051 loads per pass on a 600-card probe board).
- 1717: (subject only)
- 1721: (subject only)
- 1723: /api/status resolved the restart drain timeout through read_raw_config(), which deep-copies the whole user config (~2.6 ms per poll on David-MBP); board_paths() re-read kanban.sync once per board on every session-list poll. Tests RED first.

## Code

`hermes_cli/gateway.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_sync_client.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_sync_client.py`: `test_board_discovery_reads_the_sync_config_once`
- `tests/hermes_cli/test_kanban_sync_connected_feed.py`
- `tests/hermes_cli/test_kanban_sync_connections.py`
- `tests/hermes_cli/test_kanban_sync_steady_links.py`: `test_a_failed_pass_retries_on_the_next_pass`, `test_a_ledger_board_this_host_lacks_gets_its_link_only_file_on_the_next_pass`, `test_a_ledger_change_reconciles_on_the_next_pass`, `test_a_live_board_change_reconciles_on_the_next_pass`, `test_a_mode_change_reconciles_on_the_next_pass`, `test_a_placement_config_change_reconciles_on_the_next_pass`, `test_a_reconcile_pass_loads_the_placement_config_once`, `test_a_repair_round_loads_the_placement_config_once`, `test_a_replaced_board_file_reconciles_on_the_next_pass`, `test_a_replica_pull_reconciles_on_the_next_pass`, `test_a_subscription_change_reconciles_on_the_next_pass`, `test_a_write_during_a_pass_is_not_cached_as_reconciled` (+8 more)
- `tests/hermes_cli/test_kanban_sync_targeted_rounds.py`
- `tests/hermes_cli/test_machine_metrics.py`
- `tests/hermes_cli/test_restart_drain_timeout_readonly.py`: `test_a_config_edit_shows_on_the_next_read`, `test_cached_reads_do_not_copy_the_config`

Untested by their own patch (refactors, constants or CI routing): 1717.

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
