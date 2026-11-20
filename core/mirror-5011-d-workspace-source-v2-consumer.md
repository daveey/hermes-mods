# Workspace source protocol 2: scratch-owned external worktrees and selected artifacts

Patches: **4688, 4689, 4690, 4691, 4692, 4693**

- 4688 (`c0ade6a0f8`): [daveey] workspace_source v2 consumer: scratch-owned external worktrees and selected artifacts
- 4689 (`bc764301c0`): [daveey] workspace_source v2: own external worktrees, bound premanifest recovery, gate initial on enabled
- 4690 (`fb335a83d6`): [daveey] serialize refused source replay ownership and reservation
- 4691 (`d3b20a01f6`): workspace_source v2: keep protocol-1 worktree artifacts on the legacy path
- 4692 (`8cdd241ccb`): [daveey] Reserve published external sources and scope artifact staging to task board
- 4693 (`39c55b6e26`): [daveey] Preserve original delivery policy before source artifact snapshots

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Scratch `initial` attach: a live lease holder registers a configured clone's external worktree via the bound heartbeat `source_path`, with worker guard, bound task/run, lane check and in-transaction rechecks. Repository identity is persisted and re-proven at publish and ending; renames fail closed while retaining custody.
- Selected artifacts are staged from snapshots under the task's board (not the ambient board), published with ending-deadline budgets; selection is atomic and a failed copy keeps the previous selection.
- Two active tasks can never own one external worktree: ownership is checked against other live tasks' source journals inside the observation write transaction, including replay of refused observations (reopened to pending in the same transaction) and published attaches, before holder reuse and return-home fast-forward.
- Premanifest predecessor recovery shares the resolver's materialize budget. Registration refuses `source_disabled` when the feature is disabled.
- Protocol-1 worktree cards on source-enabled hosts keep legacy attachment preservation (no snapshot scan, selection or v2 manifest).
- The original delivery policy is captured before source artifact snapshots.

## How it works

Many `hermes_cli/kanban_source_*` modules (registration, resolver, launch, artifacts, publish, receipt, board, clones, preflight, move) plus sync attachment HTTP helpers and `tools/kanban_tools_source.py`; seven `docs/workspace-source-v2-*.md` design notes. Behavior is gated by `kanban.workspace_source` and the negotiated source protocol.

## Code

- `docs/workspace-source-v2-artifacts.md`
- `docs/workspace-source-v2-completion.md`
- `docs/workspace-source-v2-core-spike.md`
- `docs/workspace-source-v2-ending-deadline.md`
- `docs/workspace-source-v2-published-identity.md`
- `docs/workspace-source-v2-registration.md`
- `docs/workspace-source-v2-review-launch.md`
- `hermes_cli/kanban_metadata_files.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_source_artifact_http.py`
- `hermes_cli/kanban_source_artifact_launch.py`
- `hermes_cli/kanban_source_artifacts.py`
- `hermes_cli/kanban_source_board.py`
- `hermes_cli/kanban_source_clones.py`
- `hermes_cli/kanban_source_dispatch.py`
- `hermes_cli/kanban_source_launch.py`
- `hermes_cli/kanban_source_move.py`
- `hermes_cli/kanban_source_preflight.py`
- `hermes_cli/kanban_source_publish.py`
- `hermes_cli/kanban_source_receipt.py`
- `hermes_cli/kanban_source_registration.py`
- `hermes_cli/kanban_source_resolver.py`
- `hermes_cli/kanban_sync_attachment_http.py`
- `hermes_cli/kanban_sync_attachments.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_workspace_source.py`
- `tools/kanban_show.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`
- `tools/kanban_tools_source.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/hermes_cli/test_kanban_source_artifact_concurrency.py`: `test_competing_selection_during_copy_keeps_winner`, `test_snapshot_buffers_are_released_before_next_copy`, `test_portable_refuses_selection_changed_during_transport`, `test_copy_failure_preserves_previous_selection`
- `tests/hermes_cli/test_kanban_source_artifact_deadline.py`: `test_ending_transfer_caps_private_client`, `test_expired_budget_stops_next_transfer`, `test_actual_publish_paths_supply_same_git_deadline`
- `tests/hermes_cli/test_kanban_source_artifact_edge_contracts.py`: `test_metadata_ack_preserved`, `test_selection_requires_nonblank_string_run_uid`, `test_selection_rejects_invalid_container_before_io`, `test_missing_sync_uid_rolls_back_selection`, `test_publication_missing_current_run_is_typed`, `test_unobserved_receipt_defers_to_board`, `test_foreign_receipt_does_not_use_local_selection`
- `tests/hermes_cli/test_kanban_source_artifact_inheritance.py`: `test_successor_inherits_explicit_selection_on_replay`
- `tests/hermes_cli/test_kanban_source_artifact_launch.py`: `test_resolver_verifies_selected_download_before_return`, `test_transport_has_private_shared_remaining_deadline`, `test_premanifest_successor_verifies_exact_retained_selection`
- `tests/hermes_cli/test_kanban_source_artifact_pickup.py`: `test_artifact_retry_same_revision_including_holder`
- `tests/hermes_cli/test_kanban_source_artifact_publish.py`: `test_artifact_only_change_cannot_use_either_unchanged_shortcut`, `test_scratch_first_publish_preserves_kind_and_protocol`, `test_unavailable_selected_bytes_retain_even_when_git_unchanged`
- `tests/hermes_cli/test_kanban_source_artifact_run_fence.py`: `test_stage_requires_same_live_attached_run`
- `tests/hermes_cli/test_kanban_source_artifact_snapshot.py`: `test_leaf_symlink_is_not_implicit_selection`, `test_replacement_at_open_never_enters_selection`, `test_filename_credential_is_not_published`
- `tests/hermes_cli/test_kanban_source_artifact_staging.py`: `test_selection_replays_after_reopen_and_original_loss`, `test_changed_bytes_get_new_uid_and_only_selected_transfer`, `test_remote_selected_download_survives_scratch_loss`, `test_unanswered_upload_reuses_uid_and_bytes`, `test_mismatch_never_becomes_success`, `test_invalid_selection_does_not_replace_prior`, `test_snapshot_rollback_retries_without_duplicate_or_missing_bytes`, `test_copy_changed_between_precheck_and_upload_is_never_sent`, `test_portable_uses_same_run_selection_not_stale_predecessor`, `test_duplicate_names_are_distinct_and_order_is_canonical`
- `tests/hermes_cli/test_kanban_source_attach_reconcile.py`: `test_definite_initial_refusal_is_typed_and_kept_as_refused`, `test_old_board_initial_is_typed_board_lacks_source`, `test_uncertain_initial_refusal_keeps_pending`, `test_refused_replay_reopens_pending_before_rpc_and_attaches`, `test_refusal_never_overwrites_a_concurrent_settlement`, `test_published_attach_definite_refusal_forgets_observation`, `test_published_attach_uncertain_refusal_keeps_pending`, `test_published_attach_refusal_never_deletes_a_concurrent_settlement`
- `tests/hermes_cli/test_kanban_source_completion_stage.py`: `test_completion_snapshot_survives_retry_and_ending_rollback`, `test_completion_never_infers_caller_run`, `test_selection_uses_connection_board_not_ambient`
- `tests/hermes_cli/test_kanban_source_delivery_policy.py`: `test_original_delivery_policy_before_snapshot`
- `tests/hermes_cli/test_kanban_source_dispatch_v2.py`: `test_exact_grant_evidence_survives_transport_without_replica_authority`, `test_real_resolve_and_attach_keep_scratch_cwd_and_external_source`, `test_successor_rechecks_before_attach_and_keeps_uncertain_journal`, `test_published_scratch_attach_observes_source_not_worker_cwd`
- `tests/hermes_cli/test_kanban_source_identity.py`: `test_registration_persists_complete_identity_on_reopen`, `test_replaced_worktree_same_path_branch_and_head_is_refused`, `test_each_persisted_identity_component_is_revalidated`, `test_missing_v2_identity_is_not_silently_adopted`
- `tests/hermes_cli/test_kanban_source_identity_journal.py`: `test_populated_legacy_journal_migrates_without_inventing_identity`, `test_identity_change_racing_first_observation_preserves_winner`
- `tests/hermes_cli/test_kanban_source_initial_lane.py`: `test_initial_review_lane_refused_before_attach`, `test_review_successor_can_resume`, `test_implementation_initial_still_replays`
- `tests/hermes_cli/test_kanban_source_manifest_v2.py`: `test_v2_roundtrip_canonical_selected_artifacts`, `test_v2_artifact_schema_refuses`, `test_manifest_version_and_kind_are_strict`, `test_utf8_size_not_ascii_escaped_size`, `test_custody_preserves_protocol_and_retained_predecessor`, `test_malformed_custody_identity_is_not_portable`
- `tests/hermes_cli/test_kanban_source_metadata_deadline.py`: `test_private_metadata_client_bounds_response_and_never_replays`
- `tests/hermes_cli/test_kanban_source_origin_deadline.py`: `test_v2_new_commit_ending_proves_origin_once`, `test_origin_proof_git_calls_are_capped_by_the_remaining_deadline`, `test_origin_proof_github_lookup_is_capped_by_the_remaining_deadline`, `test_deadline_spent_during_origin_proof_is_preflight_timeout`, `test_unbounded_origin_proof_keeps_its_own_failure_code`
- `tests/hermes_cli/test_kanban_source_ownership_budget.py`: `test_second_task_cannot_register_an_owned_external_worktree`, `test_concurrent_registration_is_refused_inside_the_observation_transaction`, `test_a_definitely_refused_observation_owns_nothing`, `test_a_finished_task_releases_its_external_worktree`, `test_premanifest_recovery_refuses_past_the_budget`, `test_premanifest_recovery_inside_the_budget_proves_once`, `test_spent_budget_during_recovery_git_is_a_timeout_not_an_identity_refusal`, `test_disabled_host_refuses_initial_before_persistence_or_rpc`, `test_disabled_host_refuses_heartbeat_registration`, `test_refused_replay_rechecks_ownership_inside_the_observation_transaction`, `test_refused_replay_without_a_competitor_reopens_and_attaches`, `test_refused_replay_reserves_path_before_attach_rpc`
- `tests/hermes_cli/test_kanban_source_publication_fence.py`: `test_selection_change_after_portable_refuses_before_manifest_rpc`, `test_selection_change_during_manifest_rpc_is_not_portable`, `test_publication_refused_when_run_ended_after_portable`, `test_consumption_keeps_receipt_when_selection_is_unchanged`, `test_consumption_downgrades_receipt_when_selection_changed_after_preflight`, `test_consumption_cas_miss_records_no_refusal`, `test_consumption_unreadable_selection_is_not_portable`, `test_move_rolls_back_when_its_portable_receipt_goes_stale`, `test_consumption_downgrades_unchanged_receipt_too`
- `tests/hermes_cli/test_kanban_source_published_identity.py`: `test_published_attach_captures_identity_before_rpc`, `test_published_attach_refuses_manifest_repository_mismatch`, `test_protocol2_attach_without_manifest_is_typed_refusal`, `test_v2_unchanged_refuses_changed_or_missing_identity`, `test_v2_new_commit_identity_refusal_precedes_push`, `test_artifact_upgrade_persists_identity_for_next_ending`
- `tests/hermes_cli/test_kanban_source_published_ownership.py`: `test_published_source_refuses_another_initial_owner`, `test_published_observation_serializes_with_initial_and_reserves_before_rpc`, `test_published_attach_rechecks_competitor_after_identity_proof`, `test_return_home_does_not_fast_forward_another_tasks_source`
- `tests/hermes_cli/test_kanban_source_registration.py`: `test_initial_records_identity_before_protocol2_rpc`, `test_unanswered_initial_replays_durable_observation_on_new_connection`, `test_successor_uses_grant_predecessor_after_attached_uid_is_null`, `test_unknown_or_foreign_identity_fails_closed`, `test_initial_abort_never_claims_pre_spawn`, `test_exact_identity_must_match_durable_record`, `test_initial_preconditions_refuse_without_rpc`
- `tests/hermes_cli/test_kanban_source_registration_races.py`: `test_competing_observation_is_not_overwritten`, `test_lease_changed_during_git_observation_never_records`, `test_registration_uses_assigned_project_branch`
- `tests/hermes_cli/test_kanban_source_resolver.py`: modified only
- `tests/hermes_cli/test_kanban_source_scratch_move.py`: `test_running_scratch_source_move_publishes`, `test_running_scratch_move_publish_refusal_keeps_run`, `test_plain_scratch_card_moves_without_source`, `test_parked_scratch_source_card_needs_portable_custody`, `test_parked_scratch_portable_custody_moves_elsewhere`, `test_parked_scratch_holder_checks_published_external_worktree`, `test_parked_scratch_holder_without_journal_fails_closed`, `test_parked_scratch_holder_after_unchanged_ending_checks_its_own_run`, `test_parked_scratch_holder_ignores_runs_attached_at_another_manifest`, `test_reclaim_refuses_scratch_premanifest_custody`, `test_reclaim_plain_scratch_unaffected`
- `tests/hermes_cli/test_kanban_workspace_source.py`: modified only
- `tests/tools/test_kanban_source_completion.py`: `test_completion_stages_only_bound_owner_before_receipt`, `test_ending_distinguishes_absent_from_empty_selection`
- `tests/tools/test_kanban_source_heartbeat.py`: `test_heartbeat_registers_initial_source_only_for_bound_owner`, `test_heartbeat_without_source_fields_is_unchanged`, `test_heartbeat_artifacts_need_an_attached_run`, `test_heartbeat_reselects_artifacts_after_registration`, `test_register_initial_refuses_a_caller_run_that_is_not_current`
- `tests/tools/test_kanban_source_surface_edges.py`: `test_heartbeat_returns_registered_external_path`, `test_completion_without_artifact_selection_preserves_metadata`
- `tests/tools/test_kanban_source_v1_worktree_artifacts.py`: `test_v1_worktree_completion_keeps_legacy_artifacts`, `test_v1_worktree_heartbeat_selection_refused`, `test_v2_run_still_stages_selection`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Inert until the protocol advertisement flips from 1 to 2; do not flip it as part of this mirror.
- Ownership checks must be journal-aware and inside the writer lock.
- 4688 is a large squashed commit; later patches in the group fix review findings against it.
