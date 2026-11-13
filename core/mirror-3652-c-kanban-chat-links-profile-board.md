# Kanban chat links: profile-own board cards under the replica reader

Patches: **3646**.

## Behavior
- With `links_reader=replica`, a card living on a profile's own board (not the shared root board) stays in its chat's task list and sidebar count.
- `links_diff` reports these in a `profile_board_links` count (also printed by the `kanban sync links-diff` command; the printing change is untested) instead of an unclean difference. Genuine default-profile gaps stay visible.

## How it works
- Board sync carries only the shared kanban home, so profile-own boards never reach the replica. 3646 keeps them on the ledger side.
- `_is_profile_board_card` marks a qualified ledger row whose owner profile is not `default`.
- `replica_cards` adds this gateway's chats' unreleased profile-board obligations from the ledger (source `ledger-profile-board`), unless a readable replica carries a task row for that id; then the replica's links and release decide. One memoised ledger read (`_ledger_index`) serves both.

## Code
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_sync_cli.py`
- `tests/hermes_cli/test_kanban_chat_links.py`
- `tests/hermes_cli/test_kanban_run_machine.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_links.py`: `test_a_profile_board_card_stays_the_chats_from_the_ledger_and_links_diff_is_clean`, `test_a_replica_that_carries_a_profile_owned_card_keeps_its_own_answer`.
- `tests/hermes_cli/test_kanban_run_machine.py`: touched, no new test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Profile boards are deliberately not synced (one shared board by design); the reader depends on the ledger having the card.
