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


## Follow-up 1920: revision retries and creator-worker repins

Patch **1920** (`3d1c392baf80`).

### Behavior

A revision conflict triggers a Board pull and eligibility recheck before retrying, with at most three CAS attempts. A creator worker can use the `kanban_move` tool to repin its own task's unstarted worker-first card to the origin, without ending the creator's run.

### How it works

`repin_to_origin` retries only `revision_conflict`, not other refusals. `kanban_creator_repin` checks creation ownership and worker-first eligibility before calling the existing queued-move path. Running cards, unrelated cards and cards without the policy are refused.

### Code

`hermes_cli/kanban_worker_repin.py`, `hermes_cli/kanban_creator_repin.py`, `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`.

### Tests

The 1923 mirror pass ran `tests/hermes_cli/test_kanban_worker_repin.py` (33 passed) and `tests/tools/test_kanban_creator_repin.py` (4 passed). The former covers retry-after-pull and changed eligibility; the latter calls the tool entry and covers creator success and the three scope refusals. A live Board network transaction was not rerun.

### Applying

Use the full README replay. Existing worker-first placement modules are prerequisites.

### Gotchas

This grants an explicit origin repin, not arbitrary movement of another card. Transport failures still report an unknown outcome rather than claiming no mutation.

## Follow-up 1886, 1887, 1894, 1897, 1913: worker-first repins from kanban_move and repin notices

Patches: **1886** (`7a38123856`, `fix(kanban): origin applies a worker's kanban_move of a worker-first card as a Board repin; back off on not_claimable [daveey]`), **1887** (`aef5b17d58`, `fix(kanban): kanban_move --reclaim of a worker-first card is a repin request too; tolerate a local record failure after a Board repin; prune expired not_claimable backoffs [daveey]`), **1894** (`73ee883f89`, `[daveey] worker-placement: a not_claimable reserve lost to the reserved worker's claim is a pickup, not a refusal`), **1897** (`cccf2477fa`, `[daveey] worker-placement: a no_eligible_worker card tells its owner the repin command once per revision`), **1913** (`7fd3c049e8`, `[daveey] worker-placement: repin notice also fires for the overflow-off capability_mismatch/mixed rollup over the whole pool (#822)`). Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

### Behavior

- The origin applies a worker's `kanban_move` of a worker-first card as a Board repin and backs off on `not_claimable` (1886); `kanban_move --reclaim` is a repin request too, and a local record failure after a Board repin does not abort the batch (1887).
- A `not_claimable` reserve lost to the reserved worker's own claim is recorded as a pickup, not a refusal (1894).
- A `no_eligible_worker` card tells its owner the exact repin command once per card revision via a comment and a `worker_repin_notice` event (1897), and the notice also fires for the overflow-off `capability_mismatch`/mixed rollup (1913).

### How it works

`hermes_cli/kanban_move.py`, `hermes_cli/kanban_worker_placement.py`, `hermes_cli/kanban_queue_reason.py` and `hermes_cli/kanban_repin_notice.py`; gateway/TUI notifiers deliver the notice.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1886: Seen 2026-11-03 on t_01a1a13d757b23af (hub, worker-first-v1): worker-1 claimed it, the run moved it to machine:hub (kanban_move) twice, and the Board reserved it for worker-1 again each time, followed by "409 not_claimable", until the card self-blocked asking David to fix placement.
- 1887: Review LOW-2/3/6 on #797. RED on the previous head: test_a_reclaimed_move_of_a_policy_card_is_a_request_too, test_a_local_record_failure_after_a_board_repin_does_not_abort_the_batch.
- 1894: Probe t_01a19fd990770095 (hub, 11-03): the Board reserved the card for worker-1, worker-1 claimed it at 05:21:51Z, and the hub's next reserve from a copy without that lease got 409 not_claimable at 05:21:52Z. worker-placement recorded board_refused and commented "Board placement request refused" although the card ran fine on worker-1 (the local `claimed` event arrived after the refusal was written).
- 1897: Ruling 9 of the overflow plan keeps a card no pool worker can run queued and fenced with no automatic return to origin. It used to wait silently. Now the origin posts one comment (missing labels + the exact 'hermes kanban move <id> --runs-on machine:<origin>' command) and a worker_repin_notice event its origin chat receives (gateway + TUI notifiers), once per card revision (required labels + run count, not sync_version, which every reserve bumps). It runs on every reserve reply, so cards already queued get it on the next pass. The stuck summary lists them as 'waiting for a manual repin' and...
- 1913: The Board emits no_eligible_worker only on its overflow path; with worker_overflow off a card no pool worker is attested for queues as capability_mismatch, so #810's notice never fired live (fixture t_01a1a46cbd08b275).

### Code

`hermes_cli/kanban_move.py`, `hermes_cli/kanban_worker_placement.py`, `hermes_cli/kanban_queue_reason.py`, `docs/worker-first-v1.md`, `gateway/kanban_watchers_notifier.py`, `hermes_cli/kanban_db_dispatch_preclaim.py`, `hermes_cli/kanban_db_dispatch_stuck.py`, `hermes_cli/kanban_repin_notice.py`, `tui_gateway/session_notifications.py`.

### Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_queue_reason.py`: `test_mixed_wording_is_true_for_one_or_many_candidates`, `test_no_eligible_worker_thread_is_the_single_repin_notice`
- `tests/hermes_cli/test_kanban_repin_notice.py`: `test_one_comment_and_one_delivery_per_revision_across_passes`, `test_notice_text_reaches_gateway_and_tui_chats`, `test_card_without_origin_chat_still_gets_the_comment`, `test_labels_fall_back_to_the_cards_required_labels`, `test_new_card_revision_gets_a_new_notice`, `test_no_repin_call_is_made_and_the_fence_stays`, `test_already_queued_card_gets_its_notice_on_the_next_pass`, `test_other_queue_codes_post_no_notice`, `test_stuck_summary_names_the_card_and_does_not_count_it_as_a_stall`, `test_stuck_summary_keeps_other_refusals_as_lease_holds`, `test_capability_mismatch_over_the_whole_pool_gets_one_notice_and_no_second_comment`, `test_mixed_rollup_where_every_pool_worker_lacks_a_capability_gets_the_notice` (+3 more)
- `tests/hermes_cli/test_kanban_worker_move_request.py`: `test_a_worker_move_back_to_the_origin_is_repinned_there_and_the_origin_runs_it`, `test_a_pulled_move_request_is_repinned_at_the_acknowledged_revision`, `test_an_applied_repin_is_not_replayed`, `test_not_claimable_backs_off_until_the_revision_changes`, `test_not_claimable_backoff_expires`, `test_other_refusals_keep_retrying`, `test_a_reclaimed_move_of_a_policy_card_is_a_request_too`, `test_a_local_record_failure_after_a_board_repin_does_not_abort_the_batch`
- `tests/hermes_cli/test_kanban_worker_placement_race.py`: `test_not_claimable_after_the_reserved_worker_claimed_is_recorded_as_picked_up`, `test_not_claimable_without_a_claim_by_the_reserved_machine_is_still_refused`, `test_not_claimable_while_another_machine_holds_the_lease_is_still_refused`, `test_not_claimable_with_only_the_reserved_workers_claimed_event_is_a_pickup`, `test_a_later_card_in_the_tick_is_judged_against_the_copy_its_reserve_was_made_from`, `test_a_failing_pull_keeps_the_refusal_and_the_batch`, `test_not_claimable_with_no_prior_reservation_is_refused_without_an_extra_pull`
