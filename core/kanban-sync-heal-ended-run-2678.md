# Owner sync repair never reopens a card over its ended run

Patches: **2596, 2625, 2710–2712**.

## Behavior

- On a card owner's sync round, the divergence repair no longer re-sends a stale live copy of a card to the Board. Before, a live row that still held older task fields at the replica's version (for example the pre-completion fields after a pool worker finished the card) was read as an uncaptured local write and sent as `status=running`, no completion time, and the ended run as the current run. The owner's newer clock then won every field on the Board and the card read `running` for good, with no claim, no lease and an ended run.
- Now, when the live status differs and the live card's current run has already ended in either copy (or the replica is `done`/`archived` on the same run while the live card is not terminal), the live copy is treated as stale: the replica's row is applied to the live file as a feed apply (nothing captured, nothing sent). A live `claim_lock` (a local worker holding the card) is never treated as stale by the terminal-replica rule (2625).
- A second repair, run in primary-mode rounds after the existing clobbered-completion heal, moves a card that reads `running` with no `claim_lock`, no lease holder and an ended current run to where that run left it:
  - run ended `completed` and the latest `completed` event names that run: `done`;
  - run ended `blocked`, `review_requested` or `scheduled` and the park's own event is the card's last lifecycle move: back to `blocked`, `review` or `scheduled` (2625; so a card that was waiting is not re-dispatched);
  - anything else, including a dependency block or a later unblock, wake or reopen: `todo` (the promoter makes it `ready` once parents are done).
  The write goes through the capture triggers, so the same round's push corrects the Board and a `status_healed` event is recorded.
- The heal runs once per run: if a `status_healed` event already exists for the run, the card is skipped and a warning is logged (2625 adds the warning), so a heal the Board outvotes never loops. A held claim or lease keeps the card `running`.
- 2625 stores the run on the `status_healed` event as the event's run column (synced as the run uid) instead of a local run id in the payload; the once-per-run gate reads both forms.

## How it works

`hermes_cli/kanban_sync_divergence.py` (both entries) and its call site in the sync round (2596).

- 2596: `repair_divergence` calls a new `_stale_lifecycle` check when `status` differs; on true it calls `_adopt_replica` (applies the replica row via `apply_remote_changes` with local pending writes winning, observers off) and skips the push. New `heal_running_over_ended_run` is called in `_sync_board_locked` right after `heal_clobbered_completions` for primary boards. It selects running cards with no claim, no lease and an ended current run, updates status, clears the current run and, for done, sets the completion time and clears the block kind, guarded by the same predicates in the `WHERE`.
- 2625: `_stale_lifecycle` returns false on a live `claim_lock` before the terminal-replica test (a running card over an already-ended run is still stale). New `_parked_status` looks up the park event written in the run's end transaction and rejects it if any later event moved the card on. The heal now writes the park status instead of `todo` where it applies, logs the skip, and records the run as the event's run column.

## Code

`hermes_cli/kanban_sync_divergence.py`, `hermes_cli/kanban_sync_client.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_sync_terminal_regress.py` and `tests/hermes_cli/test_kanban_sync_running_heal.py`. Entry path: the module API `kanban_sync_client.repair_divergence` and `sync_board` rounds against a fake Board service (a pool worker ends a run, a stale copy is stored on the Board, the owner's next rounds pull, heal and push), plus `heal_running_over_ended_run` directly. Covered: a done-and-completed and a blocked-to-todo regression no longer reopen the card; a real uncaptured edit of a running card with an open run is still sent; the heal for done and the todo fallback, parks restored to blocked, review and scheduled, a park left by a later event falling back to `todo`, the event's run uid reaching the Board, the heal reported once and not looped, an open run left alone, a held `claim_lock` or lease keeping the card `running`, and no adoption of a terminal replica over a live claim. Entry paths no test drives: a real Board service, the `hermes kanban` CLI and the dispatcher around the sync round are **untested**.

## Follow-up 2710–2712: the park lands where its own event recorded it

- Behavior (2710): the park decision uses the card's last lifecycle move since the run's park event. The triage sweeps (`agent_block_escalated`, `ask_rechecked`, `supervisor_escalated`) count as leaving the park, and a later park event (the wake invariant's scheduled to blocked) lands that park. Events are ordered by origin time (second, then the uid's UUIDv7 millisecond, then local id), so a same-second unblock that arrived before its block is not missed. A malformed event payload reads as no payload, and each card heals in its own try, so one failing card never aborts the round. The "still reads running after one heal" warning logs once per (task, run) per process. A restored blocked park takes `block_kind` and `human_request` from its own event; a restored scheduled park clears `human_request`. The wake rows are not task columns, so a stale copy cannot touch them.
- Behavior (2711): a `kanban_continue` wait (run outcome `continued`) counts as a park and lands the `status` its own event recorded (scheduled), so it is no longer healed to `todo` and re-dispatched before its wake; a no-wait continue that went straight back to `ready` falls through to `todo`. A review reopened to ready (`review_reopened`, no payload) is a move out of the park: `_LEFT_PARK` now carries every resume lifecycle move (`review_reopened`, `triage_swept`, `gave_up`, `reclaimed`, `status`). `block_task` now records `status` (where the block landed) in the `blocked` event payload and the heal prefers it, so an exhausted-budget block that lands triage heals to triage; older events fall back to the `continuation_exhausted` / `goal_unachievable` re-kind. A metadata-only `blocked` event (human-request migration re-filing an ask) is not a move. A non-v7 uid no longer reorders same-second events, and a restored blocked ask re-syncs its resolved-check probe row.
- Behavior (2712): assignment routing records `status='todo'` in the block event payload next to `supervisor_task_id`, so a stale running copy over an assignment-routed run end heals to `todo` behind its supervisor, not `blocked` where the supervisor's completion never promotes it. A `block_loop_detected` end is still reclaimed to `todo`, not parked (documented in code).
- How it works: all in `kanban_sync_divergence._parked_status` and the once-gate (Python parse, plus `CASE json_valid` in SQL); `kanban_db.block_task` writes the extra payload field; `kanban_assignment_routing.route()` sets it for the todo landing.
- Code: `hermes_cli/kanban_sync_divergence.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_assignment_routing.py`.
- Tests: source coverage, not run in this mirror: `tests/hermes_cli/test_kanban_sync_running_heal.py` (sweeps leaving a park, wake-invariant park, same-second ordering, malformed payload, per-card isolation, restored block/schedule fields, continued runs, every resume move, exhausted-budget block, metadata-only block, resolved-check probe row, non-v7 uid, assignment-routed block), `tests/hermes_cli/test_kanban_block_kinds.py` (block events record where the block landed) and `tests/hermes_cli/test_kanban_assignment_routing.py` (a small assertion addition). Entry path: the same sync-round and `heal_running_over_ended_run` APIs as above against a fake Board service. A real Board service and the `hermes kanban` CLI are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Primary (owner) boards only: the heal is not run for non-primary boards.
- A heal the Board outvotes (the other side's clock is ahead) is logged and left alone after its first attempt.
- Parks restored only when the park's own event exists and no later lifecycle move followed it (2710–2712 widened what counts as a move and what a park lands as: a continue wait, a recorded landing status, triage); otherwise the card goes `todo`.
- Cards already stuck before this patch are healed on the next owner round; the stale-claim sweep never saw them.

## Plugin

Core change; no plugin required.
