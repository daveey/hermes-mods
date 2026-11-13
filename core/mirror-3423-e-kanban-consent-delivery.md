# Kanban consent and delivery: verb-scoped negation, fail-closed provenance, stop lineage

Patches: **3217, 3218**.

## Behavior
- Claim negation is scoped to the claim verb: negation outside the verb still needs a receipt, and claimed targets are computed per verb.
- The home executor retries while creation provenance is unsynced and refuses when it never syncs (fail closed).
- A submitting session's stop is enforced and inherited; the snapshot uses the request-scoped session, not process environment.
- Stops survive compression-lineage rotation (3218). The worker `kanban_comment` tool honours the creation stop. A summary edit cannot add a delivery claim the receipt does not prove, but keeps one the admitted receipt proves.

## How it works
- 3217 extends `kanban_consent.py`, `kanban_operator_delivery.py`, `kanban_operator_call.py` and `kanban_supervisor_requests.py`.
- 3218 makes stop lookup follow lineage in `kanban_consent.py`, fences `kanban_comment` in `tools/kanban_tools.py`, and adds checks in `kanban_completion_receipt.py` and `kanban_db_edit.py` for summary edits.

## Code
- `hermes_cli/kanban_consent.py`
- `hermes_cli/kanban_operator_delivery.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_supervisor_requests.py`
- `hermes_cli/kanban_completion_receipt.py`
- `hermes_cli/kanban_db_edit.py`
- `tools/kanban_tools.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_consent_provenance.py` and `tests/hermes_cli/test_kanban_operator_delivery.py` (names added across both): `test_home_executor_retries_while_creation_provenance_is_unsynced`, `test_home_executor_refuses_when_creation_provenance_never_syncs`, `test_submitting_session_stop_is_enforced_and_inherited`, `test_snapshot_uses_request_scoped_session_not_process_env`, `test_negation_outside_the_claim_verb_still_needs_a_receipt`, `test_claimed_targets_scopes_negation_to_the_verb`, `test_stop_survives_compression_rotation`, `test_worker_kanban_comment_tool_honours_creation_stop`, `test_summary_edit_cannot_add_an_unproved_delivery_claim`, `test_summary_edit_keeps_a_claim_the_admitted_receipt_proves`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
