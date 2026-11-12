# Refuse a move made for "ship custody"

Patches: **2638**.

## Behavior

- `kanban_move` and `hermes kanban move` refuse, with reason code `ship_custody_portable`, a move whose `reason` claims that the final ship/landing step needs a particular host's custody (for example "ship must run in hub task custody"). The message says `hermes-core-wt ship` lands from every machine, because its gate reads runs and reviews from the Board's execution record, so moving only queues the ship behind another host's backlog; run ship where the card runs.
- A non-empty `host_reason` lets the move through, for a card that really needs one host (for example an unpushed worktree that exists only there).
- Applies to the three move paths: `move_task`, `move_queued` and `move_reclaim`. Reasons that merely mention shipping without a custody claim are not refused. No dispatcher change.

## How it works

New `hermes_cli/kanban_move_ship.py`: `cites_ship_custody` matches "ship/shipping/land/landing" within 80 characters of "custody" (either order, same sentence) or "hub (task) custody"; `refusal(reason, host_reason)` returns the message or None. `kanban_move.check` gained a `reason` argument and raises `MoveRefused(ship_custody_portable)` right after the runs_on validation; all three move paths pass their reason in. The `kanban_move` tool schema text describes the rule.

## Code

`hermes_cli/kanban_move.py`, `hermes_cli/kanban_move_ship.py` (new), `tools/kanban_tools_schemas.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_move_ship_custody.py`. Entry path: the classifier and the move functions at module level, with ship-adjacent non-custody reasons as negative cases. The `kanban_move` tool handler and the CLI are **untested** here; the schema wording is not asserted.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The check is a text heuristic on the reason; a differently worded claim is not caught, and a `host_reason` of any text bypasses it.
- It judges the stated reason, not the card's actual state.

## Plugin

Core change; no plugin required.
