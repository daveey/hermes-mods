# Kanban priority capture reads the inventory at one fixed head

Patches: **3237, 3293**.

## Behavior
- `supervisor-requests --priority-operation origins|migrate` (CLI entry path untested; the tests drive the capture/verify functions directly) no longer refuses with "canonical inventory changed" when cards are created while a multi-page capture is running.
- A card created mid-window and still open (or created, completed and reopened) joins the inventory; one created above the cap and finished mid-window is out of scope; a missing card that is not an admissible creation still refuses; creations after the final head are ignored.
- A malformed server whose snapshot page heads decrease, or whose page head exceeds the final head, makes capture and verify refuse; monotonic heads stay accepted.

## How it works
- 3237 refuses any snapshot page head below the previous one. Together with the existing final >= last-head check, every page head is bounded by the final head.
- 3293 makes `check_interval` produce the inventory at one fixed head (the final head): rows created during the read are added in id order when admissible, so new ULID-style ids that sort before older ids no longer look "missing from snapshot".

## Code
- `hermes_cli/kanban_priority_prepare.py`
- `hermes_cli/kanban_priority_feed.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_priority_page_heads.py`: `test_monotonic_page_heads_stay_accepted`, `test_non_monotonic_page_heads_refuse_capture_and_verify`, `test_page_head_above_final_head_refuses`.
- `tests/hermes_cli/test_kanban_priority_fixed_head.py`: `test_card_created_mid_window_and_still_open_joins_inventory`, `test_card_created_completed_and_reopened_mid_window_joins_inventory`, `test_card_created_at_the_cap_is_accepted`, `test_card_created_above_cap_and_finished_mid_window_is_out_of_scope`, `test_missing_card_that_is_not_an_admissible_creation_refuses`, `test_creation_after_final_head_is_ignored`, `test_created_rows_join_the_inventory_in_id_order`, `test_verify_accepts_a_card_created_during_its_own_read`, `test_verify_refuses_a_card_created_above_the_cap_during_its_read`, `test_busy_board_creations_land_in_pages_already_read`, `test_busy_board_capture_is_the_inventory_at_the_final_head`, `test_busy_board_verify_reads_back_through_churn`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
