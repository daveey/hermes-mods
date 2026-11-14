# Parked passive rows across update checkpoints

Mirror snapshot 4142; ordinals 4108–4113.

## Behavior

- While a gateway turn is parked behind an update checkpoint, rows written by lease-less ("passive") writers are refused with the existing retryable `SessionTurnLeaseLostError` ("busy") instead of being appended after the checkpointed tool suffix. Previously such a row voided the checkpoint proof and the parked turn was dropped on resume. Only the parked conversation is affected; other chats and the lease holder's own writes are not.
- Public appenders (`append_message`, `append_messages_batch`, chunked batches), Kanban notices, completion notes, delegation deliveries, legacy prompt admission and destructive user rewrites are all covered.
- Callers that cannot simply retry now queue the row on the durable transcript spool and land it after the turn resumes or settles: the session mirror, the bot-DM reply watcher, the gateway transcript store, shutdown spool recovery, the TUI stop notice, the TUI model-switch marker and the TUI failed-turn boundary. If no spool is available the refusal surfaces (the mirror then reports failure) rather than claiming delivery.
- A parked automation head whose proof was voided is now settled by the unattended-unknown path and the drain continues to the queued follower, instead of pausing the queue until the next owner boot. A head sent by a person still pauses.
- Every session pause is logged at WARNING, transient reasons included.
- Deferred rows follow compression: rows spooled against a compressed-away parent land on the single proven compression continuation, in spool order, under the owning profile's spool only.

## How it works

- 4108: `parked_update_turn` scans update-checkpoint receipts in `state_meta` for a `started` admission whose logical target, physical tip or physical chain covers the session; an unreadable or oversized receipt still fences its logical target. `refuse_parked_write` raises the busy error inside the writer transaction. `SessionAuthority`'s drain calls `settle_unattended_unknown` after `refuse_checkpoint` fences a head and `continue`s to the follower. `_pause` logs each pause.
- 4109: `_check_transcript_write_guards` gains `reject_parked_turn`; the public appenders pass it, so lease-less plain appends are fenced as well. The in-transaction batch writer used by owner settlement keeps its open default. `replace_display_kind_row` deactivates old rows of a display kind and appends the replacement in one transaction, so a refused model-switch pivot leaves the previous one active. `defer_row_for_parked_turn` puts a refused row on the spool; `replay_spooled_row` and `replay_deferred_rows` land it later, preserving model-switch replacement semantics. `drain_deferred_rows` runs after settlement (`settle_and_deliver`) for both the logical and physical session ids. Test helpers use a raw-SQL writer (`raw_append_message`) to corrupt proofs deliberately. `_pause` now logs every reason at WARNING.
- 4110: `_resolved_unknown` also drains deferred rows, so rows land before the queued follower when unknown work settles (resumed or not).
- 4111: the bot-DM reply watcher thread is started under `copy_context()` so the spool path stays in the sender's profile.
- 4112: `deferred_transcript_route` proves one compression-only continuation in the DB (same profile, single non-continuation-free child, bounded chain, no cycles) and discovers proven ancestors as spool sources. Branches, resets, delegates, tool children, profile changes and ambiguous children never redirect. Ordering and blocking are keyed by the destination conversation. The store drains under the session's owning-profile scope and may use the authority's DB.
- 4113: the settlement drain binds the profile through the async runtime scope so secret-source hydration runs off the owner loop; `deferred_transcript_route` imports its heavy modules only after a real DB is proven.

## Code

Source paths touched by this feature group (including regression fixtures):

- `gateway/mirror.py`
- `gateway/session_authority.py`
- `gateway/session_settlement_recovery.py`
- `gateway/session_transcript.py`
- `gateway/shutdown_flush.py`
- `hermes_state_detached_results.py`
- `hermes_state_messages.py`
- `hermes_state_update_pause.py`
- `tools/bot_mode_dm.py`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/methods_session_interrupt.py`
- `tui_gateway/server.py`
- `tests/gateway/test_parked_automation_fence_settles.py`
- `tests/gateway/test_parked_bot_reply_profile.py`
- `tests/gateway/test_parked_passive_row_delivery.py`
- `tests/gateway/test_update_pause_cache.py`
- `tests/gateway/test_update_pause_native_boot.py`
- `tests/gateway/test_update_pause_remaining_guards.py`
- `tests/hermes_state/raw_rows.py`
- `tests/hermes_state/test_parked_turn_passive_writes.py`
- `tests/hermes_state/test_update_checkpoint.py`
- `tests/tui_gateway/test_tui_gateway_server.py`

Mail entries in the ordered series:

- 4108: [`072ff2b67a80264f4db28503172b932634c4b353`](patches/4108-daveey-update-pause-passive-rows-wait-while-a-turn-i.patch)
- 4109: [`52bcc27e9e3b6b208b53c01d9b92c7eb6abfc891`](patches/4109-fix-state-defer-all-passive-rows-across-update-check.patch)
- 4110: [`22f9ac017d4206ba5506e1466390f9f27086f2d7`](patches/4110-fix-gateway-replay-parked-rows-when-unknown-work-set.patch)
- 4111: [`c0e3ffa942d5fb324e452b6ed86dd080cb715077`](patches/4111-fix-gateway-retain-profile-scope-for-deferred-bot-re.patch)
- 4112: [`1d827a121095786c040870d67021d43e6e6fe5f6`](patches/4112-fix-gateway-replay-parked-passive-rows-across-compre.patch)
- 4113: [`7e88d5f862601159a689ec6fbc91eddb6044ac33`](patches/4113-fix-gateway-keep-deferred-replay-hydration-off-loop-.patch)

## Tests

`tests/hermes_state/test_parked_turn_passive_writes.py` (4108, extended in 4109):
- `test_passive_write_defers_and_parked_turn_resumes_then_row_lands`: each passive path (Kanban notice append/record, completion note, delegation delivery, legacy prompt admit) raises the busy error, leaves messages and the checkpoint unchanged, and lands after resume.
- `test_destructive_user_rewrite_is_refused_while_parked`, `test_other_chats_and_unparked_turns_still_take_passive_rows`, `test_settled_admission_with_leftover_receipt_does_not_fence`, `test_live_turn_owner_write_is_not_refused`: scope limits of the fence.
- `test_receipt_physical_tip_is_fenced`, `test_unreadable_receipt_still_fences_its_logical_target`, `test_neighbouring_state_meta_keys_are_not_receipts`: receipt tip/chain, malformed receipts and key-range scan.
- 4109 additions: `test_plain_append_defers_then_lands_after_resume`, `test_plain_append_to_other_chat_is_unaffected`, `test_plain_append_does_not_change_live_lease_behaviour`, `test_owner_settlement_write_is_not_blocked_by_the_fence`, `test_replace_display_kind_row_is_atomic_when_refused`.

`tests/gateway/test_parked_automation_fence_settles.py` (4108, changed in 4109, extended in 4110):
- `test_notice_between_boot_recovery_and_resume_is_deferred_and_turn_resumes`: a Kanban notice in the recovery window is refused and the turn resumes.
- `test_drain_fenced_automation_head_settles_and_follower_runs_in_same_drain`: no pause, head settles interrupted, follower completes.
- `test_drain_fenced_persons_head_still_pauses_and_says_so`: person head stays `unknown`, follower `queued`, WARNING logged.
- `test_every_pause_is_logged` became `test_every_pause_is_logged_at_warning` (all four reasons at WARNING).
- `test_real_drain_delivers_spooled_row_before_follower` (4110): a spooled row is delivered exactly once before the follower, with and without a valid proof, and the spool is emptied.

`tests/gateway/test_parked_passive_row_delivery.py` (4109, extended in 4112):
- `test_mirror_defers_to_spool_then_lands_once`, `test_mirror_without_a_spool_reports_the_refusal_not_success`: gateway mirror path.
- `test_gateway_store_defers_without_counting_a_failure_then_drains`, `test_gateway_store_keeps_order_across_deferred_rows`: transcript store.
- `test_bot_dm_reply_is_consumed_then_deferred_and_lands_after_resume`: bot-DM reply.
- `test_shutdown_spool_replay_holds_back_while_parked_then_recovers`, `test_shutdown_pending_message_moves_to_the_transcript_spool_while_parked`: shutdown spool recovery.
- `test_tui_stop_notice_survives_the_parked_turn_and_is_not_written_twice`, `test_tui_model_switch_marker_is_atomic_when_the_turn_is_parked`: TUI helpers called directly.
- `test_settlement_drains_deferred_rows_for_both_session_ids`.
- 4112: `test_deferred_parent_rows_follow_compression_after_resume` (settlement/store/boot/append entry points), `test_deferred_rows_never_redirect_across_unproven_edges` (branch, reset, delegate, tool, non-compression, profile, ambiguous), `test_compression_replay_failure_holds_later_tip_rows_in_order`, `test_compression_replay_uses_only_the_owning_profile_spool`.

`tests/gateway/test_parked_bot_reply_profile.py` (4111): `test_bot_reply_spools_and_replays_only_in_owning_profile` asserts the reply spools in the sender's profile, not the launch profile, and replays once after resume.

4109 also changed `tests/gateway/test_update_pause_cache.py`, `test_update_pause_native_boot.py`, `test_update_pause_remaining_guards.py` and `tests/hermes_state/test_update_checkpoint.py` to use the raw-SQL writer in `tests/hermes_state/raw_rows.py` when corrupting a proof, and `tests/tui_gateway/test_tui_gateway_server.py` to stub `replace_display_kind_row`. 4113 adds no tests.

Untested paths: the TUI failed-turn boundary change (`tui_gateway/methods_prompt.py`) has no test in these patches; the settlement-time profile scope change in 4113 has no new test; the REST, `execute_code` and terminal entry paths are not exercised by any listed test.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The 4108 message lists writers left unfenced at that point; 4109 closes the mirror, model-switch marker, stop notice, shutdown spool replay and TUI paths, but other raw-SQL or in-transaction writers are deliberately not fenced (owner settlement keeps its open default).
- A deferred row lands only after the turn resumes or settles; if it never does, the row stays on the spool.
- Unreadable receipts fence the logical target only, not any physical tip.
- Deferred rows redirect only across a single, same-profile, compression-only continuation; anything ambiguous keeps custody on the spool.
- A noncompression-ended source still accepts its own rows but never redirects them to a child.
- This document makes no deployment claim.

## Plugin?

No. The fence sits inside the state-layer write transaction and the gateway drain/settlement path, which a plugin cannot intercept.
