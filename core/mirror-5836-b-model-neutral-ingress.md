# Model-neutral Kanban ingress and legacy routing

Source patch ordinals: 5566–5568, 5581, 5584–5585.

## Behavior

New Kanban requirements accept class/tier or capability requirements, not concrete model/provider restrictions or mandatory pins. Legacy stored/synced restrictions remain audit evidence but do not control future routing. Interactive chat model selection and an already-running client are unchanged.

## How it works

`validate_selector`, `validate_text` and `validate_review` guard common DB mutations, CLI, agent-tool create, self-work transport and dashboard ingress. `routing_view` creates a non-mutating next-admission projection; `effective_selector` supplies a class fallback. Tier/pacing/placement consumers use that projection while concrete execution receipts remain historical facts. Desktop offers `class:fast`, `class:standard`, `class:deep` and effort rather than a concrete catalog. Origin forwarding uses an explicit field allowlist so imported implementation objects cannot enter creation receipts.

## Code

- `hermes_cli/kanban_model_neutral.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_edit.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_lane_classifier.py`
- `hermes_cli/kanban_model_tier.py`
- `tools/kanban_tools_model_neutral.py`
- `tools/kanban_tools_schemas.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `apps/desktop/src/plugins/kanban/model-override.tsx`
- `website/docs/user-guide/configuration.md`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_no_model_pins.py` covers mutation atomicity and selector/review validation. The `test_kanban_model_neutral_legacy.py`, `test_kanban_model_neutral_transport.py` and `test_kanban_model_neutral_text.py` suites cover CLI preflight, legacy reads, self-work/forwarding and text policy. `tests/plugins/test_kanban_model_neutral_entries.py` covers REST and agent-tool refusal; Desktop `model-override.test.tsx` covers class-only payloads and historical-pin display. `test_kanban_create_entry_parity.py` and `test_model_whitespace_entry_parity.py` retain creation-entry and spaced-ID boundaries; consumer fixtures move to eligible class policies.

## Applying

Apply the ingress, projection and consumer changes as one ordered feature family, including later validation, eligibility and review corrections documented separately. Generic example: `hermes kanban create --model class:deep --provider auto`. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

`kanban.pin_quota_reroute_min_hours` remains legacy-helper compatibility, not a delay or mandatory-pin escape hatch for dynamic routing. Clearing old restrictions with `hermes kanban set-model <id> none --no-mandatory` affects future dispatch only. Later review policy explicitly uses separate execution, not model diversity; do not copy superseded early guidance.

## plugin?

Core policy with built-in plugin consumers. A plugin cannot bypass common mutation validation by writing its own title/body path.
