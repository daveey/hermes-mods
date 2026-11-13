# Chat-wait lag warning once per stall per board

Patches: **3311, 3329, 3350**.

## Behavior
- A lagging board logs its "notice(s) deferred" warning once per stall rather than every tick, even while another board is healthy. The line names the board. Notices themselves are unchanged; only log level is affected.
- A stall ends only when the replica lag closes, not merely when nothing is due.

## How it works
- 3311 turns the global `_LAG_WARNED` bool into a set of board identities (the swept connection's main DB file).
- 3329 also discards the board on the nothing-due return.
- 3350 revises that: the nothing-due sweep discards a board only when the lag has closed, so a new due card inside the same lag does not warn twice.

## Code
- `hermes_cli/kanban_chat_wait.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ready_wait_checkpoint.py`: `test_lagging_board_warns_once_per_stall_while_another_board_is_healthy`, `test_board_with_nothing_due_ends_its_stall`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3329 and 3350 pull in opposite directions on the nothing-due case; apply both in order.
