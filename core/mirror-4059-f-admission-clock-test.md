# Deterministic CPU-advisory age boundaries

Mirror batch 6; ordinals 4048.

## Behavior

Test-only clock control establishes exact advisory freshness boundaries without scheduler-dependent task-creation margins.

## How it works

The fixture freezes only admission’s clock while retaining actual sampler-file writes/reads and claim flow. It then advances that clock across the pre-lease and locked-read age limits.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/hermes_cli/test_kanban_hub_admission.py`

Mail entries in the ordered series:

- 4048: [`c984e1f6e7202119c5ed9efbc382dee0352371db`](patches/4048-daveey-Freeze-admission-clock-in-advisory-age-boundary-test.patch)

## Tests

`tests/hermes_cli/test_kanban_hub_admission.py`: claim succeeds at exactly 10000 ms pre-lease age; 1 ms later that narrower sampler refuses. The locked read accepts the same sample at exactly 15000 ms and rejects 1 ms beyond it.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No production timeout, sample policy or retry widening is introduced.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
