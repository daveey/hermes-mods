# Ordered IDs for legacy recovery-cursor coverage

Patches: **2851**.

## Behavior

Test-only: the legacy-row cursor test no longer assumes randomly suffixed same-millisecond task IDs sort in creation order.

## How it works

**2851:** patch `new_task_id` in the fixture with a sequential counter formatted as `t_` plus sixteen hexadecimal digits, so every legacy fixture row is after every policy row by construction. The production ID generator is untouched.

## Code

Test-only changes; no production implementation is modified.

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_legacy_rows_do_not_advance_policy_cursor` in `test_kanban_recovery_routing.py` retains the real recovery-routing assertion with an explicit ordering precondition.

- `tests/hermes_cli/test_kanban_recovery_routing.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

This deterministic minting is fixture-local; it must not replace portable production task identities.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
