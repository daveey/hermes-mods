# Kanban operator delivery: claim classifier and unproven-caller refusal

Patches: **3331, 3332**.

## Behavior
- A delivery sentence such as "No longer pending - posted to ..." counts as a claim and needs the exact operator receipt; "to be posted", "will be posted" and similar are never claims.
- Deferred calls take no tick slot, scanning continues past a full batch behind a deferral, and an unreachable Board stops paging.
- A call from a caller with no created event in the replica is refused rather than deferred; a future `requested_at` never defers a home refusal.

## How it works
- 3331 makes a spaced ASCII hyphen end the governing span, treats `be` as governing, and moves deferrals out of the tick budget.
- 3332 runs replica-only caller checks (`_caller_replica_refusal`) before home refusal, accepts `--` and U+2212 as dashes, and adds being/be governing.

## Code
- `hermes_cli/kanban_consent.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_operator_delivery.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_consent_provenance.py`: `test_deferred_calls_take_no_tick_slot`, `test_scan_continues_past_a_full_batch_behind_a_deferral`, `test_unreachable_board_stops_paging_behind_a_deferral`, `test_caller_refusal_wins_over_a_deferral_refusal`, `test_without_a_deferral_one_tick_reads_one_batch`, `test_unproven_caller_is_refused_not_deferred`, `test_future_requested_at_never_defers_a_home_refusal`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
