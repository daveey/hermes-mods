# Authenticated foreign self-work creator intake

Source patch ordinals: 5805.

## Behavior

Authenticated foreign creators can file courier self-work without a local creator-row lookup. Foreign provenance remains in the immutable intake intent and cannot borrow a colliding local task's tenant or subscriptions.

## How it works

`kanban_selfwork_owner` returns a local lookup ID only for the authoritative local namespace. A foreign creator must match the authenticated origin gateway/profile and sender `gw:<gateway>`; the accepted foreign branch returns `None` rather than probing a replica/local collision. Lane classification treats courier self-work with a durable sender/declaration pair as agent-filed, not as a human exemption.

## Code

- `hermes_cli/kanban_selfwork_owner.py`
- `hermes_cli/kanban_lanes.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_selfwork_foreign_creator.py` covers real receiver create/append/replay, local ID collision avoidance, origin/sender mismatches, missing origin and lane treatment without a local creator.

## Applying

Apply owner resolution and lane classification together. Preserve the authenticated intake origin and immutable creator intent.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

An arbitrary `created_by` label alone is not authentication. Returning no local lookup ID does not erase foreign provenance or authorize replica substitution.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
