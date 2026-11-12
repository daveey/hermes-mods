# Core-update requeue and unused Board grants no longer spend a PR owner's handoff

Patches: **2602, 2603, 2676**.

## Behavior

- hermes-sync's core drain requeues restart-safe workers with `hermes kanban reclaim <task> --reason "hermes-sync core update: restart-safe worker requeue"`. A PR owner's one-shot handoff (an orderly dependency park, an exact-head review checkpoint, a same-second immediate checkpoint) used to be consumed by that requeued run, leaving the owner held `active_pr` with no worker after its review had already approved.
- Now such a requeue is passed over as if it never ran: the next respawn resumes the owner. The skip is deliberately narrow: only a manual reclaim with exactly that reason, on a run that ended `reclaimed` and has no `crashed`, `timed_out`, `spawn_failed` or `gave_up` event. Any other reclaim, a crash or a timeout still consumes the handoff (fail closed). A PR the requeued run reported is a newer PR comment and re-arms the guard on its own.
- The owner-wake hold release (operator re-release after a guarded park) is not consumed by a core-update requeue either: release, claim, requeue leaves the release valid, and the operator can release again. A crash still consumes it.
- A Board grant returned unused adds a newer `released` run row that ran nothing. The review-checkpoint continuation, the owner-wake hold release and the guard notice now read the latest run that actually started, so a finished exact-head review still resumes its owner. Any started, failed or unattested run still consumes the continuation.

## How it works

- 2602: new `hermes_cli/kanban_core_update_requeue.py` (`CORE_UPDATE_REQUEUE_REASON`, `is_core_update_requeue`, `requeued_run_ids`, `latest_run_skipping_requeues`). `kanban_db_dispatch_pr_state._dependency_handoff_after` and `_immediate_checkpoint_after` skip requeued runs and ignore their `reclaimed` event; `kanban_dependency_review.resumes_review` joins the park to the latest non-requeued run instead of `MAX(task_runs.id)`.
- 2603: `kanban_hold_release._park` and `released_after` use `latest_run_skipping_requeues`. The requeue module documents a residual risk: "restart-safe" is a host property, so a requeued run may have opened a PR it had not yet reported; the next run sees the prior transcript tail.
- 2676: `resumes_review`, `kanban_hold_release._park`/`released_after` and `kanban_dependency_review_notice.finished_review_hold` call `latest_run_skipping_requeues(..., skip_unused_grants=True)` (reusing `is_unused_grant`). The helper `latest_run_id` added in 2602 is removed.

The reason string must stay in sync with hermes-sync's core drain.

## Code

`hermes_cli/kanban_core_update_requeue.py` (new), `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_dependency_review.py`, `hermes_cli/kanban_dependency_review_notice.py`, `hermes_cli/kanban_hold_release.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_core_update_requeue_handoff.py` (2602), `tests/tools/test_kanban_owner_wake_hold_release.py` (2603), `tests/hermes_cli/test_kanban_dependency_review_unused_grant.py` (2676). Entry path: `check_respawn_guard`, `resumes_review` and `dispatch_once` on a board fixture, and the hold-release wake path (`wake._unblock`) for 2603. Covered: requeue keeps the review-checkpoint and ordering handoffs; a crash, no reason, or a near-miss reason still consumes them; a requeued run that published a new PR stays held; a crash event on a requeued run is not neutral; the same-second immediate checkpoint survives a requeue; the dispatcher respawns after a requeue but not after a crash; release, claim, requeue keeps the hold release while release, claim, crash re-holds (2603); unused grants keep the review continuation, used/failed grants and a release without the unused receipt still consume it, the guard notice describes the park behind an unused grant, and the hold release survives an unused grant (2676). The `hermes kanban reclaim` CLI and hermes-sync's drain itself are **untested** here (tests call `kb.reclaim_task` with the reason).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The requeue skip is an exact string match on the reclaim reason plus `manual: true`; a reworded reason in hermes-sync stops it working.
- A requeued run that had already opened a PR it did not report is the accepted residual risk.

## Plugin

Core change; no plugin required.
