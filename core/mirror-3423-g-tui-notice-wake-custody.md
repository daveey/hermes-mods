# Kanban: a tui notice and its chat's local wake deliver each event once

Patches: **3337, 3338–3341**.

## Behavior
- A notice and the chat's local wake deliver each event exactly once; an event written before the chat's local wake row existed stays with the tui row.
- The gateway's tui-owner claim follows the wake-sibling defer/cover/race rules and covers the compression lineage (tip and root). A busy gateway-owned chat gets tui notices within one tick.
- The legacy poller never claims a chat the gateway owns, hands back notices it buffered when the gateway takes the chat, and uses the gateway claim's own predicate; its owner probe opens state.db at most once per chat per TTL.
- A held claim the wake settled stays resolved after recovery and archive.

## How it works
- 3337 adds `local_since`/`created_event_id`, `tui_gateway/kanban_leg_custody.py` and the sibling changes.
- 3338 settles recovered claims by the row cursor and sees ownership on the lineage root and turn lease.
- 3339 lets a root local wake settle and floor tip rows.
- 3340 moves the left-open notice suffix into `kanban_notice_text` (line-cap refactor).
- 3341 aligns the hand-back with the gateway predicate (live local adapter, notifier-profile authority) and caches the owner answer.

## Code
- `gateway/kanban_tui_owner.py`
- `gateway/kanban_wake_sibling.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `tui_gateway/kanban_leg_custody.py`
- `tui_gateway/kanban_notice_text.py`
- `tui_gateway/session_notifications.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_tui_sibling_custody.py`: `test_n1_local_sibling_older_than_the_event_delivers_it_and_the_legacy_poller_never_does`, `test_n1_a_wake_dropped_undelivered_leaves_the_event_to_the_tui_leg_exactly_once`, `test_n2_event_before_the_local_sibling_existed_reaches_a_busy_owned_chat_within_one_tick`, `test_n2_legacy_only_chat_still_delivers_an_event_older_than_the_local_sibling`, `test_n3_a_held_claim_the_wake_delivered_resolves_as_delivered_even_after_archive`, `test_n3_a_held_claim_on_an_undelivered_event_is_kept`, `test_n4_busy_owned_tui_only_chat_never_holds_a_legacy_claim`, `test_legacy_only_idle_chat_still_delivers_once`, `test_local_sibling_on_the_lineage_root_counts_for_a_tui_row_on_the_tip`, `test_notify_sub_records_the_event_it_was_created_at`, `test_n4_compressed_owned_chat_with_ledger_work_on_the_root_never_holds_a_legacy_claim`, `test_n4_a_gateway_turn_lease_hands_the_chat_back_like_ledger_work`, `test_a_settled_claim_recovered_before_the_buffer_check_is_still_resolved_after_archive`, `test_a_root_local_wake_settles_the_tip_tui_leg_across_archive`, `test_n3_root_local_wake_resolves_a_held_tip_claim_after_archive`, `test_a_root_wake_floor_reads_the_tip_tui_row`, `test_nit1_a_forwardable_ledger_on_a_profile_the_gateway_does_not_serve_keeps_the_chat`, `test_nit1_forwardable_ledger_work_with_no_local_adapter_keeps_the_chat`, `test_nit1_the_hand_back_starts_an_info_episode_once_per_chat`, `test_nit2_the_owner_probe_opens_state_db_at_most_once_per_chat_per_ttl`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
