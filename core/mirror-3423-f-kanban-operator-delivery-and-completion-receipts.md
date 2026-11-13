# Kanban operator delivery claims and completion-receipt schema

Patches: **3242, 3252, 3260, 3281**.

## Behavior
- A delivery claim such as "No longer pending: posted to <card>" is classified as a claim (and needs the exact operator receipt): the negation window now ends at a colon, em/en dash or open parenthesis. "not posted", "queued", "failed and was not sent" stay non-claims.
- In the home executor, a call whose caller's created event has not reached the replica is deferred alone; the scan continues past it, and the watermark stays below it so it is retried next tick.
- The `completion_receipt` schema advertised to clients lists the optional `evidence.operator_delivery` object with the exact ten-field strict shape (`additionalProperties: false`, required keys, per-field text cap 128), says `observed_at` must be timezone-aware ISO-8601, and states the 32768-byte cap on canonical receipt JSON.
- An operator completing an in-review card while passing a run id is told to omit it, instead of only being told the run is not current.

## How it works
- 3242 edits the claim classifier and the operator-call/home-executor scan (`hi = eid - 1; break` replaced by deferring just that call).
- 3252 names `MAX_BYTES` and lists `operator_delivery` in the schema; 3281 introduces `OPERATOR_DELIVERY_FIELDS` / `OPERATOR_DELIVERY_TEXT_MAX`, used by both the validator and the schema generator.
- 3260 extends the operator-lane wording in `require_review`.

## Code
- `hermes_cli/kanban_consent.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_operator_delivery.py`
- `hermes_completion_receipt.py`
- `tools/kanban_tools_schemas.py`
- `hermes_cli/kanban_review_required.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_consent_provenance.py`: `test_unsynced_caller_defers_only_its_own_call` (the deferral).
- `tests/hermes_cli/test_kanban_operator_delivery.py`: extended by 3242 with claim-window cases (no new test names read).
- `tests/hermes_cli/test_kanban_complete_receipt_schema.py`: `test_evidence_advertises_the_optional_operator_delivery_the_validator_admits`, `test_description_states_timezone_and_byte_cap`, `test_operator_delivery_schema_is_the_validators_strict_shape`.
- `tests/hermes_cli/test_kanban_review_required_wording.py`: `test_operator_stale_run_refusal_names_the_remedy`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3242's classifier change is covered only by assertions added to existing tests; no new test name is dedicated to it.
