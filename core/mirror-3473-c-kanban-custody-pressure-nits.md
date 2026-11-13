# Kanban scratch custody: pressure API fixes

Patches: **3465**.

## Behavior
- `terminal_owner()` returns `None` for a malformed receipt (instead of raising `ValueError`/`KeyError`/`TypeError`), before any Board read. Board transport errors still propagate.
- `pressure_candidates()` skips trees outside managed scratch, so the disk-pressure adapter no longer builds a full archive for a tree the removal guard always refuses.

## How it works
3465 wraps the receipt parse in `kanban_scratch_custody.py` so malformed input yields no owner, and makes `_pressure_candidate` check `ws._is_managed_scratch_path` locally.

## Code
- `hermes_cli/kanban_scratch_custody.py`
- `tests/hermes_cli/test_kanban_scratch_custody.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_custody.py`: `test_pressure_terminal_owner_malformed_receipt_is_none_before_board`, `test_pressure_candidates_skip_tree_outside_managed_scratch`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
