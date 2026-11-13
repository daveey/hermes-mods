# Session lifecycle fixes: remote card outcomes, subagent sweep, deleted-chat resume

Patches: **2938, 2939, 2942**.

## Behavior
- Chat outcomes refresh when remote cards appear, change or finish; the first pass only seeds, failed reads preserve the snapshot, skipped chats are not refreshed, and one failing chat does not starve later chats.
- A subagent of a compressed delegator is reaped only when the delegator's whole continuation lineage is ended.
- Resuming a deleted chat is terminal (quiet, no traceback), and the startup resume sweep retires its marker instead of retrying every restart.

## How it works
- 2938 adds remote-card outcome refresh to `tui_gateway/notice_fanout`.
- 2939 changes `end_subagents_of_ended_parents` to walk continuation edges in one recursive query.
- 2942 checks the selected store's retirement fence in `session.resume` before materializing a minted draft, and makes `startup_resume` retire markers of deleted sessions in their named profile home.


## Code
- `hermes_state_maintenance.py`
- `tui_gateway/methods_session.py`
- `tui_gateway/methods_session_minted.py`
- `tui_gateway/notice_fanout.py`
- `tui_gateway/startup_resume.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_async_delegation_child_row_end.py`: `test_child_of_compressed_delegator_with_open_tip_is_left_alone`, `test_compressed_delegator_with_no_continuation_counts_as_ended`.
- `tests/tui_gateway/test_remote_card_outcomes.py`: `test_failed_chat_retries_unchanged_projection`, `test_failed_first_chat_does_not_starve_later_chat`, `test_failed_read_preserves_snapshot`, `test_first_pass_seeds_only`, `test_missing_chat_and_unlisted_cards`, `test_origin_alias_and_duplicate_rows_refresh_once`, `test_remote_appears_changes_and_finishes`, `test_skipped_chat_not_refreshed`, `test_unknown_unfinished_and_compression_tip`.
- `tests/tui_gateway/test_session_resume_materializes_minted_key.py`: `test_resume_deleted_minted_key_is_terminal_not_a_draft`, `test_resume_deletion_after_preflight_is_quiet_and_terminal`, `test_resume_retirement_checked_in_the_selected_profile_store`, `test_resume_unexpected_materialize_failure_still_warns`, `test_resume_valid_draft_still_materializes_after_retirement_check`.
- `tests/tui_gateway/test_startup_resume.py`: `test_deleted_session_marker_is_retired_and_not_resumed_again`, `test_deleted_session_marker_retired_in_its_named_profile_home`, `test_plain_not_found_is_not_proof_of_deletion`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
2942: a plain not-found is not treated as proof of deletion (covered by test).
