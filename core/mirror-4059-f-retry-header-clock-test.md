# Deterministic shared retry-header parsing test

Mirror batch 6; ordinals 4058.

## Behavior

Test-only time control removes scheduler aging from metrics sender HTTP-date assertions while retaining real parsing and sender clamping.

## How it works

Only retry_utils.datetime is frozen. A real formatted HTTP date is parsed at whole-second and fractional-second instants to pin integer truncation.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/agent/test_retry_delay_parsers_shared.py`

Mail entries in the ordered series:

- 4058: [`7074fd8be9b718a59857066c5c7eaedaf0d5f533`](patches/4058-daveey-Freeze-retry-header-clock-in-metrics-sender-test.patch)

## Tests

`tests/agent/test_retry_delay_parsers_shared.py`: exact 120/119-second results hold for whole/fractional clocks; numeric zero floors to one, huge values cap at 86400, and malformed headers preserve the supplied fallback.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No production retry policy or deadline changes are made.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
