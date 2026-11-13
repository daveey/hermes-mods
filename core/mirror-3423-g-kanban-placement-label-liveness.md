# Kanban placement: whole-label-set liveness and promotion keep reasons

Patches: **3334, 3335, 3359**.

## Behavior
- A card with no marker queues only when one live machine holds both the queue and the coordination labels.
- Placement recovery promotion applies the hub claim screen's keep reasons (hub-local wake check, hub worktree, host_repo); an unreadable card is kept.

## How it works
- 3334 builds the full runs_on set once and judges it by one liveness source (the `machine_ok` seam or `_machine_picks_up` with a label tuple).
- 3335 annotates `label_machines` as `str | Iterable[str]`.
- 3359 makes `promotion_mode` call `keep_reason` before proposing promote; the fake Board can serve recover.

## Code
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_recovery.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_no_marker_card_queues_only_when_one_live_machine_holds_queue_and_coordination_labels`, `test_machine_ok_seam_judges_the_whole_label_set_on_both_paths`.
- `tests/hermes_cli/test_kanban_hub_admission_claim.py`: `test_promotion_keeps_a_card_with_a_hub_local_wake_check_on_the_hub`, `test_promotion_still_moves_a_portable_hub_card_to_the_pool`.
- `tests/hermes_cli/test_kanban_placement_recovery.py`: `test_promotion_applies_the_claim_screens_keep_reasons`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
