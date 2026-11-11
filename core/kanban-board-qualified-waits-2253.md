# Board-qualified producer waits

Patches: **2223–2225**.

## Behavior

Same-host waits with an explicit producer board read that board, not the waiter's database. Scheduling, orphan checks, completion checks and wake guidance retain board identity without moving the waiter's write scope. Same-board guidance omits `--gateway`; remote guidance retains it.

## How it works

`producer_connection` opens another board read-only and fails if it cannot be read. Dependency identities include database path and task ID, allowing cycle checks across board boundaries without treating local IDs as globally unique. Explicit `--board` lookup takes precedence over `HERMES_KANBAN_DB` in wait checks. Wake notes include only the required board/gateway qualifiers.

## Code

`hermes_cli/kanban_awaiting_board.py` owns qualified connections and graph traversal. `kanban_awaiting.py`, `kanban_wait_check.py` and `kanban_wake_check.py` use it.

## Tests

Source coverage, **not run in this mirror**:

- `tests/hermes_cli/test_kanban_awaiting_cross_board.py`: scheduling in both board directions, unchanged producer state, missing-board safety, cross-board cycles, finished producers, same-board descendant rejection, large fan-in and qualified guidance.
- `tests/hermes_cli/test_kanban_park_producer_finished.py`: local wake notes omit `--gateway`, while remote notes retain it.

## Applying

Use the [README full replay procedure](README.md#applying), including the fan-in and wake-note follow-ups.

## Gotchas

A missing producer board is not created and never falls back to a matching local ID. The cycle search remains bounded and best-effort: reaching its 32-node limit does not itself prove a cycle or reject a large acyclic fan-in. These additions test same-host cross-board reads, not a new remote transport.

## Plugin

Core wait validation and wake logic; no plugin required.
