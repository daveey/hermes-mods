# Remote card references are not phantom references

Patch 0659 (`63ab7b706a`).

## Behavior

A completion summary mentioning a card known only to the hub replica is not flagged as a hallucinated reference. Truly unknown ids still are. A stale, offline or unreadable replica cannot establish absence, so the diagnostic suppresses that uncertain warning. Existing warnings are re-resolved at read time against current replica knowledge.

## How it works

Completion checks local references plus replica task ids. Diagnostic projection rechecks older warning payloads, narrowing or clearing their phantom references when the replica learns the ids or is no longer authoritative enough to prove absence.

## Code

`hermes_cli/kanban_db.py`, `hermes_cli/kanban_diagnostics.py`, `hermes_cli/kanban_session_activity.py`.

## Tests

`tests/hermes_cli/test_kanban_false_signals.py` tests completion with both remote-known and unknown ids, remote-only summaries, no replica, stale/offline/unreadable replicas and read-time narrowing/clearing of old warnings. Coverage uses local replica fixtures, not a live remote gateway.

## Applying

Apply 0659 after the Kanban diagnostics and hub replica patches with `git am -3`.

## Gotchas

This changes diagnostic evidence, not whether a remote card is complete or authorized for mutation. Uncertain replica absence is not proof of a phantom card.

## plugin?

No; core completion and diagnostic projections.
