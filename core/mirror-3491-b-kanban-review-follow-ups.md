# Kanban review follow-ups: chat links, forwarding, unblock guard

Patches: **3485, 3488, 3490–3491**.

## Behavior
- **Chat links (3485):** a chat that compressed while board sync was down no longer leaves two unreleased link rows for one card; the chat gateway's reconcile releases the owner-written ancestor row, so the link diff is clean. The owner never writes the released row back. The owner also skips `cron_` chats that the chat gateway would release anyway.
- **Unchecked worktree wording (3488):** when git filter discovery refuses an answer (for example too many filter keys), the cause reads "git filter discovery gave no usable answer" instead of "no answer". Wording only; the tree is still reported as unchecked (fail safe).
- **Placement forward (3490):** parent titles/handoffs embedded in a forwarded card are wrapped in the placement-evidence fence, so a handoff that merely mentions a coordination command no longer keeps the card on the target's home instead of the worker pool. A close marker inside parent text is rewritten so it cannot end the fence early. The footer's read call stays outside the fence; the footer names the source card's real board instead of `default`. The child body's own coordination command still binds.
- **Unblock guard (3491):** `unblock_task` refuses a stale local copy of a done card (no reopen pushed to the board), leaving one `stale_wake_ignored` note; live cards unblock as before.

## How it works
- 3485: `_compressed_away` walks the compression chain in the chat profile's state database (unreadable database releases nothing); `_owner_hub_links` uses `machine_sessions`; a `task_chat_links(task_id)` index is added to the sync schema (no column, so no schema-version change).
- 3488: one shared constant `git_budget.NO_FILTER_ENV` used at both raise sites.
- 3490: `remote_args` wraps parent lines in the evidence markers; the forwarder resolves the current board.
- 3491: `kanban_terminal_guard.unblock_held` runs inside the write transaction before the precondition and reason comment; `reopen_review_task` moves to `kanban_db_review_reopen` (re-exported); a dead fallback in `enforce_after_apply` is dropped.

## Code
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/git_budget.py`
- `hermes_cli/worktree_archive.py`
- `hermes_cli/worktree_ops.py`
- `hermes_cli/kanban_placement_forward.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_review_reopen.py`
- `hermes_cli/kanban_terminal_guard.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_links_owner_hub_subs.py`: `test_a_chat_compressed_during_the_outage_keeps_one_unreleased_row_and_a_clean_diff`, `test_an_uncompressed_second_chat_on_the_same_card_keeps_its_owner_row`, `test_owner_skips_a_cron_session_the_chat_gateway_would_release`, `test_task_chat_links_has_a_task_id_index_in_board_files_and_replicas_and_migrates_old_files`.
- `tests/hermes_cli/test_kanban_move_guard_budget.py`: `test_a_refused_filter_discovery_answer_reads_unchecked_with_an_accurate_cause`.
- `tests/hermes_cli/test_kanban_placement_forward_footer.py`: `test_parent_handoff_naming_coordination_commands_stays_portable`, `test_handoff_cannot_close_the_fence_early`, `test_childs_own_coordination_command_still_binds`, `test_footer_names_the_source_cards_board`, `test_forwarder_passes_its_board`.
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_unblock_of_a_stale_copy_of_a_done_card_does_not_reopen_it`, `test_a_refused_unblock_runs_no_precondition_and_keeps_no_comment`, `test_unblock_of_a_live_card_still_lands`, `test_unblock_of_a_done_row_is_refused_without_a_note`.
- The `enforce_after_apply` cleanup and the `reopen_review_task` move are untested by new tests here.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A released chat-link row stays a row (insert-only rule); the owner never re-writes it.
- 3491 makes `unblock_task` return false for held done cards, which automated callers (blocker clear, quota release, resolved checks) now see.
