# Direct activation borrowing controls

Mirror ordinals: 3968.

## Behavior

Adds shell-entry coverage for runtime/UV overrides, whitespace opt-out and stderr-only borrowing diagnostics; no activation implementation changes.

## How it works

The recording fake-setup helper accepts environment overrides. The direct run-in-hermes-env entry receives an isolated account store and a Python minor derived from the lockfile, then stops at setup before downloads.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/scripts/test_tool_store_pins.py`: Explicit runtime prevents borrowing, UV override remains selected, whitespace no opts out, stdout stays empty, borrowing diagnostics appear only on stderr in applicable cases, and the fake home gains no tools directory.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

The recording setup deliberately returns nonzero before downloads; that expected stop is not a real provisioning success.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
