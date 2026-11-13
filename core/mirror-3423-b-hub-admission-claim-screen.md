# Hub admission: claim-time screening and evidence rules

Patches: **2996, 3044, 3047–3050**.

## Behavior
- Hub admission refusals name complete, conditional assignee remedies (the default assignee when omitted; non-pool assignees alongside explicit labels).
- A producer's own "Pinned to the gateway that filed it" sentence is never accepted as admission evidence, including cached verdicts and the machine variant; other evidence on the same card still admits. The kanban_move tool description routes hub work through hub-admin.
- Before the claim pass, the hub judges each ready, label-less card that has no verdict and records a `hub_admission` event with `entry=claim`: admit is claimed as before; deny re-places the card on the worker queue (or keeps it where it must stay, still recorded). An unavailable judge fails open once per tick without recording a verdict.
- A card or event change between judging and applying invalidates the decision.

## How it works
- 2996 makes the remedy text in placement refusals complete and conditional on the assignee contract.
- 3044 makes `validated()` reject an admit whose quote is a producer's pin sentence and updates the move tool schema text.
- 3047 adds `hermes_cli/kanban_hub_admission_claim.py` (`screen`, called from the worker_pickup seam outside any write transaction), a doctor detector for unjudged claims and a sync hook.
- 3048 and 3049 add coverage for change-while-judged, empty-label and policy candidates and detector edges (creation-time verdicts, same-second gating).
- 3050 fences the claim decision against concurrent card and event changes before applying.

## Code
- `hermes_cli/kanban_hub_admission_claim.py`
- `hermes_cli/kanban_hub_admission.py`
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/doctor.py`
- `hermes_cli/doctor_state.py`
- `hermes_cli/kanban_db_sync.py`
- `tools/kanban_tools_schemas.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/test_kanban_hub_admission_placement.py`: `test_omitted_assignee_remedy_names_default`, `test_explicit_labels_remedy_also_names_non_pool_assignee`, `test_assignee_remedy_access_is_optional_and_uses_hub_contract`.
- `tests/test_kanban_hub_admission.py`: `test_producer_pin_sentence_is_not_admission_evidence`, `test_machine_variant_and_cached_escape_are_denied`, `test_other_evidence_on_a_card_with_a_pin_sentence_still_admits`.
- `tests/tools/test_kanban_move_hub_guidance.py`: `test_move_description_routes_hub_work_through_hub_admin`.
- `tests/hermes_cli/test_kanban_hub_admission_claim.py`: `test_a_denied_pre_gate_hub_card_moves_to_the_worker_queue_and_is_not_claimed`, `test_an_admitted_pre_gate_hub_card_is_claimed_with_its_verdict`, `test_a_denied_card_that_must_stay_is_claimed_and_kept_recorded`, `test_a_portable_wake_check_does_not_keep_a_denied_card`, `test_the_newest_wake_check_decides`, `test_a_deny_with_no_worker_queue_is_kept_and_claimed`, `test_an_unavailable_judge_fails_open_once_per_tick`, `test_an_unavailable_verdict_is_not_recorded`, `test_a_card_with_any_verdict_makes_no_model_call`, `test_a_creation_time_verdict_makes_no_model_call`, `test_a_non_hub_gateway_makes_no_model_call`, `test_labelled_and_policy_cards_are_not_screened`, `test_screen_makes_no_model_call_inside_a_write_transaction`, `test_a_card_claimed_or_relabelled_while_judged_is_not_moved`, `test_unjudged_claims_counts_hub_claims_without_a_prior_verdict`, `test_doctor_flags_unjudged_hub_claims`, `test_doctor_hub_claim_check_is_silent_off_the_hub`, `test_an_empty_label_list_is_screened_like_no_labels`, `test_a_card_changed_while_judged_is_left_to_its_new_owner`, `test_unjudged_claims_is_empty_before_the_gate_and_ignores_later_verdicts`, `test_unjudged_claims_accepts_a_creation_time_verdict_without_an_admission_event`, `test_event_change_before_apply_invalidates_the_decision`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The doctor detector is described in the patch but only exercised through the claim tests' detector cases.
