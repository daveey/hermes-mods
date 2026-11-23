# Authority-bound renewal of a scope-stopped episode

Source patch ordinals: 5818–5824.

## Behavior

`hermes kanban renew-scope <task> --episode <stop-uid> --mandate-comment <id> --reason <reason>` records a specific renewed mandate for a scope-stopped card. Renewal does not promote; a separate later `hermes kanban promote` is required. Provider safety restrictions are never renewable through this route.

## How it works

The caller must be a human operator, the owning chat or the live creator task, with a mandate comment written under that same authority after the stop. Identity checks inspect the whole process ancestry, refusing ambiguous/unreadable agent ancestry and the stopped run's own attempt to renew. The renewal binds the episode and its exact scope evidence plus mandate/writer identity. Edited/deleted/malformed mandates stop covering evidence; each new stop needs its own renewal.

Local deciding events use arrival order rather than claimed timestamps. Synced Boards require exactly `scope_renewal_v1`, submit through the canonical route and project only the exact returned live renewal. Primary mode pushes the mandate first. Execution context carries the full renewed mandate. Budget windows, runs and token accounting are not reset.

## Code

- `hermes_cli/kanban_scope_renewal.py`
- `hermes_cli/kanban_scope_renewal_cli.py`
- `hermes_cli/kanban_comment_writer.py`
- `hermes_cli/kanban_db_context.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_decision.py`
- `gateway/kanban_watchers_notifier.py`
- `tui_gateway/session_notifications.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

Source suites `tests/hermes_cli/test_kanban_scope_renewal.py`, `test_kanban_scope_renewal_synced.py`, `test_kanban_scope_renewal_receipts.py` and `test_kanban_scope_renewal_clauses.py` cover operator/chat/creator authority, ancestry spoofing and ambiguity, mandate writer/UID/content binding, multiple episodes, safety receipts, malformed input, CLI argv/owned connection, full context injection, unchanged accounting, capability/refusal paths and exact canonical reply projection.

## Applying

Apply the CLI, authority, evidence and follow-up guard tests together. On synced Boards deploy the matching capability before invoking renewal. Read the current stop, author a scope mandate under the authorized identity, renew that episode, then make a separate promotion decision.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

A comment, timer, review or budget decision alone is not renewal. Replica-local arrival order can differ from canonical Board order; the Board lease remains the execution authority. This does not authorize dropping worker markers, reusing another writer's comment or converting provider safety refusal into clearance.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
