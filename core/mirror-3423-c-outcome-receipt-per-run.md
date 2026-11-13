# Per-invocation outcome receipts for queued follow-ups

Patches: **3090**.

## Behavior
- Each `_run_agent` invocation opens its own outcome receipt, so a queued follow-up owns the concluding receipt and a late write from an abandoned predecessor can only write its own receipt.
- Abandonment captures only a lease the worker holds now.

## How it works
- 3090 has the recursive `_run_agent` open a fresh receipt and make it the handler's concluding one; `concluding_turn_identity` drops unused parameters.

## Code
- `gateway/run_turn.py`
- `gateway/run_turn_wait.py`
- `gateway/turn_outcome.py`
- `tui_gateway/prompt_turn.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_outcome_followup_receipts.py`: `test_followup_owns_the_concluding_receipt_despite_a_late_predecessor_write`, `test_abandoned_worker_never_drains_and_names_only_its_own_lease`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
