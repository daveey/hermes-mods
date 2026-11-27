# Kanban chat links, adoption provenance and rejected-retain archive

Patches: 5962, 6032, 6033, 6090, 6100, 6112, 6161, 6162, 6163.

## Behavior

- A clean run end retains lease-release custody past the push grace instead of relying on an already-old outbox row.
- Explicit adoption records durable created-link provenance bound to its exact gateway/profile/session, so reconciliation does not revive unrelated released creator links.
- Chat-link reconcile preserves profile-board links only for cards absent from the replica (or an unreadable replica). A stale origin-ledger row cannot reopen a released link, but fresh evidence for that exact link can.
- A definitively rejected run-end retain is archived into an append-only receipt (original outbox row, command bytes/digest, guards and canonical proof) before retirement, with a separate bounded read-only historical correlator for evicted task/run rows.

## How it works

kanban_run_end_lease/retry, kanban_local_adoption, kanban_chat_links, kanban_rejected_retain_retirement/history/source implement these; no canonical writes are made by the archive.

## Code

Paths touched by these entries (non-test):

- `docs/kanban-adoption-terminal-subset.md`
- `hermes_cli/kanban_adoption_authority.py`
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_run.py`
- `hermes_cli/kanban_local_adoption.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_rejected_retain_history.py`
- `hermes_cli/kanban_rejected_retain_retirement.py`
- `hermes_cli/kanban_rejected_retain_source.py`
- `hermes_cli/kanban_retain.py`
- `hermes_cli/kanban_retain_fallback.py`
- `hermes_cli/kanban_run_end_lease.py`
- `hermes_cli/kanban_run_end_retry.py`
- `hermes_cli/kanban_scope_renewal.py`
- `hermes_cli/kanban_shadow_view.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_sync_rejections.py`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/hermes_cli/test_kanban_chat_links.py`
- `tests/hermes_cli/test_kanban_local_adoption_authority.py`
- `tests/hermes_cli/test_kanban_parked_lease_retry.py`
- `tests/hermes_cli/test_kanban_rejected_retain_audit_regressions.py`
- `tests/hermes_cli/test_kanban_rejected_retain_history.py`
- `tests/hermes_cli/test_kanban_rejected_retain_retirement.py`
- `tests/hermes_cli/test_kanban_rejected_retain_source.py`
- `tests/hermes_cli/test_kanban_rejected_retain_source_boundaries.py`
- `tests/hermes_cli/test_kanban_rejected_retain_source_budget.py`
- `tests/hermes_cli/test_kanban_retain_fallback.py`
- `tests/hermes_cli/test_kanban_scope_renewal_clauses.py`
- `tests/hermes_cli/test_kanban_scope_renewal_comment_refs.py`
- `tests/hermes_cli/test_kanban_shadow_view.py`
- `tests/hermes_cli/test_worker_drain_results_gate.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Missing live command custody fails closed in the live-retirement path. A separate historical path can retire only a rejected missing_decision_episode operation with evicted local rows, one uniquely correlated persisted admission/tool history, and exact canonical done-task/ended blocked-run/never-existent-event evidence; it never reconstructs command custody or grants lifecycle authority.
- Profile-board links are left untouched only for candidate cards absent from the replica (or when that replica is unreadable). A stale origin-ledger row cannot reopen an already-released link; fresh evidence for the same link still wins.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban sync custody and chat-link ledgers inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
