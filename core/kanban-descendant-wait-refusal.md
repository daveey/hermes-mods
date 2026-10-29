# Refuse waits on a card's own descendants

Patch 0650 (`653386ca43`).

## Behavior

Schedule/continue cannot park a card waiting on its own child or transitive descendant. Such children are gated on the parent's completion, so the wait would deadlock. Producer inference does not infer a descendant as a valid card producer, and an existing return wait is checked for a cycle too.

## How it works

`dependency_cycle_reason()` checks the dependency graph and return-wait edges. Awaiting inference and both durable schedule/continue entry points validate producer references against that check.

## Code

`hermes_cli/kanban_awaiting.py`, `hermes_cli/kanban_continuation.py`, `hermes_cli/kanban_db_schedule.py`.

## Tests

New cases in `tests/hermes_cli/test_kanban_awaiting.py` exercise inferred child waits and explicit schedule/continue cycles, including descendants. They are the upstream regression coverage; see the published verification record for execution results.

## Applying

Apply 0650 after no-producer-no-park and continuation patches with `git am -3`.

## Gotchas

A parent hands off by completing its own phase, releasing the child, rather than scheduling a wait on that child. Cross-gateway graph completeness is not proved by these local fixtures.

## plugin?

No; core graph validation at inference and persistence.
