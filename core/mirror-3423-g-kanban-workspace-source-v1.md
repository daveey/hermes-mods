# Kanban workspace_source v1 (inert core PR-1)

Patches: **3299, 3300–3305**.

## Behavior
- Kanban worker nodes gain the core half of "workspace_source": a card can carry a strict v1 manifest naming a repo branch and sha, and the worker materializes exactly that branch/sha into a proven local clone before the run starts.
- The protocol ships inert: `SOURCE_PROTOCOL_COMPLETE=False`, so `enabled: true` in config only warns and is treated as false.
- Refusals are typed (`WorkspaceSourceRefusal` with a code and a scope). A host-scope refusal releases the claim as infrastructure (no circuit-breaker charge) and remembers a skip; a card-scope refusal blocks the card as needs_input.
- A grant that goes unused is released without the worker retaining custody.
- Every voluntary run ending and escalation now carries a source receipt in the same transaction as the status change (see How it works).
- An old Board that rejects several virtual fields at once gets a strip-and-resend with no omission.

## How it works
- 3299 trims `kanban_db.py` to fit its health line budget (R1 spike follow-up).
- 3300 wraps every voluntary ending and the escalate path in `with_source_receipt`, adds an AST guard that enumerates status-leaving SQL sites (including format/f-string SQL), makes `install_triggers` ensure the pending-receipt table, extends the multi-field strip+resend path to the fenced workspace_kind/branch_name fields, and renames the spike test to `test_kanban_source_receipt.py`.
- 3301 adds the manifest/custody module, the clone proof module (repo root, GitHub origin, no pushurl or url rewrite, bounded `ls-remote`, cached repo id, attestation POST from the sync round, 404 meaning the Board lacks the route), the resolver (holder reuse/ahead/diverged/no-manifest paths; materialize via a private ref namespace with no reset or force; return-home fast-forward), R4b routing on the grant snapshot before any workspace_kind branch, pull-only `workspace_source`/`workspace_custody` columns, a claim-gate skip memo, a worker-node pickup skip, config defaults and user docs.
- 3302 adds BLE001 annotations, utf-8 decoding of git output, and resolves the live checkout through `project_root_str`.
- 3303 pins the Board row-field list to the merged Board change.
- 3304 releases unused source grants without custody, batches pickup proofs, adds `kanban_source_refusal_block.py` so a primary refusal returns the unused grant without a competing status, and ignores a lost release or a newer run.
- 3305 follows up on refusal review: orphaned follow-ups expire but wait for separate feed pages, a follow-up failure does not poison a committed feed, legacy follow-up schemas get full orphan retention, and subprocess calls are guarded.

## Code
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_board_row_fields.json`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_db_workspace.py`
- `hermes_cli/kanban_run_finalizer.py`
- `hermes_cli/kanban_source_clones.py`
- `hermes_cli/kanban_source_dispatch.py`
- `hermes_cli/kanban_source_receipt.py`
- `hermes_cli/kanban_source_refusal_block.py`
- `hermes_cli/kanban_source_resolver.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_compat.py`
- `hermes_cli/kanban_sync_hub_routes.py`
- `hermes_cli/kanban_timed_park.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/kanban_workspace_source.py`
- `hermes_cli/kanban_workspace_source_config.py`
- `scripts/gen_source_receipt_fixture.py`
- `website/docs/user-guide/features/kanban.md`
- `website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/features/kanban.md`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_receipt_sites.py`: `test_receipt_rides_status_leaving_op`, `test_request_changes_receipt_rides_status_leaving_op`, `test_escalate_reuses_enclosing_receipt`, `test_close_dead_run_wrapped`, `test_refused_ending_leaves_no_pending_receipt`, `test_install_triggers_ensures_pending_table`, `test_old_board_multi_field_refusal_strips_virtual`, `test_status_leaving_sites_enumerated`, `test_guard_reads_format_and_fstring_sql`, `test_guard_rejects_unrelated_wrapper`, `test_receipt_documents_pr1_carrier_not_spike_stub`.
- `tests/hermes_cli/test_kanban_source_resolver.py`: `test_materialize_exact_branch_and_sha`, `test_stale_tracking_ref_is_never_read`, `test_return_home_fast_forwards_existing_worktree`, `test_local_branch_behind_is_moved_not_forced`, `test_holder_ahead_of_manifest_reuses_as_is`, `test_holder_diverged_refused`, `test_holder_no_manifest_reuses_local_worktree`, `test_holder_no_manifest_missing_blocks`, `test_holder_copy_gone_but_published_materializes`, `test_remote_drift_is_card_scope`, `test_remote_branch_missing_is_card_scope`, `test_manifest_invalid_is_card_scope`, `test_origin_rewrite_refused`, `test_wrong_repo_clone_refused`, `test_renamed_repo_same_id_passes`, `test_live_checkout_refused_as_clone`, `test_occupied_path_refused`, `test_destination_claimed_by_other_task_refused`, `test_dirty_destination_refused`, `test_diverged_local_branch_refused`, `test_materialize_timeout_is_host_scope`, `test_non_holder_with_retained_custody_refused`, `test_disabled_host_refuses`, `test_clone_id_cached_per_home_for_6h`, `test_attestation_body_and_old_board_404`, `test_attest_heartbeat_inert_when_disabled`, `test_attest_if_due_from_sync_round`, `test_host_refusal_releases_without_failure_and_memoizes`, `test_card_refusal_blocks_needs_input`, `test_other_exceptions_left_to_legacy_path`, `test_materialize_timeout_uses_grant_revision_then_defers_next_claim`.
- `tests/hermes_cli/test_kanban_workspace_source.py`: `test_manifest_v1_parses`, `test_manifest_strict_schema_refuses`, `test_manifest_size_cap`, `test_enabled_refused_until_protocol_complete`, `test_config_defaults_and_derived_timeouts`, `test_snapshot_keys_absent_is_old_board`, `test_snapshot_present_null_is_no_custody`, `test_grant_snapshot_threads_from_lease_response`, `test_pull_only_columns_applied_never_captured`, `test_row_fields_pin_accepts_virtual_receipt`, `test_edit_kind_refused_on_custody_card`, `test_fenced_field_refusal_strips_and_resends_without_omission`, `test_custody_card_routes_to_source_resolver_regardless_of_kind`, `test_no_source_snapshot_takes_legacy_path`, `test_kind_flip_does_not_readmit_creator`, `test_dispatch_routes_before_kind_branch`, `test_pickup_skip_disabled_is_inert`, `test_pickup_skip`, `test_worker_node_pickup_skips_unportable_source_card`, `test_sync_round_attests_sources`, `test_host_refusal_memo_skips_revision_until_republish`, `test_refusal_codes_scoped`, `test_pickup_proofs_are_batched_and_settings_readonly`, `test_primary_refusal_returns_unused_grant_without_competing_status`, `test_refusal_block_ignores_lost_release_or_newer_run`, `test_followup_failure_does_not_poison_committed_feed`, `test_orphan_followups_expire_but_wait_for_separate_feed_pages`, `test_legacy_followup_schema_gets_full_orphan_retention`, `test_pickup_batch_import_precedes_worker_definitions`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The feature is intentionally inert until the protocol-complete flag flips in a later patch.
- Behaviors against a real Board are untested here; coverage uses fake boards and local git fixtures.
