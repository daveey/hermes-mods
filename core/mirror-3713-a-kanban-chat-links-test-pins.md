# Kanban chat links and completion refs: test pins

Patches: **3693, 3695**.

## Behavior
Test-only; no production change. These tests pin clauses that mutation checks showed were untested:
- Profile-board dedup: a replica row of the same chat/owner gateway/board/id dedups the ledger's profile-board copy.
- Origin filter: another gateway's or origin profile's chat keeps its own profile-board cards and they do not leak to another reader with the same session id.
- `links_diff` does not count a skipped (unlinked/placeholder) chat's profile-board cards.
- Released-links reader returns only this profile's released links; closed-card readers list no other profile's released link; open-status readers and `links_diff` never consult the released reader.
- Completion-ref replay: a pre-uid replay binds its run's latest receipt, a receipt written after the completion never binds, and an imported completion's integer never binds a locally written receipt.

## How it works
3693 adds a new test file for the profile-board paths (`_merge_profile_board`, `_profile_board_cards`, `links_diff`). 3695 adds a new released-reader test file and three tests in the completion-refs sync suite, covering `kanban_completion_refs.pick` (latest, before, origin) and the released-links reader limits. Some mutants were judged equivalent or masked and got no test.

## Code
- `tests/hermes_cli/test_kanban_chat_links_profile_board_pins.py`
- `tests/hermes_cli/test_kanban_chat_links_released.py`
- `tests/hermes_cli/test_kanban_completion_refs_sync.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_links_profile_board_pins.py`: `test_a_replica_row_of_the_card_dedups_its_profile_board_ledger_copy`, `test_another_origins_chat_keeps_its_profile_board_cards`, `test_links_diff_does_not_count_an_unlinked_chats_profile_board_cards`.
- `tests/hermes_cli/test_kanban_chat_links_released.py`: `test_the_released_reader_returns_only_released_links_of_this_profile`, `test_a_closed_card_reader_lists_no_other_profiles_released_link`, `test_open_status_readers_never_consult_the_released_reader`.
- `tests/hermes_cli/test_kanban_completion_refs_sync.py`: `test_a_pre_uid_replay_binds_its_runs_latest_receipt_not_an_earlier_red_one`, `test_a_receipt_written_after_the_completion_never_binds`, `test_an_imported_completions_integer_never_binds_a_receipt_written_on_this_board`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The tests target code from earlier patches in the series; they fail without it.
