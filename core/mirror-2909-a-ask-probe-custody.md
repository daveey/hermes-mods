# Ask-probe grace, legacy loss and delivery-time stale-ask refresh

Patches: **2731, 2740, 2741, 2742, 2743, 2744, 2774**.

Related context: [kanban-stale-block-ask-refresh-2678.md](kanban-stale-block-ask-refresh-2678.md).

## Behavior

Transient `resolved_check` failures no longer disable detection after a short burst: the failure count threshold must also span at least six hours. A broken or lost probe leaves the card blocked, marks that automatic detection stopped, and sends one actionable warning to its origin. Legacy unmarked losses are backfilled and notified without replaying duplicate warnings. Buffered TUI Kanban notices re-read the ask at delivery so a completed card does not still ask the user to decide.

## How it works

2731 adds `kanban_claim_custody.batch_text`, refreshing text without replacing the `_KanbanText` objects or their `_kb_*` claim attributes. The durable notice text and submitted turn agree; the unkeyed submit path also refreshes (2774 tests it). 2740 adds nullable `task_wake_checks.first_failure_at` and its migration. Exit 1 resets count/time; legacy counts with no timestamp receive a new grace period. A dropped probe records `resolved_check_dropped_at` and `resolved_check_drop_reason` in `human_request`, and refiling clears both from a copy of the request. Gateway/TUI collectors wake and notify on `resolved_check_broken`/`resolved_check_lost`; remote blocked state identity uses the drop stamp. Historical promises to detect automatically are labelled historical, not silently edited. 2741 rechecks the latest filing/drop and eligibility under the write fence; remote-owned probes, current probes, resolved or refiled asks are excluded. 2742 atomically re-emits one backfilled drop event so already-advanced local cursors see it. 2743 identifies the original by portable `origin_event_uid`, exposes optional `Event.uid` via the new `kanban_db_event.py`, and limits integer-ID fallback to UID-less unsynced events. Coalescing suppresses only the exact same-kind historical drop and superseded blocked rendering while retaining the complete cursor range. 2744 and 2774 add status/race/replay and stale-ask mutation controls.

## Code

- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban_ask_resolved.py`
- `hermes_cli/kanban_ask_resolved_backfill.py`
- `hermes_cli/kanban_block_notice.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_event.py`
- `locales/af.yaml`
- `locales/ar.yaml`
- `locales/de.yaml`
- `locales/en.yaml`
- `locales/es.yaml`
- `locales/fr.yaml`
- `locales/ga.yaml`
- `locales/hu.yaml`
- `locales/it.yaml`
- `locales/ja.yaml`
- `locales/ko.yaml`
- `locales/pt.yaml`
- `locales/ru.yaml`
- `locales/tr.yaml`
- `locales/uk.yaml`
- `locales/zh-hant.yaml`
- `locales/zh.yaml`
- `tui_gateway/kanban_claim_custody.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_remote_observations.py`
- `tui_gateway/session_notifications.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_stale_block_ask.py`: `test_still_blocked_card_whose_live_ask_was_cleared_keeps_the_ask`, `test_live_ask_equal_to_the_notice_keeps_it_past_a_reblock_event_without_a_request`, `test_many_ask_marks_on_one_long_line_are_fast`.
- `tests/hermes_cli/test_kanban_ask_backfill.py`: `test_old_drop_is_marked_once_without_new_events`, `test_refile_does_not_inherit_old_mark_and_old_drop_does_not_mark_refile`, `test_resolved_event_is_not_marked`, `test_latest_drop_kind_wins`, `test_remote_probe_owner_and_live_probe_and_unblocked_are_left_alone`, `test_write_fence_rechecks_latest_filing_and_missing_probe`, `test_old_drop_is_marked_once_with_one_reemitted_event`.
- `tests/hermes_cli/test_kanban_ask_failure_grace.py`: `test_three_failures_within_hour_keep_probe`, `test_three_failures_spanning_six_hours_drop_once_and_mark`, `test_not_yet_resets_failure_time_and_counter`, `test_elapsed_time_alone_is_not_broken`, `test_lost_probe_marks_and_refiling_clears_mark`, `test_legacy_failure_count_starts_new_grace`.
- `tests/hermes_cli/test_kanban_ask_grace_migration.py`: `test_legacy_column_migration_preserves_armed_probe`, `test_satisfied_clears_streak_and_refile_starts_fresh`.
- `tests/hermes_cli/test_kanban_ask_mutation_fences.py`: `test_grace_window_is_exactly_six_hours_with_count_already_met`, `test_sql_prefilter_excludes_probed_unblocked_and_already_marked`, `test_eligible_checks_each_clause`, `test_foreign_card_without_host_is_left_and_with_own_host_is_marked`, `test_write_fence_skips_when_latest_drop_changes_in_race`, `test_write_fence_skips_when_card_stops_being_eligible_in_race`, `test_backfill_tolerates_a_database_without_wake_checks`, `test_ask_filed_after_old_drop_with_older_filing_is_judged_by_latest_filing`, `test_coalesce_keeps_other_events_and_uses_latest_drop`, `test_drop_warning_names_cause_from_mark_else_from_event`, `test_store_request_clears_both_marks_without_mutating_the_callers_dict`, `test_dropped_card_notice_names_the_work_and_refiled_card_via_session_formatter`, `test_remote_drop_mark_requires_blocked_valid_nonzero_json_or_dict`, `test_backfill_coalesces_only_exact_same_kind_references`.
- `tests/hermes_cli/test_kanban_ask_resolved_check.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/hermes_cli/test_kanban_ask_resolved_sync.py`: `test_dropped_ask_mark_and_refile_clearing_cross_the_sync_transport`.
- `tests/hermes_cli/test_kanban_ask_stale_failure.py`: `test_stale_failed_result_after_same_command_refile_leaves_new_generation_untouched`.
- `tests/hermes_cli/test_kanban_ask_survivor_fences.py`: `test_status_prefilter_avoids_even_reading_nonblocked_drop_history`, `test_status_only_change_between_scan_and_write_rejects_backfill`, `test_non_drop_replay_payload_cannot_suppress_same_kind_history`, `test_invalid_uid_reference_never_suppresses_or_crashes`, `test_synced_replay_integer_collision_does_not_remove_uidless_local_event`.
- `tests/hermes_cli/test_kanban_drop_backfill_sync.py`: `test_transport_remaps_drop_ids_but_origin_gets_exactly_one_notice`, `test_numeric_fallback_never_suppresses_synced_events_or_overrides_uid`, `test_event_from_row_uid_and_uidless`, `test_origin_uid_guards`.
- `tests/hermes_cli/test_kanban_resolved_check_drop_notice.py`: `test_block_notice_marks_stale_promise_historical`, `test_block_notice_for_dropped_card_without_event_name_still_warns`, `test_undropped_ask_renders_unchanged_and_refiled_ask_is_not_called_stopped`, `test_gateway_notifier_sends_and_wakes_origin_exactly_once`, `test_tui_origin_gets_one_notice_per_drop_event`, `test_tui_offline_origin_unseen_block_plus_drop_warns_once`, `test_tui_ordinary_block_alone_still_notifies`, `test_tui_drop_alone_still_notifies`, `test_gateway_offline_origin_unseen_block_plus_drop_sends_one_stop_warning`, `test_gateway_ordinary_block_alone_still_sends`, `test_remote_origin_gets_one_extra_notice_when_the_replica_shows_the_drop`, `test_legacy_drop_is_notified_once_after_backfill`.
- `tests/tui_gateway/test_kanban_notice_stale_ask_buffered.py`: `test_buffered_block_notice_for_a_finished_card_drops_the_ask`, `test_buffered_block_notice_for_a_still_blocked_card_is_byte_identical`, `test_unkeyed_buffered_block_notice_for_a_finished_card_drops_the_ask`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

The six-hour rule needs both enough consecutive failures and elapsed time; time alone cannot drop a probe. Losing automatic detection is not user approval or completion. Refilling the ask with a working probe, or an authorized unblock, is required. Refiling the same command starts a new generation, which a stale failed result must not mutate. Still-current, remote or unreadable asks remain byte-identical on refresh. UID references must not fall back to colliding receiver-local numeric IDs. Real messaging transports and Desktop rendering remain untested by the mirror pass.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
