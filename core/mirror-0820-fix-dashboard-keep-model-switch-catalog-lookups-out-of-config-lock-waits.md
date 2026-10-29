# fix(dashboard): keep model-switch catalog lookups out of config lock waits [daveey]

Patch 0820, source `f456a8d0811af06e79fe1614f409cc8f0c958350`.

## Behavior

fix(dashboard): keep model-switch catalog lookups out of config lock waits [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import logging`
- `_models_dev_retry_after: float = 0`
- `def fetch_models_dev(force_refresh: bool = False, *, allow_network: bool = True)`
- `def _build_switch_result(st: _Switch) -> ModelSwitchResult:`
- `def _validated_main_model_selection(`
- `+"""A scoped catalog policy must be cache-only, nested and exception safe."""`
- `+"""A cold models.dev fetch must not monopolize dashboard config writes."""`
- `+"""Dashboard cache-only switching keeps CLI catalog fetches available."""`

## Code

- `agent/models_dev.py`
- `hermes_cli/model_switch.py`
- `hermes_cli/web_server_config.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_models_dev_network_scope.py`
- `tests/hermes_cli/test_config_model_catalog_lock.py`
- `tests/hermes_cli/test_model_switch_catalog_network.py`

New or changed test definitions present in the source diff:

- `test_cache_only_scope_serves_available_data_and_restores_on_error`
- `test_blocked_catalog_fetch_does_not_block_config_autosave`
- `test_switch_catalog_network_policy`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
