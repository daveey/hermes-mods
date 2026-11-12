# Worker pickup budgets, truthful stall alarms and placement remedies

Patches: **2738, 2739, 2747, 2750, 2759, 2762, 2763, 2769, 2773, 2779**.

Related context: [kanban-pickup-stall-hold-window-2678.md](kanban-pickup-stall-hold-window-2678.md), [kanban-operator-call-2563.md](kanban-operator-call-2563.md).

## Behavior

Quota-held and Board-guarded cards do not consume slots intended for work that can start, so they cannot starve eligible pinned cards. Stall alarms distinguish per-card quota/guard waits from machine registry holds, avoid treating brief reservations as long stalls, and avoid duplicate host-wide repairs. An alarm can re-place a listed stalled card onto `pool:worker` when another worker is eligible. Explicit placement `auto` overrides context-only skills, never device skills or title/body machine markers. Hub pickup can call its admission judge under the owner secret scope.

## How it works

2738 predicts the local quota decision against the replica before copying; errors fall back to normal copying. 2739 names the skip `quota_hold`, includes a seconds-precision UTC deadline and suppresses its repair until that deadline plus the stall threshold. 2762 separately imports up to `FREE_PER_TICK=4` Board-held cards outside the pickup budget so local guards can recheck them; rejected/evicted copies are not retried for 600 seconds under the same signature (sync version, hold start, reason), while changed signatures re-arm. 2763 uses `guard_held`, not registry `hold`, and files card-specific guard guidance. 2759 advances the pin clock from the newest worker-queue observation of the card not pinned here; open host-wide repair coverage spans cause changes. 2747 qualifies memory pressure/update drain and native `at_capacity` notes with the observing machine. 2750 filters explicitly-auto-overridable skills through configurable `kanban.placement.auto_overridable_skill_markers` (normalized defaults `^myapp`, `^bb-`), preserves omitted-placement behavior, and gives separate context/device/text remedies. 2769 checks mixed skills before recommending auto and adds drift scanning for empty-dict open configuration sections. 2773 binds launch-owner scope only when multiplexing is active and none is already bound, preserves an existing turn scope, propagates it to the judge thread, and logs each unavailable branch; route remains the exact subscription judge or refusal. 2779 allows exactly `["pool:worker"]` in addition to another eligible machine, preserving alarm ownership/listing/current-card/capability/profile/freshness/free-slot checks and excluding the stalled worker.

## Code

- `agent/prompt_builder.py`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_capacity_evidence.py`
- `hermes_cli/kanban_hub_admission.py`
- `hermes_cli/kanban_hub_admission_pickup.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_pickup_held.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_pickup_stall_repin.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_diagnostics.py`
- `hermes_cli/kanban_worker_node.py`
- `scripts/ci/check_docs_drift.py`
- `scripts/ci/docs_drift_baseline.json`
- `tools/kanban_tools_placement.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/configuration.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_docs_drift.py`: `test_open_section_keys_are_checked`.
- `tests/hermes_cli/test_kanban_capacity_evidence.py`: `test_describe_names_observing_machine_for_machine_scoped_restrictions`, `test_describe_without_machine_keeps_plain_reason`.
- `tests/hermes_cli/test_kanban_pickup_held_budget.py`: `test_guarded_cards_do_not_fill_the_batch_and_a_pinned_card_is_picked`, `test_an_unkept_held_copy_is_not_reimported_every_tick`, `test_a_new_hold_is_rechecked_at_once`, `test_the_held_imports_per_tick_are_bounded`, `test_a_capped_tick_reports_a_held_pinned_card_as_held`, `test_an_unreadable_board_hold_takes_the_budget_as_before`, `test_a_stalled_guard_held_card_does_not_file_the_registry_hold_repair`.
- `tests/hermes_cli/test_kanban_pickup_quota_held.py`: `test_quota_held_cards_do_not_starve_a_pinned_card`, `test_an_episode_whose_wall_has_passed_is_copied`, `test_a_failed_hold_check_copies_as_before`, `test_a_quota_held_pinned_card_files_no_repair_until_its_deadline_passes`.
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`: `test_the_pool_queue_is_a_valid_target_when_another_pool_worker_is_eligible`, `test_the_pool_queue_is_refused_when_no_other_pool_worker_is_eligible_now`, `test_the_pool_queue_is_exactly_the_pool_label`, `test_the_pool_queue_still_needs_the_cards_capabilities`, `test_the_new_machine_needs_the_cards_required_capabilities`, `test_the_alarms_run_re_places_a_listed_card_onto_the_pool_queue`, `test_the_alarms_run_may_not_move_an_unlisted_card_or_back_onto_the_stalled_machine`.
- `tests/hermes_cli/test_kanban_pickup_stall_reservation.py`: `test_a_brief_reservation_of_a_long_ready_card_raises_no_alarm`, `test_a_reservation_held_past_the_interval_still_raises_its_alarm_from_the_reservation`, `test_observations_naming_this_machine_or_bad_payloads_do_not_restart_the_clock`, `test_a_card_whose_host_wide_cause_changes_keeps_one_open_repair`, `test_a_closed_host_wide_repair_does_not_cover_another_cause`.
- `tests/hermes_cli/test_kanban_placement.py`: `test_marker_cases_a_through_e`.
- `tests/hermes_cli/test_kanban_placement_marker_remedy.py`: `test_a_skill_marker_remedy_names_auto_only_when_auto_would_move_the_card`, `test_a_mixed_skill_card_gets_the_device_remedy_when_auto_would_not_move_it`.
- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_explicit_auto_beats_a_skill_local_marker_and_the_remedy_names_it`, `test_explicit_auto_keeps_a_device_skill_local`, `test_marker_remedies_name_the_override_that_works`, `test_a_local_pin_note_simulates_an_omitted_placement_not_an_explicit_auto`, `test_heavy_route_accepts_a_pool_label_and_checks_it_is_live`.
- `tests/test_kanban_hub_admission_dispatcher_scope.py`: `test_dispatcher_context_gets_a_live_verdict_under_the_owner_scope`, `test_a_bound_turn_scope_is_kept`, `test_unavailable_logs_the_branch`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

`hold`, `quota_hold` and `guard_held` are different diagnoses; never advise clearing a machine's registry hold for a card's respawn guard. No readable quota deadline means no expected-wait suppression. Outside-budget imports remain bounded; unreadable Board holds retain old budget behavior. `auto` does not erase device requirements or real title/body markers; the mixed-skill remedy must name the device constraint. Hub judge failure stays fail-closed, not local fallback. Pool re-placement does not grant `host_reason` or permission to move arbitrary cards. Real worker reservations, production admission calls and fleet-wide load were not exercised here.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
