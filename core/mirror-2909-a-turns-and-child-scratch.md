# Answered-turn compression, failure outcomes and child scratch isolation

Patches: **2746, 2749, 2771**.

Related context: [session-outcome.md](session-outcome.md), [kanban-delegate-child-terminal-isolation.md](kanban-delegate-child-terminal-isolation.md).

## Behavior

Compression during a later notice turn no longer restates an already answered user request as still in flight. A chat ending at a failed-turn boundary reads `unresolved{turn_failed}`, rather than asking a prose judge to classify the failure copy. Non-git-isolated local subagents get distinct private scratch directories and a warning that the inherited working directory is shared.

## How it works

2746 adds `context_compressor_turns.turn_ended_after`: a text-bearing non-summary assistant reply with no tool calls followed by a user-role `kanban_notice`, `internal_notification`, `process_complete` or `async_delegation_complete` ends the earlier request. Auto-continue, steer, scaffolding, unknown kinds, whitespace/stub rows and outstanding tool rounds do not. 2771 recognizes typed `failed_turn` or exact legacy failure text in the assistant tail, excluding the interrupted-automation notice and prose merely quoting failure text. A subsequent real reply clears the tail failure. 2749 gives `_ChildRun` a `private_scratch`, allocated with `mkdtemp` under `TMPDIR` for local non-worktree children. The ephemeral prompt names it and the parent's recorded cwd (or concrete workspace hint); relative brief paths keep the shared cwd. Git-isolated children and remote/container backends do not receive host-only scratch paths.

## Code

- `agent/context_compressor.py`
- `agent/context_compressor_turns.py`
- `hermes_state_outcome.py`
- `tools/delegate_tool_child_run.py`
- `tools/delegate_tool_progress.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_inflight_turn_ended_by_notice.py`: `test_answered_request_is_not_inflight_during_a_notice_turn`, `test_auto_continue_after_reply_keeps_request_inflight`, `test_scaffolding_row_after_reply_keeps_request_inflight`, `test_notice_without_a_completed_reply_does_not_end_the_turn`, `test_text_bearing_tool_call_round_is_not_a_completed_reply`, `test_summary_carrier_is_not_a_completed_reply`, `test_empty_assistant_stub_after_reply_does_not_reopen_the_turn`, `test_only_user_role_notice_rows_start_a_turn`, `test_whitespace_only_assistant_row_is_not_a_completed_reply`, `test_non_dict_rows_are_skipped`, `test_unknown_display_kind_after_reply_is_not_turn_starting`, `test_merged_replay_carrier_is_not_inflight_after_its_turn_ended`, `test_observed_0421_shape_is_not_inflight`, `test_observed_shape_before_the_answer_was_inflight`, `test_compress_mid_notice_turn_does_not_restate_the_answered_request`, `test_compress_mid_continued_turn_still_restates_the_request`.
- `tests/hermes_state/test_session_outcome.py`: `test_a_notice_triggered_failed_turn_with_no_finish_reason_is_turn_failed_not_judged`, `test_an_untyped_legacy_failed_turn_boundary_tail_is_turn_failed`, `test_a_reply_quoting_the_failed_turn_notice_is_still_judged`, `test_an_interrupted_automation_boundary_tail_is_not_turn_failed`, `test_a_real_reply_after_the_failure_clears_it`.
- `tests/tools/test_delegate_private_scratch.py`: `test_non_git_children_get_distinct_private_scratch_named_in_prompt`, `test_unrecorded_parent_cwd_falls_back_to_workspace_hint`, `test_block_without_known_cwd_still_says_shared`, `test_git_isolated_child_gets_no_private_scratch_block`, `test_non_local_backend_gets_no_host_private_scratch`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

The scratch prompt is cooperative guidance, not an OS sandbox; it neither changes cwd nor permits deleting sibling files. It inherits normal scratch pruning rather than introducing immediate cleanup. Only the listed persisted user-role notice kinds end an answered turn; the same text in another role does not. Failed-turn inference tests use the store derivation path, not rendered UI. Remote scratch provisioning and live model compression were not exercised by this pass.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
