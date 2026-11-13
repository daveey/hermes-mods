# Kanban placement: no worker-first card that no pool worker can claim

Patches: **3625**.

## Behavior
- An "Existing work checked" record in a card body (a heading section up to the next heading, or a bold-label paragraph up to the next blank line) is treated as evidence and no longer counts as a coordination action; the same action outside the record still keeps the card home.
- A card whose coordination labels are refused is not enrolled worker-first.
- A gateway with a worker-queue label never enrolls worker-first on a queue miss; the card stays home under legacy routing. A gateway without a queue still enrolls claimable cards in the pool.
- Prevents cards sitting ready and unclaimed while being re-reserved every tick.

## How it works
- 3625 extends `without_evidence()` to strip the record; it feeds the portability scan, `required_labels` and `claim_refusal`. `creation_decision` refuses worker-first when the gateway has a worker_queue label or when `required_labels` is None. A prior pool-queue test was rewritten to the new contract.

## Code
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_existing_work_checked.py`: `test_the_original_card_body_needs_no_coordination_label`, `test_an_existing_work_record_is_stripped_up_to_the_next_heading_or_paragraph`, `test_the_same_action_outside_the_record_still_stays_home`, `test_a_coordination_refused_card_is_not_enrolled_worker_first`, `test_no_create_gets_worker_first_when_every_pool_member_would_refuse_its_claim`, `test_a_claimable_card_still_enrolls_on_a_pool_gateway_without_a_queue`, `test_a_queue_gateway_keeps_a_queue_miss_home_instead_of_worker_first`, `test_direct_create_task_keeps_a_queue_miss_home_instead_of_pool_enrollment`.
- `tests/hermes_cli/test_kanban_pool_queue.py`: modified (rewritten pool-enrollment test).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The record is stripped only by heading or bold-label shape; other phrasing of past searches is still scanned.
