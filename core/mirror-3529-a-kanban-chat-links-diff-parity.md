# Kanban chat-links diff ignores machine chats and unsettled closed cards

Patches: **3504**.

## Behavior
- `links_diff` leaves out chats the link writer never links (machine sessions, placeholders) from both sides and reports them separately as `machine_links`, so a replica reader can see a clean diff when only real divergences remain.
- A released sibling link no longer settles a closed card whose own link is unreleased; the replica keeps a done-but-unverified card `unknown`, matching the ledger.

## How it works
3504 adds `_unlinked_chats` in `hermes_cli/kanban_chat_links.py` (the same machine-session lookup the ledger writer uses), removes the replica rule that dropped a card when any sibling link was released (each link now carries its own answer), and surfaces `machine_links` in `hermes_cli/kanban_sync_cli.py`.

## Code
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_sync_cli.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_links_parity.py`: `test_links_diff_agrees_on_a_done_unverified_card_whose_owner_written_sibling_was_released`, `test_links_diff_leaves_out_chats_the_link_writer_never_links`.
- `tests/hermes_cli/test_kanban_chat_links.py`: `test_a_released_sibling_link_does_not_settle_a_closed_cards_unreleased_link`.
- The CLI `machine_links` output is untested.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
A real replica gap (a card on a profile board the Board does not carry) is still counted as a difference by design.
