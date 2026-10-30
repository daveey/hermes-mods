# Kanban goal-loop exits, sweeps and remote notice acks

Patches:

- `patches/1176-fix-kanban-a-stale-settle-of-a-dead-remote-claim-is-.patch`
- `patches/1183-fix-kanban-a-goal-loop-whose-own-worker-parked-the-r.patch`
- `patches/1184-fix-kanban-sweeper-drops-archived-cards-notify-subs-.patch`
- `patches/1188-fix-kanban-a-goal-worker-that-parks-itself-or-never-.patch`
- `patches/1189-fix-kanban-a-woken-remote-notice-is-acked-when-its-r.patch`
- `patches/1190-feat-kanban-re-check-standing-human-asks-each-dispat.patch`
- `patches/1197-fix-kanban-stale-triage-sweep-releases-a-card-once-p.patch`

Stops goal-mode workers being booked as crashes when they park themselves or the owner is unreachable, and tightens the sweepers and remote-notice ledger.

## Behavior

- 1176: a stale settle of a dead holder's remote claim is recorded `delivered=SETTLED` with a `swept` row of reason `stale`, never `delivered`. `_record_swept` has no default reason.
- 1183: `goal_run_status` reports `scheduled` (not `superseded`) when the goal worker itself ended via `kanban_schedule` / `kanban_block(kind='time')`. Real ownership loss still reads `superseded`.
- 1184: the unloaded-chat sweeper removes notify subscriptions of archived cards that point at a chat no store holds; open cards keep theirs.
- 1188: an owner that never answered the claim makes the worker exit 69 (`KANBAN_OWNER_UNAVAILABLE_EXIT_CODE`); the dispatcher books `owner_unavailable` and requeues without spending the failure budget. Goal-loop stops on dispatcher-owned statuses stay exit 0; a broken loop (status read, judge transport, follow-up turn failure) now exits non-zero in managed, `-q` and `-Q` workers.
- 1189: a woken remote notice is acked only once its `kanban_notice` row is in the transcript, not at turn admission.
- 1190: each dispatcher tick re-checks blocked cards with a standing `human_request` and no live run; those the ask lint now refuses, or that name only done/archived cards on this board, move to agent triage with an `ask_rechecked` event. The relay pattern also matches "from an owner (Hermes) chat" style phrasings.
- 1197: the stale-triage sweep releases a card once per triage entry, at most 3 times per 24 h; past that the card stays with a `triage_sweep_capped` event and one board-supervisor card.

## How it works

- 1188: `kanban_owner_unavailable` falls back to `crashed` if the board shows `owner_admitted` for the run, or after 5 such runs in a row, so a permanently absent owner still reaches the breaker. A failed follow-up turn keeps its provider exit (75/78).
- 1189: `started()` runs `_settle_started`; otherwise the claim stays in flight with this process as holder. `_claim_notice` acks the holder's own claim once its key is present (sweep pass for unloaded chats, `collect()` for live ones). A holder that dies first goes through the dead-holder path and is re-offered (at-least-once).
- 1190: `sweep_standing_asks` runs in the same tick, once per block event; an ask re-filed unchanged after a recheck stands. `ask_rechecked` counts as agent triage in `kanban_wait_info`.
- 1197: a triage entry event (`gave_up`, `block_loop_detected`, `agent_block_escalated`, `supervisor_escalated`, `continuation_exhausted`, `blocked`, `status`) newer than the last `triage_swept` makes the card eligible. The supervisor card uses `kanban.orchestrator_profile` or default, with an idempotency key per card per day.

## Code

`tui_gateway/kanban_remote_notices.py`, `tui_gateway/kanban_unloaded.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_dead_worker.py`, `hermes_cli/kanban_db_dispatch_exit_code.py`, `hermes_cli/kanban_owner_unavailable.py` (new), `hermes_cli/kanban_worker_client.py`, `hermes_cli/goals.py`, `hermes_cli/cli_single_query.py`, `hermes_cli/gateway_client.py`, `gateway/session_kanban.py`, `hermes_cli/kanban_block_policy.py`, `hermes_cli/kanban_wait_info.py`, `hermes_cli/kanban_triage_sweep.py` (new).

## Tests

- 1176: `tests/tui_gateway/test_kanban_remote_notices.py::test_every_delivered_ack_has_a_transcript_row_after_a_stale_settle`.
- 1183: `tests/hermes_cli/test_kanban_review_lifecycle_complete.py::test_goal_run_status_reports_own_timed_park_not_superseded`.
- 1184: `tests/tui_gateway/test_kanban_unloaded_sweep.py` (`test_archived_card_sub_to_unknown_chat_is_removed`, `test_live_card_sub_to_unknown_chat_is_kept`).
- 1188: `test_kanban_goal_loop_clean_stop.py`, `test_kanban_owner_unavailable.py`, and the `-q`/`-Q` goal cases in `test_single_query_exit_contract.py` (all under `tests/hermes_cli/`).
- 1189: `tests/tui_gateway/test_kanban_remote_wake.py` (no ack without row, ack on next pass, live poller ack, redelivery after backend death).
- 1190: `tests/hermes_cli/test_kanban_ask_recheck.py` (relay lint, route gap, all-closed, foreign id, real ask, once per block event, live run, dispatch tick).
- 1197: `tests/hermes_cli/test_kanban_triage_sweep_reentry.py` (re-triage swept again, cap at 3/day with one supervisor card, orchestrator profile, old sweeps not counted, open human request and young entry untouched).
- Untested: a real process death in the 1189 window; 1184's behavior against a live multi-host setup.

## Applying

Apply the complete numbered series at the merge-base with `git am -3 --empty=keep`; entries are not independent (1188/1190/1197 touch `kanban_db_dispatch.py`; 1176/1189 touch `kanban_remote_notices.py`). See README.md. No config required; `kanban.orchestrator_profile` is optional for 1197.

## Gotchas

- Exit 69 is only for "never admitted"; an admitted-then-silent owner still counts as a crash.
- 1189 changes the ack timing: a notice may be redelivered after a crash between admission and row write.
- 1190 moves cards out of `blocked`; an unchanged re-filed ask is not escalated again.
- 1197 leaves a card in triage once capped; the supervisor card is its only producer.

## Plugin?

No, core change.
