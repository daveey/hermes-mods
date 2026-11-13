# One-shot iteration-budget checkpoint and cap logging

Patches: **3461**.

## Behavior
- Headless one-shot runs (`chat -q --max-turns N`) with no `budget_warning_ratio` configured now get an iteration-budget notice by 80% of the budget. The notice names the remaining iterations and says tools are removed at the cap. Kanban workers keep their 0.9 default.
- Interactive runs without a ratio still get no notice; a configured ratio wins over the one-shot default.
- When the cap is hit, the turn finalizer logs one countable line: `iteration_cap_exhausted source=<src> oneshot=<bool> warned=<bool> used=N/M`.
- Only the agent-level behavior is covered by tests; the `chat -q` CLI entry path itself is untested here.

## How it works
3461 changes `agent/turn_iteration_prep.py` to default the ratio to 0.8 for one-shot runs, and adds the cap-exhaustion log line in `agent/turn_finalizer.py`.

## Code
- `agent/turn_iteration_prep.py`
- `agent/turn_finalizer.py`
- `tests/agent/test_iteration_budget_warning.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_iteration_budget_warning.py`: `test_oneshot_without_ratio_gets_one_notice_by_80_percent`, `test_interactive_without_ratio_still_gets_no_notice`, `test_configured_ratio_wins_for_oneshot`, `test_cap_exhaustion_logs_one_countable_line`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
