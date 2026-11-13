# Kanban: ship review verdicts must come from the dispatched worker run

Patches: **3020, 3021**.

## Behavior
- On a card whose body names a core-fork pull, a review result carrying a verdict, completed by a caller with no dispatched worker run for the card, is refused with rejection class `review_independence`. Tests cover the kanban_complete tool and the CLI (including `--force`).
- Non-review completions and dispatched-worker completions still complete.
- The dashboard "done" verb is wired to the same gate (a listed test covers the refusal).

## How it works
- 3020 adds `independence_refusal` to `kanban_review_contract.py`, checked before the shape refusal, and wires it into the tool, CLI and dashboard plugin.
- 3021 baselines the dashboard's import of the review contract in the boundary lint.

## Code
- `hermes_cli/kanban_review_contract.py`
- `hermes_cli/kanban.py`
- `tools/kanban_tools.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `scripts/boundary_lint_baseline.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_review_contract.py`: `test_independence_refuses_outside_verdict_on_ship_review`, `test_independence_leaves_non_reviews_alone`, `test_tool_refuses_outside_approval_on_ship_review`, `test_tool_outside_independence_precedes_shape_refusal`, `test_tool_dispatched_worker_completes_same_card`, `test_tool_outside_non_review_completions_still_complete`, `test_cli_refuses_outside_approval_on_ship_review`, `test_cli_outside_non_review_completion_still_completes`, `test_cli_from_the_cards_dispatched_worker_completes`, `test_dashboard_done_refuses_outside_approval_on_ship_review`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
