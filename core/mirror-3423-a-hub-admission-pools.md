# Hub admission: priority claim pools and capacity budgets

Patches: **2954, 2955, 2956, 2957, 2959, 2960, 2961, 2962, 2963, 2964, 2965, 2967, 2972, 2973, 2974, 2975**.

## Behavior
- An opt-in hub can claim marked (capacity) cards from an extra priority pool even when the regular pool is full; normal cards keep the regular ceiling and never attempt claims when regular is full.
- Claims persist an immutable receipt; pool census counts receipts, not current class; unknown receipts or census invalidate admission rather than looking empty.
- Marked candidates precede normal ones within a dispatch lane, including after replica waiter ranking; worker lanes keep plain priority.
- Queue notes report pools and marked-lane order and keep a non-ordering dispatcher skip.
- Machine metrics report a coherent admission policy and pool census; an old Board gets the tuple stripped on retry; invalid policy advertises no support.
- Imported capacity cards carry their original chat custody without an outbox.
- Pool-aware dispatch and pickup budgets are charged marked to priority first, then regular.

## How it works
2954/2955/2956 add the pool foundation, receipts and origin capture; 2957/2964 order marked candidates; 2959/2967/2972 report policy in machine metrics (tests ported to the sampler seam); 2960 documents the contract; 2961/2975 extend queue notes; 2962 carries chat custody; 2963/2965 add concurrency and guard tests; 2973/2974 add a per-tick `PoolBudget`.


## Code
- `docs/kanban-hub-admission-v2.md`
- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_admission_origin.py`
- `hermes_cli/kanban_admission_pools.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_queries.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_db_values.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/machine_admission_metrics.py`
- `hermes_cli/machine_drain_metrics.py`
- `hermes_cli/machine_metrics.py`
- `tools/kanban_show.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_create_args.py`
- `tools/kanban_tools_schemas.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_admission_origin.py`: `test_pickup_carries_original_chat_without_outbox`.
- `tests/hermes_cli/test_kanban_hub_admission.py`: `test_admission_metadata_survives_remote_option_encoding`, `test_capacity_origin_cap_survives_task_class_edits`, `test_claim_captures_created_origin_not_mutable_shepherd`, `test_claim_persists_immutable_regular_receipt`, `test_concurrent_cross_board_claims_obey_origin_and_pool_caps`, `test_dispatch_budget_reaches_extra_pool_at_regular_cap`, `test_dispatch_full_regular_pool_claims_marked_with_shared_memory_budget`, `test_dispatch_invalid_priority_setting_defers_without_crashing`, `test_dispatch_marked_candidate_precedes_normal_in_same_lane`, `test_dispatch_pool_order_keeps_worker_priority`, `test_explicit_normal_class_is_the_default_and_needs_no_courier_probe`, `test_full_regular_pool_never_attempts_normal_claims`, `test_malformed_pool_config_refuses_cleanly`, `test_partly_free_regular_pool_attempts_only_admissible_normal_rows`, `test_pool_budget_charges_marked_to_priority_then_regular`, `test_pool_census_uses_one_board_snapshot`, `test_pool_note_keeps_non_ordering_dispatcher_skip`, `test_pool_note_ordering_skip_keeps_pool_advice`, `test_pool_note_without_skip_is_unchanged`, `test_pool_receipt_is_local_only`, `test_pool_selection_preserves_separate_caps`, `test_queue_note_reports_pools_and_marked_lane_order`, `test_queue_pool_details_do_not_change_legacy_workers`, `test_queue_unknown_pool_census_does_not_look_empty`, `test_replica_waiter_ranking_cannot_undo_marked_lane_order`, `test_row_without_admission_column_reads_as_normal`, `test_tool_create_preserves_admission_metadata`, `test_tool_invalid_capacity_refuses_before_placement`, `test_unknown_pool_receipt_invalidates_census`, `test_unsupported_fence_resets_prior_priority_receipt`, `test_urgent_claim_uses_extra_pool_with_regular_pool_full`, `test_whole_host_pool_count_uses_receipts_not_current_class`, `test_worker_pickup_gets_pool_aware_budget`.
- `tests/hermes_cli/test_kanban_hub_admission_grant_echo.py`: `test_enabled_pools_exclude_only_own_echo_and_preserve_receipt`, `test_pool_census_skips_foreign_lockless_holder_like_the_scalar`.
- `tests/hermes_cli/test_kanban_hub_admission_guards.py`: `test_candidate_pool_unknown_census_selects_no_pool`, `test_capacity_receipt_without_origin_invalidates_origin_census`, `test_dispatch_budget_keeps_legacy_scalar_when_cpu_policy_is_off`, `test_dispatch_budget_unknown_census_spawns_nothing`, `test_invalid_pool_policy_defers_at_advisory_before_lease`, `test_invalid_pool_setting_refuses_with_its_own_reason`, `test_normal_card_keeps_regular_advisory_ceiling_on_opted_in_hub`, `test_pool_census_error_at_final_gate_refuses_instead_of_scalar_admit`, `test_pool_change_during_sample_rechecks_cpu_against_new_pool`.
- `tests/hermes_cli/test_kanban_pickup_local_order.py`: `test_pickup_pool_budget_imports_only_marked_cards_when_regular_full`.
- `tests/hermes_cli/test_machine_admission_metrics.py`: `test_bad_census_omits_entire_tuple`, `test_collect_uses_one_config_and_one_census`, `test_invalid_or_disabled_policy_does_not_advertise_support`, `test_old_board_strips_admission_tuple_and_cpu_on_single_retry`, `test_reporter_rechecks_policy_after_cpu_sample`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Behavior is opt-in per hub. 2960 is documentation only; 2967 and 2972 adapt existing tests to new seams.
