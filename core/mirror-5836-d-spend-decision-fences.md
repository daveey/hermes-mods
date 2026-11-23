# Cause-specific spend holds and atomic decision acknowledgement

Source patch ordinals: 5744–5745, 5833.

## Behavior

Scope stops and provider safety refusals retain cause-specific diagnostics. A budget decision is not safety clearance. Concurrent observers cannot silently stack spend-stop budget decisions: replacing the committed decision requires acknowledging its comment with `--seen`, even after promotion.

## How it works

Promotion and unblock checks share `execution_refusal`. Budget mutation calls `kanban_spend_override_fence.check` under the existing writer lock, identifies the latest stop episode, and resolves the last decision's portable comment UID to the receiving board's local comment ID. The first decision for an episode may proceed; a later writer must have read the intervening decision. Missing evidence refuses rather than treating a large watermark as acknowledgement. The event records its episode and decision-comment reference in the same budget mutation.

## Code

- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_db_promote.py`
- `hermes_cli/kanban_spend_promote.py`
- `hermes_cli/kanban_spend_override_fence.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_budget_cli.py`
- `hermes_cli/kanban_parser.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_retain_local.py` exercises retained execution restrictions. `tests/hermes_cli/test_kanban_spend_override_fence.py` covers independent writers, deliberate reconciliation, delayed decisions after promotion, new episodes, CLI readback acknowledgement, missing evidence, legacy event/comment order and foreign numeric comment IDs.

## Applying

Use the ordered mirror series. For an intentional correction, read the reported decision comment and supply its local ID through `hermes kanban spend-budget <task> --seen <comment-id>` together with the intended budget arguments.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

`--seen` acknowledges a decision; it does not release a provider safety restriction, renew scope or synthesize authority. Event ordering and portable comment references must not be replaced by a sender-local numeric ID.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
