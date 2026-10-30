# Cold Kanban helpers reuse plugin presence records

## Behavior

Patch **1121** lets a cold `has_hook` query reuse a fingerprint-checked hook-presence record instead of importing every plugin to learn that no observer is registered. A positive observer result still loads plugins when the event is invoked. Patch **1130** skips the xAI credential probe while serving a plugin toolset snapshot for capability admission; it does not remove normal credential discovery.

## How it works

Discovery captures its input fingerprint before scanning and writes hook names to `cache/plugin_hooks.json`. Names include registered callbacks and manifest `provides_hooks`. Same-fingerprint records are unioned. A sweep with a timed-out plugin cannot establish a new complete record, but may add observed hooks to an already complete matching record.

`served_hooks` is eligible only for an undiscovered manager, with discovery enabled and no active background sweep. Missing, malformed, version-mismatched, or fingerprint-stale data returns to ordinary discovery. An already registered hook can also satisfy `has_hook`; the record is not a replacement for loading callbacks before invocation. The hook file is separate from the toolset snapshot so hook writes do not invalidate that record.

In `_composite_toolsets`, `_xai_credentials_present()` runs only when `plugins_snapshot.active() is None`. Active admission snapshots need terminal/file/Kanban membership; probing xAI cannot establish those capabilities and would import the auth machinery unnecessarily.

## Code

- `hermes_cli/plugins.py`: discovery stamp and record write; `has_hook` served lookup.
- `hermes_cli/plugins_loader.py`: `LoadedPlugin.load_timed_out` tracking.
- `hermes_cli/plugins_snapshot.py`: `discovered_hooks`, `record_hooks`, `load_hooks`, `served_hooks`.
- `hermes_cli/tools_config.py::_composite_toolsets`: active-snapshot credential-probe condition (1130).

## Tests

Tests identified from source, **not executed in this documentation pass**:

`tests/hermes_cli/test_plugin_hook_presence_record.py` (1121) runs fresh interpreter probes against isolated homes and real fixture plugins:

- `test_cold_assign_with_a_recorded_hook_set_does_no_discovery` checks assignment with/without explicit capability requirements.
- `test_missing_record_falls_back_to_discovery_and_records_for_the_next_process`.
- `test_stale_record_falls_back_and_a_new_observer_still_receives_the_event`.
- `test_recorded_observer_is_served_true_and_receives_the_event_exactly_once` checks actual observer delivery, with discovery still needed to invoke it.
- `test_manifest_declared_hook_counts_even_when_not_registered` checks persisted hook presence (not a direct served-true assertion in this patch).
- `test_sweep_with_a_timed_out_plugin_does_not_vouch_for_the_hook_set`.
- `test_assignment_is_committed_before_notify_so_a_killed_helper_leaves_it_whole` checks persistence across abrupt helper exit; it is not a new transaction change in this patch.

`tests/hermes_cli/test_kanban_capabilities_plugin_snapshot.py::test_served_default_composite_does_not_import_auth_or_providers` (1130) checks the default composite in cold processes: same capability verdict, no discovery/auth import on reuse, and full discovery after plugin edits.

Corrupt-record fallback and same-stamp union logic are visible in code but are not separate named test cases added by these patches. No deployment timing benchmark was run here.

## Applying

Replay these ordered members of the full fork series with preceding patches from the documented base (`git am -3 --empty=keep`). Existing plugin fingerprints, toolset snapshots, atomic JSON writes, and discovery lifecycle are dependencies. No independent patch application was tested in this pass.

## Gotchas

A presence record stores hook names, not executable callbacks. Do not bypass invocation-time discovery on a positive record. Timeouts must not be cached as proof of absence. Snapshot-only suppression of the credential probe is narrower than globally disabling `x_search` or provider authentication.

## Plugin

Plugins continue registering callbacks normally; manifest `provides_hooks` conservatively advertises presence even if registration does not occur. These core optimizations do not install a plugin and do not eliminate observer delivery discovery.
