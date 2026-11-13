# Kanban priority capture: replay open/terminal churn to the final head

Patches: **3216**.

## Behavior
- Priority capture replays task changes that happen while the paged snapshot is being read, up to the final head: a card created then completed mid-window is absent from the snapshot or paged late as done; an existing card completed mid-window is handled; a card reopened mid-window joins the manifest.
- A status-less patch keeps the snapshot status; an open status patch without a status keeps the replayed terminal state; an invalid replayed status refuses.
- Verification accepts a manifest row completed mid-window. A real priority change, membership change, or a priority change before completion still refuses; changes after the final head are ignored.

## How it works
3216 reworks `kanban_priority_feed.py` so terminal and open task changes between the first and last page head are replayed to the final head before verification and terminal-exception resolution.

## Code
- `hermes_cli/kanban_priority_feed.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_priority_window_churn.py` and related files `test_kanban_priority_feed.py`, `test_kanban_priority_first_head.py`: `test_terminal_task_change_is_replayed_to_final_head`, `test_terminal_change_between_first_and_last_page_head_is_replayed`, `test_card_created_then_completed_mid_window_absent_from_snapshot`, `test_card_created_then_completed_mid_window_paged_late_as_done`, `test_existing_card_completed_mid_window`, `test_existing_card_reopened_mid_window_joins_manifest`, `test_open_status_patch_without_status_keeps_replayed_terminal_state`, `test_statusless_patch_keeps_snapshot_status`, `test_completion_mid_window_resolves_terminal_exception`, `test_verify_accepts_manifest_row_completed_mid_window`, `test_real_change_mid_window_still_refuses`, `test_priority_change_before_completion_still_refuses_against_late_done_row`, `test_membership_change_mid_window_still_refuses`, `test_changes_after_final_head_are_ignored`, `test_invalid_replayed_status_refuses`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
