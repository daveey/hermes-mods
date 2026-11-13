# kanban_complete receipt schema: text pattern and size budget

Patches: **3309, 3325, 3351**.

## Behavior
- Every completion-receipt text field advertises the writer's real rule (nonblank, no C0 control characters other than tab/newline) as a JSON-schema `pattern`, so a strict client cannot build a value the card writer will refuse.
- The pattern is shorter, cutting the `kanban_complete` schema (sent on every worker request) from 11208 to 10878 bytes.
- A test budgets the schema in bytes rather than pattern length.

## How it works
- 3309 makes `TEXT_PATTERN` the single rule: `_text` fullmatches it and the tool schema advertises it.
- 3325 folds control, tab, newline and space into one range in the pattern and documents why `$ref` indirection is not used.
- 3351 replaces the pattern-length change detector with a byte budget on the serialized schema and corrects the comment about Gemini `$ref` handling.

## Code
- `hermes_completion_receipt.py`
- `tools/kanban_tools_schemas.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_complete_receipt_schema.py`: `test_text_fields_advertise_the_writers_nonblank_no_control_rule`, `test_text_pattern_is_exactly_the_writers_text_rule`, `test_kanban_complete_schema_stays_within_its_byte_budget`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The schema budget is 11000 bytes; growing the schema past it fails the test.
