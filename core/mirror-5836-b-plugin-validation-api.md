# Name-scoped public Kanban validation API

Source patch ordinals: 5578.

## Behavior

Plugins can import three pure Kanban validation functions through the public API inventory. Mixed dashboard PATCH requests reject invalid routing/text before unrelated fields or history mutate.

## How it works

`hermes_cli/data/plugin_api.json` explicitly exports `hermes_cli.kanban_model_neutral:validate_selector`, `:validate_text` and `:validate_review`. Name scoping intentionally does not expose routing projections or mutation helpers. The dashboard uses the shared validation boundary before its mixed request processing.

## Code

- `hermes_cli/data/plugin_api.json`
- `website/docs/developer-guide/plugins/public-api.md`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_plugin_validate_private_imports.py::test_kanban_pure_validation_exports_are_name_scoped` checks callable exports and refusal of broader/private imports. `tests/plugins/test_kanban_model_neutral_entries.py` covers invalid mixed PATCH preserving all fields/events and valid neutral mixed PATCH success.

## Applying

Apply the API inventory/documentation with the model-neutral dashboard integration. Import the named functions, not the entire module as a public namespace. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Pure validation does not resolve live entitlement, allocate capacity or authorize a mutation. A whitelisted name does not make sibling helpers public API.

## plugin?

Yes. This is a public plugin import seam; lifecycle and routing remain core-owned.
