# Kanban sync round follow-ups and scratch-archive promisor check

Patches: **3626, 3627**.

## Behavior
- Scratch archive reads `remote.*.promisor` as a git boolean: an empty, `No` or `0` value is not a promisor, so a full clone with a missing HEAD blob is refused instead of archived; an unparseable value refuses the archive (tree kept). A non-empty `extensions.partialClone` counts as partial.
- Kanban sync: a transport error on the push before the pulls no longer skips the pulls; the later send retries and its failure fails the round.
- The outbox-age alarm ages only ops a push would actually send while behind the feed head; held ops no longer WARN or file a stalled-outbox card during catch-up.
- Worker repin pulls the live board only under the board sync lock (taken without waiting); a repin behind a backlog longer than the page budget still confirms its CAS via a targeted row read, and a conflict retry sees the newest revision.
- `sync diff` refreshes the replica as a diagnostic only: no rebuild on resync (reported as a refresh error) and no attachment transfers.
- One Board read per card per round.

## How it works
- 3626 uses `git config --null --type=bool --get-regexp` in `_is_partial_clone`, counting only `true`.
- 3627 changes `kanban_sync_client`, adds `sendable_age_s` in `kanban_sync_outbox_age` over `kanban_sync_backlog.sendable_before_head`, and reworks `kanban_worker_repin` and `kanban_sync_cli`.

## Code
- `hermes_cli/kanban_scratch_archive.py`
- `hermes_cli/kanban_sync_backlog.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_worker_repin.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_archive_partial_clone.py`: `test_is_partial_clone_reads_git_bool_semantics`, `test_unparseable_promisor_value_refuses`.
- `tests/hermes_cli/test_kanban_sync_round_followups.py`: `test_an_ops_outage_still_pulls_every_remote_row_and_the_round_reports_the_push_error`, `test_a_push_that_fails_once_before_the_pulls_is_sent_by_the_push_after_them`, `test_a_catch_up_never_alarms_on_ops_held_behind_a_pending_run_row`, `test_a_held_stale_wake_never_alarms_during_the_catch_up`, `test_a_sendable_op_still_alarms_while_behind`, `test_a_behind_round_reads_each_held_card_from_the_board_once`, `test_the_send_after_the_pulls_at_the_head_reads_nothing`, `test_repin_never_pulls_the_live_board_while_a_round_holds_it`, `test_repin_behind_a_backlog_longer_than_the_page_budget_still_confirms_its_cas`, `test_repin_conflict_behind_a_backlog_reads_the_newest_row_and_cas_again`, `test_repin_conflict_while_a_round_holds_the_board_does_not_cas_blind`, `test_sync_diff_never_transfers_attachments`, `test_sync_diff_reports_a_resync_instead_of_rebuilding_the_replica`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A promisor value git cannot parse as a bool now refuses archiving rather than being guessed.
