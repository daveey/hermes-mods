# PID memo bounds and producer-sweeper cleanup

Mirror ordinals: 3957.

## Behavior

Pins bounded PID memo retention and cleanup of producer-admission observers on normal or exceptional loop exit. This is test-only lifecycle coverage.

## How it works

A fixed churn workload varies PID-file paths or probe callable identities. A controlled stop event lets the real producer sweep open observers, then either stops or raises from wait while a fake lease records beats and release.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/gateway/test_status_pid_memo.py`: For 600 distinct keys, immediate second reads reuse the probe and retained entries never exceed the fixed 256-key budget.
- `tests/tui_gateway/test_producer_admission_gate.py`: Checks one lease beat and release, no extra sweep, empty observer/pass maps, and ProgrammingError when querying every previously usable observer connection after either exit mode.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

The fixed workload and cap assertions must not be resized from a changed production limit; otherwise a retention regression could redefine its own expected result.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
