# Placement coordination ignores possessive prose

Patches: **2514**.

## Behavior

The placement coordination parser treated `hermes-hub` or `cross_task` followed by `'s` as an invocation: a trailing `with `, `via ` or `use ` in the prefix made it an explicit call with an empty action, so card text such as "passes with hermes-hub's older core" was refused as "needs home coordination action". A possessive (`'s` or the curly `’s`, not followed by a word character or hyphen) is now plain prose and adds no requirement. Real invocations stay fail-closed: a bare `run hermes-hub`, `use cross_task`, `hermes-hub unknown-cmd`, `hermes-hub's-tool` and `hermes-hub'sync` still need the action, and `hermes-hub status` still needs the coordination-read capability.

## How it works

In `hermes_cli/kanban_placement_coordination.py` the `_HUB` and `_CROSS` regexes gain a negative lookahead `['\u2019]s(?![\w-])` next to the existing `[/.-]` exclusion. See [kanban-coordination-attestation-2409a.md](kanban-coordination-attestation-2409a.md) for the requirement model.

## Code

`hermes_cli/kanban_placement_coordination.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_coordination_placement.py`. Covered entry path: `coord.requirements(text)` and the placement `reason` helper over prose (ASCII and curly apostrophe, newline) and over still-refused invocations. The CLI, tool and REST create paths that call it are untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

No caveats: the exemption is only for the possessive form; other prose around these names is unchanged.

## Plugin

Core change; no plugin required.
