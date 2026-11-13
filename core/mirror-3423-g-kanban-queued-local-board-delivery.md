# Kanban: say so when a write is queued locally while Board sync is stopped

Patches: **3319, 3320, 3345**.

## Behavior
- While this gateway's Board sync role is given up, tool writes (comment, unblock, link and others covered by tests) return `board_delivery: queued_local` and wording that the write waits, instead of "the live run gets this note".
- A create placed on another gateway says the chat link waits (`chat_link: queued_local`, `board_sync`) while stopped.
- CLI writes that the stopped sync keeps local (archive, reopen, reopen-review, request-review, request-changes, unlink) print a "Queued locally" notice, once.

## How it works
- 3319 adds `kanban_board_delivery.py` and `tools/kanban_tools_board_delivery.py`, which read the sync state when building the delivery report.
- 3320 adds the create-elsewhere chat-link case.
- 3345 gives CLI parity via `_print_queued_local`, a public `runstate.when`, and shared create-note anchors.

## Code
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_board_delivery.py`
- `hermes_cli/kanban_output.py`
- `hermes_cli/kanban_sync_runstate.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_board_delivery.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_board_delivery.py`: `test_comment_on_a_remote_run_while_the_sync_role_is_given_up_says_queued_locally`, `test_comment_while_the_sync_runs_is_unchanged`, `test_comment_on_a_local_run_while_stopped_keeps_the_live_run_promise`, `test_unblock_while_the_sync_role_is_given_up_says_queued_locally`, `test_link_while_the_sync_role_is_given_up_says_queued_locally`, `test_link_while_the_sync_runs_is_unchanged`, `test_unsynced_board_never_reports_queued_locally`, `test_cli_comment_on_a_remote_run_while_stopped_drops_the_live_run_line`, `test_cli_link_while_stopped_prints_the_notice`, `test_cli_unblock_while_stopped_prints_the_notice_once`, `test_reads_and_refusals_while_stopped_are_unchanged`, `test_a_finished_remote_card_has_no_live_run_host`, `test_create_placed_elsewhere_while_the_sync_role_is_given_up_says_the_chat_link_is_queued`, `test_create_queued_on_a_target_while_stopped_says_the_chat_link_waits`, `test_create_placed_elsewhere_while_the_sync_runs_is_unchanged`, `test_cli_write_while_stopped_prints_the_notice_once`, `test_cli_write_while_the_sync_runs_prints_no_notice`, `test_cli_unlink_of_no_link_while_stopped_prints_no_notice`, `test_create_note_wording_and_its_stopped_sync_rewrite_share_one_source`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- `kanban unlink` prints the notice only when a link existed.
