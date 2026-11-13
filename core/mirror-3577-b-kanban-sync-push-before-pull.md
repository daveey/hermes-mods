# Kanban sync: push before pulling, bounded pulls, outbox-age alarm

Patches: **3571, 3572, 3573, 3574, 3575, 3576**.

## Behavior
- A sync round sends its pending ops (push plus resolve-rejections) before its pulls as well as after, so a long backlog of feed pages no longer strands local edits in the outbox.
- Each database is pulled at most `PULL_PAGE_BUDGET` (20) pages per round; the loop reruns a board left behind without the poll wait. Pull-done markers are stamped only when the pull reached the feed head.
- One replica puller per board per host: a second puller sees the board lock held and returns busy. `dispatch_tick`'s live pull uses the same lock and keeps its pages if it runs out of its 2 s budget, still reevaluating so worker-first cards are reserved during a backlog.
- While a round is behind the feed head, run rows are held (a pending start cannot overwrite an end the Board holds), and a status row for a card the Board has as done/archived (or when the Board gives no answer) is skipped until a pull reaches the head, while rows for other live cards still go.
- A busy round is skipped (DEBUG log only, board not marked failed) and retried after a short delay. Reconcile pulls the replica to the head in chunks, releasing the board between them, and waits out busy rounds for a bounded time.
- `sync status` prints `oldest_pending_age_s`; the loop warns once per window when an unanswered op is older than 10 minutes after a good round, and the dispatcher tick files one card per stalled-outbox incident. Held run rows do not age the alarm.

## How it works
3571 reworks `kanban_sync_client` rounds and `pull()` (returning `Pulled`, an int with `.more`/`.busy`), adds `kanban_sync_backlog`, the `kanban_sync_outbox_age` alarm and `kanban_sync_pickup_publish` (where `publish_pickup_if_changed` moved, re-exported). 3572 adds the post-pull run-row hold, the skipped busy-round path, chunked reconcile (`replica_at_head`) and a dispatch-tick rule that continues only on `BoardBudgetExhausted`. 3573 adjusts two order-dependent tests and the alarm's message about a failing loop. 3574 moves `heal_clobbered_completions` and its placeholder constant to `kanban_sync_divergence` for the file-size cap. 3575 makes claim-screen test suites opt out of same-round promotion and lets the fake Board serve reserve. 3576 renames `hold_runs` to `before_head`, checks non-running/terminal status rows against the Board's own row (`row_evidence`) and moves selection to `kanban_sync_backlog.sendable_before_head`; `kanban_terminal_guard` gains supporting code.

## Code
- `gateway/kanban_watchers.py`
- `hermes_cli/kanban_sync_backlog.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_pickup_publish.py`
- `hermes_cli/kanban_sync_divergence.py`
- `hermes_cli/kanban_worker_placement.py`
- `hermes_cli/kanban_terminal_guard.py`
- `tests/hermes_cli/test_kanban_sync_push_starvation.py`
- `tests/hermes_cli/kanban_sync_fake_board.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_push_starvation.py`: `test_a_pending_op_is_sent_before_the_second_backlog_page`, `test_the_backlog_drains_over_rounds_and_pulled_at_marks_only_the_head`, `test_a_pushed_status_survives_older_versions_of_its_row_in_the_backlog`, `test_a_round_ending_its_page_budget_still_pushes_resolves_refreshes_and_reevaluates`, `test_the_loop_reruns_a_board_left_behind_without_the_poll_wait`, `test_a_second_replica_puller_leaves_the_cursor_alone_while_a_round_holds_the_board`, `test_sync_diff_compares_the_replica_as_is_while_a_round_holds_the_board`, `test_a_worker_first_card_is_reserved_on_the_first_tick_despite_a_backlog`, `test_the_tick_neither_pulls_nor_reads_the_feed_while_a_round_holds_the_board`, `test_sync_status_prints_the_age_of_the_oldest_pending_op`, `test_a_good_round_with_an_old_unanswered_op_warns_once_per_window`, `test_the_dispatcher_files_one_card_per_stalled_outbox_incident`, `test_a_round_stuck_past_the_window_counts_as_a_live_loop`, `test_report_opens_one_card_then_comments`, `test_a_round_left_behind_holds_a_pending_run_start_over_the_boards_end`, `test_a_round_a_tick_holds_off_is_skipped_not_failed_and_retried_shortly`, `test_a_reconcile_under_a_backlog_lets_a_round_push_between_its_replica_chunks`, `test_a_reconcile_waits_out_a_round_holding_the_board_then_gives_up`, `test_a_raw_read_timeout_in_the_ticks_pull_stays_a_recorded_pull_error`, `test_run_rows_held_while_behind_the_head_do_not_age_the_alarm`, `test_a_wake_on_a_card_the_board_completed_waits_for_the_head_while_a_live_one_goes_first`.
- `tests/hermes_cli/test_kanban_hub_admission_claim.py`: `test_a_claim_deny_with_a_portable_fleet_probe_moves_to_the_worker_queue`.
- Edited without new test names: `tests/hermes_cli/test_kanban_sync_own_writes.py`, `test_kanban_sync_rejections.py`, `test_kanban_sync_feed_apply_credit.py`, `test_kanban_sync_run_rows.py`, `test_kanban_sync_partition_repair.py`, `test_kanban_claim_deny_selfcheck.py`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The pre-pull send stops at the first run row (FIFO) because run rows are last-write-wins on the Board.
- A failing sync loop is not covered by another alarm; the outbox-age check cites a follow-up for that.
- 3574 is a pure move for the file-size cap; 3575 only changes test fixtures.
