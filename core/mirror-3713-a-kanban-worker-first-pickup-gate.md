# Kanban worker-first: never reserve a card every worker refuses

Patches: **3696**.

## Behavior
- A card enrolled worker-first whose text the pickup gate reads as a home-only coordination action (for example a quoted hub CLI command in backticks), or whose `requires` is malformed, is no longer reserved repeatedly for workers that cannot claim it.
- The placement tick records queue reason `capability_mismatch` once (one comment quoting the matched text) and repins an unstarted ready card to its origin machine. A repin refused by the Board is not re-asked at the same revision. A started card is left alone without reserves.
- A Board candidate whose pickup gate refuses the card is annotated `pickup_gate_refused` (with missing labels) and never shown as eligible.
- Label mismatches stay with the Board's own `origin` return and the card is still reserved. Unknown members, an unreadable registry, held/stale members and an empty pool are never refusals.

## How it works
3696: adds `kanban_worker_pickup_gate` and a shared verdict (`pool_refusal` / `pickup_refusals`) in `kanban_placement_coordination`; `requirements()` also reports the matched span internally with unchanged return shape. `kanban_worker_placement.reevaluate` consults it via `_repin_target` / `origin_target`, with the not_claimable backoff moved to `_backed_off`. `kanban_queue_reason` annotates gate-refused candidates and only emits the capability_mismatch text on a new transition. Creation-time enrollment was handled by an earlier change; this covers cards still enrolled.

## Code
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_queue_reason.py`
- `hermes_cli/kanban_worker_pickup_gate.py`
- `hermes_cli/kanban_worker_placement.py`
- `tests/hermes_cli/test_kanban_worker_first_pickup_gate.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_worker_first_pickup_gate.py`: `test_an_edited_enrolled_body_still_hits_the_gate`, `test_an_enrolled_card_every_pickup_gate_refuses_is_repinned_to_its_origin_once`, `test_a_started_card_every_gate_refuses_is_never_reserved_and_noted_once`, `test_origin_target_ignores_a_card_not_enrolled_worker_first`, `test_a_board_refused_origin_repin_is_not_asked_again_at_that_revision`, `test_a_card_the_gate_accepts_is_enrolled_and_reserved_as_today`, `test_a_plain_prose_mention_is_not_an_action`, `test_label_refusal_needs_every_known_candidate_to_lack_the_labels`, `test_invalid_requires_refuses_everywhere`, `test_a_label_mismatch_is_left_to_the_board_and_still_reserved`, `test_the_queue_reason_never_lists_a_refusing_candidate_as_eligible`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Builds on the earlier creation-time enrollment guard; without it new cards can still be enrolled.
- Only machine-state-independent verdicts are acted on.
