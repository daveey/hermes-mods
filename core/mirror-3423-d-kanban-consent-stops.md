# Kanban consent stops and operator delivery receipts

Patches: **3155**.

## Behavior
- A typed human timeout or deny at the approval boundary on a direct `hermes kanban comment <id>` command is recorded and stamped onto every card the session (or descendant cards) creates, as `created.consent_stops`. Supervisor submission and the home executor refuse an operator comment on a stopped target (`consent_stop`), across hosts.
- Technical outcomes never stop or grant; an ordinary user turn never clears a stop; only an explicit same-session, same-target approval supersedes it. A store failure fails closed without blocking an approved command.
- Delivery claims bind to exact operator receipts: completion cannot claim delivery with an unrelated comment, forged or changed replica receipts and requests are rejected, and non-terminal, failed or rejected requests are not delivery.

## How it works
- 3155 adds `kanban_consent` (provenance in creation) and approval-context carrying, plus canonical envelope, request/result and completion-receipt checks in the operator call/delivery modules.

## Code
- `hermes_cli/kanban_consent.py`, `hermes_cli/kanban_db_creation.py`, `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_operator_call.py`, `hermes_cli/kanban_operator_delivery.py`, `hermes_cli/kanban_supervisor_requests.py`, `hermes_cli/kanban_completion_receipt.py`, `hermes_completion_receipt.py`
- `tools/approval_context.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_consent_provenance.py`: `test_real_gateway_settlement_carries_consent_to_worker`, `test_consent_binds_command_target_not_body_mentions`, `test_technical_or_machine_refusal_is_not_human_nonconsent`, `test_user_turn_does_not_clear_stop_and_child_inherits`, `test_comment_stop_does_not_deny_unrelated_action`, `test_home_executor_enforces_creation_stop_from_old_worker`, `test_timeout_creation_provenance_refuses_cross_host_relay`, `test_explicit_same_target_approval_supersedes_stop`, `test_other_target_or_machine_approval_never_grants`, `test_store_failure_fails_closed_without_blocking_an_approved_command`.
- `tests/hermes_cli/test_kanban_operator_call.py`: `test_transport_requires_exact_canonical_envelope`, `test_changed_replica_request_never_executes`, `test_changed_replica_result_cannot_finish_call`, `test_completion_cannot_claim_delivery_with_unrelated_comment`, `test_exact_receipt_admitted_at_canonical_completion`, `test_forged_replica_receipt_rejected`, `test_nonterminal_or_failed_request_is_not_delivery`.
- `tests/hermes_cli/test_kanban_operator_canonical.py`: `test_canonical_request_must_bind_delivery`, `test_canonical_result_must_attest_exact_application`, `test_canonical_event_identity_required`, `test_rejected_disposition_cannot_prove_delivery`, `test_executor_readback_rejects_changed_comment`, `test_malformed_delivery_schema_rejected`, `test_applied_result_carries_exact_delivery_identity`.
- `tests/hermes_cli/test_kanban_operator_delivery.py`: `test_non_delivery_wording_is_not_a_claim`, `test_pending_request_cannot_be_claimed_as_delivered`, `test_local_card_comment_claim_stays_outside_operator_gate`, `test_own_synced_comment_on_foreign_card_stays_outside_operator_gate`, `test_other_authors_synced_comment_is_not_own_delivery`, `test_own_synced_comment_never_replaces_a_filed_request_receipt`, `test_unreachable_board_refuses_delivery_proof_cleanly`.
- `tests/hermes_cli/test_kanban_operator_labels.py`, `tests/test_kanban_hub_admission_audit.py`: changed; no new test names read.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Stops are carried by creation provenance, so a card created by an old worker is still enforced by the home executor.
