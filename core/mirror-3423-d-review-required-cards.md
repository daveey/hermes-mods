# Explicit card review requirements and review receipts

Patches: **3125, 3126, 3127, 3128, 3169, 3173, 3174**.

## Behavior
- A card can carry a `review_required` flag (CLI create/edit flag, tool create argument, synced column). Completion is gated: the implementer cannot approve; a reviewer can; rework needs another review.
- Workers cannot disable the flag, and a worker-subprocess CLI cannot approve a pending review. Refusals name the operator rule ("an operator, outside a kanban worker run") and refuse before the goal judge.
- Home review continuation receipts are carried to replicas (feed and pickup, remapped run UID), and portable receipts are revoked on contradictory evidence or lifecycle retraction.

## How it works
- 3125 adds the new `kanban_review_required` module with DB migration (default false), parser flags, sync declaration/capture, carried options, remote args and the completion gate; 3126 keeps the guards inside worker subprocesses.
- 3127 and 3128 are line-count refactors of `kanban.py` (routing refusals via `receipt_refusal`, folding the on/off edit conversion) with no behavior change.
- 3169 rewords refusals, documents the rule in the kanban user guide and checks review-required before the goal judge.
- 3173 carries home review continuation receipts (observed PR hold, resume, review release) to replicas; 3174 fences portable receipts on origin and revokes on same-result contradiction or lifecycle retraction.

## Code
- `hermes_cli/kanban_review_required.py`, `hermes_cli/kanban_review_release.py`
- `hermes_cli/kanban.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_operator.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_parser.py`, `hermes_cli/kanban_output.py`, `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_placement_forward.py`, `hermes_cli/kanban_cli_metadata.py`
- `hermes_cli/kanban_operator_resume.py`, `hermes_cli/kanban_observed_pr_hold.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_worker_node.py`
- `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`, `website/docs/user-guide/features/kanban.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_review_required_plumbing.py`: `test_create_parser_flag_default_and_set`, `test_edit_parser_accepts_on_off_and_rejects_other`, `test_cli_create_handler_persists_flag`, `test_cli_edit_handler_enables_then_disables_for_operator`, `test_cli_edit_review_required_alone_is_a_valid_edit`, `test_cli_edit_worker_cannot_disable`, `test_operator_edit_worker_cannot_disable_but_can_enable`, `test_operator_edit_non_worker_can_disable`, `test_migration_adds_column_default_false_to_legacy_db`, `test_new_task_default_is_false`, `test_sync_declares_and_captures_review_required`, `test_carried_options_keeps_true_and_drops_default`, `test_legacy_clients_without_option_support_cannot_carry_flag`, `test_remote_args_preserves_flag`, `test_disable_refusal_names_operator_outside_worker_run`.
- `tests/tools/test_kanban_review_required.py`: `test_tool_create_flag_true_and_default_false`, `test_tool_schema_declares_boolean_flag`, `test_completion_gate`, `test_reviewer_completion_allowed`, `test_operator_can_approve_review_but_implementer_cannot`, `test_rework_requires_another_review`, `test_flag_rechecked_after_preflight`, `test_internal_forwarded_completion_receipt_can_settle_stub`, `test_pending_review_worker_cli_cannot_approve`.
- `tests/tools/test_kanban_review_required_wording.py`: `test_worker_approving_pending_review_without_claim_names_operator`, `test_operator_cli_complete_outside_review_names_edit_escape_hatch`, `test_worker_refusal_does_not_offer_operator_escape_hatch`, `test_dashboard_done_outside_review_names_edit_escape_hatch`, `test_review_required_refuses_before_goal_judge`.
- `tests/hermes_cli/test_kanban_operator_resume_observed.py`: `test_current_observed_hold_allows_home_resume`, `test_observation_must_be_in_current_ready_episode`.
- `tests/hermes_cli/test_kanban_operator_resume_worker_copy.py`: `test_home_resume_accepts_worker_only_observation`.
- `tests/hermes_cli/test_kanban_review_release.py`: `test_home_promotion_releases_partial_replica`, `test_invalid_receipt_does_not_release_unknown_replica`, `test_new_promotion_does_not_rearm_old_receipt`, `test_same_result_contradiction_revokes_receipt`, `test_lifecycle_retraction_revokes_receipt`.
- `tests/hermes_cli/test_kanban_review_release_transport.py`: `test_remote_review_change_during_precompute_drops_stamp`, `test_home_review_receipt_crosses_feed_and_pickup`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3173's message describes itself as a checkpoint implementation; 3174 adds the origin and contradiction fences.
- Permission rules were unchanged by 3169; only wording and ordering changed.
