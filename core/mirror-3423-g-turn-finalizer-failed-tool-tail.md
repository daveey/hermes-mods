# Turn finalizer: a failed turn with a tool tail keeps its reason

Patches: **3343**.

## Behavior
- A turn that stopped as `session_persistence_failed` with a tool row last is no longer relabelled `pending_tool_result`; the real reason stays, a WARNING is logged, and the tool tail is closed with the real reason's explainer (or the partial-failed-turn notice when the explainer is off).
- Advisory exit reasons with a tool tail still close as pending.

## How it works
- 3343 skips the silent-stop relabel in `agent/turn_finalizer.py` when the turn already failed.

## Code
- `agent/turn_finalizer.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_turn_finalizer_interrupt_alternation.py`: `test_persistence_failed_tool_tail_keeps_its_reason`, `test_no_failed_turn_with_tool_tail_is_relabelled_pending`, `test_advisory_exit_reason_with_tool_tail_still_closes_as_pending`, `test_persistence_failed_tool_tail_close_uses_the_storage_explainer`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
