# Kanban parks: published park data and required wake reason

Mirror snapshot 5406; ordinals 5377, 5378, 5379, 5380, 5381, 5382, 5383.

Entries:

- **5377**: fix(kanban): publish the park, honour SCHEDULED_UNTIL offsets, require a wake reason
- **5378**: fix(kanban): review of the wake-reason change: guidance, blocker and clock waits, goal parks
- **5379**: fix(kanban): placement requeue loops still escalate on the third identical park
- **5380**: fix(kanban): a placement requeue is never refused for quoted text, nor run here when it fails
- **5381**: fix(kanban): review nits on the placement hold: a quoted SCHEDULED_UNTIL never makes it a timed park; test the hold-failed exit
- **5382**: chore(kanban): clear the code-health blockers of the wake-reason branch
- **5383**: test(kanban): three more tests park a clock with its wake reason

## Behavior

- **5377**: owner records now publish the park (awaiting, wake_check, wake time, reason); remote projections prefer it; `SCHEDULED_UNTIL` accepts Z and numeric offsets; a timed park naming no wake reason is refused `no_wake_reason`.
- **5378**: guidance and schemas teach `awaiting`; a shared blocker is a wake reason; declared clock waits need a `why` and never escalate unless they carry a wake probe; goal-loop blocks declare a clock.
- **5379/5380**: placement requeue loops still escalate on the third identical park; system clock re-parks skip agent-prose checks; a failed forward/requeue never runs the worker locally for sent or qualified cards.
- **5381**: quoted `SCHEDULED_UNTIL` text is defused.
- **5382/5383**: health refactors and tests declare clock reasons.

## How it works

- `kanban_awaiting.py`, `kanban_wait_replica.py`, `kanban_wait_info.py`, `kanban_timed_park.py`, `kanban_placement_forward.py`, `kanban_chat_links.py`, `kanban_session_activity*.py`, prompt/goal/done-tail guidance, docs.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_placement_forward.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_timed_park.py`
- `hermes_cli/kanban_wait_info.py`
- `hermes_cli/kanban_wait_replica.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `scripts/gen_source_receipt_fixture.py`
- `tools/kanban_tools_schemas.py`
- `agent/prompt_builder.py`
- `gateway/session_kanban.py`
- `hermes_cli/cli_single_query.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_done_tail.py`
- `hermes_cli/kanban_goal_recovery.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_create_args.py`
- `tools/kanban_tools_graph.py`
- `tools/terminal_tool_background.py`
- `website/docs/user-guide/features/kanban.md`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_session_activity_remote_wait.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/gateway/test_kanban_auto_decompose_min_age.py`
- `tests/hermes_cli/test_kanban_active_pr_awaited_review.py`
- `tests/hermes_cli/test_kanban_awaiting.py`
- `tests/hermes_cli/test_kanban_block_kinds.py`
- `tests/hermes_cli/test_kanban_blocker_park_wake.py`
- `tests/hermes_cli/test_kanban_blocker_remote_probe_park.py`
- `tests/hermes_cli/test_kanban_chat_links.py`
- `tests/hermes_cli/test_kanban_cli_outside_park_live_claim.py`
- `tests/hermes_cli/test_kanban_continuation.py`
- `tests/hermes_cli/test_kanban_db.py`
- `tests/hermes_cli/test_kanban_g2e_reclaim_cost.py`
- `tests/hermes_cli/test_kanban_goal_loop_clean_stop.py`
- `tests/hermes_cli/test_kanban_move_guard_review_nits.py`
- `tests/hermes_cli/test_kanban_operator_call.py`
- `tests/hermes_cli/test_kanban_origin_ledger.py`
- `tests/hermes_cli/test_kanban_park_done_producer.py`
- `tests/hermes_cli/test_kanban_park_invariants.py`
- `tests/hermes_cli/test_kanban_placement.py`
- `tests/hermes_cli/test_kanban_pool_queue.py`
- `tests/hermes_cli/test_kanban_pr_comment_provenance.py`
- `tests/hermes_cli/test_kanban_refused_op_comments.py`
- `tests/hermes_cli/test_kanban_request_lifecycle.py`
- `tests/hermes_cli/test_kanban_review_lifecycle.py`
- `tests/hermes_cli/test_kanban_review_lifecycle_complete.py`
- `tests/hermes_cli/test_kanban_run_end_lease.py`
- `tests/hermes_cli/test_kanban_shared_blocker_livelock.py`
- `tests/hermes_cli/test_kanban_shared_blockers.py`
- `tests/hermes_cli/test_kanban_show_wake.py`
- `tests/hermes_cli/test_kanban_soak_wait.py`
- `tests/hermes_cli/test_kanban_source_publish.py`
- `tests/hermes_cli/test_kanban_source_receipt.py`
- `tests/hermes_cli/test_kanban_source_receipt_sites.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_supervisor_requests.py`
- `tests/hermes_cli/test_kanban_sweep_gate.py`
- `tests/hermes_cli/test_kanban_timed_park.py`
- `tests/hermes_cli/test_kanban_triage_park.py`
- `tests/hermes_cli/test_kanban_wait_info.py`
- `tests/hermes_cli/test_kanban_wake_check.py`
- `tests/hermes_cli/test_kanban_wake_episode_metadata.py`
- `tests/hermes_cli/test_kanban_wake_episode_probeless.py`
- `tests/hermes_cli/test_kanban_wake_episode_triage_sync.py`
- `tests/hermes_cli/test_kanban_wake_unproven.py`
- `tests/plugins/test_kanban_dashboard_plugin.py`
- `tests/plugins/test_kanban_workflow_matrix.py`
- `tests/tools/test_kanban_outside_park_live_claim.py`
- `tests/tools/test_kanban_post_run_calls.py`
- `tests/tools/test_kanban_tools.py`
- `tests/tui_gateway/test_session_outcome_notices.py`
- `tests/hermes_cli/test_kanban_goal_recovery.py`
- `tests/hermes_cli/test_kanban_pr_replica_comment_uid.py`
- `tests/hermes_cli/test_kanban_review_model_cycles.py`
- `tests/tools/test_kanban_watch_park.py`
- `tests/hermes_cli/test_kanban_db_schedule.py`
- `tests/hermes_cli/test_kanban_placement_recover.py`
- `tests/hermes_cli/test_kanban_continuation_exhaustion.py`
- `tests/tools/test_kanban_done_tail.py`
- `tests/tools/test_kanban_source_ending_initial.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Bare clock parks are now refused; callers must declare a reason.

## Why core instead of a plugin

Park gating and publication are core kanban.
