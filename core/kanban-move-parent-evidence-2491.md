# Dependency evidence preserved across checkpointed queued moves

Patches: **2444, 2445**.

## Behavior

`hermes kanban move` of a queued (not running) card now forwards `--checkpoint` text: it is stored on the `moved` event as `checkpoint` and added as a "Move checkpoint" comment by the operator. When such a checkpointed card is moved to another gateway, the dependency checks (PR handoff and review-parent completion) can still see the result of a parent whose run lives on another gateway, using the refreshed Board replica rather than the move text. This does not exempt anything from PR policy: a publication after park is not authorized by the move, and a move after a crashed, timed-out or reclaimed run stays guarded.

## How it works

New `kanban_move_parent_evidence`: `prepare` scopes to parents of non-terminal cards whose latest `moved` event is `queued` with a non-blank string checkpoint, and reads each such parent's latest replica run (plus `parent_status` and `was_claimed`); `apply` stores them as JSON in a local `kanban_move_parent_results` table. `kanban_db_sync.refresh_remote_parents` now reads parent status and result in one `SAVEPOINT` snapshot of the replica, writes both in one transaction, and still takes no write lock on an unchanged round. `result()` is a fail-closed reader: it returns a mirror only if the parent is not local, is a parent of the task, the latest move is a checkpointed queued move made after an orderly `blocked` run ended, and no crashed/reclaimed/timed_out event is at or after that run's end. `kanban_db_dispatch_pr_state._dependency_handoff_after` and `kanban_dependency_review._completed_review_run` fall back to it when no local run exists; the review path additionally requires parent status `done`, claimed, outcome `completed`, and ended at or after park. See [kanban-dependency-handoff-history-2409b.md](kanban-dependency-handoff-history-2409b.md) for the related handoff logic. 2445 only refactors test setup in `test_kanban_move_parent_evidence.py` to satisfy a health gate.

## Code

`hermes_cli/kanban_move_parent_evidence.py` (new), `hermes_cli/kanban_move.py` (`move_queued`), `hermes_cli/kanban.py` (`_cmd_move`), `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_dependency_review.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_move_parent_evidence.py` (reader custody rejections, scoping, review completion, single-snapshot refresh, no write on unchanged sync) and `test_kanban_move_pr_handoff.py` (`km.move_queued`/`move_task`/`move_reclaim` handoffs, parent-result wakeups, and `cli._cmd_move` preserving the checkpoint, called directly with a namespace). Argparse registration of `--checkpoint` already existed before these patches and is not re-tested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Evidence is copied only for parents of cards with a checkpointed queued move; other foreign parent histories are not mirrored.
- Only the latest active `moved` event counts; a later move without a checkpoint drops the scope on the next refresh.
- A checkpoint on a running-card move (`move_task`/`move_reclaim`) is not changed by this patch; only `move_queued` gained the parameter.

## Plugin

Core change; no plugin required.
