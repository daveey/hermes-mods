# Cross-gateway priority donation preserved after pickup

Patches: **2378**.

## Behavior

Pickup ranks the full replica but dispatch previously ranked only its imported subset, dropping priority donated by remote waiters. Dispatch now overlays the read-only graph with local rows and child-edge authority so a card inheriting a higher remote priority gets the next slot.

## How it works

`kanban_dispatch_priority.order_with_remote_waiters` derives donations transitively (cycles, missing and terminal waiters cut off, cap and creation order tie-breaks) with no writes; local terminal waiters, unlinks and priority edits override stale replica data. Non-primary executors, absent or unreadable replicas and open failures fall back to local order and still claim local work.

## Code

`hermes_cli/kanban_dispatch_priority.py`, `kanban_db_dispatch_queries.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pickup_priority.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Donation is derived at ranking time only and never imported or stored.

## Plugin

Core change; no plugin required.
