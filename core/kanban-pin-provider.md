# Bind a concrete Kanban model pin to its provider

Patch 0653 (`9c943cbd2f`).

## Behavior

A concrete model pinned without a provider resolves its provider at pin time, instead of inheriting changing profile configuration at each spawn. The profile's configured provider wins when it serves that id; otherwise the single authenticated serving provider is selected (with a vendor first-party fallback). Missing or ambiguous providers are refused with `pin_provider_unresolved`, naming the candidates. Explicit providers and class selectors are unchanged.

## How it works

`resolve_pin_provider()` is shared by `create_task()` and `set_model_override()`. The dispatcher uses the same resolver for legacy concrete pins with NULL providers, recording `pinned+provider_resolved` or `pinned+provider_unresolved` in tier events.

## Code

`hermes_cli/kanban_pin_provider.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_model_tier.py`, `hermes_cli/kanban.py`, `tools/kanban_tools.py`.

## Tests

`tests/hermes_cli/test_kanban_pin_provider.py`: **19 passed** under the canonical runner on the applied public patch. Entry coverage: direct database create, the registered tool handler, CLI create/set-model, and dispatcher legacy-row resolution plus worker argv. Cases include auto/class-deep profile configs, unknown-model refusals without mutations, ambiguous authenticated providers, configured tie-breaking, custom and live catalogs, missing credentials, explicit provider preservation and class-selector preservation. A dashboard request or actual provider HTTP send was not exercised.

## Applying

Apply 0653 after 0652 on the fork; both patches were verified with `git am -3` in a disposable worktree at `90a63800fb`.

## Gotchas

An explicit provider is the caller's choice; this resolver does not guarantee it is usable. Legacy unresolved pins are reported, not silently rerouted. Catalog/credential coverage is fixture-backed, not a live account probe.

## plugin?

No; the invariant belongs to core card creation, model mutation and dispatch.
