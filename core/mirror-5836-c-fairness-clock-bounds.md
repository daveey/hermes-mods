# Kanban fairness journal: reject overflowing clock bounds (mirror 5638–5640)

## Behavior
- Capture arming rejects `clock_error_ns` outside `0 <= value < 2**63` (and non-integer values); the writer-clock CLI validates the scaled seconds before activating a capture.
- An unserializable BLOB task id produces an `export_failed` gap with detail `TypeError`; export does not advance past it.
- Provenance branches of the journal are covered by dedicated tests.

## How it works
`hermes_cli/kanban_fairness_journal.py` validates the capture clock-error bound before arming; `scripts/kanban_fairness.py` validates the CLI arm argument; the BLOB regression exercises the existing store drain error handling. The follow-up test commits exercise the recursive JSON decoder in a portable way (not depending on the interpreter's recursion limit) and cover the BLOB case.

## Code
- `hermes_cli/kanban_fairness_journal.py`
- `scripts/kanban_fairness.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_fairness_clock_bounds.py`: `test_out_of_sqlite_range_clock_bound_refuses_before_replace` preserves the original capture; `test_cli_invalid_clock_bound_is_tool_error_without_capture` invokes the CLI main function and checks exit 3/no plan; `test_blob_task_id_reports_type_error_gap_without_advancing` checks an export gap and unchanged sequence. `test_deep_persisted_clock_reports_gap_without_advancing` selects the Python recursive decoder explicitly. A spawned CLI process is untested by these cases.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. No configuration.

## Gotchas
- Overflow checks are about integer width; do not widen the accepted range to make a failing fixture pass.
- Recursive-decoder tests must stay portable across interpreters.

## plugin?
No. Core change.
