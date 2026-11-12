# Worker pickup keeps a copy the local dispatcher holds

Patches: **2536**.

## Behavior

A pool worker's pickup copies foreign cards from the Board into its local board. If the local dispatcher's respawn guard held a copied card in `ready`/`review` (for example an `active_pr` hold), the next tick's `evict()` dropped it and `pickup()` copied it again, first in candidate order. On a nearly full worker (pickup budget 1) that card took the only slot every tick while cards pinned to the machine waited with `budget_exhausted`. Now `evict()` keeps a `ready` or `review` copy that has a recorded `respawn_guarded` hold which `check_respawn_guard` still confirms. A kept copy is already present, so the pickup skip logic spends no budget on it. It is evicted as before once it ends, blocks, parks, or the guard lifts, and a failed guard check evicts as before.

## How it works

`hermes_cli/kanban_worker_node.py` adds `_guard_held(conn, ids)`, which uses `guard_hold_reason(..., include_replies=False)` to find a recorded hold and `kanban_db_dispatch.check_respawn_guard` to confirm it. It runs before the eviction write transaction because the guard may read PR state. Keeping is safe for the Board lease: the copy holds no lease, any machine can still lease the card, a local spawn still needs a grant, and the feed keeps the copy current. Hold maintenance stays with the card's home. See [kanban-respawn-guard-hold.md](kanban-respawn-guard-hold.md) and [kanban-foreign-card-replica.md](kanban-foreign-card-replica.md).

## Code

`hermes_cli/kanban_worker_node.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pickup_held_copy.py`. Entry path: the worker-node test harness's `round()`/`tick()` and direct `evict()` calls against a fake hub and worker, with the respawn guard stubbed. Covers a held copy not retaking the budget (copied once across ticks while a pinned card runs), eviction when the guard lifts, when the dispatcher never recorded a hold, when the card leaves the lane, and when the guard check raises. The real guard reading PR state is untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A held copy now stays in the local board until the guard lifts or the card leaves `ready`/`review`.
- Only a recorded hold counts; the live guard alone does not keep a copy.

## Plugin

Core change; no plugin required.
