# Outcome judging binds an unscoped chat owner

Patches: **2899**.

## Behavior

On a multiplexed gateway, outcome refresh/derivation invoked from an unscoped sweeper or handler judges under the owning state.db home’s secret scope. Existing routed scope is preserved, and caller context is restored even after failure.

## How it works

**2899:** `_judge_summary` receives the DB handle and enters `_owner_scope`. Only when the DB has a path, multiplexing is active and no secret scope exists does it bind both the parent home override and `owner_secret_scope(home)`. Nested finally blocks reset secret and home tokens. Failed judges still use the existing logged heuristic fallback.

## Code

- `hermes_state_outcome.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_outcome_judge_owner_scope.py` covers unscoped refresh/derive, a failing judge, existing caller scope, restored home override, pathless handles and single-profile process behavior.

- `tests/hermes_state/test_outcome_judge_owner_scope.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

An already bound caller scope is not replaced by guessed session ownership. This adds no credentials and changes no heuristic outcome definition. End-to-end provider requests were not made by this mirror.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
