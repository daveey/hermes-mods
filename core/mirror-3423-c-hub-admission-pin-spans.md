# Hub admission: pin-line self-assertion and hub-admin placement

Patches: **3059, 3064, 3092, 3100**.

## Behavior
- An admit whose only evidence is a quote from inside a producer's pin line (including wrapped continuation lines) is denied as self-assertion; relayed owner denies carry that reason.
- Abbreviation dots (letter-dot forms such as `e.g.`) extend a pin's span; filenames, versions, URLs, `no.` and ellipses end it.
- A card whose text names a hub action that the pickup claim gate refuses on every pool worker stays home instead of being routed to the worker queue.
- A hub action inside an open `hub-admin '...'` quote needs the hub-write capability rather than being refused; bare spellings stay home.

## How it works
- 3059 adds pin-line overlap detection in `validated()` and a specific why in `stored_owner_verdict()`.
- 3064 and 3092 refine how the wrapped pin span ends at sentence boundaries.
- 3100 changes `pool_route` to keep claim-gate-refused cards home and `requirements()` to treat hub-admin-wrapped actions as `capability:hub-write`.

## Code
- `hermes_cli/kanban_hub_admission.py`
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_placement_coordination.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/test_kanban_hub_admission.py`: `test_quote_from_inside_a_pin_line_is_not_admission_evidence`, `test_quote_also_found_outside_the_pin_line_is_judged_on_that_text`, `test_stored_owner_verdict_quoting_a_pin_sentence_is_a_final_deny_with_its_why`, `test_pin_span_ends_at_the_real_sentence_end_after_an_abbreviation`, `test_pin_ending_in_a_dotted_token_does_not_swallow_the_next_line`.
- `tests/tools/test_kanban_move_hub_guidance.py`: `test_move_guidance_routes_hub_reads_and_writes_through_worker_access`, `test_move_guidance_discourages_a_hub_move_and_gates_it_on_admission`.
- `tests/test_kanban_hub_admission_placement.py`: `test_hub_admin_wrapped_action_needs_hub_write`, `test_bare_or_unwrapped_action_still_stays_home`, `test_claim_stall_template_no_worker_can_claim_stays_home`, `test_claim_stall_template_via_hub_admin_queues_for_hub_write_workers`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The whole-line span and run-to-end-of-field for an unterminated pin are deliberate and fail safe.
