# Kanban chat task lists while a chat's own Board sync is down

Patches: **3376, 3383, 3396**.

## Behavior
- A chat's task list, sidebar counts and ask-gate (with the replica links reader) no longer wait on the chat's own gateway sync: the card's owner publishes the chat's link rows.
- The owner's links pass turns each counted hub subscription on an open card it owns into an unreleased link row for that chat, using the same uid the chat's gateway writes, so a recovered gateway upserts the same row and never creates a second one.
- A card placed from a chat has the chat's `created` link written by the owner in the create transaction (skipping a chat on the owner, a placement placeholder, and an unproven local-hash chat).
- `kanban_create` reports a placed create while this gateway's sync is stopped as `chat_link: owner_written` (the link is on the Board; only this replica lags); `chat_link: queued_local` remains for a local-hash chat for which the owner writes no link.
- The queued-create note is rewritten at an exact head constant, so wording whose separator ends in a colon keeps correct punctuation.

## How it works
- 3376 replaces `CHAT_TASKS_QUEUED` with `CHAT_TASKS_HEAD` + `CHAT_TASKS_SEP`; the rewrite inserts right after the exact head instead of stripping a character set.
- 3383 adds `_owner_hub_links` to `kanban_chat_links.py`.
- 3396 makes the courier create/adopt path in `kanban_origin_creation` write the same-uid created link in the create transaction and updates the create notes in `kanban_tools_board_delivery.py`.

## Code
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_origin_creation.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_board_delivery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_board_delivery.py`: `test_queued_rewrite_lands_after_the_exact_head_for_any_separator`, `test_create_placed_elsewhere_while_the_sync_role_is_given_up_says_only_this_gateways_copy_lags`, `test_create_queued_on_a_target_while_stopped_says_this_gateways_copy_lags`, `test_create_placed_from_a_desktop_hash_chat_while_stopped_says_the_link_waits_here`, `test_create_note_wording_and_its_stopped_sync_rewrite_share_one_source`, `test_a_card_placed_while_this_gateways_sync_is_stopped_shows_in_the_chat_after_one_replica_pull`, `test_when_this_gateways_sync_recovers_its_reconcile_keeps_the_one_link`.
- `tests/hermes_cli/test_kanban_chat_links_owner_hub_subs.py`: `test_a_card_placed_while_the_chat_gateways_sync_is_down_shows_in_its_task_list`, `test_the_recovered_chat_gateway_upserts_the_same_row_never_a_second`, `test_a_card_finished_during_the_outage_is_released_by_the_recovered_chat_gateway`, `test_owner_publishes_counted_hub_subscriptions_of_open_cards_only`, `test_owner_never_reopens_or_doubles_a_link_the_chat_gateway_already_wrote`.
- `tests/hermes_cli/test_kanban_origin_notify.py`: `test_courier_adoption_writes_the_origin_chats_created_link`, `test_courier_create_writes_the_origin_chats_created_link_once`, `test_courier_create_writes_no_link_for_a_chat_on_the_owner_or_a_placeholder`.
- `tests/tui_gateway/test_remote_withdrawal_transport.py` was also touched by 3396; no new test names were read from it.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A local-hash chat gets no owner-written link because only that chat's own gateway can tell a Desktop chat from a worker's own hash.
