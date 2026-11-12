# Independent contention for admission timeout legs

Patches: **2852**.

## Behavior

Test-only: both admission `reserve(timeout=...)` and `extend(timeout=...)` must actually wait out a held lock, so either function ignoring the timeout fails the test.

## How it works

**2852:** each leg owns a separate spawned lock holder. The retry-pause seam releases that holder only after the long-timeout caller has waited at least a second; short default attempts still fail quickly. The shared Timer race and the formerly uncontended second leg are removed.

## Code

Test-only changes; no production implementation is modified.

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_reserve_and_extend_default_to_one_short_try_and_accept_a_longer_timeout` in `test_admission_fence.py` checks each function’s short default and longer explicit timeout against a separately held lock.

- `tests/hermes_cli/test_admission_fence.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

The one-second threshold is a test synchronization condition, not a change to production admission budgets.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
