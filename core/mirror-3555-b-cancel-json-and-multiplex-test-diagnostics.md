# Update-pause cancelled-JSON handling and multiplex test diagnostics

Patches: **3540, 3545**.

## Behavior
- Update-pause: the executor's cancelled-tool JSON (status `cancelled`, error starting with "Tool execution cancelled") counts as no own result, so the bare pause stamp is committed. A tool's own JSON that merely says cancelled with other error text is still kept.
- Multiplex live test: the downtime-ensure test now asserts its precondition at READY and, on a miss, prints the multiplexer's served/parked maps, recorded fields and multiplex/error log lines, so a boot-time park names its cause. No runtime behavior changes in 3540.

## How it works
- 3545 extends the own-result check in the update-pause cancel module to recognise the JSON from `_ToolCallRef.emit_cancelled`.
- 3540 is test-only: the precondition is checked against the identify served set at READY, with diagnostics on failure.

## Code
- `agent/update_pause_cancel.py`
- `tests/gateway/test_multiplex_hot_serve_live.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_mid_tool.py`: `test_the_executors_cancelled_json_is_no_own_result`.
- `tests/gateway/test_multiplex_hot_serve_live.py`: modifies an existing test (no new test names); 3540 is diagnostics only and the original flake was not reproduced.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The CI flake behind 3540 was not reproduced, so its cause remains unknown.
