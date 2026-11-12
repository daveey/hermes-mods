# Review resume reads a parent run from the Board replica

Patches: **2517**.

## Behavior

A review parent created and run on another gateway has no local run, so `_completed_review_run` returned `None` and the owner card stayed held by `active_pr` even after an exact-head approval. Now the parent's latest run is read from the fresh same-board replica, and the same verdict, exact-head and PR-scope rules apply as for a local run: an exact-head approval releases one continuation, and a change request on the captured PR hands back a repair. Everything else keeps the hold: a run with no claimed event, a parent not `done`, an outcome other than `completed`, a run that ended before the park, a stale, future-stamped or other-board replica, sync mode off, a missing replica, or any local row for the parent (a local row always decides).

## How it works

`hermes_cli/kanban_dependency_history.py` factors the snapshot rule into `_with_fresh_replica(conn, parked_at, read, default)`: one committed same-board snapshot pulled after the park and within the stale bound, else `default`. `replica_historical_parent` now uses it, and new `replica_review_run` returns the parent's latest claimed, completed run that ended at or after the park (Board data only, never a move- or comment-supplied verdict). `_completed_review_run` in `kanban_dependency_review.py` falls back to it when the mirror of a moved parent has no answer. See [kanban-dependency-review-continuation.md](kanban-dependency-review-continuation.md) and [kanban-foreign-card-replica.md](kanban-foreign-card-replica.md).

## Code

`hermes_cli/kanban_dependency_history.py`, `hermes_cli/kanban_dependency_review.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_dependency_review_replica_parent.py` and an updated `tests/hermes_cli/test_kanban_move_parent_evidence.py` (which stubs `replica_review_run` to isolate the mirror rules). Covered entry path: `dispatch.check_respawn_guard` and `kb.claim_task` on a parked owner with a replica-only review parent. CLI, tool, REST and Desktop surfaces are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Needs a fresh replica pulled after the park; without one behavior is unchanged.
- The replica supplies run data only; it never creates a verdict.

## Plugin

Core change; no plugin required.
