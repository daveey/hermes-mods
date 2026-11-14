# Kanban change cursor identity and REPLACE contract

Mirror ordinals: 3961, 3973, 3983.

## Behavior

Pins full invalidation on shared-memory identity changes and refusal of in-memory cursors. Documents, but does not fix, cross-task REPLACE losing a deletion mark.

## How it works

A new WAL shared-memory identity forces FULL with a newly bound cursor even when file identity, schema and revision are unchanged; subsequent reads can rearm. An in-memory board has no trustworthy file identity. The cursor module documents that cross-task unique-key conflicts using INSERT OR REPLACE with recursive_triggers off can silently delete another task row without its DELETE trigger.

## Code

- `hermes_cli/kanban_task_change.py`

## Tests

- `tests/hermes_cli/test_kanban_task_change.py`: Models only the shm observation and asserts FULL/fresh cursor followed by an empty incremental result. A real initialized in-memory backup always returns FULL,None before and after a write, including when supplied a disk cursor.
- `tests/hermes_cli/test_kanban_task_change_replace_contract.py`: Independent writer/reader connections reproduce replacement of task a by task b reporting only b, not FULL. The ON CONFLICT DO UPDATE control reports both a and b, and documentation assertions keep the limitation explicit.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Prefer ON CONFLICT DO UPDATE when a unique key can move between tasks. Reader pragmas cannot enable another writer's delete triggers; globally enabling recursive triggers changes sync outbox/tombstone behavior and is not part of this patch.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
