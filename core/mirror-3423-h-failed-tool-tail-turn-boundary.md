# Failed tool-tail turns close with the typed failed-turn row

Patches: **3382**.

## Behavior
- A failed turn that ends on a tool row (for example `session_persistence_failed`) is closed with a row stamped `display_kind=failed_turn` carrying the same failure display metadata the failed-turn boundary writer derives from the turn result, so Desktop can draw it as a failed turn (error card).
- The boundary writer adds no duplicate row.

## How it works
- 3382 stamps the close row written by `_close_failed_tool_tail` in `agent/turn_finalizer.py` with the failed-turn display kind and `_failed_turn_display_metadata`; the result's error/failure_reason stamping moves into `_stamp_exit_failure` so row and result share one derivation.

## Code
- `agent/turn_finalizer.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_turn_finalizer_interrupt_alternation.py`: `test_persistence_failed_tool_tail_close_is_the_failed_turn_boundary` (the source message says it covers the explainer on and off).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
