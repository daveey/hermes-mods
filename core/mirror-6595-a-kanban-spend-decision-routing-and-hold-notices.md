# Kanban spend-decision routing, ownerless stops and hold notices

Patches: 5963, 5999, 6109, 6110, 6111, 6157, 6158.

## Behavior

- The earlier stranded-park changes avoid duplicate asks for an existing decision wrapper when its source already has an open human ask, is no longer spend-pending, or another wrapper owns the current stop. Same-episode legacy wrappers and dismissals remain reusable routing evidence, never release permission.
- Patch 6157 supersedes the earlier creation of ordinary-stop supervisor cards and the paced chat-idleness recheck: new routing uses genuine recorded renewal authority regardless of chat idleness, then the recorded creator task, otherwise scope_authority_unavailable. It creates no new decision/guard card.
- Hold guidance is revisioned from the effective restrictions and recorded authority, not merely the original stop payload. Authentic recipients are bound before publication; newly bound recipients can replay an existing notice. Notification delivery does not grant an observer renewal authority.
- Own-hold notice repair runs before dispatch capacity admission and ordinary spend review does not suppress immediate owner visibility. Unloaded decision notices bypass normal completion-traffic grace and slot-wait deferral. Per-card publication failures roll back and retain the hold for a later tick.

## How it works

kanban_spend_decision, kanban_stranded_park/brief, kanban_actionability and kanban_hold_notices implement routing and sweeps.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_actionability.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_decision_maintenance.py`
- `hermes_cli/kanban_hold_audience.py`
- `hermes_cli/kanban_hold_effective.py`
- `hermes_cli/kanban_hold_notices.py`
- `hermes_cli/kanban_retain.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_spend_reviewer.py`
- `hermes_cli/kanban_stranded_brief.py`
- `hermes_cli/kanban_stranded_park.py`
- `tui_gateway/kanban_unloaded.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/hermes_cli/test_kanban_contained_spend_history.py`
- `tests/hermes_cli/test_kanban_hold_authority_audience.py`
- `tests/hermes_cli/test_kanban_hold_dispatch_tick.py`
- `tests/hermes_cli/test_kanban_hold_failure_isolation.py`
- `tests/hermes_cli/test_kanban_hold_notices.py`
- `tests/hermes_cli/test_kanban_hold_payload_controls.py`
- `tests/hermes_cli/test_kanban_hold_revisions.py`
- `tests/hermes_cli/test_kanban_local_adoption.py`
- `tests/hermes_cli/test_kanban_own_hold_custody.py`
- `tests/hermes_cli/test_kanban_pool_tenant.py`
- `tests/hermes_cli/test_kanban_release.py`
- `tests/hermes_cli/test_kanban_scope_renewal.py`
- `tests/hermes_cli/test_kanban_shared_blocker_hardening.py`
- `tests/hermes_cli/test_kanban_spend_budget.py`
- `tests/hermes_cli/test_kanban_spend_budget_hold.py`
- `tests/hermes_cli/test_kanban_spend_decision.py`
- `tests/hermes_cli/test_kanban_spend_decision_causes.py`
- `tests/hermes_cli/test_kanban_spend_decision_routing.py`
- `tests/hermes_cli/test_kanban_spend_reviewer.py`
- `tests/hermes_cli/test_kanban_spend_route_repair.py`
- `tests/hermes_cli/test_kanban_stranded_park.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- None of these administrative dispositions releases execution, spend or safety restrictions.
- Unknown reasons are rendered plainly rather than as an empty-reason string.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban dispatch maintenance and decision routing inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
