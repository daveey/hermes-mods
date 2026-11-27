# Explicit Carbon routing and model-neutral obligations

Patch coverage: 6264, 6265, 6266, 6267, 6288, 6289, 6335, 6341, 6375.

## Behavior

class:carbon is an explicit-only routing class, available through intentional card/review selection and authenticated picker pins. Automatic fast/standard/deep capacity, ratio routing, fallback and quota alternatives cannot silently select Carbon. A newer review handoff takes precedence over the card selector.

Model-neutral policy rejects requested named execution roles and mandatory model flags without dropping non-model review/CI obligations. Historical directive retirement preserves rejected wording as evidence and keeps the neutralized obligations active.

## How it works

Eligible-route filtering separates automatic classes from Carbon routes, with request metadata and outbound fences still validating exact identity/capability. Picker APIs expose eligible explicit pins without admitting other metered routes as ordinary subscription pins.

Neutralization uses the unchanged ingress recognizer to choose only identity spans that must be replaced. Markdown-rendered spans map back to original bytes; exclusions, provenance and receipt predicates are retained. Unknown mixed obligations refuse instead of being silently inactivated. Named-role grammar recognizes bounded requested role slots, not arbitrary model mentions. REST create retains mandatory flag fields through parsing, rejects active values before writes, and excludes inert flags from DB kwargs. Pure pre-dispatch policy refusals are definitive no-create courier results; downstream untyped errors are not promoted to that guarantee.

## Code

Affected implementation and integration paths in the source series:

- `agent/model_routing.py`
- `agent/model_routing_managed.py`
- `agent/model_routing_request.py`
- `apps/desktop/src/plugins/kanban/model-override.tsx`
- `hermes_cli/kanban_model_neutral.py`
- `hermes_cli/kanban_model_retirement.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_pin_reroute.py`
- `hermes_cli/kanban_quota_episode.py`
- `hermes_cli/kanban_ratio_routing.py`
- `hermes_cli/kanban_review_model.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `tools/kanban_tools_model_neutral.py`
- `tools/kanban_tools_schemas.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/agent/test_carbon_routing.py`
- `tests/hermes_cli/test_carbon_dispatch.py`
- `tests/hermes_cli/test_carbon_picker.py`
- `tests/hermes_cli/test_kanban_model_retirement_preserve.py`
- `tests/hermes_cli/test_model_neutral_review_grammar.py`
- `tests/hermes_cli/test_kanban_model_neutral_courier.py`
- `tests/test_kanban_create_flags265.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Explicit-only is not an entitlement to an unqualified route: attestation/capability and identity fences still apply. Null, false and integer zero flags are inert, not generally truthy aliases. Model mentions in receipts/provenance must not become accidental routing obligations.

## Why core, not a plugin?

Routing, outbound request policy, card ingress and courier no-write semantics must agree at core boundaries, not just in picker UI.
