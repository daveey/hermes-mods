# Schema-discovered targeted computer scrolling

Patch coverage: 6249.

## Behavior

Computer-use scroll includes target x/y coordinates when the connected driver's live discovery supports them, whether via its capability token or both schema properties. Drivers advertising only a schema no longer silently take the untargeted path.

## How it works

The input adapter accepts input.scroll.coordinates for tool scroll, or the conjunction of supports_input_property(scroll, x) and supports_input_property(scroll, y). If neither proof exists, the request omits coordinates and preserves the legacy behavior. This is discovery-based dispatch, not a guessed driver-version table.

## Code

Affected implementation and integration paths in the source series:

- `tools/computer_use/cua_backend_input.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/tools/test_computer_use_scroll_coordinates.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

One coordinate property alone is insufficient. Capability and property names are protocol identifiers and must remain exact. This documentation run did not connect a desktop driver or perform a physical scroll.

## Why core, not a plugin?

The common input backend must interpret live driver schemas consistently for every caller; an optional plugin would leave the existing transport path incorrect.
