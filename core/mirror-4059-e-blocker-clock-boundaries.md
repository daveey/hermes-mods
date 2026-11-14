# Quiet-board claim and provider-blocker clocks

Mirror ordinals: 3955, 3988.

## Behavior

Pins release of dead local claims even without a board revision change, and makes open provider-scoped blockers recheck external route inputs every 300 seconds.

## How it works

The blocker probe lane appends a route-recheck deadline before filtering its held cards by task_route. Assignee policy, effective config and expiring walls can change scope without board writes, so a file/revision key alone is insufficient. Unscoped blockers keep their existing cadence. Lookback expiry remains strictly after the inclusive 14-day boundary.

## Code

- `hermes_cli/kanban_idle_candidates.py`
- `hermes_cli/kanban_shared_blockers.py`

## Tests

- `tests/hermes_cli/test_kanban_sweep_gate_boundaries.py`: Uses a real process to show a status-only feed leaves a live claim intact, then death releases its lock/PID without an intervening revision. The old unscoped assertion holds at boundary-1 and boundary, then permits a scoped probe at boundary+1.
- `tests/hermes_cli/test_kanban_sweep_gate.py`: Changes an assignee route without a board write; confirms an immediate idle tick skips, the 300-second deadline probes the newly matching card, and unscoped blockers add no route-recheck deadline.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

This is bounded polling of route-dependent scope, not immediate invalidation on every external policy change. A provider-scoped blocker must be open and have cards behind it.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
