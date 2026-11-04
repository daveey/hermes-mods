# Kanban: worker-created cards readable from the board replica

Patches: **1900** (`b1f608189c`, `[daveey] kanban: read worker-created cards from the board replica; refuse their writes as foreign_card naming the owner; show ready-card queue position`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- `GET /api/plugins/kanban/tasks/{id}` reads a card created on a Board worker node from the board replica (`kanban/sync-shadow/<board>.db`) when it is not in the live file, and writes to such a card are refused as `foreign_card` naming the owner; ready cards show their queue position.

## How it works

`hermes_cli/kanban_foreign_card.py`, `hermes_cli/kanban_owner_archive_relay.py` and `hermes_cli/kanban_queue_position.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1900: A card created on a Board worker node (worker-1) is in no Desktop-reachable gateway's live kanban.db: a synced board's live file holds only that gateway's own cards, the replica (kanban/sync-shadow/<board>.db) holds the whole board. GET /api/plugins/kanban/tasks/{id} read only the live file, so cross_task read answered "not found on any connected gateway" and a chat concluded the card was a phantom (t_01a19fd40c53bb19). Writes answered stale_run / "cannot archive" / "unknown id or terminal state".

## Code

`hermes_cli/kanban.py`, `hermes_cli/kanban_foreign_card.py`, `hermes_cli/kanban_owner_archive_relay.py`, `hermes_cli/kanban_queue_position.py`, `plugins/kanban/dashboard/plugin_api.py`, `tools/kanban_tools.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_foreign_card_writes.py`: `test_tool_complete_names_owner_not_stale_run`, `test_tool_complete_unknown_id_is_still_stale_run`, `test_tool_archive_names_owner`, `test_tool_comment_names_owner`, `test_cli_complete_names_owner`, `test_cli_archive_names_owner`, `test_picked_up_copy_completes_and_lands_in_live_file`, `test_e2e_worker_created_card_reads_from_hub_replica_and_refuses_hub_writes`, `test_e2e_picked_up_copy_completes_into_the_outbox`
- `tests/plugins/test_kanban_foreign_card_api.py`: `test_replica_only_card_reads_with_source_and_owner`, `test_replica_ignored_when_board_does_not_sync`, `test_live_card_unchanged_plus_source_live`, `test_missing_everywhere_is_404`, `test_comment_on_replica_only_card_is_409_foreign_card`, `test_patch_on_replica_only_card_is_409_foreign_card`, `test_comment_on_missing_card_stays_404`, `test_comment_on_live_card_still_lands`, `test_ready_card_reports_local_queue_position`, `test_done_card_has_no_queue`, `test_worker_first_card_reports_worker_pool_reason`, `test_replica_open_falls_back_to_query_only` (+4 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
