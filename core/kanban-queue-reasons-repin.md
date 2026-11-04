# Worker queue reasons and explicit origin repin

Patches: **1757** (`761a9caf66`, `[daveey] Persist worker queue reasons and explicit origin repin (#701)`); **1795** (`133828f11a`, `[daveey] Record the Board's structural return-to-origin as a queue reason (#711)`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Worker queue observations persist as queue reasons, and an explicit origin repin is supported; a lost repin reply is reported as an unknown outcome (1757).
- The Board's structural return-to-origin (`state=origin`) is recorded as a queue reason (1795).

## How it works

`hermes_cli/kanban_queue_reason.py`, `kanban_worker_repin.py`, `machine_drain_metrics.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1757: * [daveey] Persist worker queue observations and support explicit origin repin
- 1795: * [daveey] Persist worker queue observations and support explicit origin repin

## Code

`agent/core_update_drain.py`, `hermes_cli/kanban.py`, `hermes_cli/kanban_move.py`, `hermes_cli/kanban_queue_reason.py`, `hermes_cli/kanban_worker_placement.py`, `hermes_cli/kanban_worker_repin.py`, `hermes_cli/machine_drain_metrics.py`, `hermes_cli/machine_metrics.py`, `tools/kanban_show.py`, `tools/kanban_tools.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_queue_integration.py`: `test_cli_json_text_tool_and_thread_share_observation`, `test_initial_pull_failure_is_durable_and_recovers`, `test_origin_reason_is_current_before_feed_and_survives_pull_error`, `test_origin_reason_text_is_allowlisted`, `test_origin_return_records_reason_and_unfences_origin`, `test_reserve_failure_dedupes_and_recovers`
- `tests/hermes_cli/test_kanban_queue_reason.py`: `test_candidate_order_and_replay_do_not_repeat_comments`, `test_comment_failure_rolls_back_observation`, `test_completed_attempt_does_not_revive_old_wait_on_ready_retry`, `test_drain_bounds_are_explicit_and_never_inferred`, `test_each_reason_has_durable_matching_thread_and_change_only_dedupe`, `test_foreign_and_running_copies_never_write`, `test_latest_qualifies_historical_wait`, `test_latest_uses_origin_record_time_not_replica_insertion_order`, `test_legacy_and_board_errors_are_honest`, `test_lifecycle_changes_do_not_present_old_wait_as_current`, `test_matching_authoritative_assignment_remains_current_without_duplicate`, `test_reason_persists_once_and_recovers` (+4 more)
- `tests/hermes_cli/test_kanban_queue_safety.py`: `test_assigned_to_queued_feed_is_not_a_second_transition`, `test_error_record_failure_preserves_refusal_path`, `test_record_failure_does_not_skip_successful_reserve_pull`, `test_rejection_code_and_class_are_sanitized`, `test_resync_has_no_outage_comment`, `test_revision_race_is_not_a_board_outage`
- `tests/hermes_cli/test_kanban_worker_repin.py`: `test_board_offline_reports_unknown_outcome_with_local_copy_intact`, `test_board_revision_conflict_leaves_policy_intact`, `test_cli_identity_for_another_gateway_is_refused`, `test_cli_move_repins_an_unstarted_origin_policy_card`, `test_committed_request_with_lost_reply_never_claims_policy_unchanged`, `test_http_refusal_leaves_policy_and_outbox_unchanged`, `test_http_success_with_failed_readback_keeps_local_policy`, `test_http_transport_failure_has_unknown_outcome`, `test_hub_identity_must_match_board_gateway`, `test_ineligible_policy_cards_refuse_without_network_or_mutation`, `test_manual_and_running_cards_keep_the_generic_path`, `test_non_origin_target_refuses_instead_of_a_stripped_local_relabel` (+4 more)
- `tests/hermes_cli/test_machine_drain_metrics.py`: `test_absent_and_unparseable`, `test_actual_producer_bound_and_dead_writer`, `test_never_invents_end`, `test_no_retry_of_other_board_errors`, `test_reporter_falls_back_without_discarding_capacity`, `test_stat_failure_is_unknown`, `test_unreadable_is_unknown`
- `tests/hermes_cli/test_machine_metrics.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
