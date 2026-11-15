# Fork-safe nested loop CPU accounting

Mirror ordinals: 3958, 3959, 3995, 3996.

## Behavior

Inherited passes book only child CPU and wall time since the latest fork; child-opened nested passes and record() do not double-count the inherited outer pass. Exited-thread accounting invariants gain focused tests.

## How it works

A fork-generation mark and child clock baselines are stored by the registered at-fork hook. Pass exit chooses its own opening marks or the latest child fork marks. The hook clears parent thread-exit marks and queued exits but preserves the surviving thread nesting depth. Span tokens already reject another native thread ID.

## Code

- `hermes_cli/loop_accounting.py`

## Tests

- `tests/hermes_cli/test_loop_accounting.py`: Pins queued-exit folding before span baselines, thread CPU and snapshots; empty reset/fork exit queues; replacement of a stale thread-exit identity; exact nested CPU/wall values at nonzero baselines across one or repeated hooks. Real os.fork coverage verifies nonnegative CPU, outer-only process accounting and registered-hook behavior in an isolated subprocess.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Real fork cases are platform-dependent. Clearing all inherited depth would count child nested work twice; clamping a negative result would hide incorrect baselines rather than fix them.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
