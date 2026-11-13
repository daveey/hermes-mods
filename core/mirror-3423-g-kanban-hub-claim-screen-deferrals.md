# Hub claim screen: named deferrals and origin-aware skip wording

Patches: **3312, 3317, 3330, 3336**.

## Behavior
- Cards the hub claim screen leaves unclaimed in a tick are named in the dispatch result, in `kanban dispatch` output (JSON and text paths, per tests), the stuck warning and the screen's log line.
- The screen re-reads each card's row at its turn, so a write to a later card during an earlier judge call no longer defers it, and a card that stopped being screenable is skipped.
- A claim deferral carries its origin (local gate or Board), and the skip wording reads it instead of matching a list of name prefixes.

## How it works
- 3312 adds `DispatchResult.admission_deferred` and the per-turn row re-read.
- 3317 and 3330 are test-support refactors: a shared `hub_admission_support.py`, sibling suites importing it rather than another test module; 3317 also names the no-JSON1 fallback in `_running_rows`.
- 3336 makes `ClaimDeferral` a str subclass with an origin field stamped where the deferral is produced and read by `kanban_dispatch_skip`.

## Code
- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_stuck.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_hub_admission_claim.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/machine_metrics.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_hub_admission_claim.py`: `test_deferred_ids_appear_in_the_dispatch_result`, `test_print_deferred_names_the_cards`, `test_a_write_to_a_later_card_during_an_earlier_judge_call_does_not_defer_it`, `test_a_later_card_that_stops_being_screenable_is_not_judged`.
- `tests/hermes_cli/test_kanban_claim_deferral_origin.py`: `test_a_local_gate_deferral_is_not_worded_as_a_board_deferral`, `test_a_board_lease_refusal_is_worded_as_the_board_whatever_its_name`, `test_the_origin_rides_the_recorded_skip_and_the_notice_fields`, `test_a_skip_without_an_origin_reads_as_the_board`.
- `tests/hermes_cli/test_kanban_dispatch_skip.py`: `test_claim_deferred_words_name_who_deferred_it`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A skip with no origin reads as the Board.
