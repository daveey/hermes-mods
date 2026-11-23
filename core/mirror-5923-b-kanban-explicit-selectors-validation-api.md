# Kanban explicit selectors, neutral readers and the published validation API

Mirror snapshot 5923; ordinals 5892, 5893.

Entries:

- **5892**: fix(kanban): harden explicit selectors and neutral readers
- **5893**: Publish strict Kanban validation API and isolate extraction tests

Builds on the earlier model-neutral Kanban routing (dynamic `class:` selectors) and public plugin API features.

## Behavior

- **Explicit selectors**: local REST PATCH/bulk and the tool-side request-review path now validate exact routing classes before any effect: a model must be exactly `class:fast`, `class:standard` or `class:deep` (returned as `(model, 'auto')`), an empty selector clears, and a concrete provider, a provider without a model, an unknown class, invalid types or a truthy mandatory flag is refused with the canonical model-neutral error. Create, the shared `validate_selector`/`validate_text` and the create wrapper are unchanged baselines.
- **Mandatory flags**: REST update/bulk refuses `pin_mandatory`, `model_pin_mandatory` and `require_model` unless the value is null, false or integer 0 (top level and inside `metadata`); refusal happens before any connection is opened and a bulk with an invalid shared patch touches no id. Falsy flags are accepted and not stored.
- **Dashboard model options** are advertised as the auto group of exact classes only.
- **Neutral readers**: standing-instruction promotion into worker context and hold decisions go through an opt-in restricted path that drops any candidate a recognized model mandate touches at any representation (raw line, quoted join, rendered message); raw comment history is unchanged and permitted lines are never evicted by refused ones. On a tier-resolution error the legacy selector fallback is normalized in memory, and a run's provider wall is never inferred from a raw stored provider override.
- **Public API (5893)**: `MANDATORY_FLAGS`, `validate_explicit_selector` and `validate_update_flags` are added to the name-scoped plugin API (with a new developer guide page), preserving whole-module and private routing rejection; routing, history projection and standing-instruction helpers stay private. Six new top-level extraction test files gained the unittest-discovery isolation guards the repository enforces, without dropping bodies or assertions.

## How it works

- `hermes_cli/kanban_model_neutral.py`: `validate_explicit_selector`, `validate_update_flags`, `standing_allowed`, `standing_candidates`, `standing_wait_words`; `agent/model_routing.py`: non-normalizing `exact_class_selector`; `agent/standing_instructions.py`: `restricted_explicit_lines`, `dedupe_candidates`; `agent/kanban_delegation_policy.py` uses the shared predicate.
- `tools/kanban_tools_model_neutral.py`: `model_neutral_review` guard registered ahead of worker guard, goal gate, source registration and staging; `tools/kanban_tools.py` holds-message helper; `plugins/kanban/dashboard/plugin_api.py` REST validation and options.
- `hermes_cli/kanban_model_tier.py`, `kanban_provider_walls.py`, `kanban_db_context.py` carry the reader changes. `hermes_cli/data/plugin_api.json` lists the published names.

## Code

Shipped non-test paths:

- `agent/kanban_delegation_policy.py`, `agent/model_routing.py`, `agent/standing_instructions.py`
- `hermes_cli/kanban_db_context.py`, `kanban_model_neutral.py`, `kanban_model_tier.py`, `kanban_provider_walls.py`
- `hermes_cli/data/plugin_api.json`
- `plugins/kanban/dashboard/plugin_api.py`
- `tools/kanban_tools.py`, `tools/kanban_tools_model_neutral.py`
- `website/docs/developer-guide/plugins/kanban-validation.md`, `website/docs/developer-guide/plugins/public-api.md`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/test_kanban_extraction141_rest.py` (strict flags and unknown classes refuse before any connection; PATCH and bulk cases; baseline controls; model options are the exact-class auto group)
- `tests/test_kanban_extraction141_review.py` (refusal before any effect, ordering against missing summary, valid classes reach handoff, registry entry is the guarded handler)
- `tests/test_kanban_extraction141_selectors.py`, `tests/test_kanban_extraction141_spawn_hook.py`
- `tests/test_kanban_subset130_readers.py` (no raw stored provider wall), `tests/test_kanban_subset130_standing.py` (restricted standing promotion)
- `tests/tools/test_kanban_review_entry148.py`, `tests/extraction142_evidence_plugin.py`
- `tests/hermes_cli/test_plugin_validate_private_imports.py` (published-name conformance, 5893)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- The strictness is opt-in at update/review boundaries; create, database and selfwork admission paths do not use it.
- The source describes this as a scoped subset of a larger model-neutral hardening goal; transport-wide, history-wide and fleet-level coverage were explicitly left open, so do not read these entries as complete enforcement.
- The REST path uses strict flag semantics while the review tool keeps base truthiness for mandatory flags; both are pinned by tests.
- Keep new public API names in `plugin_api.json` and the docs together or the repository-wide API guard fails.

## Plugin

Touches the in-tree Kanban dashboard plugin and the public plugin API surface; no external plugin install is needed.
