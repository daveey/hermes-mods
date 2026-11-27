# Kanban spend budget: window semantics, input checks and immediate reviewer

Patches: 6019, 6069, 6070, 6071, 6094, 6095, 6096, 6097.

## Behavior

- An ordinary spend-stop override counts a fresh window at promote, while restricted scope/safety history stays cumulative; historical spend is labeled separately and the window is shown in CLI JSON/help and budget comments.
- For the current window, the spend-budget CLI refuses a change leaving any nonzero limit at or below recorded spend, including board fallback fields omitted from the replacement override. Ordinary pending stops use a fresh window at promotion instead. The refusal names every tripped limit and suggests --exempt or explicitly larger limits; --clear is not advertised as a bypass.
- A spend reviewer judges a spend stop at once rather than waiting for an idle owner: queued stops are aged by their own notice, bounded end to end, accepted only through the exact subscription route, re-check the setting at grant, prune finished threads, and a one-shot dispatch waits for reviews it started.

## How it works

kanban_spend_budget(_cli), kanban_db_promote and kanban_spend_reviewer carry the logic; the reviewer runs in a context-copied thread bound to the owner scope.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_db_promote.py`
- `hermes_cli/kanban_dispatch_maintenance.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_budget_cli.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_spend_reviewer.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/hermes_cli/conftest.py`
- `tests/hermes_cli/test_kanban_spend_budget_below_spent.py`
- `tests/hermes_cli/test_kanban_spend_reviewer.py`
- `tests/hermes_cli/test_kanban_spend_window.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- A stop disposed of by a closed decision card is never queued or granted.
- Turning the reviewer off releases queued stops to the usual route.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban budget accounting and dispatch maintenance inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
