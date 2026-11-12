# Dependency handoffs across unused grants and evicted history

Patches: **2380, 2401**.

## Behavior

A bare Board grant/release with no execution trace no longer counts as the parent run, so dependency dispatch and queue notices are preserved; used, failed, contradictory or unreleased grants still consume the handoff. For review resume after executor partition eviction, a parent is discharged only by fresh same-board replica history proving it finished unchanged; unverified or unreadable history keeps the hold, and a local row without history overrides an old replica.

## How it works

`kanban_unused_grant.is_unused_grant`/`latest_started_run` ignore only positively attested unstarted grants; `recorded_guard` exposes the last advisory dispatch hold for queue-position notices. `kanban_dependency_history.replica_historical_parent` pins stamp, task, run and events to one committed feed snapshot, consulted by `kanban_dependency_review` after local custody.

## Code

`hermes_cli/kanban_unused_grant.py`, `kanban_queue_position.py`, `kanban_chat_wait.py`, `kanban_parent_unclaimed.py`, `kanban_db_dispatch_pr_state.py`, `kanban_dependency_history.py`, `kanban_dependency_review.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_dependency_unused_grant.py`, `test_kanban_guard_queue_notice.py`, `test_kanban_dependency_review_evicted.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

A terminal status alone never discharges history. The recorded guard is advisory, not a fresh verdict.

## Plugin

Core change; no plugin required.
