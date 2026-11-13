# Gateway turn outcome: run-owned custody

Patches: **3022–3027**.

## Behavior
- A turn's outcome evidence is frozen to its own run: retries target the frozen session id after agent replacement, an unreadable marker warns and preserves unknown marker identity, and a displaced handler uses its worker receipt, never a successor's slot.
- Abandoned worker waits (timeout or cancel) capture the run's own live lease before the handler refresh, including queued follow-ups; finished worker receipts are preserved against cached agent reuse; absent session ids do not refresh.

## How it works
- 3022 freezes outcome evidence in `gateway/turn_outcome.py`, `run_turn.py`, `run_turn_runner.py` and `tui_gateway/prompt_turn.py`.
- 3023 keeps scope cleanup in the topical helper.
- 3024 captures custody on abandoned waits; 3025 moves the worker wait into `gateway/run_turn_wait.py` to keep the facade file cap.
- 3026 gates abandonment capture on this executor future instead of a predecessor receipt.
- 3027 gives host-sleep poll fixtures a real executor future.

## Code
- `gateway/turn_outcome.py`
- `gateway/run_turn.py`
- `gateway/run_turn_runner.py`
- `gateway/run_turn_wait.py`
- `tui_gateway/prompt_turn.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_state/test_outcome_run_custody.py`: `test_retry_targets_frozen_sid_after_agent_replacement`, `test_marker_read_failure_warns_and_preserves_unknown_marker`, `test_displaced_handler_uses_worker_receipt_not_successor_slot`, `test_no_run_scope_never_names_successor`, `test_abandoned_worker_captures_own_live_lease`, `test_pending_capture_preserves_finished_receipt`, `test_null_sid_does_not_refresh`, `test_finished_worker_preserves_frozen_receipt`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
3025 is a pure code move with no tests of its own.
