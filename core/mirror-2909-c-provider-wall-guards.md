# Provider-wall reset and profile-scope regression guards

Patches: **2859, 2876**.

## Behavior

Test-only: pin the later of file/live-pool reset deadlines and preserve launch-home credential scope on dispatcher reads, without replacing an already routed turn’s scope.

## How it works

- **2859:** test both orders of a shorter provider-file wall and a longer live-pool wall; assert reroute to a class peer and record the later reset.
- **2876:** dispatcher-entry tests use two distinct profile homes/scopes. Unscoped ticks read the launch-home pool; routed turns keep their existing scope; an exception restores the caller scope and keeps the quota wall rather than inferring room.

## Code

Test-only changes; no production implementation is modified.

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_kanban_pin_reroute.py` checks the later-of deadline and reroute event. `test_kanban_provider_walls_scope_entry.py` checks dispatcher `tick`/spawn gating, routed scope preservation and reset-on-error through the provider-wall entry path.

- `tests/hermes_cli/test_kanban_pin_reroute.py`
- `tests/hermes_cli/test_kanban_provider_walls_scope_entry.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

No quota policy or credential-routing implementation changes in these patches. Pool-read failure must remain fail-closed, not evidence of spare quota.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
