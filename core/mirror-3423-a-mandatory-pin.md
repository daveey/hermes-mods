# Kanban: mandatory model pins on the worker pool

Patches: **2983, 2984, 2985**.

## Behavior
- A card with a mandatory model pin can ride the worker pool; the claiming worker runs exactly that model or refuses, naming why; a refused pin is never cleared to the default.
- Old-core pool members keep such cards at home; a mandatory pin never goes worker-first, and moves/re-places keep its label and need an attested target; the courier still cannot carry it.
- Marking a placed card mandatory adds the capability label; a worker-first card refuses the mark unchanged.

## How it works
2983 adds `kanban_mandatory_pin` and stops pool portability from reusing the courier's uncarried options; 2984 routes leaving home only through the worker queue; 2985 makes `mark()` union the label into `runs_on`.


## Code
- `hermes_cli/kanban_hub_admission_pickup.py`
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_mandatory_pin.py`
- `hermes_cli/kanban_model_assignment.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_pin_reroute.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_placement_diagnostics.py`
- `hermes_cli/kanban_placement_recovery.py`
- `hermes_cli/kanban_runs_on_capabilities.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_mandatory_pin.py`: `test_a_claim_on_an_unattested_destination_is_refused`, `test_a_class_pin_is_refused_where_class_routing_is_off`, `test_a_denied_mandatory_pin_with_no_queue_is_refused_not_sent_worker_first`, `test_a_home_card_waiting_on_the_attestation_is_named_stranded_once_the_pool_attests`, `test_a_hub_pickup_re_place_keeps_the_label`, `test_a_mandatory_pin_card_is_enrolled_for_the_attested_pool`, `test_a_mandatory_pin_never_enrolls_worker_first`, `test_a_mark_without_a_model_pins_nothing`, `test_a_move_keeps_the_label_and_needs_an_attested_target`, `test_a_refused_mandatory_pin_is_never_cleared_to_the_profile_default`, `test_a_worker_that_cannot_serve_the_pin_refuses_the_claim`, `test_a_worker_that_serves_the_pin_claims_it_and_runs_exactly_that_model`, `test_an_old_core_pool_keeps_the_mandatory_pin_card_home`, `test_carry_leaves_a_pin_to_this_machine_alone`, `test_marking_a_placed_card_mandatory_adds_the_label`, `test_marking_a_worker_first_card_mandatory_is_refused_and_changes_nothing`, `test_marking_needs_a_pin_and_a_live_card`, `test_recovery_never_promotes_a_home_mandatory_pin`, `test_refusal_names_why_the_exact_pin_cannot_run_here`, `test_the_courier_still_cannot_carry_a_mandatory_pin`.
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`: `test_the_alarms_pool_move_keeps_a_mandatory_pins_label`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Attestation of the capability is required before a destination accepts the card.
