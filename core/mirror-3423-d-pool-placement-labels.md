# Pool placement labels and profile parity

Patches: **3113, 3115, 3129, 3147, 3168, 3172**.

## Behavior
- A mandatory-model-pin mark on a card in a pool queue always carries the `capability:mandatory-model-pin` label, even from a process that does not run board sync, so an older pool worker cannot lease it.
- A pickup-stall pool move reads the card's `skills` the way the Board does: unreadable skills refuse the pool queue, and readable skills add `skill:<s>` requirements. `machine:<X>` moves are unchanged.
- A card with a NULL or empty assignee is judged as `profile:default`, in the pool move and in the alarm placement/stranded re-place paths.
- A card denied by hub admission keeps the worker queue (`runs_on`) across a gateway restart, and the worker-first self-check re-pins stranded worker-first cards to the pool.

## How it works
- 3113 makes `mark()` read `runs_on` whenever the column exists and take "me" from the board file's own sync-state row; the shared `carry()` gate adds the label whenever any `pool:*` label is present (fail closed), with the `machine:<me>` exemption only without a pool label.
- 3115 makes `scope_refusal` refuse a `pool:worker` move when `skills` is present but not a JSON list of strings, and require `skill:<s>`.
- 3129 builds `profile:default` for an assignee-less card in the repin need.
- 3147 shares one profile label helper between `alarm_placement` and `_replace_stranded`.
- 3168 makes `kanban_repin_notice.profile_label` the single copy used by `required_labels`, the pickup-stall helper and the repin need.
- 3172 lets a denied create during a restarted sync loop's first round join the worker queue (a crash or start-refusal record still waits for a completed round), and adds a self-check repin of stranded worker-first cards.

## Code
- `hermes_cli/kanban_mandatory_pin.py`
- `hermes_cli/kanban_pickup_stall_repin.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_repin_notice.py`
- `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_sync_runstate.py`, `hermes_cli/kanban_sync_stop.py`
- `hermes_cli/kanban_worker_first_selfcheck.py`, `hermes_cli/kanban_worker_placement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_mandatory_pin.py`: `test_marking_labels_a_pool_card_in_a_process_without_sync`.
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`: `test_skills_the_board_cannot_read_refuse_the_pool_queue_only`, `test_the_pool_queue_needs_the_cards_skill_labels`, `test_no_skills_still_takes_the_pool_queue`, `test_a_row_without_a_skills_column_reads_as_no_skills`, `test_a_card_with_no_assignee_needs_the_default_profile_like_the_board`.
- `tests/hermes_cli/test_kanban_pickup_alarm_placement.py`: `test_an_alarm_with_no_assignee_is_judged_as_profile_default_and_not_re_pinned`, `test_a_null_or_empty_assignee_needs_profile_default_like_the_board`.
- `tests/hermes_cli/test_kanban_profile_label_parity.py`: `test_shared_helper_reads_null_and_empty_as_default`, `test_all_three_sites_agree`, `test_every_site_routes_through_the_shared_helper`.
- `tests/hermes_cli/test_kanban_pool_deny_runs_on.py`: `test_denied_create_during_a_restarted_loops_first_round_joins_the_worker_queue`, `test_a_crash_or_start_refusal_record_still_waits_for_a_completed_round`, `test_self_check_repins_a_stranded_worker_first_card_to_the_pool`, `test_self_check_leaves_other_worker_first_cards_to_the_reserve`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The 3172 restart window came from the sync loop being recorded "stopped" at every gateway restart until its first completed round, which could take about 20 minutes on a busy hub.
