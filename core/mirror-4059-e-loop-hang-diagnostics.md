# Loop-command traceback diagnostics survive capture

Mirror ordinals: 3993, 3994.

## Behavior

Adds 30-second blocked-thread diagnostics to loop-command tests without relaxing assertions or the existing hard timeout.

## How it works

An autouse fixture duplicates the runner stderr file descriptor while capfd is disabled, directs faulthandler.dump_traceback_later to that descriptor, then cancels the timer before closing it. This avoids losing the dump in capture buffers when a test file is hard-killed.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/tui_gateway/test_loop_command.py`: Existing loop-command assertions are retained; the patch adds fixture diagnostics rather than a new automated root-cause assertion. The controlled hard-timeout experiment reported in the commit message was not repeated here.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

This is diagnostic only and does not identify or fix the hang. Timer cancellation must precede closing the duplicated descriptor.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
