# Creator-chat backfill reuses one guarded replica reader

Patches: **2350**.

## Behavior

Creator-chat backfill of an unresolved backlog opens the guarded replica a single time per pass, never opens it when all candidates resolve locally, retries a missing or unopenable replica on the next pass, and still sees links created or released during the pass.

## How it works

`_ReplicaReader` is a lazy context manager holding one guarded handle per repair pass; it caches neither ownership nor misses, and closes on resolution error, missing schema or exceptions. Standalone creation uses the same reader scoped to one lookup. Local writes are read before descendants.

## Code

`hermes_cli/kanban_creator_chats.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_kanban_backfill_replica_budget.py` (single open for any backlog size, no open when all local, bounded open failure, new/released links visible, close on error).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Intentionally no result cache: each lookup within the pass re-reads current replica state.

## Plugin

Core change; no plugin required.
