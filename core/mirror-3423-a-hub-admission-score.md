# Hub admission phase 2: score ordering, queue explain and capacity report

Patches: **2966, 2968, 2969**.

## Behavior
- Opted-in hubs order each lane by a deterministic score (capped priority, class, bounded descendants, bounded current-interval aging, minus failures; weights in `kanban.admission.weights`, validated) and then keep marked-first partitioning; workers keep plain priority.
- `hermes kanban queue [--explain ID] [--json]` prints order, breakdown, position and pools; `queue --report` measures ready-wait p50 around each capacity card's completion and flags repeat no-effect origins (recommendation only).
- The assignees listing moved out of the CLI facade.

## How it works
2966 adds `kanban_admission_score`, `_explain` and `_report`; 2968 relocates assignees listing to `kanban_assignees_cli`; 2969 adds tests for cap, ties, minimum samples and ready floor.


## Code
- `docs/kanban-hub-admission-v2.md`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_admission_explain.py`
- `hermes_cli/kanban_admission_pools.py`
- `hermes_cli/kanban_admission_report.py`
- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_assignees_cli.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_queue_position.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_hub_admission.py`: `test_age_counts_current_ready_interval_not_card_age`, `test_aging_lifts_starved_card_within_bound`, `test_breakdown_sums_every_component_with_default_weights`, `test_capacity_card_outranks_higher_priority_normal_card`, `test_capacity_report_cli_window_excludes_old_cards`, `test_capacity_report_flags_repeat_no_effect_origin_and_unknown_is_not_failure`, `test_capacity_report_measures_ready_wait_p50_around_completion`, `test_capacity_report_one_moved_card_keeps_origin_class`, `test_capacity_report_too_few_samples_is_unknown`, `test_capacity_report_wait_starts_at_latest_ready_event_not_creation`, `test_configured_weights_are_validated_and_applied`, `test_consecutive_failures_lower_the_score`, `test_descendants_raise_score_and_are_capped`, `test_equal_scores_break_on_ready_start_then_id`, `test_equal_scores_prefer_earlier_ready_start_over_smaller_id`, `test_invalid_weights_refuse_with_their_own_reason`, `test_legacy_uncapped_priority_is_capped_in_the_score`, `test_opted_in_hub_lane_order_uses_score_and_keeps_marked_first`, `test_queue_explain_on_worker_reports_plain_priority`, `test_queue_explain_prints_breakdown_position_and_pools`, `test_queue_explain_unknown_or_unqueued_card`, `test_queue_note_carries_score_breakdown_on_opted_in_hub`, `test_replica_descendants_count_and_local_unlink_or_completion_wins`, `test_worker_lane_order_keeps_plain_priority`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The capacity report needs enough samples; too few reads as unknown (tested). The queue CLI is covered by the listed `queue --explain` tests only; `--report` is covered by `test_capacity_report_cli_window_excludes_old_cards`.
